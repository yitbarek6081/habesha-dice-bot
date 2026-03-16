import os, time, random, requests
from flask import Flask, render_template, jsonify, request
from threading import Thread
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

def send_telegram_msg(msg, keyboard=None):
    if BOT_TOKEN:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": ADMIN_ID, "text": msg}
        if keyboard: payload["reply_markup"] = keyboard
        requests.post(url, json=payload)

def generate_bingo_card():
    card = []
    ranges = [(1,15), (16,30), (31,45), (46,60), (61,75)]
    for r in ranges:
        col = random.sample(range(r[0], r[1]+1), 5)
        card.append(col)
    flat_card = []
    for row in range(5):
        for col in range(5):
            flat_card.append(card[col][row])
    flat_card[12] = 0 
    return flat_card

@app.route('/')
def index(): return render_template('index.html')

# --- WALLET SYSTEM ---
@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    data = request.json
    p, amt, tid = data.get('phone'), data.get('amount'), data.get('transaction_id')
    keyboard = {"inline_keyboard": [[
        {"text": "✅ አጽድቅ (Add)", "callback_data": f"add_{p}_{amt}"},
        {"text": "❌ ሰርዝ", "callback_data": "reject"}
    ]]}
    msg = f"🔔 አዲስ ተቀማጭ ጥያቄ!\n👤 ስልክ: {p}\n💰 መጠን: {amt} ETB\n🧾 ID: {tid}"
    send_telegram_msg(msg, keyboard)
    return jsonify({"success": True, "msg": "ጥያቄው ለአድሚን ተልኳል!"})

@app.route('/request_withdraw', methods=['POST'])
def request_withdraw():
    data = request.json
    p, amt, target = data.get('phone'), float(data.get('amount')), data.get('target_phone')
    user = wallets.find_one({"phone": p})
    if not user or user.get('balance', 0) < amt:
        return jsonify({"success": False, "msg": "በቂ ሂሳብ የሎትም!"})
    keyboard = {"inline_keyboard": [[
        {"text": "✅ ተከፍሏል (ቀንስ)", "callback_data": f"wit_{p}_{amt}"},
        {"text": "❌ ሰርዝ", "callback_data": "reject"}
    ]]}
    msg = f"📤 የገንዘብ ማውጫ ጥያቄ!\n👤 ተጫዋች: {p}\n💰 መጠን: {amt} ETB\n📱 መላኪያ ስልክ: {target}"
    send_telegram_msg(msg, keyboard)
    return jsonify({"success": True, "msg": "የማውጫ ጥያቄው ተልኳል።"})

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.json
    if "callback_query" in update:
        query = update["callback_query"]
        data, mid, cid = query["data"], query["message"]["message_id"], query["message"]["chat"]["id"]
        if data.startswith("add_"):
            _, p, a = data.split("_")
            wallets.update_one({"phone": p}, {"$inc": {"balance": float(a)}}, upsert=True)
            txt = f"✅ ተሳክቷል! ለ {p} {a} ETB ተጨምሯል።"
        elif data.startswith("wit_"):
            _, p, a = data.split("_")
            wallets.update_one({"phone": p}, {"$inc": {"balance": -float(a)}})
            txt = f"✅ ተሳክቷል! ከ {p} {a} ETB ተቀንሷል።"
        else: txt = "❌ ጥያቄው ተሰርዟል።"
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText", 
                      json={"chat_id": cid, "message_id": mid, "text": txt})
    return "ok"

# --- GAME ACTIONS & AUTO-VERIFY ---
@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    phone = request.json.get('phone')
    if game_state["status"] != "playing": return jsonify({"success": False, "msg": "ጌሙ አልተጀመረም!"})
    player_data = game_state["players"].get(phone)
    if not player_data: return jsonify({"success": False})

    drawn_numbers = {int(b[1:]) for b in game_state["drawn_balls"]}
    drawn_numbers.add(0) # FREE Space

    def is_winner(card):
        for i in range(0, 25, 5): 
            if all(card[i+j] in drawn_numbers for j in range(5)): return True
        for i in range(5): 
            if all(card[i+j*5] in drawn_numbers for j in range(5)): return True
        if all(card[i*6] in drawn_numbers for i in range(5)) or all(card[(i+1)*4] in drawn_numbers for i in range(5)): return True
        return False

    if any(is_winner(c) for c in player_data["cards"]):
        win_amt, admin_amt = game_state["pot"] * 0.8, game_state["pot"] * 0.2
        wallets.update_one({"phone": phone}, {"$inc": {"balance": win_amt}})
        wallets.update_one({"phone": ADMIN_PHONE}, {"$inc": {"balance": admin_amt}}, upsert=True)
        game_state["winner"], game_state["status"] = phone, "result"
        send_telegram_msg(f"🏆 ቢንጎ ተረጋግጧል!\n👤 አሸናፊ: {phone}\n💰 ሽልማት: {win_amt} ETB")
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "ካርቴላዎ ገና አልሞላም!"})

@app.route('/get_status')
def get_status():
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone})
    if not user and phone: # አዲስ ተጫዋች ከሆነ ዋሌት መክፈት
        wallets.insert_one({"phone": phone, "balance": 0})
        user = {"balance": 0}
    p_data = game_state["players"].get(phone, {"active": False, "cards": []})
    return jsonify({**game_state, "balance": user['balance'] if user else 0, "my_cards": p_data["cards"], "is_player": p_data["active"]})

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    data = request.json
    phone, t_num = data.get('phone'), str(data.get('ticket_num'))
    if game_state["status"] != "lobby": return jsonify({"success":False, "msg":"ሽያጭ ተዘግቷል!"})
    if t_num in game_state["sold_tickets"]: return jsonify({"success":False, "msg":"ተይዟል!"})
    user = wallets.find_one({"phone": phone})
    if not user or user.get('balance', 0) < 10: return jsonify({"success":False, "msg":"በቂ ሂሳብ የሎትም!"})
    wallets.update_one({"phone": phone}, {"$inc": {"balance": -10}})
    game_state["sold_tickets"][t_num], game_state["pot"] = phone, game_state["pot"] + 10
    card = generate_bingo_card()
    if phone not in game_state["players"]: game_state["players"][phone] = {"cards": [card], "active": True}
    else: game_state["players"][phone]["cards"].append(card)
    return jsonify({"success": True})

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        game_state.update({"status":"lobby","winner":None,"pot":0,"players":{},"sold_tickets":{},"drawn_balls":[]})
        for i in range(30, 0, -1): (game_state.update({"timer":i}), time.sleep(1))
        game_state["status"] = "preparing"
        time.sleep(3)
        if len(game_state["players"]) >= 2:
            game_state["status"] = "playing"
            shuffled = balls.copy()
            random.shuffle(shuffled)
            for b in shuffled:
                if game_state["status"] != "playing": break
                game_state["current_ball"], game_state["drawn_balls"] = b, game_state["drawn_balls"] + [b]
                time.sleep(5)
            time.sleep(5)
        else: time.sleep(2)

Thread(target=game_loop, daemon=True).start()
if __name__ == '__main__': app.run(host='0.0.0.0', port=10000)
