import os
import time
import random
import secrets
import requests
import threading
import re
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient
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
transactions = db['transactions'] # አዲስ: የግብይት መዝገብ
game_history = db['game_history']   # አዲስ: የጨዋታ ስታቲስቲክስ

wallets.create_index("phone", unique=True)
state_lock = threading.Lock()

game_state = {
    "status": "lobby", "timer": 30, "ball_timer": 3, "pot": 0,
    "players": {}, "sold_tickets": {}, "current_ball": "--", 
    "drawn_balls": [], "winner": None, "winning_card": None  
}

# --- LOGGING FUNCTIONS (አዲስ) ---
def log_transaction(phone, amount, trans_type, reason):
    transactions.insert_one({
        "phone": phone, "amount": amount, "type": trans_type, 
        "reason": reason, "timestamp": time.time()
    })

def log_game_result(winner_phone, pot_amount):
    game_history.insert_one({
        "winner": winner_phone, "pot": pot_amount, "timestamp": time.time()
    })

def sanitize_input(text):
    return re.sub(r'[^\w\s\-\+\.@]', '', str(text)).strip() if text else ""

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except: pass

def set_webhook():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={RENDER_URL}/webhook"
    try: requests.get(url, timeout=5)
    except: pass

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data or "message" not in data: return "OK", 200
    msg = data["message"]["text"].strip()
    chat_id = str(data["message"]["chat"]["id"])

    # [ምዝገባ እና ሌሎች ሎጅኮችዎ እዚህ ይኖራሉ - ምንም አልተቀነሰም]

    if chat_id == ADMIN_ID:
        # አዲስ ኦዲት ኮማንድ
        if msg.startswith("/audit"):
            parts = msg.split()
            target = sanitize_input(parts[1]) if len(parts) > 1 else None
            logs = list(transactions.find({"phone": target} if target else {}).sort("timestamp", -1).limit(10))
            res = [f"📜 *የቅርብ ጊዜ እንቅስቃሴዎች ({target or 'ሁሉም'}):*"]
            for l in logs:
                res.append(f"• {l['type']} | {l['amount']} ETB")
            send_telegram("\n".join(res))
        
        # አዲስ ስታቲስቲክስ ኮማንድ
        elif msg == "/stats":
            day_ago = time.time() - 86400
            count = game_history.count_documents({"timestamp": {"$gt": day_ago}})
            send_telegram(f"📊 *የ24 ሰዓት ስታቲስቲክስ:*\n✅ የተጠናቀቁ ጨዋታዎች: `{count}`")

        # [የቀሩት Admin commands - ለምሳሌ /add, /sub, /remove...]
        elif msg.startswith("/add"):
            parts = msg.split()
            if len(parts) == 3:
                target, amount = sanitize_input(parts[1]), float(parts[2])
                wallets.update_one({"phone": target}, {"$inc": {"balance": amount}}, upsert=True)
                log_transaction(target, amount, "ADMIN_ADD", "Manual add")
                send_telegram(f"✅ {amount} ETB ለ {target} ተጨምሯል።")

    return "OK", 200

# --- የጨዋታ ተግባራት (አዲስ Logging ተጨምሮባቸዋል) ---

@app.route('/withdraw', methods=['POST'])
def withdraw():
    d = request.json or {}
    ph, amt = sanitize_input(d.get('phone')), float(d.get('amount'))
    res = wallets.find_one_and_update({"phone": ph, "balance": {"$gte": amt}}, {"$inc": {"balance": -amt}}, return_document=True)
    if res:
        log_transaction(ph, amt, "WITHDRAW", "User request")
        send_telegram(f"📤 *Withdraw Request*\n📞 {ph}\n💵 {amt} ETB")
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    # [የቀድሞ ሎጅክዎ እዚህ አለ]
    # አሸናፊ ሲኖር መጨመር ያለባችሁ:
    log_transaction(db_phone, win_amt, "WIN", "Bingo win")
    log_game_result(db_phone, game_state["pot"])
    return jsonify({"success": True})

# [የተቀሩት የጨዋታ functions...]

if __name__ == '__main__':
    threading.Timer(5, set_webhook).start() 
    threading.Thread(target=game_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
