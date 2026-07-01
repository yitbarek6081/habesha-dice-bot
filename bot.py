import os
import time
import random
import requests
import threading
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

# Thread lock to prevent race conditions across requests & background loop
state_lock = threading.Lock()

game_state = {
    "status": "lobby", 
    "timer": 30, 
    "pot": 0, 
    "players": {},       # chat_id -> {"cards": {ticket_num: flat_card}, "username": uname}
    "sold_tickets": {},  # ticket_num -> chat_id
    "current_ball": "--", 
    "drawn_balls": [], 
    "winner": None
}

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

def set_webhook():
    webhook_url = f"{RENDER_URL}/webhook"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}"
    try:
        requests.get(url, timeout=5)
    except Exception as e:
        print(f"Webhook set failed: {e}")

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data:
        return "OK", 200
        
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"]
        chat_id = str(data["message"]["chat"]["id"])

        # --- Referral tracking (New players only) ---
        if msg.startswith("/start"):
            parts = msg.split()
            if len(parts) > 1:
                agent_phone = parts[1]
                existing_user = wallets.find_one({"phone": chat_id})
                
                if not existing_user:
                    wallets.update_one(
                        {"phone": chat_id},
                        {"$set": {
                            "phone": chat_id, 
                            "balance": 0, 
                            "referred_by": agent_phone
                        }},
                        upsert=True
                    )

        # --- Admin controls ---
        if chat_id == ADMIN_ID and msg.startswith("/add"):
            try:
                parts = msg.split()
                if len(parts) == 3:
                    target_phone, amount = parts[1], float(parts[2])
                    wallets.update_one({"phone": target_phone}, {"$inc": {"balance": amount}}, upsert=True)
                    send_telegram(f"✅ ለ `{target_phone}` {amount} ETB ተጨምሯል።")
            except:
                send_telegram("❌ ስህተት! ፎርማቱ: /add ስልክ መጠን")
        
        elif chat_id == ADMIN_ID and msg.startswith("/sub"):
            try:
                parts = msg.split()
                if len(parts) == 3:
                    target_phone, amount = parts[1], float(parts[2])
                    wallets.update_one({"phone": target_phone}, {"$inc": {"balance": -amount}})
                    send_telegram(f"⚠️ ከ `{target_phone}` {amount} ETB ተቀንሷል።")
            except:
                send_telegram("❌ ስህተት! ፎርማቱ: /sub ስልክ መጠን")

    return "OK", 200

def is_winner(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0)  # Free space center mark
    
    # 1. የአግድም (Rows) ማረጋገጫ
    for i in range(5):
        row = [card[i*5 + j] for j in range(5)]
        if all(num in drawn_set for num in row): 
            return True 
        
    # 2. የቁመት (Columns) ማረጋገጫ
    for j in range(5):
        col = [card[i*5 + j] for i in range(5)]
        if all(num in drawn_set for num in col): 
            return True 
        
    # 3. የሰያፍ (Diagonals) ማረጋገጫ 📉 📈
    diag1 = [card[0], card[6], card[12], card[18], card[24]]
    diag2 = [card[4], card[8], card[12], card[16], card[20]]
    
    if all(num in drawn_set for num in diag1): return True
    if all(num in drawn_set for num in diag2): return True
    
    # 4. የአራቱ ማዕዘናት (4 Corners) ማረጋገጫ 🔲
    corners = [card[0], card[4], card[20], card[24]]
    if all(num in drawn_set for num in corners): return True
    
    return False

def reset_game():
    with state_lock:
        game_state.update({
            "status": "lobby", "winner": None, "pot": 0, "players": {}, 
            "sold_tickets": {}, "drawn_balls": [], "current_ball": "--", "timer": 30
        })

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        with state_lock:
            current_status = game_state["status"]

        if current_status == "lobby":
            for i in range(30, -1, -1):
                with state_lock:
                    if game_state["status"] != "lobby": 
                        break
                    game_state["timer"] = i
                time.sleep(1)
            
            with state_lock:
                if game_state["status"] == "lobby" and len(game_state["players"]) >= 2:
                    game_state["status"] = "playing"
                    game_state["drawn_balls"] = []
                    shuffled = balls.copy()
                    random.shuffle(shuffled)
                else:
                    game_state["timer"] = 30
                    shuffled = []

            # Ball drawing process
            for b in shuffled:
                with state_lock:
                    if game_state["status"] != "playing": 
                        break
                    game_state["current_ball"] = b
                    game_state["drawn_balls"].append(b)
                
                time.sleep(5)
            
            # Handle scenario where 75 balls pass without a winner declaration
            with state_lock:
                if game_state["status"] == "playing":
                    game_state["status"] = "result"
                    game_state["winner"] = "No Winner (House)"
                    send_telegram("ℹ️ ጨዋታው ያለ አሸናፊ ተጠናቋል። ሁሉም ኳሶች አልቀዋል።")
                    threading.Thread(target=lambda: (time.sleep(5), reset_game())).start()

        time.sleep(1)

@app.route('/')
def index(): 
    return render_template('index.html')

@app.route('/get_status')
def get_status():
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone}) if phone else None
    
    with state_lock:
        p_data = game_state["players"].get(phone, {"cards": {}})
        cards_list = list(p_data["cards"].values())
        
        status_copy = {
            **game_state,
            "balance": user['balance'] if user else 0, 
            "my_cards": cards_list, 
            "active_players": len(game_state["players"])
        }
    return jsonify(status_copy)

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json or {}
    ph, t_num, uname = d.get('phone'), str(d.get('ticket_num')), d.get('username')
    
    if not ph or not t_num:
        return jsonify({"success": False, "msg": "የተሳሳተ መረጃ!"})

    with state_lock:
        if game_state["status"] != "lobby":
            return jsonify({"success": False, "msg": "ጨዋታ ተጀምሯል!"})
        if t_num in game_state["sold_tickets"]:
            return jsonify({"success": False, "msg": "ይህ ካርተላ ቀድሞ ተይዟል!"})
        if ph in game_state["players"] and len(game_state["players"][ph]["cards"]) >= 2:
            return jsonify({"success": False, "msg": "ከ 2 ካርተላ በላይ መግዛት አይቻልም!"})
        
        # ደጋግሞ ክሊክ ሲያደርግ ቀድሞ በሂደት ላይ መሆኑን ለመመዝገብ
        game_state["sold_tickets"][t_num] = "RESERVED_LOCK"

    # ከዳታቤዝ ብር መቀነስ
    res = wallets.find_one_and_update(
        {"phone": ph, "balance": {"$gte": 10}}, 
        {"$inc": {"balance": -10}},
        return_document=True
    )
    
    if res:
        # የቢንጎ ቁጥሮችን ማመንጨት (Columns generation)
        columns = []
        for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]:
            columns.append(random.sample(range(r[0], r[1]+1), 5))
            
        # ቁጥሮቹን በግሪዱ አቀማመጥ ልክ ማስተካከል (Row-by-Row እንዲቀመጡ)
        flat = []
        for row_idx in range(5):
            for col_idx in range(5):
                flat.append(columns[col_idx][row_idx])
                
        flat[12] = 0  # Free Space
        
        with state_lock:
            # በሂደቱ መሃል ጨዋታው ተጀምሮ ከሆነ ብሩን መመለስ
            if game_state["status"] != "lobby":
                if game_state["sold_tickets"].get(t_num) == "RESERVED_LOCK":
                    del game_state["sold_tickets"][t_num]
                wallets.update_one({"phone": ph}, {"$inc": {"balance": 10}})
                return jsonify({"success": False, "msg": "ጨዋታ ተጀምሯል!"})
                
            game_state["sold_tickets"][t_num] = ph
            game_state["pot"] += 10
            
            if ph not in game_state["players"]:
                game_state["players"][ph] = {"cards": {t_num: flat}, "username": uname}
            else:
                game_state["players"][ph]["cards"][t_num] = flat
                
        return jsonify({"success": True})
    
    # ባላንስ ከሌለው ወይም ችግር ካለ መቆለፊያውን መልሶ ማንሳት
    with state_lock:
        if game_state["sold_tickets"].get(t_num) == "RESERVED_LOCK":
            del game_state["sold_tickets"][t_num]
            
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    d = request.json or {}
    ph, t_num = d.get('phone'), str(d.get('ticket_num'))
    
    with state_lock:
        if game_state["status"] == "lobby" and game_state["sold_tickets"].get(t_num) == ph:
            wallets.update_one({"phone": ph}, {"$inc": {"balance": 10}}) 
            game_state["pot"] -= 10
            del game_state["sold_tickets"][t_num]
            
            if ph in game_state["players"]:
                if t_num in game_state["players"][ph]["cards"]:
                    del game_state["players"][ph]["cards"][t_num]
                if not game_state["players"][ph]["cards"]: 
                    del game_state["players"][ph]
            return jsonify({"success": True})
            
    return jsonify({"success": False, "msg": "መሰረዝ አይቻልም!"})

@app.
