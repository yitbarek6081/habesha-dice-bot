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

game_state = {
    "status": "lobby", "timer": 30, "pot": 0, "players": {},
    "sold_tickets": {}, "current_ball": "--", "drawn_balls": [], "winner": None
}

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"})
    except: print("Telegram Error")

def set_webhook():
    webhook_url = f"{RENDER_URL}/webhook"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}"
    try:
        r = requests.get(url)
        print(f"Webhook set result: {r.json()}")
    except:
        print("Webhook set failed")

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"]
        chat_id = str(data["message"]["chat"]["id"])
        if chat_id == ADMIN_ID and msg.startswith("/add"):
            try:
                parts = msg.split()
                if len(parts) == 3:
                    target_phone = parts[1]
                    amount = float(parts[2])
                    wallets.update_one({"phone": target_phone}, {"$inc": {"balance": amount}}, upsert=True)
                    send_telegram(f"✅ ለ `{target_phone}` {amount} ETB ተጨምሯል።")
                else:
                    send_telegram("❌ ስህተት! ፎርማቱ: `/add phone amount` መሆን አለበት።")
            except Exception as e:
                send_telegram(f"❌ ስህተት: {str(e)}")
    return "OK", 200

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
        if game_state["status"] == "lobby":
            for i in range(30, -1, -1):
                if game_state["status"] != "lobby": break
                game_state["timer"] = i
                time.sleep(1)
            if len(game_state["players"]) >= 2:
                game_state["status"] = "playing"
                shuffled = balls.copy(); random.shuffle(shuffled)
                for b in shuffled:
                    if game_state["status"] != "playing": break
                    game_state["current_ball"] = b
                    game_state["drawn_balls"].append(b)
                    time.sleep(5)
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
    res = wallets.find_one_and_update({"phone": ph, "balance": {"$gte": 10}}, {"$inc": {"balance": -10}}, return_document=ReturnDocument.AFTER)
    if res and game_state["status"] == "lobby":
        game_state["sold_tickets"][t_num], game_state["pot"] = ph, game_state["pot"] + 10
        card = []
        for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]: card.append(random.sample(range(r[0], r[1]+1), 5))
        flat = [card[c][r] for r in range(5) for c in range(5)]; flat[12] = 0
        if ph not in game_state["players"]: game_state["players"][ph] = {"cards": [flat], "username": uname}
        else: game_state["players"][ph]["cards"].append(flat)
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    d = request.json
    ph, t_num = d.get('phone'), str(d.get('ticket_num'))
    if game_state["status"] == "lobby" and game_state["sold_tickets"].get(t_num) == ph:
        wallets.update_one({"phone": ph}, {"$inc": {"balance": 10}})
        del game_state["sold_tickets"][t_num]
        game_state["pot"] -= 10
        if ph in game_state["players"] and game_state["players"][ph]["cards"]:
            game_state["players"][ph]["cards"].pop()
            if not game_state["players"][ph]["cards"]:
                del game_state["players"][ph]
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    d = request.json
    msg = f"💰 *Deposit Request*\n📞 Phone: `{d['phone']}`\n💵 Amount: `{d['amount']}`\n\n👇 *Click to Copy:*\n`/add {d['phone']} {d['amount']}`"
    send_telegram(msg)
    return jsonify({"success": True})

@app.route('/withdraw', methods=['POST'])
def withdraw():
    d = request.json
    ph, amt = d.get('phone'), float(d.get('amount'))
    user = wallets.find_one({"phone": ph})
    if user and user.get('balance', 0) >= amt:
        wallets.update_one({"phone": ph}, {"$inc": {"balance": -amt}})
        send_telegram(f"💸 *Withdraw Request*\n📞 `{ph}`\n💵 `{amt} ETB`")
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "Insufficient balance!"})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    ph = request.json.get('phone')
    p_data = game_state["players"].get(ph)
    if game_state["status"] == "playing" and p_data and any(is_winner(c, game_state["drawn_balls"]) for c in p_data["cards"]):
        win_amt = game_state["pot"] * 0.8
        wallets.update_one({"phone": ph}, {"$inc": {"balance": win_amt}})
        game_state["winner"], game_state["status"] = p_data["username"], "result"
        send_telegram(f"🏆 *Winner Found!*\n👤: {p_data['username']}\n💰 Prize: {win_amt} ETB")
        def reset():
            time.sleep(10); game_state.update({"status": "lobby", "winner": None, "pot": 0, "players": {}, "sold_tickets": {}, "drawn_balls": [], "current_ball": "--", "timer": 30})
        threading.Thread(target=reset).start()
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "ቢንጎ አልሞላም!"})

if __name__ == '__main__':
    threading.Timer(5, set_webhook).start() 
    threading.Thread(target=game_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
