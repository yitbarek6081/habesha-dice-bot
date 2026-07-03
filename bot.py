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

# --- CONFIG (Environment Variables) ---
ADMIN_ID = os.getenv("ADMIN_ID", "7956330391") 
BOT_TOKEN = os.getenv("BOT_TOKEN", "8708969585:AAE-MQTUle1g83tGTmL0pNBm7oJOYw0u5dc") 
MONGO_URL = os.getenv("MONGO_URL")
RENDER_URL = os.getenv("RENDER_URL", "https://habesha-dice-bot.onrender.com")

# Telegram Webhookን ከተንኮል አዘል ጥቃቶች ለመጠበቅ የሚረዳ ሚስጥራዊ ቁልፍ
WEBHOOK_SECRET_TOKEN = secrets.token_hex(16)

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']

# ኢንዴክስ በመፍጠር ዳታቤዙ ፈጣን እና ደህንነቱ የተጠበቀ እንዲሆን ማድረግ
wallets.create_index("phone", unique=True)

state_lock = threading.Lock()

game_state = {
    "status": "lobby", 
    "timer": 30, 
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
        requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=4)
    except Exception as e:
        print(f"Telegram Error: {e}")

def set_webhook():
    webhook_url = f"{RENDER_URL}/webhook"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    try:
        # በሴኩሪቲ ቶከን የታጀበ የዌብሁክ ምዝገባ
        requests.post(url, json={"url": webhook_url, "secret_token": WEBHOOK_SECRET_TOKEN}, timeout=4)
    except Exception as e:
        print(f"Webhook set failed: {e}")

@app.route('/webhook', methods=['POST'])
def webhook():
    # ከቴሌግራም የመጣውን ሚስጥራዊ ቁልፍ ማረጋገጥ
    received_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if received_token != WEBHOOK_SECRET_TOKEN:
        return "Unauthorized", 403

    data = request.json
    if not data or "message" not in data or "text" not in data["message"]:
        return "OK", 200
        
    msg = data["message"]["text"].strip()
    chat_id = str(data["message"]["chat"]["id"])

    # --- Referral tracking (New players only) ---
    if msg.startswith("/start"):
        parts = msg.split()
        if len(parts) > 1:
            agent_phone = sanitize_input(parts[1])
            existing_user = wallets.find_one({"phone": chat_id}, {"_id": 1})
            
            if not existing_user:
                user_token = secrets.token_hex(16) # ለአዲሱ ተጠቃሚ ሴኪውር ቶከን መስጠት
                wallets.update_one(
                    {"phone": chat_id},
                    {"$setOnInsert": {
                        "phone": chat_id, 
                        "balance": 0, 
                        "token": user_token,
                        "referred_by": agent_phone,
                        "username": f"Player_{chat_id[:4]}"
                    }},
                    upsert=True
                )

    # --- Admin controls ---
    if chat_id == ADMIN_ID:
        if msg.startswith("/add"):
            try:
                parts = msg.split()
                if len(parts) == 3:
                    target_phone, amount = sanitize_input(parts[1]), float(parts[2])
                    if amount > 0:
                        wallets.update_one({"phone": target_phone}, {"$inc": {"balance": amount}}, upsert=True)
                        send_telegram(f"✅ ለ `{target_phone}` {amount} ETB ተጨምሯል።")
            except:
                send_telegram("❌ ስህተት! ፎርማቱ: /add ስልክ መጠን")
        
        elif msg.startswith("/sub"):
            try:
                parts = msg.split()
                if len(parts) == 3:
                    target_phone, amount = sanitize_input(parts[1]), float(parts[2])
                    if amount > 0:
                        wallets.update_one({"phone": target_phone}, {"$inc": {"balance": -amount}})
                        send_telegram(f"⚠️ ከ `{target_phone}` {amount} ETB ተቀንሷል።")
            except:
                send_telegram("❌ ስህተት! ፎርማቱ: /sub ስልክ መጠን")

        # አድሚኑ የሁሉንም ሰው ባላንስ እንዲያይ የሚያስችለው ትዕዛዝ
        elif msg == "/balances":
            try:
                all_wallets = wallets.find({}, {"phone": 1, "balance": 1, "username": 1, "_id": 0})
                report = "📊 *የተጠቃሚዎች የሂሳብ መዝገብ (User Balances):*\n\n"
                count = 0
                for w in all_wallets:
                    uname = w.get("username", "Unknown")
                    ph = w.get("phone", "N/A")
                    bal = w.get("balance", 0)
                    report += f"👤 {uname} ({ph}) ➡️ *{bal:,.2f} ETB*\n"
                    count += 1
                if count == 0:
                    report += "ምንም የተመዘገበ ተጠቃሚ የለም።"
                send_telegram(report)
            except Exception as e:
                send_telegram(f"❌ ሪፖርቱን ማውጣት አልተሳካም: {e}")

        # ✨ አዲስ፡ አድሚኑ በማንኛውም ሰዓት የደህንነት ፍተሻ እንዲያደርግ የሚያስችለው ትዕዛዝ
        elif msg == "/security_check":
            try:
                # 1. ኔጌቲቭ ባላንስ ያላቸውን መፈለግ (Double Spending / Hack ሙከራ)
                negative_wallets = list(wallets.find({"balance": {"$lt": 0}}, {"phone": 1, "balance": 1, "username": 1, "_id": 0}))
                
                # 2. በጣም ከፍተኛ ገንዘብ ያላቸውን አካውንቶች መለየት (ለምሳሌ ከ 20,000 ETB በላይ)
                high_wallets = list(wallets.find({"balance": {"$gt": 20000}}, {"phone": 1, "balance": 1, "username": 1, "_id": 0}))
                
                # 3. የደህንነት ቶከን የሌላቸው (ያልተፈቀደ አጠራጣሪ አሰራር)
                missing_token_wallets = list(wallets.find({"token": {"$exists": False}}, {"phone": 1, "username": 1, "_id": 0}))

                report = "🛡️ *የዕለቱ የሲስተም ደህንነት ፍተሻ (Security Audit Report)*\n"
                report += f"📅 ቀን: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                
                has_issue = False

                if negative_wallets:
                    has_issue = True
                    report += "🚨 *ከባድ ማስጠንቀቂያ (Negative Balance!)*\n"
                    for w in negative_wallets:
                        report += f"⚠️ {w.get('username')} ({w.get('phone')}): *{w.get('balance')} ETB*\n"
                    report += "\n"
                
                if high_wallets:
                    has_issue = True
                    report += "💰 *ከፍተኛ የገንዘብ ክምችት ያላቸው (Whale Wallets):*\n"
                    for w in high_wallets:
                        report += f"ℹ️ {w.get('username')} ({w.get('phone')}): *{w.get('balance'):,.2f} ETB*\n"
                    report += "\n"

                if missing_token_wallets:
                    has_issue = True
                    report += "🔒 *የደህንነት ቁልፍ (Auth Token) የሌላቸው አካውንቶች:*\n"
                    for w in missing_token_wallets:
                        report += f"❌ {w.get('username')} ({w.get('phone')})\n"
                    report += "\n"

                if not has_issue:
                    report += "✅ *ሁሉም ነገር ሰላም ነው!* ምንም አይነት የደህንነት ስጋት ወይም የሂሳብ መዛባት አልተገኘም።"

                send_telegram(report)
            except Exception as e:
                send_telegram(f"❌ የደህንነት ፍተሻው ላይ ስህተት አጋጥሟል: {e}")

    return "OK", 200

# ተጠቃሚዎችን በስልክ ቁጥር እና በቶከን የሚያረጋግጥ የደህንነት ሲስተም
def verify_user(phone, token):
    if not phone or not token:
        return False
    user = wallets.find_one({"phone": phone}, {"token": 1, "_id": 0})
    return user and user.get("token") == token

# ✨ አዲስ ፈንክሽን፡ አሸናፊ የሆነበትን መስመር እና ዓይነት ለይቶ የሚያወጣ
def check_winning_line(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0)  # FREE space

    # 1. አግድም መስመሮች (Rows)
    for i in range(5):
        row_indices = [i*5 + j for j in range(5)]
        if all(card[idx] in drawn_set for idx in row_indices):
            return row_indices, "አግድም (Row)"

    # 2. ቁልቁል መስመሮች (Columns)
    for i in range(5):
        col_indices = [j*5 + i for j in range(5)]
        if all(card[idx] in drawn_set for idx in col_indices):
            return col_indices, "ቁልቁል (Column)"

    # 3. ዲያጎናል (ከላይ ግራ ወደ ታች ቀኝ)
    diag1_indices = [i*6 for i in range(5)]
    if all(card[idx] in drawn_set for idx in diag1_indices):
        return diag1_indices, "ዲያጎናል (Diagonal 📉)"

    # 4. ዲያጎናል (ከላይ ቀኝ ወደ ታች ግራ)
    diag2_indices = [4 + i*4 for i in range(5)]
    if all(card[idx] in drawn_set for idx in diag2_indices):
        return diag2_indices, "ዲያጎናል (Diagonal 📈)"

    # 5. አራቱ ማዕዘናት (4 Corners)
    corner_indices = [0, 4, 20, 24]
    if all(card[idx] in drawn_set for idx in corner_indices):
        return corner_indices, "አራቱ ማዕዘናት (4 Corners)"

    return None, None

# ለአሮጌው ኮድ ተኳሃኝነት እንዲኖረው የተተወ ፍተሻ
def is_winner(card, drawn_numbers):
    indices, _ = check_winning_line(card, drawn_numbers)
    return indices is not None

def reset_game():
    with state_lock:
        game_state.update({
            "status": "lobby", "winner": None, "winning_card": None, "pot": 0, "players": {}, 
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
                    time.sleep(2)
                    continue

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

# አዲስ ወይም ነባር ተጠቃሚዎችን በሴኪውር መንገድ ለመመዝገብ/ለመቀበል የተዘጋጀ የኤፒአይ መውጫ
@app.route('/api/auth', methods=['POST'])
def api_auth():
    d = request.json or {}
    ph = sanitize_input(d.get('phone'))
    uname = sanitize_input(d.get('username'))
    if not ph or len(ph) < 10:
        return jsonify({"success": False, "msg": "ትክክለኛ ስልክ ያስገቡ!"})
        
    user = wallets.find_one({"phone": ph})
    if user:
        wallets.update_one({"phone": ph}, {"$set": {"username": uname if uname else user.get("username")}})
        return jsonify({"success": True, "token": user["token"], "username": user.get("username")})
    else:
        new_token = secrets.token_hex(16)
        wallets.insert_one({"phone": ph, "balance": 0, "token": new_token, "username": uname if uname else f"User_{ph[-4:]}"})
        return jsonify({"success": True, "token": new_token, "username": uname})

@app.route('/get_status')
def get_status():
    phone = sanitize_input(request.args.get('phone'))
    token = request.headers.get('Authorization') 
    
    user = None
    if phone and verify_user(phone, token):
        user = wallets.find_one({"phone": phone}, {"balance": 1, "_id": 0})
    
    with state_lock:
        p_data = game_state["players"].get(phone, {"cards": {}}) if phone else {"cards": {}}
        cards_list = list(p_data["cards"].values())
        
        status_copy = {
            "status": game_state["status"],
            "timer": game_state["timer"],
            "pot": game_state["pot"],
            "current_ball": game_state["current_ball"],
            "drawn_balls": game_state["drawn_balls"],
            "winner": game_state["winner"],
            "winning_card": game_state["winning_card"],
            "balance": user['balance'] if user else 0, 
            "my_cards": cards_list, 
            "active_players": len(game_state["players"]),
            "sold_tickets": game_state["sold_tickets"]
        }
    return jsonify(status_copy)

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json or {}
    ph, t_num = sanitize_input(d.get('phone')), str(d.get('ticket_num'))
    token = request.headers.get('Authorization')
    
    if not verify_user(ph, token):
        return jsonify({"success": False, "msg": "ያልተፈቀደ ሙከራ (Unauthorized)!"}), 401

    with state_lock:
        if game_state["status"] != "lobby":
            return jsonify({"success": False, "msg": "ጨዋታ ተጀምሯል!"})
        if t_num in game_state["sold_tickets"]:
            return jsonify({"success": False, "msg": "ይህ ካርተላ ቀድሞ ተይዟል!"})
        if ph in game_state["players"] and len(game_state["players"][ph]["cards"]) >= 2:
            return jsonify({"success": False, "msg": "ከ 2 ካርተላ በላይ መግዛት አይቻልም!"})
        
        game_state["sold_tickets"][t_num] = "RESERVED_LOCK"

    res = wallets.find_one_and_update(
        {"phone": ph, "balance": {"$gte": 10}}, 
        {"$inc": {"balance": -10}},
        projection={"balance": 1, "username": 1},
        return_document=True
    )
    
    if res:
        uname = res.get("username", f"Player_{ph[-4:]}")
        columns = []
        for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]:
            columns.append(random.sample(range(r[0], r[1]+1), 5))
            
        flat = []
        for row_idx in range(5):
            for col_idx in range(5):
                flat.append(columns[col_idx][row_idx])
                
        flat[12] = 0  
        
        with state_lock:
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
    
    with state_lock:
        if game_state["sold_tickets"].get(t_num) == "RESERVED_LOCK":
            del game_state["sold_tickets"][t_num]
            
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    d = request.json or {}
    ph, t_num = sanitize_input(d.get('phone')), str(d.get('ticket_num'))
    token = request.headers.get('Authorization')
    
    if not verify_user(ph, token):
        return jsonify({"success": False, "msg": "Unauthorized!"}), 401
    
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

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    d = request.json or {}
    ph = sanitize_input(d.get('phone'))
    amt = float(d.get('amount', 0))
    t_id = sanitize_input(d.get('transaction_id', 'N/A'))
    token = request.headers.get('Authorization')
    
    if not verify_user(ph, token) or amt <= 0:
        return jsonify({"success": False, "msg": "Unauthorized!"}), 401
        
    user = wallets.find_one({"phone": ph}, {"referred_by": 1, "_id": 0})
    
    if user and "referred_by" in user:
        agent_phone = user["referred_by"]
        msg = (f"👤 **አዲስ ተመዝጋቢ በኤጀንት!**\n\n"
               f"📝 ስም: `{ph}`\n"
               f"🆔 Chat ID: `{ph}`\n"
               f"💵 መጠን: `{amt}` ETB\n"
               f"📲 ያመጣው ኤጀንት (ስልክ): **{agent_phone}**\n\n"
               f"👇 Approve ለማድረግ:\n`/add {ph} {amt}`")
    else:
        msg = (f"💰 *Deposit Request*\n"
               f"📞 Phone: `{ph}`\n"
               f"💵 Amount: `{amt}` ETB\n"
               f"🆔 ID: `{t_id}`\n\n"
               f"👇 Approve:\n`/add {ph} {amt}`")
               
    send_telegram(msg)
    return jsonify({"success": True})

@app.route('/withdraw', methods=['POST'])
def withdraw():
    d = request.json or {}
    ph, amt = sanitize_input(d.get('phone')), float(d.get('amount', 0))
    token = request.headers.get('Authorization')
    
    if not verify_user(ph, token) or amt <= 0:
        return jsonify({"success": False, "msg": "Unauthorized!"}), 401
    
    res = wallets.find_one_and_update(
        {"phone": ph, "balance": {"$gte": amt}},
        {"$inc": {"balance": -amt}},
        projection={"balance": 1},
        return_document=True
    )
    if res:
        msg = f"📤 *Withdraw Request*\n📞 Phone: `{ph}`\n💵 Amount: `{amt}` ETB\n\n⚠️ ብሩን በቴሌብር ላክና ባላንሱን ለመመለስ ካስፈለገ `/add` ተጠቀም።"
        send_telegram(msg)
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

# ✨ የተስተካከለው የ claim_bingo ክፍል (መስመር ለይቶ በከዋክብት የሚያሳይ)
@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    d = request.json or {}
    ph = sanitize_input(d.get('phone'))
    token = request.headers.get('Authorization')
    
    if not verify_user(ph, token):
        return jsonify({"success": False, "msg": "Unauthorized!"}), 401
        
    with state_lock:
        if game_state["status"] != "playing":
            return jsonify({"success": False, "msg": "ጨዋታው በሂደት ላይ አይደለም ወይም ሌላ አሸናፊ ተገኝቷል!"})
            
        p_data = game_state["players"].get(ph)
        if not p_data:
            return jsonify({"success": False, "msg": "ይገባኛል ጥያቄው ውድቅ ተደርጓል!"})
            
        cards_to_check = p_data["cards"]
        
        for t_num, card in cards_to_check.items():
            win_indices, line_type = check_winning_line(card, game_state["drawn_balls"])
            
            if win_indices is not None:
                game_state["status"] = "result"
                game_state["winner"] = p_data["username"]
                game_state["winning_card"] = card  
                
                win_amt = game_state["pot"] * 0.8
                wallets.update_one({"phone": ph}, {"$inc": {"balance": win_amt}})
                
                # ያሸነፈበትን ካርተላ በምልክት ማዘጋጀት
                card_rows = []
                for r in range(5):
                    row_vals = []
                    for c in range(5):
                        idx = r*5 + c
                        val = card[idx]
                        val_str = "FREE" if val == 0 else str(val)
                        
                        # ቁጥሩ ያሸነፈበት መስመር ላይ ካለ በ ⭐ [ ] ⭐ ምልክት ይከበባል
                        if idx in win_indices:
                            row_vals.append(f"⭐{val_str}⭐")
                        else:
                            row_vals.append(val_str)
                            
                    card_rows.append(" | ".join(row_vals))
                card_text = "\n".join(card_rows)
                
                # በቴሌግራም የሚላከው ዝርዝር ሪፖርት
                success_msg = (
                    f"🏆 *WINNER!* \n"
                    f"👤 Name: {p_data['username']} \n"
                    f"📞 Phone: `{ph}` \n"
                    f"🎫 Ticket No: {t_num} \n"
                    f"🎯 ያሸነፈበት መስመር: *{line_type}*\n"
                    f"💰 Prize: {win_amt} ETB\n\n"
                    f"📊 *Winning Card (ያሸነፈበት ካርተላ):* \n"
                    f"`{card_text}`"
                )
                
                send_telegram(success_msg)
                threading.Thread(target=lambda: (time.sleep(10), reset_game())).start()
                return jsonify({"success": True})
            
    return jsonify({"success": False, "msg": "ቢንጎ አልሞላም!"})

def daily_security_audit_loop():
    """በየ 24 ሰዓቱ በራስ-ሰር የደህንነት ፍተሻ አድርጎ ለአድሚን ሪፖርት የሚልክ"""
    while True:
        time.sleep(10) # ሰርቨሩ እንደተነሳ ወዲያውኑ እንዳይልክና CPU እንዳያጨናንቅ የ 10 ሰከንድ እረፍት
        try:
            # ጥያቄውን በቀጥታ ወደ ራሳችን የዌብሁክ አድራሻ በመላክ ፍተሻውን ማንቀሳቀስ
            webhook_url = f"{RENDER_URL}/webhook"
            headers = {"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET_TOKEN}
            payload = {
                "message": {
                    "chat": {"id": int(ADMIN_ID)},
                    "text": "/security_check"
                }
            }
            requests.post(webhook_url, json=payload, headers=headers, timeout=5)
        except Exception as e:
            print(f"Daily audit trigger failed: {e}")
            
        time.sleep(86400) # ለሚቀጥሉት 24 ሰዓታት መተኛት (24 * 60 * 60 ሰከንድ)

if __name__ == '__main__':
    threading.Timer(5, set_webhook).start() 
    threading.Thread(target=game_loop, daemon=True).start()
    threading.Thread(target=daily_security_audit_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
