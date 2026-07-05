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

# ስልክ ቁጥር ልዩ (Unique) እንዲሆን በማድረግ የአካውንት መደራረብን መከላከል
wallets.create_index("phone", unique=True)

# Thread lock across requests & background loop
state_lock = threading.Lock()

game_state = {
    "status": "lobby", 
    "timer": 30, 
    "ball_timer": 3,      # አዲሱ የ3 ሰከንድ የኳስ መጀመሪያ ቆጠራ (3->2->1->0)
    "pot": 0, 
    "players": {},       # chat_id -> {"cards": {ticket_num: flat_card}, "username": uname}
    "sold_tickets": {},  # ticket_num -> chat_id
    "current_ball": "--", 
    "drawn_balls": [], 
    "winner": None,
    "winning_card": None  
}

def sanitize_input(text):
    if not text:
        return ""
    return re.sub(r'[^\w\s\-\+\.@]', '', str(text)).strip()

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
        msg = data["message"]["text"].strip()
        chat_id = str(data["message"]["chat"]["id"])

        # --- 1. የ /start ትዕዛዝ (ሪፈራል ወይም መደበኛ) ሲመጣ ---
        if msg.startswith("/start"):
            parts = msg.split()
            agent_phone = sanitize_input(parts[1]) if len(parts) > 1 else None
            
            # ተጫዋቹ ቀድሞ ሙሉ በሙሉ መመዝገቡን በ telegram_id ወይም በ phone ቼክ ማድረግ (ባላንስ እንዳይጠፋ)
            already_registered = wallets.find_one({"$or": [{"telegram_id": chat_id}, {"phone": chat_id}]})
            
            if already_registered:
                # ነባር ተጫዋች ከሆነ የድሮ ባላንሱ ሳይነካ በቀጥታ ጨዋታውን እንዲከፍት ሊንክ መስጠት
                webapp_keyboard = {
                    "inline_keyboard": [[{"text": "🎮 ወደ ጨዋታው ግባ", "web_app": {"url": RENDER_URL}}]]
                }
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                requests.post(url, json={
                    "chat_id": chat_id, 
                    "text": f"ℹ️ ሰላም {already_registered.get('username', 'ተጫዋች')}! ቀድመው የተመዘገቡ ነባር ተጫዋች ነዎት። ባላንስዎ፦ {already_registered.get('balance', 0)} ETB ነው። በቀጥታ መጫወት ይችላሉ!", 
                    "reply_markup": webapp_keyboard
                })
                return "OK", 200

            # አዲስ ተጫዋች ከሆነ ጊዜያዊ የምዝገባ መረጃ መያዝ
            reg_session = {
                "phone": f"TEMP_{chat_id}", 
                "telegram_id": chat_id,
                "reg_status": "awaiting_phone",
                "balance": 0
            }
            if agent_phone:
                reg_session["referred_by"] = agent_phone
            
            # የድሮ ያልተጠናቀቀ ጊዜያዊ ዳታ ካለ ማፅዳት
            wallets.delete_one({"telegram_id": chat_id, "reg_status": {"$exists": True}})
            wallets.insert_one(reg_session)

            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            requests.post(url, json={
                "chat_id": chat_id, 
                "text": "👋 እንኳን ወደ BESH BINGO በደህና መጡ!\n\nየተደራሽነት እና የክፍያ ሂደቱን ለማቅለል፤ እባክዎ **የቴሌብር (Telebirr) ወይም ሲቢኢ ብር (CBE Birr)** ስልክ ቁጥርዎን ያስገቡ፦"
            })
            return "OK", 200

        # --- 2. አዲስ ተጠቃሚዎች በሂደት ላይ ያሉ የምዝገባ ደረጃዎች ---
        session = wallets.find_one({"telegram_id": chat_id, "reg_status": {"$exists": True}})
        
        if session:
            current_status = session.get("reg_status")
            
            # ደረጃ 1፦ ስልክ ቁጥር ሲያስገቡ
            if current_status == "awaiting_phone":
                clean_phone = msg.replace("+", "").replace(" ", "")
                if not clean_phone.isdigit() or len(clean_phone) < 9:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    requests.post(url, json={"chat_id": chat_id, "text": "❌ እባክዎ ትክክለኛ የስልክ ቁጥር ብቻ በቁጥር ያስገቡ (ምሳሌ: 0912345678)፦"})
                    return "OK", 200

                # ስልክ ቁጥሩ በሌላ ሰው የተያዘ መሆኑን ማረጋገጥ
                duplicate_phone = wallets.find_one({"phone": clean_phone})
                if duplicate_phone:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    requests.post(url, json={"chat_id": chat_id, "text": "❌ ይህ ስልክ ቁጥር ቀድሞ ተመዝግቧል። እባክዎ ሌላ ቁጥር ያስገቡ፦"})
                    return "OK", 200

                wallets.update_one(
                    {"telegram_id": chat_id}, 
                    {"$set": {"phone": clean_phone, "reg_status": "awaiting_name"}}
                )
                
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                requests.post(url, json={"chat_id": chat_id, "text": "✅ ስልክ ቁጥርዎ ተቀብለናል።\n\nቀጥሎ ደግሞ ድረ-ገጹ ላይ የሚታየውን **የተጫዋች ስምዎን (የመጫወቻ ስም)** ያስገቡ፦"})
                return "OK", 200

            # ደረጃ 2፦ ስም ሲያስገቡ (ምዝገባ ማጠናቀቂያ)
            elif current_status == "awaiting_name":
                player_name = sanitize_input(msg)
                if len(player_name) < 2:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    requests.post(url, json={"chat_id": chat_id, "text": "❌ ስምዎ በጣም አጭር ነው። እባክዎ ድጋሚ ያስገቡ፦"})
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
                requests.post(url, json={
                    "chat_id": chat_id, 
                    "text": success_text, 
                    "reply_markup": webapp_keyboard
                })
                
                send_telegram(f"🎉 *አዲስ ተጫዋች በጽሑፍ ተመዘገበ!*\n👤 ስም: `{player_name}`\n📞 ስልክ: `{final_user['phone']}`\n🔗 ኤጀንት: `{agent_phone}`")
                return "OK", 200

        # --- Admin controls ---
        if chat_id == ADMIN_ID:
            
            # 1. የደህንነት ፍተሻ (Security Check)
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

            # 2. የአንድን ተጠቃሚ ባላንስ ለይቶ ለማየት
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
                            send_telegram(f"❌ ስህተት! `{target_phone}` በዳታቤዝ ውስጥ የለውም።")
                except Exception as e:
                    send_telegram("❌ ስህተት! ፎርማቱ: `/check_balance ስልክ`")

            # 3. የሁሉንም ተጠቃሚዎች ባላንስ ዝርዝር በአንድ ላይ ለማየት
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

            # 4. አላስፈላጊ ተጠቃሚን ከሲስተም ሰርዞ ለማስወጣት
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
        # በቴሌግራም መጥቶ የነበረውን ጊዜያዊ አካውንት መፈለግ
        temp_user = wallets.find_one({"phone": f"TEMP_{clean_phone}"})
        if not temp_user:
            temp_user = wallets.find_one({"phone": clean_phone})

        if temp_user:
            # አካውንቱ ካለ መረጃውን ማስተካከል። የድሮ ባላንሱ አይነካም
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
            # ሙሉ በሙሉ አዲስ ከሆነ መመዝገብ
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
    # 'B1', 'I16' ወዘተ ወደ ንፁህ ቁጥር (Integer) መቀየር
    drawn_set = set()
    for b in drawn_numbers:
        if len(b) > 1:
            try:
                drawn_set.add(int(b[1:]))
            except:
                pass
    drawn_set.add(0) # መካከለኛዋ ነጻ ክፍል (FREE) በ 0 ተወክላለች

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

def is_winner(card, drawn_numbers):
    indices, _ = check_winning_line(card, drawn_numbers)
    return indices is not None

def reset_game():
    with state_lock:
        game_state.update({
            "status": "lobby", "winner": None, "winning_card": None, "pot": 0, "players": {}, 
            "sold_tickets": {}, "drawn_balls": [], "current_ball": "--", "timer": 30, "ball_timer": 3
        })

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        with state_lock:
            current_status = game_state["status"]

        if current_status == "lobby":
            # 1. የሎቢ (Lobby) 30 ሰከንድ ቆጠራ
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
                    game_state["ball_timer"] = 3  # የኳስ ቆጠራውን 3 ላይ መጀመር
                    shuffled = balls.copy()
                    random.shuffle(shuffled)
                else:
                    game_state["timer"] = 30
                    shuffled = []

            # 2. 🎮 ወደ ዋናው ቦርድ ከገቡ በኋላ የ3 ሰከንድ የኳስ መጀመሪያ ቆጠራ (3->2->1->0)
            if shuffled:
                for j in range(3, -1, -1):
                    with state_lock:
                        if game_state["status"] != "playing":
                            break
                        game_state["ball_timer"] = j
                    time.sleep(1)

                # 3. 🔴 ቆጠራው 0 ሲሆን ኳሶችን በየ5 ሰከንዱ መጣል መጀመር
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
                    send_telegram("ℹ️ ጨዋታው ያለ አሸናፊ ተጠናቋል። ሁሉም ኳሶች አልቀዋል።")
                    threading.Thread(target=lambda: (time.sleep(5), reset_game())).start()

        time.sleep(1)

@app.route('/')
def index(): 
    return render_template('index.html')

@app.route('/get_status')
def get_status():
    phone = sanitize_input(request.args.get('phone'))
    user = wallets.find_one({"$or": [{"phone": phone}, {"telegram_id": phone}]}) if phone else None
    
    with state_lock:
        db_phone = user['phone'] if user else phone
        p_data = game_state["players"].get(db_phone, {"cards": {}})
        cards_list = list(p_data["cards"].values())
        
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
            "balance": user['balance'] if user else 0, 
            "my_cards": cards_list, 
            "active_players": len(game_state["players"])
        }
    return jsonify(status_copy)

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
            return jsonify({"success": False, "msg": "ጨዋታ ተጀምሯል!"})
        if t_num in game_state["sold_tickets"]:
            return jsonify({"success": False, "msg": "ይህ ካርተላ ቀድሞ ተይዟል!"})
        if db_phone in game_state
