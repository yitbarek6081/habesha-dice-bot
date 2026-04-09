import os, time, random, requests, threading
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient, ReturnDocument
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- CONFIG ---
ADMIN_ID = "7956330391" 
BOT_TOKEN = "8708969585:AAE-MQTUle1g83tGTmL0pNBm7oJOYw0u5dc" 
MONGO_URL = os.getenv("MONGO_URL") # በ Render Dashboard ላይ መግባት አለበት
RENDER_URL = "https://habesha-dice-bot.onrender.com" 

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']
game_db = db['game_state_v4']

# --- INITIAL STATE ---
def get_initial_state():
    return {
        "id": "global", "status": "lobby", "timer": 30, "pot": 0, "players": {},
        "sold_tickets": {}, "current_ball": "--", "drawn_balls": [], "winner": None
    }

def get_db_state():
    state = game_db.find_one({"id": "global"})
    if not state:
        game_db.insert_one(get_initial_state())
        return get_initial_state()
    state.pop('_id', None)
    return state

def update_db_state(update_data):
    game_db.update_one({"id": "global"}, {"$set": update_data}, upsert=True)

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except: print("Telegram Error")

# --- BINGO CHECKER ---
def is_winner(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0) # Free space (center)
    # Check Rows and Columns
    for i in range(5):
        if all(card[i*5 + j] in drawn_set for j in range(5)): return True 
        if all(card[j*5 + i] in drawn_set for j in range(5)): return True 
    # Check Diagonals
    if all(card[i*6] in drawn_set for i in range(5)): return True 
    if all(card[(i+1)*4] in drawn_set for i in range(5)): return True 
    return False

# --- GAME LOOP ---
def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        try:
            state = get_db_state()
            if state["status"] == "lobby":
                if state["timer"] > 0:
                    game_db.update_one({"id": "global"}, {"$inc": {"timer": -1}})
                else:
                    if len(state["players"]) >= 2: # ቢያንስ 2 ሰው ሲኖር ጨዋታ ይጀምራል
                        update_db_state({"status": "playing", "drawn_balls": []})
                        shuffled = balls.copy(); random.shuffle(shuffled)
                        drawn = []
                        for b in shuffled:
                            curr = get_db_state()
                            if curr["status"] != "playing": break
                            drawn.append(b)
                            update_db_state({"current_ball": b, "drawn_balls": drawn})
                            time.sleep(5) # በየ 5 ሰከንዱ ኳስ ይወጣል
                    else:
                        update_db_state({"timer": 30}) # ሰው እስኪገባ መልሶ ይቆጥራል
            time.sleep(1)
        except Exception as e:
            print(f"Error: {e}"); time.sleep(5)

# --- ROUTES ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_status')
def get_status():
    state = get_db_state()
    phone = request.args.get('phone', '')
    user = wallets.find_one({"phone": phone})
    return jsonify({
        **state,
        "balance": user['balance'] if user else 0,
        "my_cards": state["players"].get(phone, {}).get("cards", []) if phone else [],
        "active_players": len(state["players"])
    })

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json
    ph, t_num, uname = d.get('phone'), str(d.get('ticket_num')), d.get('username')
    state = get_db_state()
    if state["status"] != "lobby": return jsonify({"success": False, "msg": "ጨዋታ ተጀምሯል!"})
    
    res = wallets.find_one_and_update(
        {"phone": ph, "balance": {"$gte": 10}}, 
        {"$inc": {"balance": -10}}, 
        return_document=ReturnDocument.AFTER
    )
    if res:
        # ካርተላ ማመንጫ
        card = []
        for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]:
            card.append(random.sample(range(r[0], r[1]+1), 5))
        flat = [card[c][r] for r in range(5) for c in range(5)]
        flat[12] = 0 # Center Free Space
        
        p_data = state["players"].get(ph, {"cards": [], "username": uname})
        p_data["cards"].append(flat)
        game_db.update_one({"id": "global"}, {
            "$set": {f"players.{ph}": p_data, f"sold_tickets.{t_num}": ph},
            "$inc": {"pot": 10}
        })
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/cancel_ticket', methods=['POST'])
def cancel():
    d = request.json
    ph, t_num = d.get('phone'), str(d.get('ticket_num'))
    state = get_db_state()
    if state["sold_tickets"].get(t_num) == ph and state["status"] == "lobby":
        wallets.update_one({"phone": ph}, {"$inc": {"balance": 10}})
        game_db.update_one({"id": "global"}, {
            "$unset": {f"sold_tickets.{t_num}": ""},
            "$pull": {f"players.{ph}.cards": state["players"][ph]["cards"][-1]},
            "$inc": {"pot": -10}
        })
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/claim_bingo', methods=['POST'])
def claim():
    ph = request.json.get('phone')
    state = get_db_state()
    p_data = state["players"].get(ph)
    if state["status"] == "playing" and p_data:
        if any(is_winner(c, state["drawn_balls"]) for c in p_data["cards"]):
            win_amt = state["pot"] * 0.8
            wallets.update_one({"phone": ph}, {"$inc": {"balance": win_amt}})
            update_db_state({"winner": p_data["username"], "status": "result"})
            send_telegram(f"🏆 *BINGO!* \n👤 Winner: {p_data['username']} \n💰 Prize: {win_amt} ETB")
            threading.Thread(target=lambda: (time.sleep(10), game_db.update_one({"id": "global"}, {"$set": get_initial_state()}))).start()
            return jsonify({"success": True})
    return jsonify({"success": False, "msg": "ቢንጎ አልሞላም!"})

@app.route('/withdraw', methods=['POST'])
def withdraw():
    d = request.json
    ph, amt = d.get('phone'), float(d.get('amount'))
    user = wallets.find_one({"phone": ph})
    if user and user['balance'] >= amt:
        wallets.update_one({"phone": ph}, {"$inc": {"balance": -amt}})
        send_telegram(f"📤 *Withdraw Request*\n📞 Phone: `{ph}`\n💵 Amount: `{amt}` ETB")
        return "OK", 200
    return "Error", 400

@app.route('/request_deposit', methods=['POST'])
def req_dep():
    d = request.json
    send_telegram(f"💰 *Deposit Request*\n📞 Phone: `{d['phone']}`\n💵 Amt: `{d['amount']}` ETB\n🆔 ID: `{d.get('transaction_id')}`\n\nApprove: `/add {d['phone']} {d['amount']}`")
    return jsonify({"success": True})

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if "message" in data and "text" in data["message"]:
        msg, chat_id = data["message"]["text"], str(data["message"]["chat"]["id"])
        if chat_id == ADMIN_ID and msg.startswith("/add"):
            p = msg.split()
            if len(p) == 3:
                wallets.update_one({"phone": p[1]}, {"$inc": {"balance": float(p[2])}}, upsert=True)
                send_telegram(f"✅ {p[2]} ETB ተጨምሯል")
    return "OK", 200

if __name__ == '__main__':
    requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={RENDER_URL}/webhook")
    threading.Thread(target=game_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
