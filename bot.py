import os, time, random, requests, threading
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient, ReturnDocument
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- CONFIG ---
ADMIN_ID = "7956330391" 
BOT_TOKEN = "8708969585:AAE-MQTUle1g83tGTmL0pNBm7oJOYw0u5dc" 
MONGO_URL = os.getenv("MONGO_URL")
RENDER_URL = "https://habesha-dice-bot.onrender.com" 

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']
game_db = db['game_state'] # ሰዓቱን እዚህ እናስቀምጣለን

# የመጀመሪያ ሁኔታ (Initial Memory State)
initial_state = {
    "id": "global", "status": "lobby", "timer": 30, "pot": 0, "players": {},
    "sold_tickets": {}, "current_ball": "--", "drawn_balls": [], "winner": None
}

def get_db_state():
    state = game_db.find_one({"id": "global"})
    if not state:
        game_db.insert_one(initial_state)
        return initial_state
    return state

def update_db_state(update_data):
    game_db.update_one({"id": "global"}, {"$set": update_data})

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"})
    except: print("Telegram Error")

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"]
        chat_id = str(data["message"]["chat"]["id"])

        if msg.startswith("/start"):
            parts = msg.split()
            if len(parts) > 1:
                agent_phone = parts[1]
                existing_user = wallets.find_one({"phone": chat_id})
                if not existing_user:
                    wallets.update_one(
                        {"phone": chat_id},
                        {"$set": {"phone": chat_id, "balance": 0, "referred_by": agent_phone}},
                        upsert=True
                    )

        if chat_id == ADMIN_ID:
            if msg.startswith("/add"):
                try:
                    p = msg.split()
                    wallets.update_one({"phone": p[1]}, {"$inc": {"balance": float(p[2])}}, upsert=True)
                    send_telegram(f"✅ ለ `{p[1]}` {p[2]} ETB ተጨምሯል።")
                except: send_telegram("❌ ስህተት! /add ስልክ መጠን")
    return "OK", 200

# --- የቢንጎ ህግ ማረጋገጫ ---
def is_winner(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0) 
    for i in range(5):
        if all(card[i*5 + j] in drawn_set for j in range(5)): return True 
        if all(card[j*5 + i] in drawn_set for j in range(5)): return True 
    if all(card[i*6] in drawn_set for i in range(5)): return True 
    if all(card[(i+1)*4] in drawn_set for i in range(5)): return True 
    return False

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        try:
            state = get_db_state()
            if state["status"] == "lobby":
                if state["timer"] > 0:
                    game_db.update_one({"id": "global"}, {"$inc": {"timer": -1}})
                else:
                    if len(state["players"]) >= 2:
                        update_db_state({"status": "playing", "drawn_balls": []})
                        shuffled = balls.copy(); random.shuffle(shuffled)
                        drawn = []
                        for b in shuffled:
                            current_state = get_db_state()
                            if current_state["status"] != "playing": break
                            drawn.append(b)
                            update_db_state({"current_ball": b, "drawn_balls": drawn})
                            time.sleep(5)
                    else:
                        update_db_state({"timer": 30})
            time.sleep(1)
        except Exception as e:
            time.sleep(2)

@app.route('/get_status')
def get_status():
    # ሰዓቱ መቆሙን ቼክ አድርጎ በየጥያቄው መቀስቀስ (Fail-safe)
    if not any(t.name == "BingoLoop" for t in threading.enumerate()):
        threading.Thread(target=game_loop, name="BingoLoop", daemon=True).start()
        
    state = get_db_state()
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone})
    p_data = state["players"].get(phone if phone else "", {"cards": []})
    
    return jsonify({
        **state,
        "balance": user['balance'] if user else 0,
        "my_cards": p_data["cards"],
        "active_players": len(state["players"])
    })

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json
    ph, t_num, uname = d.get('phone'), str(d.get('ticket_num')), d.get('username')
    state = get_db_state()
    
    if ph in state["players"] and len(state["players"][ph]["cards"]) >= 2:
        return jsonify({"success": False, "msg": "ከ 2 ካርተላ በላይ መግዛት አይቻልም!"})
    
    res = wallets.find_one_and_update(
        {"phone": ph, "balance": {"$gte": 10}}, 
        {"$inc": {"balance": -10}}, 
        return_document=ReturnDocument.AFTER
    )
    if res and state["status"] == "lobby":
        card = []
        for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]:
            card.append(random.sample(range(r[0], r[1]+1), 5))
        flat = [card[c][r] for r in range(5) for c in range(5)]; flat[12] = 0 
        
        game_db.update_one({"id": "global"}, {
            "$set": {f"players.{ph}": {"cards": (state["players"].get(ph, {}).get("cards", []) + [flat]), "username": uname},
                     f"sold_tickets.{t_num}": ph},
            "$inc": {"pot": 10}
        })
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "ባላንስ የለም ወይም ጨዋታ ተጀምሯል!"})

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    d = request.json
    ph, amt = str(d.get('phone')), d.get('amount')
    user = wallets.find_one({"phone": ph})
    ref = f"\n📲 ኤጀንት: {user['referred_by']}" if user and "referred_by" in user else ""
    msg = f"💰 *Deposit Request*\n📞 Phone: `{ph}`\n💵 Amount: `{amt}` ETB{ref}\n\nApprove:\n`/add {ph} {amt}`"
    send_telegram(msg)
    return jsonify({"success": True})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    ph = request.json.get('phone')
    state = get_db_state()
    p_data = state["players"].get(ph)
    if state["status"] == "playing" and p_data and any(is_winner(c, state["drawn_balls"]) for c in p_data["cards"]):
        win_amt = state["pot"] * 0.8
        wallets.update_one({"phone": ph}, {"$inc": {"balance": win_amt}})
        update_db_state({"winner": p_data["username"], "status": "result"})
        send_telegram(f"🏆 *WINNER!* \n👤 {p_data['username']} \n💰 Prize: {win_amt} ETB")
        def reset():
            time.sleep(10)
            update_db_state(initial_state)
        threading.Thread(target=reset).start()
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "ቢንጎ አልሞላም!"})

@app.route('/')
def index(): return render_template('index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
