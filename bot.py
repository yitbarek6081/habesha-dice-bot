import os, time, random, requests, threading
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient, ReturnDocument
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- CONFIG ---
ADMIN_ID = "7956330391" 
BOT_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']

game_state = {
    "status": "lobby", "timer": 30, "pot": 0, "players": {},
    "sold_tickets": {}, "current_ball": "--", "drawn_balls": [], "winner": None
}

def send_telegram(text):
    if BOT_TOKEN:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        try: requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"})
        except: print("Telegram Error")

# --- አሸናፊ ለመሆን (Horizontal, Vertical, Diagonal) ---
def is_winner(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0) # FREE space
    # 1. Horizontal & Vertical
    for i in range(5):
        if all(card[i*5 + j] in drawn_set for j in range(5)): return True
        if all(card[j*5 + i] in drawn_set for j in range(5)): return True
    # 2. Diagonal (\ and /)
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
                    time.sleep(5) # እያንዳንዱ እጣ 5 ሰከንድ
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

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    d = request.json
    # ለኮፒ እንዲመች ተደርጎ የተዘጋጀ መልእክት
    msg = f"💰 *Deposit*\n📞 `{d['phone']}`\n💵 `{d['amount']} ETB`\n\n👇 *ለመሙላት ይሄን ይንኩት:*\n`/add {d['phone']} {d['amount']}`"
    send_telegram(msg)
    return jsonify({"success": True})

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    update = request.json
    if "message" in update and "text" in update["message"]:
        t = update["message"]["text"]
        if str(update["message"]["chat"]["id"]) == ADMIN_ID and t.startswith("/add"):
            p = t.split(); ph, amt = p[1], float(p[2])
            wallets.update_one({"phone": ph}, {"$inc": {"balance": amt}}, upsert=True)
            send_telegram(f"✅ ለ {ph} {amt} ብር ተሞልቷል!")
    return "OK"

# የተቀሩት (Claim, Withdraw, Cancel) ባሉበት ይቀጥላሉ...
if __name__ == '__main__':
    threading.Thread(target=game_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
