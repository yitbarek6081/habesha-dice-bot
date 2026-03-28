import os, time, random, requests, threading
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient, ReturnDocument
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# --- CONFIG ---
ADMIN_ID = "7956330391" 
BOT_TOKEN = "8708969585:AAE-MQTUle1g83tGTmL0pNBm7oJOYw0u5dc" 
MONGO_URL = os.getenv("MONGO_URL")
RENDER_URL = "https://habesha-dice-bot.onrender.com" 

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']

game_state = {
    "status": "lobby", "timer": 30, "pot": 0, "players": {},
    "sold_tickets": {}, "current_ball": "--", "drawn_balls": [], "winner": None
}

def send_telegram(text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}
    if reply_markup: payload["reply_markup"] = reply_markup
    try: requests.post(url, json=payload)
    except: print("Telegram Error")

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if "callback_query" in data:
        call = data["callback_query"]
        cmd = call["data"].split("_") # Format: add_phone_amount
        if cmd[0] == "approve":
            ph, amt = cmd[1], float(cmd[2])
            wallets.update_one({"phone": ph}, {"$inc": {"balance": amt}}, upsert=True)
            send_telegram(f"✅ ተረጋግጧል: ለ `{ph}` {amt} ETB ተጨምሯል።")
        return "OK", 200

    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"]
        if msg.startswith("/add") and str(data["message"]["chat"]["id"]) == ADMIN_ID:
            parts = msg.split()
            if len(parts) == 3:
                wallets.update_one({"phone": parts[1]}, {"$inc": {"balance": float(parts[2])}}, upsert=True)
                send_telegram("✅ ባላንስ ተጨምሯል።")
    return "OK", 200

def is_winner(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0) # FREE space
    # Check rows, columns, and diagonals
    for i in range(5):
        if all(card[i*5 + j] in drawn_set for j in range(5)): return True
        if all(card[j*5 + i] in drawn_set for j in range(5)): return True
    if all(card[i*6] in drawn_set for i in range(5)): return True
    if all(card[(i+1)*4] in drawn_set for i in range(5)): return True
    return False

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        if game_state["status"] == "lobby":
            for i in range(30, -1, -1):
                if game_state["status"] != "lobby": break
                game_state["timer"] = i
                time.sleep(1)
            if len(game_state["players"]) >= 2:
                game_state["status"] = "playing"
                game_state["drawn_balls"] = []
                shuffled = balls.copy(); random.shuffle(shuffled)
                for b in shuffled:
                    if game_state["status"] != "playing": break
                    game_state["current_ball"] = b
                    game_state["drawn_balls"].append(b)
                    time.sleep(4) # Ball speed
            else: game_state["timer"] = 30
        time.sleep(1)

@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone})
    p_data = game_state["players"].get(phone, {"cards": []})
    return jsonify({**game_state, "balance": user['balance'] if user else 0, "my_cards": p_data["cards"], "active_players": len(game_state["players"])})

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json
    ph, t_num, uname = d.get('phone'), str(d.get('ticket_num')), d.get('username')
    if ph in game_state["players"] and len(game_state["players"][ph]["cards"]) >= 2:
        return jsonify({"success": False, "msg": "Max 2 tickets!"})
    
    res = wallets.find_one_and_update({"phone": ph, "balance": {"$gte": 10}}, {"$inc": {"balance": -10}})
    if res and game_state["status"] == "lobby":
        game_state["sold_tickets"][t_num], game_state["pot"] = ph, game_state["pot"] + 10
        # Correct Bingo Card Generation
        card_cols = [random.sample(range(i*15+1, i*15+16), 5) for i in range(5)]
        flat = [card_cols[c][r] for r in range(5) for c in range(5)]
        flat[12] = 0
        if ph not in game_state["players"]: game_state["players"][ph] = {"cards": [flat], "username": uname}
        else: game_state["players"][ph]["cards"].append(flat)
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "Insufficient Balance!"})

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    d = request.json
    markup = {"inline_keyboard": [[{"text": "✅ Approve", "callback_data": f"approve_{d['phone']}_{d['amount']}"}]]}
    send_telegram(f"💰 *Deposit*\n📞 `{d['phone']}`\n💵 `{d['amount']}` ETB\n🆔 `{d.get('transaction_id')}`", markup)
    return jsonify({"success": True})

@app.route('/withdraw', methods=['POST'])
def withdraw():
    d = request.json
    ph, amt = d.get('phone'), float(d.get('amount'))
    res = wallets.find_one_and_update({"phone": ph, "balance": {"$gte": amt}}, {"$inc": {"balance": -amt}})
    if res:
        send_telegram(f"📤 *Withdraw Request*\n📞 `{ph}`\n💵 `{amt}` ETB")
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    ph = request.json.get('phone')
    p_data = game_state["players"].get(ph)
    if game_state["status"] == "playing" and p_data and any(is_winner(c, game_state["drawn_balls"]) for c in p_data["cards"]):
        win_amt = game_state["pot"] * 0.8
        wallets.update_one({"phone": ph}, {"$inc": {"balance": win_amt}})
        game_state["winner"], game_state["status"] = p_data["username"], "result"
        send_telegram(f"🏆 *BINGO!* \n👤 {p_data['username']} \n💰 {win_amt} ETB")
        threading.Thread(target=lambda: (time.sleep(10), game_state.update({"status": "lobby", "pot": 0, "players": {}, "sold_tickets": {}, "drawn_balls": []}))).start()
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "Not a winner yet!"})

if __name__ == '__main__':
    threading.Thread(target=game_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
