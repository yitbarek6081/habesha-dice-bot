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

# RAM እና ዳታቤዝ እንዳይጨናነቅ maxPoolSize ተጨምሯል
client = MongoClient(MONGO_URL, maxPoolSize=10)
db = client['bingo_db']
wallets = db['wallets']

# ስልክ ቁጥር ልዩ (Unique) እንዲሆን በማድረግ የአካውንት መደራረብን መከላከል
wallets.create_index("phone", unique=True)

# Thread lock across requests & background loop
state_lock = threading.Lock()

game_state = {
    "status": "lobby", 
    "timer": 30, 
    "ball_timer": 3,      
    "pot": 0, 
    "players": {},       # phone -> {"cards": {ticket_num: flat_card}, "username": uname, "balance": bal}
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
    if not text:
        return ""
    return re.sub(r'[^\w\s\-\+\.@]', '', str(text)).strip()

def _send_telegram_worker(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=4)
    except:
        pass

def send_telegram(text):
    threading.Thread(target=_send_telegram_worker, args=(text,), daemon=True).start()

def set_webhook():
    webhook_url = f"{RENDER_URL}/webhook"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}"
    try:
        requests.get(url, timeout=4)
    except:
        pass

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data:
        return "OK", 200
        
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"].strip()
        chat_id = str(data["message"]["chat"]["id"])

        # --- 1. የ /start ትዕዛዝ ---
        if msg.startswith("/start"):
            parts = msg.split()
            agent_phone = sanitize_input(parts[1]) if len(parts) > 1 else None
            
            already_registered = wallets.find_one({"$or": [{"telegram_id": chat_id}, {"phone": chat_id}]})
            
            if already_registered:
                webapp_keyboard = {
                    "inline_keyboard": [[{"text": "🎮 ወደ ጨዋታው ግባ", "web_app": {"url": RENDER_URL}}]]
                }
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                try:
                    requests.post(url, json={
                        "chat_id": chat_id, 
                        "text": f"ℹ️ ሰላም {already_registered.get('username', 'ተጫዋች')}! ቀድመው የተመዘገቡ ነባር ተጫዋች ነዎት። ባላንስዎ፦ {already_registered.get('balance', 0)} ETB ነው። በቀጥታ መጫወት ይችላሉ!", 
                        "reply_markup": webapp_keyboard
                    }, timeout=3)
                except: pass
                return "OK", 200

            reg_session = {
                "phone": f"TEMP_{chat_id}", 
                "telegram_id": chat_id,
                "reg_status": "awaiting_phone",
                "balance": 0
            }
            if agent_phone:
                reg_session["referred_by"] = agent_phone
            
            wallets.delete_one({"telegram_id": chat_id, "reg_status": {"$exists": True}})
            wallets.insert_one(reg_session)

            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            try:
                requests.post(url, json={
                    "chat_id": chat_id, 
                    "text": "👋 እንኳን ወደ BESH BINGO በደህና መጡ!\n\nየተደራሽነት እና የክፍያ ሂደቱን ለማቅለል፤ እባክዎ **የቴሌብር (Telebirr) ወይም ሲቢኢ ብር (CBE Birr)** ስልክ ቁጥርዎን ያስገቡ፦"
                }, timeout=3)
            except: pass
            return "OK", 200

        # --- 2. የምዝገባ ደረጃዎች ---
        session = wallets.find_one({"telegram_id": chat_id, "reg_status": {"$exists": True}})
        
        if session:
            current_status = session.get("reg_status")
            
            if current_status == "awaiting_phone":
                clean_phone = msg.replace("+", "").replace(" ", "")
                if not clean_phone.isdigit() or len(clean_phone) < 9:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    try: requests.post(url, json={"chat_id": chat_id, "text": "❌ እባክዎ ትክክለኛ የስልክ ቁጥር ብቻ በቁጥር ያስገቡ (ምሳሌ: 0912345678)፦"}, timeout=3)
                    except: pass
                    return "OK", 200

                duplicate_phone = wallets.find_one({"phone": clean_phone})
                if duplicate_phone:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    try: requests.post(url, json={"chat_id": chat_id, "text": "❌ ይህ ስልክ ቁጥር ቀድሞ ተመዝግቧል። እባክዎ ሌላ ቁጥር ያስገቡ፦"}, timeout=3)
                    except: pass
                    return "OK", 200

                wallets.update_one(
                    {"telegram_id": chat_id}, 
                    {"$set": {"phone": clean_phone, "reg_status": "awaiting_name"}}
                )
                
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                try: requests.post(url, json={"chat_id": chat_id, "text": "✅ ስልክ ቁጥርዎ ተቀብለናል።\n\nቀጥሎ ደግሞ ድረ-ገጹ ላይ የሚታየውን **የተጫዋች ስምዎን (የመጫወቻ ስም)** ያስገቡ፦"}, timeout=3)
                except: pass
                return "OK", 200

            elif current_status == "awaiting_name":
                player_name = sanitize_input(msg)
                if len(player_name) < 2:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    try: requests.post(url, json={"chat_id": chat_id, "text": "❌ ስምዎ በጣም አጭር ነው። እባክዎ ድጋሚ ያስገቡ፦"}, timeout=3)
                    except: pass
                    return "OK", 200

                wallets.update_one(
                    {"telegram_id": chat_id}, 
                    {"$set": {"username": player_name}, "$unset": {"reg_status": ""}}
                )
                
                final_user = wallets.find_one({"telegram_id": chat_id})
                agent_phone = final_user.get("referred_by", "የለውም")

                webapp_keyboard = {
                    "inline_keyboard": [[{"text": "🎮 ጨዋታውን ክፈት (Open Game)", "web_app": {"url": RENDER_URL}}]]
                }
                
                success_text = f"🎉 እንኳን ደስ አለዎት! ምዝገባዎ ሙሉ በሙሉ ተጠናቋል።\n\n👤 ስም: {player_name}\n📱 ስልክ: {final_user['phone']}\n\nአሁን ታች ያለውን ቁልፍ ተጭነው መጫወት ይችላሉ!"
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                try:
                    requests.post(url, json={
                        "chat_id": chat_id, 
                        "text": success_text, 
                        "reply_markup": webapp_keyboard
                    }, timeout=3)
                except: pass
                
                send_telegram(f"🎉 *አዲስ ተጫዋች በጽሑፍ ተመዘገበ!*\n👤 ስም: `{player_name}`\n📞 ስልክ: `{final_user['phone']}`\n🔗 ኤጀንት: `{agent_phone}`")
                return "OK", 200

        # --- Admin controls ---
        if chat_id == ADMIN_ID:
            if msg == "/security_check":
                try:
                    high_balance_users = list(wallets.find({"balance": {"$gt": 5000}}))
                    sec_report = ["🛡️ *ወቅታዊ የሲስተም የደህንነት ፍተሻ ሪፖርት:*\n"]
                    if high_balance_users:
                        sec_report.append("⚠️ *ከፍተኛ ባላንስ ያላቸው ተጠቃሚዎች (ሊያጠራጥሩ የሚችሉ):*")
                        for u in high_balance_users:
                            sec_report.append(f"• 👤 `{u.get('username')}` | 📞 `{u.get('phone')}` | 💵 *{u.get('balance')} ETB*")
                    else:
                        sec_report.append("✅ ከተለመደው በላይ በጣም ከፍተኛ ባላንስ ያለው ተጠቃሚ አልተገኘም።")
                    send_telegram("\n".join(sec_report))
                except Exception as e:
                    send_telegram(f"❌ በደህንነት ፍተሻው ላይ ስህተት አጋጥሟል: {e}")

            elif msg.startswith("/check_balance") or msg.startswith("/check"):
                try:
                    parts = msg.split()
                    if len(parts) == 2:
                        target_phone = sanitize_input(parts[1])
                        user = wallets.find_one({"$or": [{"phone": target_phone}, {"telegram_id": target_phone}]})
                        if user:
                            uname = user.get("username", "የማይታወቅ")
                            bal = user.get("balance", 0)
                            invited_by = user.get("referred_by", "በቀጥታ የመጣ (የለውም)")
                            send_telegram(f"🔍 *የተጠቃሚ ወቅታዊ መረጃ:*\n\n👤 ስም: `{uname}`\n📞 ስልክ/ID: `{user.get('phone')}`\n💵 ባላንስ: *{bal} ETB*\n🔗 ያመጣው ኤጀንት: `{invited_by}`")
                        else:
                            send_telegram(f"❌ ስህተት! `{target_phone}` በዳታቤዝ ውስጥ የለም።")
                except:
                    send_telegram("❌ ስህተት! ፎርማቱ: `/check_balance ስልክ`")

            elif msg in ["/all_balances", "/all"]:
                try:
                    all_users = list(wallets.find({}))
                    if not all_users:
                        send_telegram("📭 በዳታቤዝ ውስጥ ምንም ተጠቃሚ አልተገኘም።")
                    else:
                        report_lines = ["📋 *የሁሉንም ተጠቃሚዎች ባላንስ ዝርዝር:*\n"]
                        total_system_balance = 0
                        for idx, user in enumerate(all_users, 1):
                            phone = user.get("phone", "N/A")
                            uname = user.get("username", "የማይታወቅ")
                            bal = user.get("balance", 0)
                            total_system_balance += bal
                            report_lines.append(f"{idx}. 👤 `{uname}` | 📞 `{phone}` | 💵 *{bal} ETB*")
                        report_lines.append(f"\n💰 *በሲስተሙ ላይ ያለ ጠቅላላ የብር ድምር:* `{total_system_balance} ETB`")
                        
                        full_report = "\n".join(report_lines)
                        if len(full_report) > 4000:
                            for chunk in [full_report[i:i+4000] for i in range(0, len(full_report), 4000)]:
                                send_telegram(chunk)
                        else:
                            send_telegram(full_report)
                except Exception as e:
                    send_telegram(f"❌ ስህተት: ሪፖርቱን ማውጣት አልተቻለም! {e}")

            elif msg.startswith("/remove"):
                try:
                    parts = msg.split()
                    if len(parts) == 2:
                        target_phone = sanitize_input(parts[1])
                        result = wallets.delete_one({"$or": [{"phone": target_phone}, {"telegram_id": target_phone}]})
                        if result.deleted_count > 0:
                            send_telegram(f"🗑️ ተጠቃሚው 📞 `{target_phone}` ከዳታቤዝ ላይ ሙሉ በሙሉ ተሰርዟል።")
                        else:
                            send_telegram(f"❌ ስህተት! `{target_phone}` የተባለ ስልክ ቁጥር አልተገኘም።")
                except Exception as e:
                    send_telegram(f"❌ ስህተት መረጃ! ፎርማቱ: `/remove ስልክ` ({e})")

            elif msg.startswith("/add"):
                try:
                    parts = msg.split()
                    if len(parts) == 3:
                        target_phone, amount = sanitize_input(parts[1]), float(parts[2])
                        if amount > 0:
                            wallets.update_one({"$or": [{"phone": target_phone}, {"telegram_id": target_phone}]}, {"$inc": {"balance": amount}}, upsert=True)
                            send_telegram(f"✅ ለ `{target_phone}` {amount} ETB ተጨምሯል።")
                except:
                    send_telegram("❌ ስህተት! ፎርማቱ: /add ስልክ መጠን")
            
            elif msg.startswith("/sub"):
                try:
                    parts = msg.split()
                    if len(parts) == 3:
                        target_phone, amount = sanitize_input(parts[1]), float(parts[2])
                        if amount > 0:
                            wallets.update_one({"$or": [{"phone": target_phone}, {"telegram_id": target_phone}]}, {"$inc": {"balance": -amount}})
                            send_telegram(f"⚠️ ከ `{target_phone}` {amount} ETB ተቀንሷል።")
                except:
                    send_telegram("❌ ስህተት! ፎርማቱ: /sub ስልክ መጠን")

    return "OK", 200

@app.route('/register_or_login', methods=['POST'])
def register_or_login():
    data = request.json or {}
    input_phone = sanitize_input(data.get('phone'))
    input_username = sanitize_input(data.get('username'))

    if not input_phone or not input_username:
        return jsonify({"success": False, "msg": "እባክዎ ስም እና ስልክ በትክክል ያስገቡ!"}), 400

    clean_phone = input_phone.replace("+", "").replace(" ", "")

    try:
        temp_user = wallets.find_one({"phone": f"TEMP_{clean_phone}"})
        if not temp_user:
            temp_user = wallets.find_one({"phone": clean_phone})

        if temp_user:
            wallets.update_one(
                {"_id": temp_user["_id"]},
                {
                    "$set": {"phone": clean_phone, "username": input_username},
                    "$unset": {"reg_status": ""}
                }
            )
            updated_user = wallets.find_one({"_id": temp_user["_id"]})
            return jsonify({"success": True, "msg": "እንኳን ደህና መጡ!", "balance": updated_user.get("balance", 0)})
        else:
            new_user = {"phone": clean_phone, "username": input_username, "balance": 0}
            wallets.insert_one(new_user)
            send_telegram(f"🌐 *አዲስ ተጫዋች በሊንክ (Web) ተመዘገበ!*\n👤 ስም: `{input_username}`\n📞 ስልክ: `{clean_phone}`")
            return jsonify({"success": True, "msg": "ምዝገባዎ ተጠናቋል!", "balance": 0})

    except Exception as e:
        existing = wallets.find_one({"phone": clean_phone})
        if existing:
            wallets.update_one({"phone": clean_phone}, {"$set": {"username": input_username}})
            return jsonify({"success": True, "msg": "አካውንትዎ ተገኝቷል!", "balance": existing.get("balance", 0)})
        return jsonify({"success": False, "msg": f"የምዝገባ ስህተት፦ {str(e)}"}), 500

def check_winning_line(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0)

    for i in range(5):
        row_indices = [i*5 + j for j in range(5)]
        if all(card[idx] in drawn_set for idx in row_indices):
            return row_indices, f"አግድም (Row {i+1})"

    for i in range(5):
        col_indices = [i + j*5 for j in range(5)]
        if all(card[idx] in drawn_set for idx in col_indices):
            return col_indices, f"ቁልቁል (Column {i+1})"

    diag1_indices = [0, 6, 12, 18, 24]
    if all(card[idx] in drawn_set for idx in diag1_indices):
        return diag1_indices, "ዲያጎናል (Diagonal 📉)"

    diag2_indices = [4, 8, 12, 16, 20]
    if all(card[idx] in drawn_set for idx in diag2_indices):
        return diag2_indices, "ዲያጎናል (Diagonal 📈)"

    corner_indices = [0, 4, 20, 24]
    if all(card[idx] in drawn_set for idx in corner_indices):
        return corner_indices, "አራቱ ማዕዘናት (4 Corners)"

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
                    game_state["ball_timer"] = 3
                    shuffled = balls.copy()
                    random.shuffle(shuffled)
                else:
                    game_state["timer"] = 30
                    shuffled = []

            if shuffled:
                for j in range(3, -1, -1):
                    with state_lock:
                        if game_state["status"] != "playing":
                            break
                        game_state["ball_timer"] = j
                    time.sleep(1)

                for b in shuffled:
                    with state_lock:
                        if game_state["status"] != "playing": 
                            break
                        game_state["current_ball"] = b
                        game_state["drawn_balls"].append(b)
                    time.sleep(5)
            
            with state_lock:
                if game_state["status"] == "playing":
                    game_state["status"] = "result"
                    game_state["winner"] = "No Winner (House)"
                    game_state["winning_card"] = None
                    game_state["winning_ticket_num"] = None
                    send_telegram("ℹ️ ጨዋታው ያለ አሸናፊ ተጠናቋል። ሁሉም ኳሶች አልቀዋል።")
                    threading.Thread(target=lambda: (time.sleep(5), reset_game())).start()

        time.sleep(1)

@app.route('/')
def index(): 
    return render_template('index.html')

# --- ⚡ ፕሌየር ባላንስን የሚጠብቅ HIGH CONCURRENCY GET_STATUS ⚡ ---
@app.route('/get_status')
def get_status():
    global cached_status, last_cache_time
    phone = sanitize_input(request.args.get('phone', ''))
    now = time.time()

    # 1. መጀመሪያ የጨዋታውን ሁኔታ ከ Cache (ራም) ላይ በፍጥነት እናወጣለን
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
                "players_snapshot": {k: {"cards": v["cards"], "username": v["username"]} for k, v in game_state["players"].items()}
            }
            last_cache_time = now
        res = cached_status.copy()

    p_snap = res.pop("players_snapshot", {})
    
    # 2. የተጫዋቹን ትክክለኛ ባላንስ ከዳታቤዝ የምናነበው በየ 0.5 ሰከንዱ አንድ ጊዜ ብቻ ነው (MongoDB እንዳይሞት)
    user_bal = 0
    if phone:
        user = wallets.find_one({"$or": [{"phone": phone}, {"telegram_id": phone}]})
        if user:
            user_bal = user.get("balance", 0)
            db_phone = user["phone"]
            user_data = p_snap.get(db_phone, {"cards": {}})
            res["my_cards"] = list(user_data.get("cards", {}).values())
        else:
            res["my_cards"] = []
    else:
        res["my_cards"] = []

    res["balance"] = user_bal
    return jsonify(res)

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json or {}
    ph, t_num, uname = sanitize_input(d.get('phone')), str(d.get('ticket_num')), sanitize_input(d.get('username'))
    
    if not ph or not t_num:
        return jsonify({"success": False, "msg": "የተሳሳተ መረጃ!"})

    user = wallets.find_one({"$or": [{"phone": ph}, {"telegram_id": ph}]})
    if not user:
        return jsonify({"success": False, "msg": "ተጠቃሚው አልተገኘም!"})
    db_phone = user["phone"]

    with state_lock:
        if game_state["status"] != "lobby":
            return
