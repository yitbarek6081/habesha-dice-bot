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

# ዋናው የጨዋታ ሁኔታ መቆጣጠሪያ
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
    "all_cards": {}  
}

loop_started = False

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
        "all_cards": game_state.get("all_cards", {}), 
        "active_players": len(game_state["players"]),
        "balances": all_balances  
    }
    socketio.emit('game_update', state_payload)

# Webhook & Bot Logic
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
            agent_phone = sanitize_input(parts[1]) if len(parts) > 1 else None
            
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
                clean_phone = msg.replace("+", "").replace(" ", "")
                if not clean_phone.isdigit() or len(clean_phone) < 9:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    requests.post(url, json={"chat_id": chat_id, "text": "❌ እባክዎ ትክክለኛ የስልክ ቁጥር ብቻ በቁጥር ያስገቡ (ምሳሌ: 0912345678)፦"})
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
                        target_phone = sanitize_input(parts[1])
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
                        target_phone, amount = sanitize_input(parts[1]), float(parts[2])
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
                        target_phone, amount = sanitize_input(parts[1]), float(parts[2])
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

    clean_phone = input_phone.replace("+", "").replace(" ", "")

    try:
        temp_user = wallets.find_one({"phone": f"TEMP_{clean_phone}"})
        if not temp_user:
            temp_user = wallets.find_one({"phone": clean_phone})

        if temp_user:
            wallets.update_one(
                {"_id": temp_user
