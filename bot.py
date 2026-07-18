import os
from gevent import monkey
monkey.patch_all()

import random
import requests
import re
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient, ReturnDocument  
from flask_cors import CORS
from flask_socketio import SocketIO, emit

app = Flask(__name__, template_folder='templates')
CORS(app)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")

ADMIN_ID = os.getenv("ADMIN_ID", "7956330391") 
BOT_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://habesha-dice-bot.onrender.com") 

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']

try:
    wallets.create_index("phone", unique=True)
except Exception as e:
    print(f"Index creation notice: {e}")

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
    if not text:
        return ""
    return re.sub(r'[^\w\s\-\+\.@]', '', str(text)).strip()

def clean_ethiopian_phone(phone_str):
    if not phone_str:
        return None
    cleaned = re.sub(r'[^0-9+]', '', str(phone_str))
    match = re.match(r'^(?:\+?251|0)?([79]\d{8})$', cleaned)
    if match:
        return match.group(1)
    return cleaned

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

def set_webhook():
    webhook_url = f"{WEB_APP_URL}/webhook"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}"
    try:
        requests.get(url, timeout=5)
    except Exception as e:
        print(f"Webhook set failed: {e}")

def broadcast_game_state():
    all_balances = {}
    try:
        for u in wallets.find({}, {"phone": 1, "balance": 1}):
            if "phone" in u:
                all_balances[u["phone"]] = u.get("balance", 0)
    except Exception:
        pass

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
        "active_players": len(game_state["players"]),
        "balances": all_balances  
    }
    socketio.emit('game_update', state_payload)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data:
        return "OK", 200
        
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"].strip()
        chat_id = str(data["message"]["chat"]["id"])

        if msg.startswith("/start"):
            parts = msg.split()
            raw_agent_phone = sanitize_input(parts[1]) if len(parts) > 1 else None
            agent_phone = clean_ethiopian_phone(raw_agent_phone) if raw_agent_phone else None
            
            already_registered = wallets.find_one({"$or": [{"telegram_id": chat_id}, {"phone": chat_id}]})
            if already_registered:
                webapp_keyboard = {"inline_keyboard": [[{"text": "🎮 ወደ ጨዋታው ግባ", "web_app": {"url": WEB_APP_URL}}]]}
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                requests.post(url, json={
                    "chat_id": chat_id, 
                    "text": f"ℹ️ ሰላም {already_registered.get('username', 'ተጫዋች')}! ባላንስዎ፦ {already_registered.get('balance', 0)} ETB ነው።", 
                    "reply_markup": webapp_keyboard
                })
                return "OK", 200

            reg_session = {"phone": f"TEMP_{chat_id}", "telegram_id": chat_id, "reg_status": "awaiting_phone", "balance": 0}
            if agent_phone:
                reg_session["referred_by"] = agent_phone
            
            wallets.delete_one({"telegram_id": chat_id, "reg_status": {"$exists": True}})
            wallets.insert_one(reg_session)

            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": chat_id, "text": "👋 እባክዎ የቴሌብር/ሲቢኢ ብር ስልክ ቁጥርዎን ያስገቡ፦"})
            return "OK", 200

        session = wallets.find_one({"telegram_id": chat_id, "reg_status": {"$exists": True}})
        if session:
            current_status = session.get("reg_status")
            if current_status == "awaiting_phone":
                clean_phone = clean_ethiopian_phone(msg)
                if not clean_phone or len(clean_phone) != 9:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    requests.post(url, json={"chat_id": chat_id, "text": "❌ እባክዎ ትክክለኛ የኢትዮጵያ ስልክ ቁጥር ያስገቡ (ምሳሌ: 09xxxxxxxx)፦"})
                    return "OK", 200

                duplicate_phone = wallets.find_one({"phone": clean_phone})
                if duplicate_phone:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    requests.post(url, json={"chat_id": chat_id, "text": "❌ ይህ ስልክ ቁጥር ቀድሞ ተመዝግቧል።"})
                    return "OK", 200

                wallets.update_one({"telegram_id": chat_id}, {"$set": {"phone": clean_phone, "reg_status": "awaiting_name"}})
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                requests.post(url, json={"chat_id": chat_id, "text": "✅ በመቀጠል የሚታየውን የተጫዋች ስምዎን ያስገቡ፦"})
                return "OK", 200

            elif current_status == "awaiting_name":
                player_name = sanitize_input(msg)
                wallets.update_one({"telegram_id": chat_id}, {"$set": {"username": player_name}, "$unset": {"reg_status": ""}})
                final_user = wallets.find_one({"telegram_id": chat_id})
                
                webapp_keyboard = {"inline_keyboard": [[{"text": "🎮 ጨዋታውን ክፈት", "web_app": {"url": WEB_APP_URL}}]]}
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                requests.post(url, json={"chat_id": chat_id, "text": "🎉 ምዝገባዎ ተጠናቋል!", "reply_markup": webapp_keyboard})
                broadcast_game_state()
                return "OK", 200

        if chat_id == ADMIN_ID:
            if msg.startswith("/add"):
                try:
                    parts = msg.split()
                    raw_target, amount = sanitize_input(parts[1]), float(parts[2])
                    target_phone = clean_ethiopian_phone(raw_target)
                    wallets.update_one({"phone": target_phone}, {"$inc": {"balance": amount}}, upsert=True)
                    send_telegram(f"✅ ለ `{target_phone}` {amount} ETB ተጨምሯል።")
                    broadcast_game_state() 
                except:
                    send_telegram("❌ ፎርማት ስህተት: /add ስልክ መጠን")

    return "OK", 200

@app.route('/register_or_login', methods=['POST'])
def register_or_login():
    data = request.json or {}
    input_phone = sanitize_input(data.get('phone'))
    input_username = sanitize_input(data.get('username'))

    clean_phone = clean_ethiopian_phone(input_phone)
    if not clean_phone or len(clean_phone) != 9:
        return jsonify({"success": False, "msg": "ትክክለኛ ስልክ ቁጥር አይደለም!"}), 400

    user = wallets.find_one({"phone": clean_phone})
    if user:
        wallets.update_one({"phone": clean_phone}, {"$set": {"username": input_username}})
        broadcast_game_state()
        return jsonify({"success": True, "msg": "እንኳን ደህና መጡ!", "balance": user.get("balance", 0)})
    else:
        wallets.insert_one({"phone": clean_phone, "username": input_username, "balance": 0})
        broadcast_game_state()
        return jsonify({"success": True, "msg": "ተመዝግበዋል!", "balance": 0})

@app.route('/get_status')
def get_status():
    raw_phone = sanitize_input(request.args.get('phone'))
    phone = clean_ethiopian_phone(raw_phone)
    user = wallets.find_one({"phone": phone})
    
    p_data = game_state["players"].get(phone, {"cards": {}})
    cards_list = list(p_data["cards"].values())
    
    clean_players = {}
    for k, v in game_state["players"].items():
        clean_players[k] = {"username": v.get("username", ""), "cards": list(v.get("cards", {}).values())}
    
    status_copy = {
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
        "players": clean_players, 
        "balance": user['balance'] if user else 0, 
        "my_cards": cards_list, 
        "active_players": len(game_state["players"])
    }
    return jsonify(status_copy)

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json or {}
    raw_phone = sanitize_input(d.get('phone'))
    ph = clean_ethiopian_phone(raw_phone)
    t_num = str(d.get('ticket_num'))
    uname = sanitize_input(d.get('username'))
    
    if not ph or not t_num:
        return jsonify({"success": False, "msg": "የተሳሳተ መረጃ!"})

    user = wallets.find_one({"phone": ph})
    if not user:
        return jsonify({"success": False, "msg": "ተጠቃሚው አልተገኘም! መጀመሪያ ሪፍረሽ ያድርጉ።"})

    if game_state["status"] != "lobby":
        return jsonify({"success": False, "msg": "ጨዋታ ተጀምሯል!"})
    if t_num in game_state["sold_tickets"]:
        return jsonify({"success": False, "msg": "ይህ ካርተላ ቀድሞ ተይዟል!"})
    if ph in game_state["players"] and len(game_state["players"][ph]["cards"]) >= 2:
        return jsonify({"success": False, "msg": "ከ 2 ካርተላ በላይ መግዛት አይቻልም!"})
    
    res = wallets.find_one_and_update(
        {"phone": ph, "balance": {"$gte": 10}}, 
        {"$inc": {"balance": -10}},
        return_document=ReturnDocument.AFTER
    )
    
    if res:
        columns = []
        for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]:
            columns.append(random.sample(range(r[0], r[1]+1), 5))
            
        flat = []
        for row_idx in range(5):
            for col_idx in range(5):
                flat.append(columns[col_idx][row_idx])
        flat[12] = 0  
        
        game_state["sold_tickets"][t_num] = ph
        game_state["pot"] += 10
        game_state["all_cards"][t_num] = flat
        
        p_uname = uname if uname else res.get("username", f"User_{ph[-4:]}")
        if ph not in game_state["players"]:
            game_state["players"][ph] = {"cards": {t_num: flat}, "username": p_uname}
        else:
            game_state["players"][ph]["cards"][t_num] = flat
                
        broadcast_game_state() 
        return jsonify({"success": True, "balance": res["balance"]})
            
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    d = request.json or {}
    raw_phone = sanitize_input(d.get('phone'))
    ph = clean_ethiopian_phone(raw_phone)
    t_num = str(d.get('ticket_num'))
    
    if game_state["status"] != "lobby":
        return jsonify({"success": False, "msg": "መሰረዝ አይቻልም!"})

    if game_state["sold_tickets"].get(t_num) == ph:
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

def reset_game():
    game_state.update({
        "status": "lobby", "winner": None, "winning_card": None, "winning_ticket_num": None, 
        "pot": 0, "players": {}, "sold_tickets": {}, "drawn_balls": [], "current_ball": "--", "timer": 30, "all_cards": {}
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
                shuffled = balls.copy()
                random.shuffle(shuffled)
                broadcast_game_state()
                
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
