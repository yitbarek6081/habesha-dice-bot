import os, time, random, requests, threading
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- CONFIG ---
ADMIN_ID = "7956330391" 
ADMIN_PHONE = "0945880474" 
BOT_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']

game_state = {
    "status": "lobby", 
    "timer": 30,
    "pot": 0,
    "players": {},
    "sold_tickets": {}, 
    "current_ball": "--",
    "drawn_balls": [],
    "winner": None
}

def send_telegram_msg(msg):
    if BOT_TOKEN:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": ADMIN_ID, "text": msg, "parse_mode": "HTML"}
        try: requests.post(url, json=payload)
        except: pass

# --- 1. GAME LOGIC ---
def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        for i in range(30, -1, -1):
            game_state["timer"] = i
            time.sleep(1)
        
        if len(game_state["players"]) >= 2:
            game_state["status"] = "playing"
            shuffled = balls.copy()
            random.shuffle(shuffled)
            for b in shuffled:
                if game_state["status"] != "playing": break
                game_state["current_ball"] = b
                game_state["drawn_balls"].append(b)
                time.sleep(4)
            time.sleep(10)
            game_state.update({"status":"lobby","winner":None,"pot":0,"players":{},"sold_tickets":{},"drawn_balls":[],"current_ball":"--"})
        else:
            game_state["timer"] = 30

# --- 2. BOT POLLING LOGIC ---
def bot_polling():
    last_update_id = 0
    print("🤖 ቦቱ ትዕዛዝ ለመቀበል ዝግጁ ነው...")
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={last_update_id + 1}&timeout=30"
            res = requests.get(url, timeout=35).json()
            if res.get("result"):
                for update in res["result"]:
                    last_update_id = update["update_id"]
                    if "message" in update and "text" in update["message"]:
                        msg_text = update["message"]["text"].strip()
                        sender_id = str(update["message"]["chat"]["id"])
                        
                        if sender_id == ADMIN_ID:
                            if msg_text.startswith("/add"):
                                parts = msg_text.split()
                                if len(parts) == 3:
                                    p, a = parts[1], float(parts[2])
                                    wallets.update_one({"phone": p}, {"$inc": {"balance": a}}, upsert=True)
                                    send_telegram_msg(f"✅ ተሳክቷል! ለ {p} <b>{a} ETB</b> ተጨምሯል!")
                            
                            elif msg_text.startswith("/minus"):
                                parts = msg_text.split()
                                if len(parts) == 3:
                                    p, a = parts[1], float(parts[2])
                                    wallets.update_one({"phone": p}, {"$inc": {"balance": -a}})
                                    send_telegram_msg(f"✅ ተሳክቷል! ከ {p} <b>{a} ETB</b> ተቀንሷል!")
            time.sleep(1)
        except Exception as e:
            print(f"Bot Error: {e}")
            time.sleep(5)

# --- ROUTES ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone})
    p_data = game_state["players"].get(phone, {"active": False, "cards": []})
    return jsonify({**game_state, "balance": user['balance'] if user else 0, "my_cards": p_data["cards"], "is_player": p_data["active"]})

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    if game_state["status"] != "lobby": return jsonify({"success":False, "msg":"ሽያጭ ተዘግቷል!"})
    data = request.json
    ph, t_num, uname = data.get('phone'), str(data.get('ticket_num')), data.get('username')
    if t_num in game_state["sold_tickets"]: return jsonify({"success":False, "msg":"ተይዟል!"})
    p_info = game_state["players"].get(ph, {"cards": []})
    if len(p_info["cards"]) >= 2: return jsonify({"success":False, "msg":"ቢበዛ 2 ካርቴላ!"})
    user = wallets.find_one({"phone": ph})
    if not user or user.get('balance', 0) < 10: return jsonify({"success":False, "msg":"በቂ ሂሳብ የሎትም!"})
    wallets.update_one({"phone": ph}, {"$inc": {"balance": -10}})
    game_state["sold_tickets"][t_num] = ph
    game_state["pot"] += 10
    card = []
    for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]:
        card.append(random.sample(range(r[0], r[1]+1), 5))
    flat = []
    for r in range(5):
        for c in range(5): flat.append(card[c][r])
    flat[12] = 0
    if ph not in game_state["players"]: game_state["players"][ph] = {"cards": [flat], "active": True, "username": uname}
    else: game_state["players"][ph]["cards"].append(flat)
    return jsonify({"success": True})

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    if game_state["status"] != "lobby": return jsonify({"success":False, "msg":"ጌሙ ተጀምሯል!"})
    data = request.json
    ph, t_num = data.get('phone'), str(data.get('ticket_num'))
    if game_state["sold_tickets"].get(t_num) == ph:
        del game_state["sold_tickets"][t_num]
        game_state["pot"] -= 10
        wallets.update_one({"phone": ph}, {"$inc": {"balance": 10}})
        if ph in game_state["players"]:
            game_state["players"][ph]["cards"].pop()
            if not game_state["players"][ph]["cards"]: del game_state["players"][ph]
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    d = request.json
    msg = f"💰 <b>ተቀማጭ!</b>\n👤 {d['phone']}\n💵 {d['amount']} ETB\n🧾 {d['transaction_id']}\n<code>/add {d['phone']} {d['amount']}</code>"
    send_telegram_msg(msg)
    return jsonify({"success": True})

@app.route('/request_withdraw', methods=['POST'])
def request_withdraw():
    d = request.json
    user = wallets.find_one({"phone": d['phone']})
    if not user or user.get('balance', 0) < float(d['amount']): return jsonify({"success": False, "msg":"በቂ ሂሳብ የሎትም!"})
    msg = f"📤 <b>ወጪ!</b>\n👤 {d['phone']}\n💰 {d['amount']} ETB\n📱 {d['target_phone']}\n<code>/minus {d['phone']} {d['amount']}</code>"
    send_telegram_msg(msg)
    return jsonify({"success": True})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    ph = request.json.get('phone')
    if game_state["status"] != "playing": return jsonify({"success": False})
    p_data = game_state["players"].get(ph)
    drawn = {int(b[1:]) for b in game_state["drawn_balls"] if len(b) > 1}
    drawn.add(0)
    def is_win(c):
        wins = [range(0,5), range(5,10), range(10,15), range(15,20), range(20,25), range(0,25,5), range(1,25,5), range(2,25,5), range(3,25,5), range(4,25,5), [0,6,12,18,24], [4,8,12,16,20]]
        return any(all(c[i] in drawn for i in w) for w in wins)
    if p_data and any(is_win(c) for c in p_data["cards"]):
        win_amt = game_state["pot"] * 0.8
        wallets.update_one({"phone": ph}, {"$inc": {"balance": win_amt}})
        wallets.update_one({"phone": ADMIN_PHONE}, {"$inc": {"balance": game_state["pot"]*0.2}}, upsert=True)
        game_state["winner"] = p_data["username"]; game_state["status"] = "result"
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "ገና ነዎት!"})

# --- START THREADS AND APP ---
if __name__ == '__main__':
    # Threadዎቹን ከመጥራታችን በፊት ፈንክሽኖቹ ከላይ መጻፋቸውን አረጋግጠናል
    threading.Thread(target=game_loop, daemon=True).start()
    threading.Thread(target=bot_polling, daemon=True).start()
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
