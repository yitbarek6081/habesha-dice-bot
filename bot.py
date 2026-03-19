import os, time, random, requests, threading
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient, ReturnDocument
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- CONFIG (የራስህን መረጃ እዚህ አስገባ) ---
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

# --- የድል አበሳሰር ህግ (Winning Logic) ---
def is_winner(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0) # FREE space
    # 1. Horizontal & 2. Vertical
    for i in range(5):
        if all(card[i*5 + j] in drawn_set for j in range(5)): return True
        if all(card[j*5 + i] in drawn_set for j in range(5)): return True
    # 3. Diagonal (\ and /)
    if all(card[i*6] in drawn_set for i in range(5)): return True
    if all(card[(i+1)*4] in drawn_set for i in range(5)): return True
    return False

# --- Telegram Webhook for /add command ---
@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    update = request.json
    if "message" in update and "text" in update["message"]:
        text = update["message"]["text"]
        chat_id = str(update["message"]["chat"]["id"])
        if chat_id == ADMIN_ID and text.startswith("/add"):
            try:
                parts = text.split()
                ph, amt = parts[1], float(parts[2])
                res = wallets.find_one_and_update({"phone": ph}, {"$inc": {"balance": amt}}, upsert=True, return_document=ReturnDocument.AFTER)
                send_telegram(f"✅ ለ {ph} {amt} ብር ተሞልቷል!\nአዲስ ባላንስ: {res['balance']} ETB")
            except: send_telegram("❌ ስህተት! አጻጻፍ: `/add ስልክ መጠን` ይጠቀሙ")
    return "OK"

@app.route('/get_status')
def get_status():
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone}, {"balance": 1})
    p_data = game_state["players"].get(phone, {"cards": []})
    return jsonify({**game_state, "balance": user['balance'] if user else 0, "my_cards": p_data["cards"]})

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json
    ph, t_num = d.get('phone'), str(d.get('ticket_num'))
    res = wallets.find_one_and_update({"phone": ph, "balance": {"$gte": 10}}, {"$inc": {"balance": -10}}, return_document=ReturnDocument.AFTER)
    if res and game_state["status"] == "lobby":
        game_state["sold_tickets"][t_num], game_state["pot"] = ph, game_state["pot"] + 10
        card = []
        for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]: card.append(random.sample(range(r[0], r[1]+1), 5))
        flat = [card[c][r] for r in range(5) for c in range(5)]; flat[12] = 0
        if ph not in game_state["players"]: game_state["players"][ph] = {"cards": [flat], "username": d.get('username')}
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
        if ph in game_state["players"]:
            if len(game_state["players"][ph]["cards"]) > 1: game_state["players"][ph]["cards"].pop()
            else: del game_state["players"][ph]
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    d = request.json
    msg = (f"💰 *New Deposit Request*\n📞 ስልክ: `{d['phone']}`\n💵 መጠን: `{d['amount']} ETB`\n🆔 TxID: `{d['transaction_id']}`\n\n👇 *ለመሙላት ይሄን ይንኩት (Copy):*\n`/add {d['phone']} {d['amount']}`")
    send_telegram(msg)
    return jsonify({"success": True})

# Game Loop & Other Routes... (ተመሳሳይ ናቸው)
