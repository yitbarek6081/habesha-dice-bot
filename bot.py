import os
from gevent import monkey
monkey.patch_all()

import random
import requests
import re
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient
from flask_cors import CORS
from flask_socketio import SocketIO, emit

app = Flask(__name__, template_folder='templates')
CORS(app)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")

# --- CONFIG ---
ADMIN_ID = os.getenv("ADMIN_ID", "7956330391") 
BOT_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://habesha-dice-bot.onrender.com") 

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']
wallets.create_index("phone", unique=True)

game_state = {
    "status": "lobby", 
    "timer": 30, 
    "ball_timer": 3,      
    "pot": 0, 
    "players": {},        
    "sold_tickets": {},  
    "current_ball": "--", 
    "drawn_balls": [], 
    "winner": None,
    "winning_card": None,
    "winning_ticket_num": None  
}

loop_started = False

def sanitize_input(text):
    if not text: return ""
    return re.sub(r'[^\w\s\-\+\.@]', '', str(text)).strip()

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

def set_webhook():
    webhook_url = f"{WEB_APP_URL}/webhook"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}"
    try: requests.get(url, timeout=5)
    except Exception as e: print(f"Webhook set failed: {e}")

def broadcast_game_state():
    all_balances = {}
    try:
        for u in wallets.find({}, {"phone": 1, "balance": 1}):
            if "phone" in u: all_balances[u["phone"]] = u.get("balance", 0)
    except: pass

    state_payload = {
        "status": game_state["status"], "timer": game_state["timer"],
        "ball_timer": game_state["ball_timer"], "pot": game_state["pot"],
        "sold_tickets": game_state["sold_tickets"], "current_ball": game_state["current_ball"],
        "drawn_balls": game_state["drawn_balls"], "winner": game_state["winner"],
        "winning_card": game_state["winning_card"], "winning_ticket_num": game_state["winning_ticket_num"],
        "active_players": len(game_state["players"]), "balances": all_balances  
    }
    socketio.emit('game_update', state_payload)

# --- 🛠️ የተሻሻለ የቢንጎ ቼኪንግ ሎጂክ ---
def check_winning_line(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0)

    # Rows
    for i in range(5):
        indices = [i*5 + j for j in range(5)]
        if all(card[idx] in drawn_set for idx in indices): return indices, f"አግድም (Row {i+1})"

    # Columns
    for i in range(5):
        indices = [i + j*5 for j in range(5)]
        if all(card[idx] in drawn_set for idx in indices): return indices, f"ቁልቁል (Column {i+1})"

    # Diagonals
    diag1 = [0, 6, 12, 18, 24]
    if all(card[idx] in drawn_set for idx in diag1): return diag1, "ዲያጎናል (Diagonal 📉)"
    
    diag2 = [4, 8, 12, 16, 20]
    if all(card[idx] in drawn_set for idx in diag2): return diag2, "ዲያጎናል (Diagonal 📈)"

    # Corners
    corners = [0, 4, 20, 24]
    if all(card[idx] in drawn_set for idx in corners): return corners, "አራቱ ማዕዘናት (4 Corners)"

    return None, None

def reset_game():
    game_state.update({
        "status": "lobby", "winner": None, "winning_card": None, "winning_ticket_num": None, 
        "pot": 0, "players": {}, "sold_tickets": {}, "drawn_balls": [], "current_ball": "--", 
        "timer": 30, "ball_timer": 3
    })
    broadcast_game_state()

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        current_status = game_state["status"]
        if current_status == "lobby":
            for i in range(30, -1, -1):
                if game_state["status"] != "lobby": break
                game_state["timer"] = i
                broadcast_game_state()
                socketio.sleep(1)
            
            if game_state["status"] == "lobby" and len(game_state["players"]) >= 2:
                game_state["status"] = "playing"
                game_state["drawn_balls"] = []
                shuffled = balls.copy()
                random.shuffle(shuffled)
            else:
                game_state["timer"] = 30
                shuffled = []
            
            if shuffled:
                for b in shuffled:
                    if game_state["status"] != "playing": break
                    game_state["current_ball"] = b
                    game_state["drawn_balls"].append(b)
                    broadcast_game_state()
                    socketio.sleep(4)
            
            if game_state["status"] == "playing":
                game_state["status"] = "result"
                socketio.start_background_task(lambda: (socketio.sleep(5), reset_game()))
        socketio.sleep(1)

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json or {}
    ph, t_num = sanitize_input(d.get('phone')), str(d.get('ticket_num'))
    user = wallets.find_one({"$or": [{"phone": ph}, {"telegram_id": ph}]})
    if not user: return jsonify({"success": False, "msg": "ተጠቃሚው አልተገኘም!"})
    
    db_phone = user["phone"]
    if game_state["status"] != "lobby": return jsonify({"success": False, "msg": "ጨዋታ ተጀምሯል!"})
    
    # 🛠️ የተሻሻለ የካርድ አምራች (ያለ መደርደር)
    columns = []
    for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]:
        columns.append(random.sample(range(r[0], r[1]+1), 5))
    
    flat = [0] * 25
    for c in range(5):
        for r in range(5):
            flat[r * 5 + c] = columns[c][r]
    flat[12] = 0
    
    res = wallets.find_one_and_update({"phone": db_phone, "balance": {"$gte": 10}}, {"$inc": {"balance": -10}}, return_document=True)
    if res:
        game_state["sold_tickets"][t_num] = db_phone
        game_state["pot"] += 10
        if db_phone not in game_state["players"]:
            game_state["players"][db_phone] = {"cards": {t_num: flat}, "username": res.get("username")}
        else:
            game_state["players"][db_phone]["cards"][t_num] = flat
        broadcast_game_state()
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

# ... (ሌሎች route-ዎች እንዳሉ ይቆዩ) ...

@socketio.on('connect')
def handle_connect():
    global loop_started
    if not loop_started:
        loop_started = True
        set_webhook()
        socketio.start_background_task(game_loop)
    broadcast_game_state()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
