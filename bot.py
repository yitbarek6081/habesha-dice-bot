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
    """
    የተሰጠውን ስልክ ቁጥር ወደ 9 አሃዝ (9xxxxxxxx ወይም 7xxxxxxxx) ይቀይራል።
    ልክ ካልሆነ None ይመልሳል።
    """
    if not phone_str:
        return None
    cleaned = re.sub(r'[^0-9+]', '', str(phone_str))
    
    # የሀገር ውስጥ ኮዶችን በሪገክስ ማጣራት
    match = re.match(r'^(?:\+?251|0)?([79]\d{8})$', cleaned)
    if match:
        return match.group(1)
    return None

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
                webapp_keyboard = {
                    "inline_keyboard": [[{"text": "🎮 ወደ ጨዋታው ግባ", "web_app": {"url": WEB_APP_URL}}]]
                }
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                requests.post(url, json={
                    "chat_id": chat_id, 
                    "text": f"ℹ️ ሰላም {already_registered.get('username', 'ተጫዋች')}! ቀድመው የተመዘገቡ ነባር ተጫዋች ነዎት። ባላንስዎ፦ {already_registered.get('balance', 0)} ETB ነው። በቀጥታ መጫወት ይችላሉ!", 
                    "reply_markup": webapp_keyboard
                })
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
            requests.post(url, json={
                "chat_id": chat_id, 
                "text": "👋 እንኳን ወደ BESH BINGO በደህና መጡ!\n\nየተደራሽነት እና የክፍያ ሂደቱን ለማቅለል፤ እባክዎ **የተጫዋች (የመጫወቻ) ወይም የቴሌብር/ሲቢኢ ብር** ስልክ ቁጥርዎን ያስገቡ፦"
            })
            return "OK", 200

        session = wallets.find_one({"telegram_id": chat_id, "reg_status": {"$exists": True}})
        
        if session:
            current_status = session.get("reg_status")
            
            if current_status == "awaiting_phone":
                clean_phone = clean_ethiopian_phone(msg)
                if not clean_phone:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    requests.post(url, json={"chat_id": chat_id, "text": "❌ እባክዎ ትክክለኛ የኢትዮጵያ ስልክ ቁጥር ያስገቡ (ምሳሌ: 0912345678 ወይም 0712345678)፦"})
                    return "OK", 200

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
                    "inline_keyboard": [[{"text": "🎮 ጨዋታውን ክፈት (Open Game)", "web_app": {"url": WEB_APP_URL}}]]
                }
                
                success_text = f"🎉 እንኳን ደስ አለዎት! ምዝገባዎ ሙሉ በሙሉ ተጠናቋል።\n\n👤 ስም: {player_name}\n📱 ስልክ: {final_user['phone']}\n\nአሁን ታች ያለውን ቁልፍ ተጭነው መጫወት ይችላሉ!"
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                requests.post(url, json={
                    "chat_id": chat_id, 
                    "text": success_text, 
                    "reply_markup": webapp_keyboard
                })
                
                send_telegram(f"🎉 *አዲስ ተጫዋች በጽሑፍ ተመዘገበ!*\n👤 ስም: `{player_name}`\n📞 ስልክ: `{final_user['phone']}`\n🔗 ኤጀንት: `{agent_phone}`")
                broadcast_game_state() 
                return "OK", 200

        if chat_id == ADMIN_ID:
            if msg == "/security_check":
                try:
                    high_balance_users = list(wallets.find({"balance": {"$gt": 5000}}))
                    sec_report = ["🛡️ *ወቅታዊ የሲስተም የደሁንነት ፍተሻ ሪፖርት:*\n"]
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
                        raw_target = sanitize_input(parts[1])
                        target_phone = clean_ethiopian_phone(raw_target) or raw_target
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
                        raw_target = sanitize_input(parts[1])
                        target_phone = clean_ethiopian_phone(raw_target) or raw_target
                        result = wallets.delete_one({"$or": [{"phone": target_phone}, {"telegram_id": target_phone}]})
                        if result.deleted_count > 0:
                            send_telegram(f"🗑️ ተጠቃሚው 📞 `{target_phone}` ከዳታቤዝ ላይ ሙሉ በሙሉ ተሰርዟል።")
                            broadcast_game_state()
                        else:
                            send_telegram(f"❌ ስህተት! `{target_phone}` የተባለ ስልክ ቁጥር አልተገኘም።")
                except Exception as e:
                    send_telegram(f"❌ ስህተት መረጃ! ፎርማቱ: `/remove ስልክ` ({e})")

            elif msg.startswith("/add"):
                try:
                    parts = msg.split()
                    if len(parts) == 3:
                        raw_target, amount = sanitize_input(parts[1]), float(parts[2])
                        target_phone = clean_ethiopian_phone(raw_target) or raw_target
                        if amount > 0:
                            user = wallets.find_one({"$or": [{"phone": target_phone}, {"telegram_id": target_phone}]})
                            if user:
                                wallets.update_one({"_id": user["_id"]}, {"$inc": {"balance": amount}})
                            else:
                                wallets.insert_one({
                                    "phone": target_phone, 
                                    "balance": amount, 
                                    "username": f"User_{target_phone[-4:]}"
                                })
                            send_telegram(f"✅ ለ `{target_phone}` {amount} ETB ተጨምሯል።")
                            broadcast_game_state() 
                except:
                    send_telegram("❌ ስህተት! ፎርማቱ: /add ስልክ መጠን")
            
            elif msg.startswith("/sub"):
                try:
                    parts = msg.split()
                    if len(parts) == 3:
                        raw_target, amount = sanitize_input(parts[1]), float(parts[2])
                        target_phone = clean_ethiopian_phone(raw_target) or raw_target
                        if amount > 0:
                            wallets.update_one({"$or": [{"phone": target_phone}, {"telegram_id": target_phone}]}, {"$inc": {"balance": -amount}})
                            send_telegram(f"⚠️ ከ `{target_phone}` {amount} ETB ተቀንሷል።")
                            broadcast_game_state() 
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

    clean_phone = clean_ethiopian_phone(input_phone)
    if not clean_phone:
        return jsonify({"success": False, "msg": "እባክዎ ትክክለኛ የኢትዮጵያ ስልክ ያስገቡ!"}), 400

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
            broadcast_game_state()
            return jsonify({"success": True, "msg": "እንኳን ደህና መጡ!", "balance": updated_user.get("balance", 0)})
        else:
            new_user = {"phone": clean_phone, "username": input_username, "balance": 0}
            wallets.insert_one(new_user)
            send_telegram(f"🌐 *አዲስ ተጫዋች በሊንክ (Web) ተመዘገበ!*\n👤 ስም: `{input_username}`\n📞 ስልክ: `{clean_phone}`")
            broadcast_game_state()
            return jsonify({"success": True, "msg": "ምዝገባዎ ተጠናቋል!", "balance": 0})

    except Exception as e:
        existing = wallets.find_one({"phone": clean_phone})
        if existing:
            wallets.update_one({"phone": clean_phone}, {"$set": {"username": input_username}})
            broadcast_game_state()
            return jsonify({"success": True, "msg": "አካውንትዎ ተገኝቷል!", "balance": existing.get("balance", 0)})
        return jsonify({"success": False, "msg": f"የምዝገባ ስህተት፦ {str(e)}"}), 500

def check_winning_line(card, drawn_numbers, player_marked_numbers=None):
    drawn_set = set()
    for b in drawn_numbers:
        if len(b) > 1:
            try:
                drawn_set.add(int(b[1:]))
            except ValueError:
                pass
    drawn_set.add(0) 

    marked_set = set(player_marked_numbers) if player_marked_numbers is not None else None

    def is_hit(idx):
        val = card[idx]
        if idx == 12 or val == 0 or val == "FREE" or val == "★":
            return True
        try:
            val_int = int(val)
            if marked_set is not None:
                return (val_int in drawn_set) and (val_int in marked_set)
            return val_int in drawn_set
        except:
            return False

    all_win_indices = set()
    line_types = []

    for i in range(5):
        row_indices = [i*5 + j for j in range(5)]
        if all(is_hit(idx) for idx in row_indices):
            all_win_indices.update(row_indices)
            line_types.append(f"ረድፍ {i+1}")

    for j in range(5):
        col_indices = [j + i*5 for i in range(5)]
        if all(is_hit(idx) for idx in col_indices):
            all_win_indices.update(col_indices)
            line_types.append(f"አምድ {j+1}")

    diag1_indices = [0, 6, 12, 18, 24]
    if all(is_hit(idx) for idx in diag1_indices):
        all_win_indices.update(diag1_indices)
        line_types.append("ዲያጎናል ↘")

    diag2_indices = [4, 8, 12, 16, 20]
    if all(is_hit(idx) for idx in diag2_indices):
        all_win_indices.update(diag2_indices)
        line_types.append("ዲያጎናል ↙")

    corner_indices = [0, 4, 20, 24]
    if all(is_hit(idx) for idx in corner_indices):
        all_win_indices.update(corner_indices)
        line_types.append("4 ማዕዘን")

    if all_win_indices:
        return list(all_win_indices), " + ".join(line_types)
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
        current_status = game_state["status"]

        if current_status == "lobby":
            for i in range(30, -1, -1):
                if game_state["status"] != "lobby": 
                    break
                game_state["timer"] = i
                broadcast_game_state() 
                socketio.sleep(1) 
            
            if game_state["status"] == "lobby" and len(game_state["players"]) >= 2:
                game_state["status"] = "playing"
                game_state["drawn_balls"] = []
                game_state["ball_timer"] = 3
                shuffled = balls.copy()
                random.shuffle(shuffled)
            else:
                game_state["timer"] = 30
                shuffled = []
            broadcast_game_state()

            if shuffled:
                for j in range(3, -1, -1):
                    if game_state["status"] != "playing":
                        break
                    game_state["ball_timer"] = j
                    broadcast_game_state() 
                    socketio.sleep(1)

                for b in shuffled:
                    if game_state["status"] != "playing": 
                        break
                    game_state["current_ball"] = b
                    game_state["drawn_balls"].append(b)
                    broadcast_game_state() 
                    socketio.sleep(4) 
            
            if game_state["status"] == "playing":
                game_state["status"] = "result"
                game_state["winner"] = "No Winner (House)"
                game_state["winning_card"] = None
                game_state["winning_ticket_num"] = None
                game_state["winning_indices"] = None
                game_state["winning_line_name"] = None
                send_telegram("ℹ️ ጨዋታው ያለ አሸናፊ ተጠናቋል።")
                socketio.start_background_task(lambda: (socketio.sleep(10), reset_game()))
            broadcast_game_state()

        socketio.sleep(1)

@app.route('/')
def index(): 
    return render_template('index.html')

@app.route('/get_status')
def get_status():
    raw_phone = sanitize_input(request.args.get('phone'))
    phone = clean_ethiopian_phone(raw_phone) or raw_phone
    user = wallets.find_one({"$or": [{"phone": phone}, {"telegram_id": phone}]}) if phone else None
    
    db_phone = user['phone'] if user else phone
    p_data = game_state["players"].get(db_phone, {"cards": {}})
    cards_list = list(p_data["cards"].values())
    
    clean_players = {}
    for k, v in game_state["players"].items():
        clean_players[k] = {
            "username": v.get("username", ""),
            "cards": list(v.get("cards", {}).values())
        }
    
    is_waiting = False
    if game_state["status"] in ["playing", "result"] and db_phone not in game_state["players"]:
        is_waiting = True

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
        "winning_indices": game_state.get("winning_indices"),
        "winning_line_name": game_state.get("winning_line_name"),
        "all_cards": game_state.get("all_cards", {}),
        "players": clean_players, 
        "balance": user['balance'] if user else 0, 
        "my_cards": cards_list, 
        "active_players": len(game_state["players"]),
        "is_waiting": is_waiting 
    }
    return jsonify(status_copy)

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json or {}
    raw_phone = sanitize_input(d.get('phone'))
    ph = clean_ethiopian_phone(raw_phone) or raw_phone
    t_num, uname = str(d.get('ticket_num')), sanitize_input(d.get('username'))
    
    if not ph or not t_num:
        return jsonify({"success": False, "msg": "የተሳሳተ መረጃ!"})

    user = wallets.find_one({"$or": [{"phone": ph}, {"telegram_id": ph}]})
    if not user:
        return jsonify({"success": False, "msg": "ተጠቃሚው አልተገኘም!"})
    db_phone = user["phone"]

    if game_state["status"] != "lobby":
        return jsonify({"success": False, "msg": "ጨዋታ ተጀምሯል!"})
    if t_num in game_state["sold_tickets"]:
        return jsonify({"success": False, "msg": "ይህ ካርተላ ቀድሞ ተይዟል!"})
    if db_phone in game_state["players"] and len(game_state["players"][db_phone]["cards"]) >= 2:
        return jsonify({"success": False, "msg": "ከ 2 ካርተላ በላይ መግዛት አይቻልም!"})
    
    game_state["sold_tickets"][t_num] = "RESERVED_LOCK"

    res = wallets.find_one_and_update(
        {"phone": db_phone, "balance": {"$gte": 10}}, 
        {"$inc": {"balance": -10}},
        return_document=ReturnDocument.AFTER
    )
    
    if res:
        columns = []
        for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]:
            shuffled_pool = random.sample(range(r[0], r[1]+1), 5)
            columns.append(shuffled_pool)
            
        flat = []
        for row_idx in range(5):
            for col_idx in range(5):
                flat.append(columns[col_idx][row_idx])
                
        flat[12] = 0  
        
        if game_state["status"] != "lobby":
            if game_state["sold_tickets"].get(t_num) == "RESERVED_LOCK":
                del game_state["sold_tickets"][t_num]
            wallets.update_one({"phone": db_phone}, {"$inc": {"balance": 10}})
            broadcast_game_state()
            return jsonify({"success": False, "msg": "ጨዋታ ተጀምሯል!"})
            
        game_state["sold_tickets"][t_num] = db_phone
        game_state["pot"] += 10
        
        if "all_cards" not in game_state:
            game_state["all_cards"] = {}
        game_state["all_cards"][t_num] = flat
        
        p_uname = uname if uname else res.get("username", f"User_{db_phone[-4:]}")
        if db_phone not in game_state["players"]:
            game_state["players"][db_phone] = {"cards": {t_num: flat}, "username": p_uname}
        else:
            game_state["players"][db_phone]["cards"][t_num] = flat
                
        broadcast_game_state() 
        return jsonify({"success": True})
    
    if game_state["sold_tickets"].get(t_num) == "RESERVED_LOCK":
        del game_state["sold_tickets"][t_num]
            
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    d = request.json or {}
    raw_phone = sanitize_input(d.get('phone'))
    ph = clean_ethiopian_phone(raw_phone) or raw_phone
    t_num = str(d.get('ticket_num'))
    
    user = wallets.find_one({"$or": [{"phone": ph}, {"telegram_id": ph}]})
    if not user:
        return jsonify({"success": False, "msg": "ተጠቃሚው አልተገኘም!"})
    db_phone = user["phone"]

    if game_state["status"] != "lobby":
        return jsonify({"success": False, "msg": "ጨዋታው ስለተጀመረ መሰረዝ አይቻልም!"})

    if game_state["sold_tickets"].get(t_num) == db_phone:
        wallets.update_one({"phone": db_phone}, {"$inc": {"balance": 10}}) 
        game_state["pot"] -= 10
        del game_state["sold_tickets"][t_num]
        
        if "all_cards" in game_state and t_num in game_state["all_cards"]:
            del game_state["all_cards"][t_num]
            
        if db_phone in game_state["players"]:
            if t_num in game_state["players"][db_phone]["cards"]:
                del game_state["players"][db_phone]["cards"][t_num]
            if not game_state["players"][db_phone]["cards"]: 
                del game_state["players"][db_phone]
        broadcast_game_state() 
        return jsonify({"success": True})
            
    return jsonify({"success": False, "msg": "ካርተላውን መሰረዝ አይቻልም!"})

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    d = request.json or {}
    raw_phone = sanitize_input(str(d.get('phone')))
    ph = clean_ethiopian_phone(raw_phone) or raw_phone
    amt = d.get('amount')
    t_id = sanitize_input(d.get('transaction_id', 'N/A'))
    
    user = wallets.find_one({"$or": [{"phone": ph}, {"telegram_id": ph}]})
    db_phone = user["phone"] if user else ph
    
    if user and "referred_by" in user:
        agent_phone = user["referred_by"]
        msg = (f"👤 **አዲስ ተመዝጋቢ በኤጀንት!**\n\n"
               f"📝 ስም: `{user.get('username', 'N/A')}`\n"
               f"🆔 ስልክ: `{db_phone}`\n"
               f"💵 መጠን: `{amt}` ETB\n"
               f"📲 ያመጣው ኤጀንት (ስልክ): **{agent_phone}**\n\n"
               f"👇 Approve ለማድረግ:\n`/add {db_phone} {amt}`")
    else:
        msg = (f"💰 *Deposit Request*\n"
               f"📞 Phone: `{db_phone}`\n"
               f"💵 Amount: `{amt}` ETB\n"
               f"🆔 ID: `{t_id}`\n\n"
               f"👇 Approve:\n`/add {db_phone} {amt}`")
               
    send_telegram(msg)
    return jsonify({"success": True})

@app.route('/request_withdrawal', methods=['POST']) 
def withdraw():
    d = request.json or {}
    raw_phone = sanitize_input(d.get('phone'))
    ph = clean_ethiopian_phone(raw_phone) or raw_phone
    amt = float(d.get('amount'))
    
    user = wallets.find_one({"$or": [{"phone": ph}, {"telegram_id": ph}]})
    if not user:
        return jsonify({"success": False, "msg": "ተጠቃሚው አልተገኘም!"})
    db_phone = user["phone"]

    res = wallets.find_one_and_update(
        {"phone": db_phone, "balance": {"$gte": amt}},
        {"$inc": {"balance": -amt}},
        return_document=ReturnDocument.AFTER
    )
    if res:
        msg = f"📤 *Withdraw Request*\n📞 Phone: `{db_phone}`\n💵 Amount: `{amt}` ETB\n\n⚠️ ብሩን በቴሌብር ላክና ባላንሱን ለመመለስ ካስፈለገ `/add` teqedem."
        send_telegram(msg)
        broadcast_game_state() 
        return jsonify({"success": True, "msg": "የውዝድሮው ጥያቄዎ በተሳካ ሁኔታ ተልኳል!"})
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    d = request.json or {}
    raw_phone = sanitize_input(d.get('phone'))
    ph = clean_ethiopian_phone(raw_phone) or raw_phone
    marked_0 = d.get('marked_0', [])
    marked_1 = d.get('marked_1', [])
    
    user_info = wallets.find_one({"$or": [{"phone": ph}, {"telegram_id": ph}]})
    if not user_info:
        return jsonify({"success": False, "msg": "ተጠቃሚው አልተገኘም!"})
    db_phone = user_info["phone"]

    if game_state["status"] != "playing":
        return jsonify({"success": False, "msg": "ጨዋታው በሂደት ላይ አይደለም!"})
        
    p_data = game_state["players"].get(db_phone)
    if not p_data:
        return jsonify({"success": False, "msg": "ይገባኛል ጥያቄው ውድቅ ተደርጓል!"})
        
    cards_to_check = p_data["cards"]
    
    for idx_key, (t_num, card) in enumerate(cards_to_check.items()):
        current_marked = marked_0 if idx_key == 0 else marked_1
        
        win_indices, line_type = check_winning_line(card, game_state["drawn_balls"], player_marked_numbers=current_marked)
        
        if win_indices is not None:
            winning_numbers_in_card = [card[idx] for idx in win_indices if idx != 12 and card[idx] != 0]
            
            max_drawn_index = -1
            for num in winning_numbers_in_card:
                for idx_drawn, ball_str in enumerate(game_state["drawn_balls"]):
                    try:
                        b_num = int(ball_str[1:])
                        if b_num == num:
                            if idx_drawn > max_drawn_index:
                                max_drawn_index = idx_drawn
                    except ValueError:
                        pass
            
            total_drawn = len(game_state["drawn_balls"])
            if max_drawn_index != -1 and (total_drawn - 1 - max_drawn_index) >= 3:
                return jsonify({"success": False, "msg": "⚠️ አልፎሃል! ቢንጎ ያሰኘህ ቁጥር ከወጣ 3 ኳስ አልፎታል። አራተኛው ኳስ ሳይጠራ መናገር ነበረብህ!"})

            game_state["status"] = "result"
            game_state["timer"] = 10
            game_state["winner"] = p_data["username"]
            game_state["winning_card"] = card  
            game_state["winning_ticket_num"] = str(t_num) 
            game_state["winning_indices"] = win_indices
            game_state["winning_line_name"] = line_type 

            win_amt = game_state["pot"] * 0.8
            wallets.update_one({"phone": db_phone}, {"$inc": {"balance": win_amt}})
            
            agent_msg = ""
            if user_info and "referred_by" in user_info:
                agent_phone = user_info["referred_by"]
                agent_commission = win_amt * 0.05
                wallets.update_one({"phone": agent_phone}, {"$inc": {"balance": agent_commission}})
                agent_msg = f"\n🤝 *Agent Bonus:* ኤጀንት `📞 {agent_phone}` የ *{agent_commission:.2f} ETB* ኮሚሽን ገቢ ተደርጎለታል።"
            
            card_rows = []
            for r in range(5):
                row_vals = []
                for c in range(5):
                    idx = r * 5 + c  
                    val = card[idx]
                    val_str = "FREE" if val == 0 else str(val)
                    if idx in win_indices:
                        row_vals.append(f"⭐{val_str}⭐")
                    else:
                        row_vals.append(val_str)
                card_rows.append(" | ".join(row_vals))
            card_text = "\n".join(card_rows)
            
            success_msg = (
                f"🏆 *WINNER!* \n"
                f"👤 Name: {p_data['username']} \n"
                f"📞 Phone: `{db_phone}` \n"
                f"🎫 Ticket No: {t_num} \n"
                f"🎯 ያሸነፈበት መስመር: *{line_type}*\n"
                f"💰 Prize: {win_amt} ETB\n"
                f"{agent_msg}\n\n"
                f"📊 *Winning Card:* \n"
                f"`{card_text}`"
            )
            
            send_telegram(success_msg)
            broadcast_game_state() 

            def countdown_and_reset():
                for t in range(10, -1, -1):
                    game_state["timer"] = t
                    broadcast_game_state()
                    socketio.sleep(1)
                reset_game()

            socketio.start_background_task(countdown_and_reset)
            return jsonify({"success": True})
            
    return jsonify({"success": False, "msg": "ቢንጎ አልሞላም!"})

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
