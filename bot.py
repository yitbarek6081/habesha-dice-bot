import os
import time
import random
import requests
import threading
import re
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- CONFIG (Render Environment Variables) ---
ADMIN_ID = os.getenv("ADMIN_ID")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
RENDER_URL = "https://habesha-dice-bot.onrender.com" 

client = MongoClient(MONGO_URL, maxPoolSize=10) # RAM እንዳይበላ የኮኔክሽን ገደብ
db = client['bingo_db']
wallets = db['wallets']
wallets.create_index("phone", unique=True)

state_lock = threading.Lock()

game_state = {
    "status": "lobby", 
    "timer": 30, 
    "ball_timer": 3,      
    "pot": 0, 
    "players": {},       # phone -> {"cards": {t_num: flat_card}, "username": uname, "balance": bal}
    "sold_tickets": {},  # ticket_num -> phone
    "current_ball": "--", 
    "drawn_balls": [], 
    "winner": None,
    "winning_card": None,
    "winning_ticket_num": None  
}

# --- ⚡ 0.1 CPU CACHE SYSTEM ⚡ ---
cached_status = None
last_cache_time = 0
CACHE_DURATION = 0.5  

def sanitize_input(text):
    if not text: return ""
    return re.sub(r'[^\w\s\-\+\.@]', '', str(text)).strip()

def _send_telegram_worker(text):
    if not BOT_TOKEN or not ADMIN_ID: return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=4)
    except: pass

def send_telegram(text):
    threading.Thread(target=_send_telegram_worker, args=(text,), daemon=True).start()

def set_webhook():
    if not BOT_TOKEN: return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={RENDER_URL}/webhook"
    try: requests.get(url, timeout=4)
    except: pass

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/webhook', methods=['POST'])
def webhook():
    if not BOT_TOKEN: return "OK", 200
    data = request.json or {}
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"].strip()
        chat_id = str(data["message"]["chat"]["id"])

        if msg.startswith("/start"):
            parts = msg.split()
            agent_phone = sanitize_input(parts[1]) if len(parts) > 1 else None
            
            already_registered = wallets.find_one({"$or": [{"telegram_id": chat_id}, {"phone": chat_id}]})
            if already_registered:
                webapp_keyboard = {"inline_keyboard": [[{"text": "🎮 ወደ ጨዋታው ግባ", "web_app": {"url": RENDER_URL}}]]}
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                try:
                    requests.post(url, json={
                        "chat_id": chat_id, 
                        "text": f"ℹ️ ሰላም {already_registered.get('username', 'ተጫዋች')}! ባላንስዎ፦ {already_registered.get('balance', 0)} ETB ነው።", 
                        "reply_markup": webapp_keyboard
                    }, timeout=3)
                except: pass
                return "OK", 200

            reg_session = {"phone": f"TEMP_{chat_id}", "telegram_id": chat_id, "reg_status": "awaiting_phone", "balance": 0}
            if agent_phone: reg_session["referred_by"] = agent_phone
            
            wallets.delete_one({"telegram_id": chat_id, "reg_status": {"$exists": True}})
            wallets.insert_one(reg_session)

            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            try: requests.post(url, json={"chat_id": chat_id, "text": "👋 እንኳን ወደ BESH BINGO በደህና መጡ!\n\nእባክዎ የቴሌብር ስልክ ቁጥርዎን ያስገቡ፦"}, timeout=3)
            except: pass
            return "OK", 200

        session = wallets.find_one({"telegram_id": chat_id, "reg_status": {"$exists": True}})
        if session:
            current_status = session.get("reg_status")
            if current_status == "awaiting_phone":
                clean_phone = msg.replace("+", "").replace(" ", "")
                if not clean_phone.isdigit() or len(clean_phone) < 9: return "OK", 200
                if wallets.find_one({"phone": clean_phone}): return "OK", 200

                wallets.update_one({"telegram_id": chat_id}, {"$set": {"phone": clean_phone, "reg_status": "awaiting_name"}})
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                try: requests.post(url, json={"chat_id": chat_id, "text": "✅ ስምዎን ያስገቡ፦"}, timeout=3)
                except: pass
                return "OK", 200

            elif current_status == "awaiting_name":
                player_name = sanitize_input(msg)
                if len(player_name) < 2: return "OK", 200
                wallets.update_one({"telegram_id": chat_id}, {"$set": {"username": player_name}, "$unset": {"reg_status": ""}})
                return "OK", 200

    return "OK", 200

def check_winning_line(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0)
    for i in range(5):
        if all(card[i*5 + j] in drawn_set for j in range(5)): return True, f"አግድም {i+1}"
        if all(card[i + j*5] in drawn_set for j in range(5)): return True, f"ቁልቁል {i+1}"
    if all(card[idx] in drawn_set for idx in [0, 6, 12, 18, 24]): return True, "ዲያጎናል 📉"
    if all(card[idx] in drawn_set for idx in [4, 8, 12, 16, 20]): return True, "ዲያጎናል 📈"
    if all(card[idx] in drawn_set for idx in [0, 4, 20, 24]): return True, "ማዕዘናት"
    return None, None

def reset_game():
    with state_lock:
        game_state.update({
            "status": "lobby", "winner": None, "winning_card": None, "winning_ticket_num": None, "pot": 0, "players": {}, 
            "sold_tickets": {}, "drawn_balls": [], "current_ball": "--", "timer": 30, "ball_timer": 3
        })

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        with state_lock: status = game_state["status"]
        if status == "lobby":
            for i in range(30, -1, -1):
                with state_lock:
                    if game_state["status"] != "lobby": break
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
            if shuffled:
                time.sleep(3)
                for b in shuffled:
                    with state_lock:
                        if game_state["status"] != "playing": break
                        game_state["current_ball"] = b
                        game_state["drawn_balls"].append(b)
                    time.sleep(5)
            with state_lock:
                if game_state["status"] == "playing":
                    game_state["status"] = "result"
                    threading.Thread(target=lambda: (time.sleep(5), reset_game())).start()
        time.sleep(1)

# --- ⚡ HIGH CONCURRENCY GET_STATUS ⚡ ---
@app.route('/get_status')
def get_status():
    global cached_status, last_cache_time
    phone = request.args.get('phone', '')
    now = time.time()

    if cached_status and (now - last_cache_time < CACHE_DURATION):
        res = cached_status.copy()
    else:
        with state_lock:
            cached_status = {
                "status": game_state["status"],
                "timer": game_state["timer"],
                "ball_timer": game_state["ball_timer"],
                "pot": game_state["pot"],
                "current_ball": game_state["current_ball"],
                "drawn_balls": game_state["drawn_balls"],
                "winner": game_state["winner"],
                "winning_ticket_num": game_state["winning_ticket_num"],
                "active_players": len(game_state["players"]),
                "players_snapshot": {k: {"cards": v["cards"], "balance": v["balance"]} for k, v in game_state["players"].items()}
            }
            last_cache_time = now
        res = cached_status.copy()

    p_snap = res.pop("players_snapshot", {})
    user_data = p_snap.get(phone, {"cards": {}, "balance": 0})
    
    res["balance"] = user_data["balance"]
    res["my_cards"] = list(user_data["cards"].values())
    
    return jsonify(res)

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json or {}
    ph, t_num, uname = sanitize_input(d.get('phone')), str(d.get('ticket_num')), sanitize_input(d.get('username'))
    if not ph or not t_num: return jsonify({"success": False, "msg": "የተሳሳተ መረጃ!"})

    with state_lock:
        if game_state["status"] != "lobby" or t_num in game_state["sold_tickets"]:
            return jsonify({"success": False, "msg": "መግዛት አይቻልም!"})
        if ph in game_state["players"] and len(game_state["players"][ph]["cards"]) >= 2:
            return jsonify({"success": False, "msg": "ከ 2 ካርተላ በላይ አይፈቀድም!"})
        game_state["sold_tickets"][t_num] = "LOCK"

    res = wallets.find_one_and_update({"phone": ph, "balance": {"$gte": 10}}, {"$inc": {"balance": -10}}, return_document=True)
    if res:
        columns = [random.sample(range(r[0], r[1]+1), 5) for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]]
        flat = [columns[c][r] for r in range(5) for c in range(5)]
        flat[12] = 0  

        with state_lock:
            game_state["sold_tickets"][t_num] = ph
            game_state["pot"] += 10
            if ph not in game_state["players"]:
                game_state["players"][ph] = {"cards": {t_num: flat}, "username": uname or "User", "balance": res["balance"]}
            else:
                game_state["players"][ph]["cards"][t_num] = flat
                game_state["players"][ph]["balance"] = res["balance"]
        return jsonify({"success": True})
    
    with state_lock:
        if game_state["sold_tickets"].get(t_num) == "LOCK": del game_state["sold_tickets"][t_num]
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    d = request.json or {}
    ph = sanitize_input(d.get('phone'))

    with state_lock:
        if game_state["status"] != "playing" or ph not in game_state["players"]:
            return jsonify({"success": False, "msg": "ውድቅ ተደርጓል!"})
        
        p_data = game_state["players"][ph]
        for t_num, card in p_data["cards"].items():
            is_win, line_type = check_winning_line(card, game_state["drawn_balls"])
            if is_win:
                game_state["status"] = "result"
                game_state["winner"] = p_data["username"]
                game_state["winning_ticket_num"] = str(t_num)
                win_amt = game_state["pot"] * 0.8
                
                def update_winner_db():
                    wallets.update_one({"phone": ph}, {"$inc": {"balance": win_amt}})
                threading.Thread(target=update_winner_db, daemon=True).start()

                send_telegram(f"🏆 *WINNER!* \n👤 Name: {p_data['username']}\n💰 Prize: {win_amt} ETB")
                threading.Thread(target=lambda: (time.sleep(5), reset_game()), daemon=True).start()
                return jsonify({"success": True})
                
    return jsonify({"success": False, "msg": "ቢንጎ አልሞላም!"})

# --- GUNICORN/PRODUCTION Threads ---
# እነዚህ Threads ከ app.run ውጪ መሆናቸው በ Gunicorn ላይ ሁልጊዜ እንዲሰሩ ያደርጋቸዋል
threading.Timer(4, set_webhook).start() 
threading.Thread(target=game_loop, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)), threaded=True)
