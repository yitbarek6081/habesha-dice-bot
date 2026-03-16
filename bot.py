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
        if keyboard:
            payload["reply_markup"] = keyboard
        requests.post(url, json=payload)

# --- GAME LOGIC ---
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

# --- WALLET ACTIONS (DEPOSIT & WITHDRAW) ---

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    data = request.json
    phone, amt, tid = data.get('phone'), data.get('amount'), data.get('transaction_id')
    
    keyboard = {"inline_keyboard": [[
        {"text": "✅ አጽድቅ (Add)", "callback_data": f"add_{phone}_{amt}"},
        {"text": "❌ ሰርዝ", "callback_data": "reject"}
    ]]}
    
    msg = f"🔔 አዲስ የገንዘብ ማስገቢያ ጥያቄ!\n👤 ስልክ: {phone}\n💰 መጠን: {amt} ETB\n🧾 ID: {tid}"
    send_telegram_msg(msg, keyboard)
    return jsonify({"success": True, "msg": "ጥያቄው ለአድሚን ተልኳል።"})

@app.route('/request_withdraw', methods=['POST'])
def request_withdraw():
    data = request.json
    phone, amt, target = data.get('phone'), float(data.get('amount')), data.get('target_phone')
    
    user = wallets.find_one({"phone": phone})
    if not user or user.get('balance', 0) < amt:
        return jsonify({"success": False, "msg": "በቂ ሂሳብ የሎትም!"})

    keyboard = {"inline_keyboard": [[
        {"text": "✅ ተከፍሏል (ቀንስ)", "callback_data": f"wit_{phone}_{amt}"},
        {"text": "❌ ሰርዝ", "callback_data": "reject"}
    ]]}
    
    msg = f"📤 የገንዘብ ማውጫ ጥያቄ!\n👤 ተጫዋች: {phone}\n💰 መጠን: {amt} ETB\n📱 መላኪያ ስልክ: {target}"
    send_telegram_msg(msg, keyboard)
    return jsonify({"success": True, "msg": "የማውጫ ጥያቄው ተልኳል።"})

# --- TELEGRAM WEBHOOK (FOR BUTTONS) ---

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.json
    if "callback_query" in update:
        query = update["callback_query"]
        data = query["data"]
        msg_id = query["message"]["message_id"]
        chat_id = query["message"]["chat"]["id"]

        if data.startswith("add_"):
            _, p, a = data.split("_")
            wallets.update_one({"phone": p}, {"$inc": {"balance": float(a)}}, upsert=True)
            txt = f"✅ ተሳክቷል! ለ {p} {a} ETB ተጨምሯል።"
        elif data.startswith("wit_"):
            _, p, a = data.split("_")
            wallets.update_one({"phone": p}, {"$inc": {"balance": -float(a)}})
            txt = f"✅ ተሳክቷል! ከ {p} {a} ETB ተቀንሷል።"
        else:
            txt = "❌ ጥያቄው ተሰርዟል።"

        # Edit message to show result
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
        requests.post(url, json={"chat_id": chat_id, "message_id": msg_id, "text": txt})
        
    return "ok"

# --- CORE BINGO ENDPOINTS ---

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    data = request.json
    phone, t_num = data.get('phone'), str(data.get('ticket_num'))
    if game_state["status"] != "lobby": return jsonify({"success":False, "msg":"ሽያጭ ተዘግቷል!"})
    if t_num in game_state["sold_tickets"]: return jsonify({"success":False, "msg":"ተይዟል!"})
    
    user = wallets.find_one({"phone": phone})
    if not user or user.get('balance', 0) < 10: return jsonify({"success":False, "msg":"በቂ ሂሳብ የሎትም!"})

    wallets.update_one({"phone": phone}, {"$inc": {"balance": -10}})
    game_state["sold_tickets"][t_num] = phone
    game_state["pot"] += 10
    
    card = generate_bingo_card()
    if phone not in game_state["players"]:
        game_state["players"][phone] = {"cards": [card], "active": True}
    else:
        game_state["players"][phone]["cards"].append(card)
    return jsonify({"success": True})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    phone = request.json.get('phone')
    if game_state["status"] != "playing": return jsonify({"success": False})
    
    win_amt = game_state["pot"] * 0.8
    admin_amt = game_state["pot"] * 0.2
    wallets.update_one({"phone": phone}, {"$inc": {"balance": win_amt}})
    wallets.update_one({"phone": ADMIN_PHONE},
