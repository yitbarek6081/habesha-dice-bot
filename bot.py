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
game_db = db['game_state_v4'] # ጨዋታው በዳታቤዝ እንዲቀመጥ

# የመጀመሪያ ሁኔታ
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
    return state

def update_db_state(update_data):
    game_db.update_one({"id": "global"}, {"$set": update_data}, upsert=True)

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except: print("Telegram Error")

# --- WEBHOOK SETUP ---
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
                if not wallets.find_one({"phone": chat_id}):
                    wallets.update_one({"phone": chat_id}, {"$set": {"phone": chat_id, "balance": 0, "referred_by": agent_phone}}, upsert=True)

        if chat_id == ADMIN_ID:
            if msg.startswith("/add"):
                p = msg.split()
                if len(p) == 3:
                    wallets.update_one({"phone": p[1]}, {"$inc": {"balance": float(p[2])}}, upsert=True)
                    send_telegram(f"✅ ለ `{p[1]}` {p[2]} ETB ተጨምሯል።")
            elif msg.startswith("/sub"):
                p = msg.split()
                if len(p) == 3:
                    wallets.update_one({"phone": p[1]}, {"$inc": {"balance": -float(p[2])}})
                    send_telegram(f"⚠️ ከ `{p[1]}` {p[2]} ETB ተቀንሷል።")

    return "OK", 200

# --- BINGO LOGIC ---
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
                        shuf = balls.copy(); random.shuffle(shuf)
                        drawn = []
                        for b in shuf:
                            curr = get_db_state()
                            if curr["status"] != "playing": break
                            drawn.append(b)
                            update_db_state({"current_ball": b, "drawn_balls": drawn})
                            time.sleep(5)
                    else:
                        update_db_state({"timer": 30})
            time.sleep(1)
        except Exception as e:
            print(f"Loop Error: {e}"); time.sleep(5)

# --- ROUTES ---
@app.route('/get_status')
def get_status():
    state = get_db_state()
    phone = request.args.get('phone', '')
    user = wallets.find_one({"phone": phone})
    state.pop('_id', None) # JSON Error ለመከላከል
    return jsonify({
        **state,
        "balance": user['balance'] if user else 0,
        "my_cards": state["players"].get(phone, {}).get("cards", []),
        "active_players": len(state["players"])
    })

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json
    ph, t_num, uname = d.get('phone'), str(d.get('ticket_num')), d.get('username')
    state = get_db_state()
    if state["status"] != "lobby": return jsonify({"success": False, "msg": "ጨዋታ ተጀምሯል!"})
    
    res = wallets.find_one_and_update({"phone": ph, "balance": {"$gte": 10}}, {"$inc": {"balance": -10}})
    if res:
        card = []
        for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]: card.append(random.sample(range(r
