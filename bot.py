import os
import time
import random
import secrets
import requests
import threading
import re

# 🔥 የዲኤንኤስ (DNS) እና የኔትወርክ ስህተቱን ለመፍታት መጀመሪያ ላይ መታከል ያለበት
import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient
from flask_cors import CORS
from flask_socketio import SocketIO, emit

app = Flask(__name__, template_folder='templates')
CORS(app)
# የረጅም ጊዜ ግንኙነት (Ping Timeout) ለማስተካከል SocketIO ውቅር ተሻሽሏል
socketio = SocketIO(app, cors_allowed_origins="*", ping_timeout=60, ping_interval=25)

# --- CONFIG ---
ADMIN_ID = os.getenv("ADMIN_ID", "7956330391") 
BOT_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://habesha-dice-bot.onrender.com") 

# 🛠️ ማስተካከያ፦ connect=False በመጨመር ሰርቨሩ ገና ሳይነሳ DNS ፈልጎ ክራሽ እንዳያደርግ ይከላከላል
client = MongoClient(MONGO_URL, connect=False)
db = client['bingo_db']
wallets = db['wallets']

wallets.create_index("phone", unique=True)

state_lock = threading.Lock()

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

def sanitize_input(text):
    if not text: return ""
    return re.sub(r'[^\w\s\-\+\.@]', '', str(text)).strip()

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=3)
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
    with state_lock:
        status_copy = {**game_state, "active_players": len(game_state["players"])}
    try:
        socketio.emit('game_update', status_copy)
    except Exception:
        pass # የኔትወርክ መቋረጥ ካለ ሰርቨሩ እንዳይደናቀፍ ማለፍ

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data: return "OK", 200
        
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"].strip()
        chat_id = str(data["message"]["chat"]["id"])

        if msg.startswith("/start"):
            parts = msg.split()
            agent_phone = sanitize_input(parts[1]) if len(parts) > 1 else None
            
            already_registered = wallets.find_one({"$or": [{"telegram_id": chat_id}, {"phone": chat_id}]})
            
            if already_registered:
                webapp_keyboard = {"inline_keyboard": [[{"text": "🎮 ወደ ጨዋታው ግባ", "web_app": {"url": WEB_APP_URL}}]]}
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                requests.post(url, json={
                    "chat_id": chat_id, 
                    "text": f"ℹ️ ሰላም {already_registered.get('username', 'ተጫዋች')}! ቀድመው የተመዘገቡ ነባር ተጫዋች ነዎት። ባላንስዎ፦ {already_registered.get('balance', 0)} ETB ነው።", 
                    "reply_markup": webapp_keyboard
                })
                return "OK", 200

            reg_session = {"phone": f"TEMP_{chat_id}", "telegram_id": chat_id, "reg_status": "awaiting_phone", "balance": 0}
            if agent_phone: reg_session["referred_by"] = agent_phone
            
            wallets.delete_one({"telegram_id": chat_id, "reg_status": {"$exists": True}})
            wallets.insert_one(reg_session)

            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            requests.post(url, json={
                "chat_id": chat_id, 
                "text": "👋 እንኳን ወደ BESH BINGO በደህና መጡ!\n\nእባክዎ **የቴሌብር (Telebirr) ወይም ሲቢኢ ብር (CBE Birr)** ስልክ ቁጥርዎን ያስገቡ፦"
            })
            return "OK", 200

        session = wallets.find_one({"telegram_id": chat_id, "reg_status": {"$exists": True}})
        if session:
            current_status = session.get("reg_status")
            if current_status == "awaiting_phone":
                clean_phone = msg.replace("+", "").replace(" ", "")
                if not clean_phone.isdigit() or len(clean_phone) < 9:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    requests.post(url, json={"chat_id": chat_id, "text": "❌ እባክዎ ትክክለኛ የስልክ ቁጥር ብቻ ያስገቡ፦"})
                    return "OK", 200

                duplicate_phone = wallets.find_one({"phone": clean_phone})
                if duplicate_phone:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    requests.post(url, json={"chat_id": chat_id, "text": "❌ ይህ ስልክ ቁጥር ቀድሞ ተመዝግቧል። ሌላ ያስገቡ፦"})
                    return "OK", 200

                wallets.update_one({"telegram_id": chat_id}, {"$set": {"phone": clean_phone, "reg_status": "awaiting_name"}})
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                requests.post(url, json={"chat_id": chat_id, "text": "✅ ስምዎን (የመጫወቻ ስም) ያስገቡ፦"})
                return "OK", 200

            elif current_status == "awaiting_name":
                player_name = sanitize_input(msg)
                if len(player_name) < 2:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    requests.post(url, json={"chat_id": chat_id, "text": "❌ ስምዎ በጣም አጭር ነው። ድጋሚ ያስገቡ፦"})
                    return "OK", 200

                wallets.update_one({"telegram_id": chat_id}, {"$set": {"username": player_name}, "$unset": {"reg_status": ""}})
                final_user = wallets.find_one({"telegram_id": chat_id})
                
                webapp_keyboard = {"inline_keyboard": [[{"text": "🎮 ጨዋታውን ክፈት", "web_app": {"url": WEB_APP_URL}}]]}
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                requests.post(url, json={
                    "chat_id": chat_id, 
                    "text": f"🎉 ምዝገባዎ ተጠናቋል!\n👤 ስም: {player_name}\n📱 ስልክ: {final_user['phone']}", 
                    "reply_markup": webapp_keyboard
                })
                send_telegram(f"🎉 *አዲስ ተጫዋች:* `{player_name}` | `{final_user['phone']}`")
                return "OK", 200

        # Admin Controls
        if chat_id == ADMIN_ID:
            if msg == "/security_check":
                high = list(wallets.find({"balance": {"$gt": 5000}}))
                msg_out = "🛡️ ሪፖርት:\n" + ("\n".join([f"• `{u.get('username')}` | `{u.get('balance')} ETB`" for u in high]) if high else "✅ ሰላም ነው")
                send_telegram(msg_out)
            elif msg.startswith("/check"):
                parts = msg.split()
                if len(parts) == 2:
                    user = wallets.find_one({"$or": [{"phone": sanitize_input(parts[1])}, {"telegram_id": sanitize_input(parts[1])}]})
                    if user: send_telegram(f"👤 `{user.get('username')}` | 💵 *{user.get('balance')} ETB*")
            elif msg.startswith("/add"):
                parts = msg.split()
                if len(parts) == 3:
                    wallets.update_one({"$or": [{"phone": sanitize_input(parts[1])}, {"telegram_id": sanitize_input(parts[1])}]}, {"$inc": {"balance": float(parts[2])}}, upsert=True)
                    send_telegram("✅ ተጨምሯል")
            elif msg.startswith("/sub"):
                parts = msg.split()
                if len(parts) == 3:
                    wallets.update_one({"$or": [{"phone": sanitize_input(parts[1])}, {"telegram_id": sanitize_input(parts[1])}]}, {"$inc": {"balance": -float(parts[2])}})
                    send_telegram("⚠️ ተቀንሷል")

    return "OK", 200

@app.route('/register_or_login', methods=['POST'])
def register_or_login():
    data = request.json or {}
    ph, uname = sanitize_input(data.get('phone')), sanitize_input(data.get('username'))
    if not ph or not uname: return jsonify({"success": False, "msg": "ቅጹን ያሟሉ!"}), 400
    clean_phone = ph.replace("+", "").replace(" ", "")
    
    existing = wallets.find_one({"phone": clean_phone})
    if existing:
        wallets.update_one({"phone": clean_phone}, {"$set": {"username": uname}})
        return jsonify({"success": True, "balance": existing.get("balance", 0)})
    
    wallets.insert_one({"phone": clean_phone, "username": uname, "balance": 0})
    return jsonify({"success": True, "balance": 0})

def reset_game():
    with state_lock:
        game_state.update({
            "status": "lobby", "winner": None, "winning_card": None, "winning_ticket_num": None, "pot": 0, "players": {}, 
            "sold_tickets": {}, "drawn_balls": [], "current_ball": "--", "timer": 30, "ball_timer": 3
        })
    broadcast_game_state()

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        with state_lock: current_status = game_state["status"]

        if current_status == "lobby":
            for i in range(30, -1, -1):
                with state_lock:
                    if game_state["status"] != "lobby": break
                    game_state["timer"] = i
                broadcast_game_state()
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
            broadcast_game_state()

            if shuffled:
                for j in range(3, -1, -1):
                    with state_lock: game_state["ball_timer"] = j
                    broadcast_game_state()
                    time.sleep(1)

                for b in shuffled:
                    with state_lock:
                        if game_state["status"] != "playing": break
                        game_state["current_ball"] = b
                        game_state["drawn_balls"].append(b)
                    broadcast_game_state()
                    time.sleep(4) # ⚡ ኔትወርክ ለማይረጋጋበት ፍጥነት ከ 5 ወደ 4 ሰከንድ ዝቅ ተደርጓል
            
            with state_lock:
                if game_state["status"] == "playing":
                    game_state["status"] = "result"
                    game_state["winner"] = "No Winner (House)"
                    threading.Thread(target=lambda: (time.sleep(5), reset_game())).start()
            broadcast_game_state()
        time.sleep(1)

@app.route('/')
def index(): return render_template('index.html')

# 🎯 ማስተካከያ፦ በትንሽ ኔትወርክ (Slow Connection) የፍሮንትአንድ ፖሊንግ መረጃ መሳቢያ ኤንድፖይንት
@app.route('/get_status')
def get_status():
    phone = sanitize_input(request.args.get('phone'))
    user = wallets.find_one({"$or": [{"phone": phone}, {"telegram_id": phone}]}) if phone else None
    
    with state_lock:
        db_phone = user['phone'] if user else phone
        p_data = game_state["players"].get(db_phone, {"cards": {}})
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
    ph, t_num = sanitize_input(d.get('phone')), str(d.get('ticket_num'))
    user = wallets.find_one({"$or": [{"phone": ph}, {"telegram_id": ph}]})
    if not user: return jsonify({"success": False, "msg": "ተጠቃሚ የለም!"})
    db_phone = user["phone"]

    with state_lock:
        if game_state["status"] != "lobby" or t_num in game_state["sold_tickets"]:
            return jsonify({"success": False, "msg": "መግዛት አይቻልም!"})
        game_state["sold_tickets"][t_num] = "LOCK"

    res = wallets.find_one_and_update({"phone": db_phone, "balance": {"$gte": 10}}, {"$inc": {"balance": -10}}, return_document=True)
    if res:
        columns = [random.sample(range(r[0], r[1]+1), 5) for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]]
        flat = [columns[c][r] for r in range(5) for c in range(5)]
        flat[12] = 0  
        
        with state_lock:
            game_state["sold_tickets"][t_num] = db_phone
            game_state["pot"] += 10
            if db_phone not in game_state["players"]:
                game_state["players"][db_phone] = {"cards": {t_num: flat}, "username": res.get("username", "Player")}
            else:
                game_state["players"][db_phone]["cards"][t_num] = flat
        broadcast_game_state()
        return jsonify({"success": True})
    
    with state_lock:
        if game_state["sold_tickets"].get(t_num) == "LOCK": del game_state["sold_tickets"][t_num]
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    d = request.json or {}
    ph, amt = sanitize_input(str(d.get('phone'))), d.get('amount')
    user = wallets.find_one({"$or": [{"phone": ph}, {"telegram_id": ph}]})
    username = user.get("username", "የማይታወቅ") if user else "ያልተመዘገበ"
    
    send_telegram(f"💰 *የዲፖዚት ጥያቄ!*\n👤 ስም: `{username}`\n📞 ስልክ: `{ph}`\n💵 መጠን: `{amt} ETB`\nማጽደቂያ: `/add {ph} {amt}`")
    return jsonify({"success": True})

if __name__ == '__main__':
    threading.Thread(target=game_loop, daemon=True).start()
    set_webhook()
    # ማሳሰቢያ፦ allow_unsafe_werkzeug=True መታከሉ በሊኑክስ ሰርቨር ላይ ክራሽ እንዳያደርግ ይከላከላል
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), allow_unsafe_werkzeug=True)
