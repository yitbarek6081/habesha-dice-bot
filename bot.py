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

# gevent ለትንሽ ሰርቨር እጅግ ፈጣን እና ቀልጣፋ ነው
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")

# --- CONFIG ---
ADMIN_ID = os.getenv("ADMIN_ID", "7956330391") 
BOT_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://habesha-dice-bot.onrender.com") 

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']

try:
    wallets.create_index("phone", unique=True)
except Exception:
    pass

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
    "winning_ticket_num": None,
    "winning_indices": None,
    "winning_line_name": None,  
    "all_cards": {}  
}

loop_started = False

def sanitize_input(text):
    if not text: return ""
    return re.sub(r'[^\w\s\-\+\.@]', '', str(text)).strip()

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=2)
    except Exception:
        pass

def set_webhook():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEB_APP_URL}/webhook"
    try: requests.get(url, timeout=3)
    except Exception: pass

def broadcast_game_state():
    # በየሴኮንዱ ዳታቤዝ ከመጫን የጌም ስቴቱን ብቻ ፈጥኖ ይልካል
    state_payload = {
        "status": game_state["status"],
        "timer": game_state["timer"],
        "ball_timer": game_state["ball_timer"],
        "pot": game_state["pot"],
        "sold_tickets": game_state["sold_tickets"],
        "current_ball": game_state["current_ball"],
        "drawn_balls": game_state["drawn_balls"],
        "winner": game_state["winner"],
        "winning_card": game_state["winning_card"],
        "winning_ticket_num": game_state["winning_ticket_num"],
        "winning_indices": game_state.get("winning_indices"),
        "winning_line_name": game_state.get("winning_line_name"), 
        "all_cards": game_state.get("all_cards", {}), 
        "active_players": len(game_state["players"])
    }
    socketio.emit('game_update', state_payload)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json or {}
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"].strip()
        chat_id = str(data["message"]["chat"]["id"])

        if msg.startswith("/start"):
            parts = msg.split()
            agent_phone = sanitize_input(parts[1]) if len(parts) > 1 else None
            already_registered = wallets.find_one({"$or": [{"telegram_id": chat_id}, {"phone": chat_id}]})
            
            if already_registered:
                webapp_keyboard = {"inline_keyboard": [[{"text": "🎮 ወደ ጨዋታው ግባ", "web_app": {"url": WEB_APP_URL}}]]}
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={
                    "chat_id": chat_id, "text": f"ℹ️ ሰላም {already_registered.get('username', 'ተጫዋች')}! ባላንስዎ፦ {already_registered.get('balance', 0)} ETB ነው።", "reply_markup": webapp_keyboard
                })
                return "OK", 200

            wallets.delete_one({"telegram_id": chat_id, "reg_status": {"$exists": True}})
            reg_session = {"phone": f"TEMP_{chat_id}", "telegram_id": chat_id, "reg_status": "awaiting_phone", "balance": 0}
            if agent_phone: reg_session["referred_by"] = agent_phone
            wallets.insert_one(reg_session)

            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={
                "chat_id": chat_id, "text": "👋 እንኳን ወደ BESH BINGO በደህና መጡ!\n\nእባክዎ የቴሌብር/ሲቢኢ ብር ስልክ ቁጥርዎን ያስገቡ፦"
            })
            return "OK", 200

        session = wallets.find_one({"telegram_id": chat_id, "reg_status": {"$exists": True}})
        if session:
            current_status = session.get("reg_status")
            if current_status == "awaiting_phone":
                clean_phone = msg.replace("+", "").replace(" ", "")
                if wallets.find_one({"phone": clean_phone}):
                    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "❌ ይህ ስልክ ቁጥር ቀድሞ ተመዝግቧል። ሌላ ያስገቡ፦"})
                    return "OK", 200
                wallets.update_one({"telegram_id": chat_id}, {"$set": {"phone": clean_phone, "reg_status": "awaiting_name"}})
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "✅ በመቀጠል ድረ-ገጹ ላይ የሚታየውን ስምዎን ያስገቡ፦"})
                return "OK", 200

            elif current_status == "awaiting_name":
                player_name = sanitize_input(msg)
                wallets.update_one({"telegram_id": chat_id}, {"$set": {"username": player_name}, "$unset": {"reg_status": ""}})
                final_user = wallets.find_one({"telegram_id": chat_id})
                webapp_keyboard = {"inline_keyboard": [[{"text": "🎮 ጨዋታውን ክፈት", "web_app": {"url": WEB_APP_URL}}]]}
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={
                    "chat_id": chat_id, "text": f"🎉 ምዝገባዎ ተጠናቋል!\n👤 ስም: {player_name}\n📱 ስልክ: {final_user['phone']}", "reply_markup": webapp_keyboard
                })
                broadcast_game_state()
                return "OK", 200

        if chat_id == ADMIN_ID:
            if msg.startswith("/add"):
                try:
                    parts = msg.split()
                    target_phone, amount = sanitize_input(parts[1]), float(parts[2])
                    wallets.update_one({"phone": target_phone}, {"$inc": {"balance": amount}}, upsert=True)
                    send_telegram(f"✅ ለ `{target_phone}` {amount} ETB ተጨምሯል።")
                    broadcast_game_state()
                except Exception: pass
    return "OK", 200

@app.route('/register_or_login', methods=['POST'])
def register_or_login():
    data = request.json or {}
    clean_phone = sanitize_input(data.get('phone')).replace("+", "").replace(" ", "")
    input_username = sanitize_input(data.get('username'))
    
    existing = wallets.find_one({"phone": clean_phone})
    if existing:
        wallets.update_one({"phone": clean_phone}, {"$set": {"username": input_username}})
        broadcast_game_state()
        return jsonify({"success": True, "balance": existing.get("balance", 0)})
    
    wallets.insert_one({"phone": clean_phone, "username": input_username, "balance": 0})
    broadcast_game_state()
    return jsonify({"success": True, "balance": 0})

def check_winning_line(card, drawn_numbers, player_marked_numbers=None):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1 and b[1:].isdigit()}
    drawn_set.add(0)
    marked_set = set(player_marked_numbers) if player_marked_numbers is not None else None

    def is_hit(idx):
        val = card[idx]
        if idx == 12 or val == 0: return True
        try:
            val_int = int(val)
            if marked_set is not None: return (val_int in drawn_set) and (val_int in marked_set)
            return val_int in drawn_set
        except Exception: return False

    all_win_indices = set()
    line_types = []

    for i in range(5):
        row = [i*5 + j for j in range(5)]
        if all(is_hit(idx) for idx in row): all_win_indices.update(row); line_types.append(f"ረድፍ {i+1}")
        col = [j + i*5 for j in range(5)]
        if all(is_hit(idx) for idx in col): all_win_indices.update(col); line_types.append(f"አምድ {j+1}")

    d1 = [0, 6, 12, 18, 24]
    if all(is_hit(idx) for idx in d1): all_win_indices.update(d1); line_types.append("ዲያጎናል ↘")
    d2 = [4, 8, 12, 16, 20]
    if all(is_hit(idx) for idx in d2): all_win_indices.update(d2); line_types.append("ዲያጎናል ↙")
    corners = [0, 4, 20, 24]
    if all(is_hit(idx) for idx in corners): all_win_indices.update(corners); line_types.append("4 ማዕዘን")

    if all_win_indices: return list(all_win_indices), " + ".join(line_types)
    return None, None

def reset_game():
    game_state.update({
        "status": "lobby", "winner": None, "winning_card": None, "winning_ticket_num": None, 
        "winning_indices": None, "winning_line_name": None, "pot": 0, "players": {}, 
        "sold_tickets": {}, "drawn_balls": [], "current_ball": "--", "timer": 30, "ball_timer": 3, "all_cards": {}
    })
    broadcast_game_state() 

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        if game_state["status"] == "lobby":
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
                
                for j in range(3, -1, -1):
                    game_state["ball_timer"] = j
                    broadcast_game_state()
                    socketio.sleep(1)

                for b in shuffled:
                    if game_state["status"] != "playing": break
                    game_state["current_ball"] = b
                    game_state["drawn_balls"].append(b)
                    broadcast_game_state()
                    socketio.sleep(4)
            else:
                game_state["timer"] = 30
                broadcast_game_state()
        socketio.sleep(1)

@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    phone = sanitize_input(request.args.get('phone'))
    user = wallets.find_one({"phone": phone})
    p_data = game_state["players"].get(phone, {"cards": {}})
    
    return jsonify({
        "status": game_state["status"],
        "timer": game_state["timer"],
        "ball_timer": game_state["ball_timer"],
        "pot": game_state["pot"],
        "sold_tickets": game_state["sold_tickets"],
        "current_ball": game_state["current_ball"],
        "drawn_balls": game_state["drawn_balls"],
        "winner": game_state["winner"],
        "winning_card": game_state["winning_card"],
        "winning_ticket_num": game_state["winning_ticket_num"],
        "winning_indices": game_state.get("winning_indices"),
        "winning_line_name": game_state.get("winning_line_name"),
        "all_cards": game_state.get("all_cards", {}),
        "balance": user['balance'] if user else 0, 
        "my_cards": list(p_data["cards"].values()), 
        "active_players": len(game_state["players"])
    })

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json or {}
    ph, t_num, uname = sanitize_input(d.get('phone')), str(d.get('ticket_num')), sanitize_input(d.get('username'))
    
    if game_state["status"] != "lobby" or t_num in game_state["sold_tickets"]:
        return jsonify({"success": False, "msg": "መግዛት አይቻልም!"})

    res = wallets.find_one_and_update({"phone": ph, "balance": {"$gte": 10}}, {"$inc": {"balance": -10}}, return_document=True)
    if res:
        columns = [random.sample(range(r[0], r[1]+1), 5) for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]]
        flat = [columns[c][r] for r in range(5) for c in range(5)]
        flat[12] = 0
        
        game_state["sold_tickets"][t_num] = ph
        game_state["pot"] += 10
        game_state["all_cards"][t_num] = flat
        
        if ph not in game_state["players"]:
            game_state["players"][ph] = {"cards": {t_num: flat}, "username": uname or res.get("username")}
        else:
            game_state["players"][ph]["cards"][t_num] = flat
            
        broadcast_game_state()
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    d = request.json or {}
    ph, t_num = sanitize_input(d.get('phone')), str(d.get('ticket_num'))
    if game_state["status"] == "lobby" and game_state["sold_tickets"].get(t_num) == ph:
        wallets.update_one({"phone": ph}, {"$inc": {"balance": 10}})
        game_state["pot"] -= 10
        del game_state["sold_tickets"][t_num]
        if t_num in game_state["all_cards"]: del game_state["all_cards"][t_num]
        if ph in game_state["players"]:
            if t_num in game_state["players"][ph]["cards"]: del game_state["players"][ph]["cards"][t_num]
            if not game_state["players"][ph]["cards"]: del game_state["players"][ph]
        broadcast_game_state()
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    d = request.json or {}
    ph = sanitize_input(d.get('phone'))
    marked_0 = d.get('marked_0', [])
    marked_1 = d.get('marked_1', [])
    
    if game_state["status"] != "playing" or ph not in game_state["players"]:
        return jsonify({"success": False, "msg": "ጨዋታው በሂደት ላይ አይደለም!"})
        
    p_data = game_state["players"][ph]
    for idx_key, (t_num, card) in enumerate(p_data["cards"].items()):
        current_marked = marked_0 if idx_key == 0 else marked_1
        win_indices, line_type = check_winning_line(card, game_state["drawn_balls"], player_marked_numbers=current_marked)
        
        if win_indices is not None:
            # የ3 ኳስ መቅደም ህግ ፈጣን ፍተሻ
            winning_numbers = [card[idx] for idx in win_indices if idx != 12 and card[idx] != 0]
            max_drawn_index = -1
            for num in winning_numbers:
                for idx_drawn, ball_str in enumerate(game_state["drawn_balls"]):
                    if int(ball_str[1:]) == num and idx_drawn > max_drawn_index:
                        max_drawn_index = idx_drawn
            
            if max_drawn_index != -1 and (len(game_state["drawn_balls"]) - 1 - max_drawn_index) >= 3:
                return jsonify({"success": False, "msg": "⚠️ አልፎሃል! 3 ኳስ አልፎታል።"})

            # አሸናፊውን መመዝገብ
            game_state.update({
                "status": "result", "timer": 10, "winner": p_data["username"],
                "winning_card": card, "winning_ticket_num": str(t_num),
                "winning_indices": win_indices, "winning_line_name": line_type
            })
            
            win_amt = game_state["pot"] * 0.8
            wallets.update_one({"phone": ph}, {"$inc": {"balance": win_amt}})
            
            user_info = wallets.find_one({"phone": ph})
            if user_info and "referred_by" in user_info:
                wallets.update_one({"phone": user_info["referred_by"]}, {"$inc": {"balance": win_amt * 0.05}})
            
            broadcast_game_state()
            socketio.start_background_task(lambda: (socketio.sleep(10), reset_game()))
            return jsonify({"success": True})
            
    return jsonify({"success": False, "msg": "ቢንጎ አልሞላም!"})

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    d = request.json or {}
    send_telegram(f"💰 *Deposit*\n📞 Сልክ: `{sanitize_input(d.get('phone'))}`\n💵 መጠን: `{d.get('amount')}` ETB\n🆔 ID: `{sanitize_input(d.get('transaction_id'))}`")
    return jsonify({"success": True})

@app.route('/request_withdrawal', methods=['POST']) 
def withdraw():
    d = request.json or {}
    ph, amt = sanitize_input(d.get('phone')), float(d.get('amount'))
    if wallets.find_one_and_update({"phone": ph, "balance": {"$gte": amt}}, {"$inc": {"balance": -amt}}):
        send_telegram(f"📤 *Withdraw*\n📞 Сልክ: `{ph}`\n💵 መጠን: `{amt}` ETB")
        broadcast_game_state()
        return jsonify({"success": True, "msg": "የውዝድሮው ጥያቄዎ በተሳካ ሁኔታ ተልኳል!"})
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

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
