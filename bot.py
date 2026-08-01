import os
import time
from gevent import monkey
monkey.patch_all()

import random
import requests
import re
import gevent
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient
from flask_cors import CORS
from flask_socketio import SocketIO, emit

app = Flask(__name__, template_folder='templates')
CORS(app)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")

# --- CONFIG ---
ADMIN_ID = os.getenv("ADMIN_ID") 
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
reset_task_reference = None
pending_claims = []
claim_lock_active = False

def sanitize_input(text):
    if not text:
        return ""
    return re.sub(r'[^\w\s\-\\.\@]', '', str(text)).strip()

def send_telegram(text):
    def _send():
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
        except Exception as e:
            print(f"Telegram Error: {e}")
    gevent.spawn(_send)

def set_webhook():
    webhook_url = f"{WEB_APP_URL}/webhook"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}"
    try:
        requests.get(url, timeout=5)
    except Exception as e:
        print(f"Webhook set failed: {e}")

def broadcast_game_state():
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

def notify_user_balance_update(phone_num, new_balance):
    socketio.emit('balance_update', {"phone": phone_num, "balance": new_balance})

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    d = request.json or {}
    ph = sanitize_input(str(d.get('phone')))
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

# --- WITHDRAWAL ENDPOINT (በቴሌግራም ማሳወቂያ እና ባላንስ መቀነስ የተስተካከለ) ---
@app.route('/request_withdrawal', methods=['POST'])
def request_withdrawal():
    d = request.json or {}
    ph = sanitize_input(str(d.get('phone')))
    try:
        amt = float(d.get('amount', 0))
    except ValueError:
        return jsonify({"success": False, "msg": "ትክክለኛ የገንዘብ መጠን ያስገቡ!"})

    if amt < 20:
        return jsonify({"success": False, "msg": "ቢያንስ ማውጣት የሚችሉት 20 ETB ነው!"})

    user = wallets.find_one({"$or": [{"phone": ph}, {"telegram_id": ph}]})
    if not user:
        return jsonify({"success": False, "msg": "ተጠቃሚው አልተገኘም!"})

    db_phone = user["phone"]
    current_balance = float(user.get("balance", 0))

    if current_balance < amt:
        return jsonify({"success": False, "msg": "በቂ ባላንስ የለዎትም!"})

    # ተጠቃሚው ብር ሲጠይቅ ከባላንሱ ወዲያውኑ ይቀነሳል (ወይም አድሚን ሲያጸድቅ እንዲሆን ከፈለጉ ማስተካከል ይቻላል)
    updated_user = wallets.find_one_and_update(
        {"phone": db_phone, "balance": {"$gte": amt}},
        {"$inc": {"balance": -amt}},
        return_document=True
    )

    if not updated_user:
        return jsonify({"success": False, "msg": "በቂ ባላንስ የለዎትም!"})

    new_balance = updated_user.get("balance", 0)

    # ለቴሌግራም አድሚን ማሳወቂያ መላክ
    msg = (f"📤 *Withdrawal Request*\n"
           f"👤 ስም: `{updated_user.get('username', 'N/A')}`\n"
           f"📞 ስልክ: `{db_phone}`\n"
           f"💵 የሚወጣው መጠን: `{amt}` ETB\n"
           f"💰 የቀረው ባላንስ: `{new_balance}` ETB\n\n"
           f"ኢንፎormation: ገንዘቡን ከሰጡት በኋላ ትክክል መሆኑን ያረጋግጡ። እምቢ ለማለት፦\n`/add {db_phone} {amt}`")
    send_telegram(msg)

    # ለተጠቃሚው በሶኬት በኩል አዲስ ባላንስ ማሳወቅ
    notify_user_balance_update(db_phone, new_balance)

    return jsonify({"success": True, "msg": "የውጭ ጥያቄዎ በተሳካ ሁኔታ ተልኳል!", "balance": new_balance})

@app.route('/request_transfer', methods=['POST'])
def request_transfer():
    d = request.json or {}
    sender_ph = sanitize_input(d.get('phone'))
    receiver_ph = sanitize_input(d.get('receiver_phone'))
    try:
        amt = float(d.get('amount', 0))
    except ValueError:
        return jsonify({"success": False, "msg": "ትክክለኛ የገንዘብ መጠን ያስገቡ!"})

    if amt <= 0:
        return jsonify({"success": False, "msg": "እባክዎ ትክክለኛ የብር መጠን ያስገቡ!"})

    sender = wallets.find_one({"$or": [{"phone": sender_ph}, {"telegram_id": sender_ph}]})
    if not sender or sender.get("balance", 0) < amt:
        return jsonify({"success": False, "msg": "በቂ ባላንስ የለዎትም!"})
    
    db_sender_phone = sender["phone"]

    receiver = wallets.find_one({"phone": receiver_ph})
    if not receiver:
        return jsonify({"success": False, "msg": "ተቀባዩ ስልክ ቁጥር በሲስተሙ አልተገኘም!"})
    
    if db_sender_phone == receiver_ph:
        return jsonify({"success": False, "msg": "ለራስዎ ገንዘብ ማስተላለፍ አይችሉም!"})

    sender_res = wallets.find_one_and_update(
        {"phone": db_sender_phone}, 
        {"$inc": {"balance": -amt}}, 
        return_document=True
    )
    
    msg = (f"💸 *Direct Transfer Request*\n"
           f"📤 ላኪ: `{db_sender_phone}`\n"
           f"📥 ተቀባይ: `{receiver_ph}`\n"
           f"💵 መጠን: `{amt}` ETB")
    send_telegram(msg)

    notify_user_balance_update(db_sender_phone, sender_res.get('balance', 0))
    return jsonify({"success": True, "msg": "የብር ማስተላለፍ ጥያቄዎ ተሳክቷል!", "balance": sender_res.get('balance', 0)})

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data:
        return "OK", 200
        
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"].strip()
        chat_id = str(data["message"]["chat"]["id"])

        if chat_id == ADMIN_ID:
            if msg.startswith("/all") or msg.startswith("/all_balances"):
                all_users = list(wallets.find({"phone": {"$not": {"$regex": "^TEMP_"}}}))
                text = "📊 *የሁሉም ተጠቃሚዎች ዝርዝር፦*\n\n"
                total_sys_balance = 0
                for u in all_users:
                    bal = u.get('balance', 0)
                    total_sys_balance += bal
                    text += f"👤 {u.get('username', 'N/A')} | 📞 `{u.get('phone')}` | 💰 {bal} ETB\n"
                text += f"\n💎 **ጠቅላላ የሲስተም ባላንስ፦** {total_sys_balance} ETB"
                send_telegram(text)
                return "OK", 200

            elif msg.startswith("/add"):
                parts = msg.split()
                if len(parts) > 2:
                    target_ph = sanitize_input(parts[1])
                    try:
                        amt = float(parts[2])
                        u = wallets.find_one_and_update({"phone": target_ph}, {"$inc": {"balance": amt}}, return_document=True)
                        if u:
                            send_telegram(f"✅ ለ `{target_ph}` ተጠቃሚ {amt} ETB ተጨምሯል። አዲስ ባላንስ፦ {u.get('balance')} ETB")
                            notify_user_balance_update(target_ph, u.get('balance', 0))
                        else:
                            send_telegram("❌ ተጠቃሚው አልተገኘም!")
                    except ValueError:
                        send_telegram("❌ ትክክለኛ መጠን ያስገቡ!")
                return "OK", 200

            elif msg.startswith("/sub"):
                parts = msg.split()
                if len(parts) > 2:
                    target_ph = sanitize_input(parts[1])
                    try:
                        amt = float(parts[2])
                        u = wallets.find_one_and_update({"phone": target_ph}, {"$inc": {"balance": -amt}}, return_document=True)
                        if u:
                            send_telegram(f"✅ ከ `{target_ph}` ተጠቃሚ ላይ {amt} ETB ተቀንሷል። አዲስ ባላንስ፦ {u.get('balance')} ETB")
                            notify_user_balance_update(target_ph, u.get('balance', 0))
                        else:
                            send_telegram("❌ ተጠቃሚው አልተገኘም!")
                    except ValueError:
                        send_telegram("❌ ትክክለኛ መጠን ያስገቡ!")
                return "OK", 200

        if msg.startswith("/start"):
            parts = msg.split()
            webapp_keyboard = {
                "inline_keyboard": [[{"text": "🎮 ወደ ጨዋታው ግባ (Open Web App)", "web_app": {"url": WEB_APP_URL}}]]
            }
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            requests.post(url, json={
                "chat_id": chat_id, 
                "text": "👋 እንኳን ወደ BESH BINGO በደህና መጡ!\n\nለመጫወት እና ለመመዝገብ ከታች ያለውን ቁልፍ ይጫኑ፦", 
                "reply_markup": webapp_keyboard
            })
            return "OK", 200

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
        existing = wallets.find_one({"phone": clean_phone})
        if existing:
            wallets.update_one({"phone": clean_phone}, {"$set": {"username": input_username}})
            gevent.spawn(broadcast_game_state)
            return jsonify({"success": True, "msg": "እንኳን ደህና መጡ!", "balance": existing.get("balance", 0)})
        else:
            new_user = {"phone": clean_phone, "username": input_username, "balance": 0}
            wallets.insert_one(new_user)
            send_telegram(f"🌐 *አዲስ ተጫዋች በሊንክ (Web) ተመዘገበ!*\n👤 ስም: `{input_username}`\n📞 ስልክ: `{clean_phone}`")
            gevent.spawn(broadcast_game_state)
            return jsonify({"success": True, "msg": "ምዝገባዎ ተጠናቋል!", "balance": 0})
    except Exception as e:
        existing = wallets.find_one({"phone": clean_phone})
        if existing:
            wallets.update_one({"phone": clean_phone}, {"$set": {"username": input_username}})
            gevent.spawn(broadcast_game_state)
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
    global reset_task_reference, claim_lock_active, pending_claims
    reset_task_reference = None
    claim_lock_active = False
    pending_claims = []
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
                broadcast_game_state()
            else:
                game_state["timer"] = 30
                broadcast_game_state()
                continue

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
                    if len(game_state["players"]) < 2:
                        game_state["status"] = "result"
                        game_state["winner"] = "No Winner (Insufficient Players)"
                        game_state["winning_card"] = None
                        game_state["winning_ticket_num"] = None
                        game_state["winning_indices"] = None
                        game_state["winning_line_name"] = None
                        send_telegram("ℹ️ ተጫዋቾች ከሁለት ስለወረዱ ጨዋታው ተቋርጧል።")
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
                
                def house_countdown_and_reset():
                    for t in range(10, -1, -1):
                        if game_state["status"] != "result":
                            return
                        game_state["timer"] = t
                        broadcast_game_state()
                        socketio.sleep(1)
                    reset_game()

                global reset_task_reference
                reset_task_reference = socketio.start_background_task(house_countdown_and_reset)
            broadcast_game_state()

        socketio.sleep(1)

@app.route('/')
def index(): 
    return render_template('index.html')

@app.route('/get_status')
def get_status():
    phone = sanitize_input(request.args.get('phone'))
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
    ph, t_num, uname = sanitize_input(d.get('phone')), str(d.get('ticket_num')), sanitize_input(d.get('username'))
    
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
        return_document=True
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
                
        gevent.spawn(notify_user_balance_update, db_phone, res.get("balance", 0))
        gevent.spawn(broadcast_game_state)
        
        return jsonify({"success": True, "balance": res.get("balance", 0)})
    
    if game_state["sold_tickets"].get(t_num) == "RESERVED_LOCK":
        del game_state["sold_tickets"][t_num]
            
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    d = request.json or {}
    ph, t_num = sanitize_input(d.get('phone')), str(d.get('ticket_num'))
    
    user = wallets.find_one({"$or": [{"phone": ph}, {"telegram_id": ph}]})
    if not user:
        return jsonify({"success": False, "msg": "ተጠቃሚው አልተገኘም!"})
    db_phone = user["phone"]

    if game_state["status"] != "lobby":
        return jsonify({"success": False, "msg": "ጨዋታው ስለተጀመረ መሰረዝ አይቻልም!"})

    if game_state["sold_tickets"].get(t_num) == db_phone:
        res = wallets.find_one_and_update({"phone": db_phone}, {"$inc": {"balance": 10}}, return_document=True)
        game_state["pot"] -= 10
        del game_state["sold_tickets"][t_num]
        
        if "all_cards" in game_state and t_num in game_state["all_cards"]:
            del game_state["all_cards"][t_num]
            
        if db_phone in game_state["players"]:
            if t_num in game_state["players"][db_phone]["cards"]:
                del game_state["players"][db_phone]["cards"][t_num]
            if not game_state["players"][db_phone]["cards"]: 
                del game_state["players"][db_phone]
        
        if res:
            gevent.spawn(notify_user_balance_update, db_phone, res.get("balance", 0))
        gevent.spawn(broadcast_game_state) 
        return jsonify({"success": True})
            
    return jsonify({"success": False, "msg": "ካርተላውን መሰረዝ አይቻልም!"})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    global claim_lock_active, pending_claims
    d = request.json or {}
    ph = sanitize_input(d.get('phone'))
    marked_0 = d.get('marked_0', [])
    marked_1 = d.get('marked_1', [])
    
    user_info = wallets.find_one({"$or": [{"phone": ph}, {"telegram_id": ph}]})
    if not user_info:
        return jsonify({"success": False, "msg": "ተጠቃሚው አልተገኘም!"})
    db_phone = user_info["phone"]

    if game_state["status"] not in ["playing", "result"]:
        return jsonify({"success": False, "msg": "ጨዋታው በሂደት ላይ አይደለም!"})
        
    p_data = game_state["players"].get(db_phone)
    if not p_data:
        return jsonify({"success": False, "msg": "ይገባኛል ጥያቄው ውድቅ ተደርጓል!"})
        
    cards_to_check = p_data["cards"]
    valid_win_found = False
    winning_ticket_num = None
    winning_card_data = None
    winning_line_type = None
    winning_indices_list = None
    
    current_drawn_balls = game_state["drawn_balls"]
    if not current_drawn_balls:
        return jsonify({"success": False, "msg": "ገና ኳስ አልወጣም!"})
        
    last_called_ball = current_drawn_balls[-1]

    for idx_key, (t_num, card) in enumerate(cards_to_check.items()):
        current_marked = marked_0 if idx_key == 0 else marked_1
        win_indices, line_type = check_winning_line(card, current_drawn_balls, player_marked_numbers=current_marked)
        
        if win_indices is not None:
            valid_win_found = True
            winning_ticket_num = str(t_num)
            winning_card_data = card
            winning_line_type = line_type
            winning_indices_list = win_indices
            break 
            
    if not valid_win_found:
        return jsonify({"success": False, "msg": "ቢንጎ አልሞላም!"})
        
    claim_info = {
        "phone": db_phone,
        "username": p_data["username"],
        "ticket_num": str(winning_ticket_num),
        "card": winning_card_data,
        "indices": winning_indices_list,
        "line_name": winning_line_type,
        "winning_ball": last_called_ball
    }

    if game_state["status"] == "playing":
        if not claim_lock_active:
            claim_lock_active = True
            game_state["status"] = "result"
            game_state["timer"] = 10
            pending_claims = [claim_info]

            def process_claims_by_ball():
                global claim_lock_active, pending_claims
                socketio.sleep(1.5)

                total_prize = game_state["pot"] * 0.8  
                num_winners = len(pending_claims)

                if num_winners == 1:
                    winner_display = pending_claims[0]["username"]
                else:
                    winner_names = [c["username"] for c in pending_claims]
                    winner_display = " & ".join(winner_names)

                game_state["winner"] = winner_display
                game_state["winning_card"] = pending_claims[0]["card"]  
                game_state["winning_ticket_num"] = pending_claims[0]["ticket_num"] 
                game_state["winning_indices"] = pending_claims[0]["indices"]
                game_state["winning_line_name"] = pending_claims[0]["line_name"] 

                def background_win_task():
                    if num_winners == 1:
                        w = pending_claims[0]
                        win_res = wallets.find_one_and_update(
                            {"phone": w["phone"]}, 
                            {"$inc": {"balance": total_prize}}, 
                            return_document=True
                        )
                        if win_res:
                            gevent.spawn(notify_user_balance_update, w["phone"], win_res.get("balance", 0))
                        
                        success_msg = f"🏆 *WINNER!* \n👤 Name: {w['username']} | 📞 Phone: `{w['phone']}` | 🎫 Ticket: {w['ticket_num']} \n🎯 Winning Ball: {w['winning_ball']} \n💰 Prize Won: {total_prize:.2f} ETB"
                        send_telegram(success_msg)
                    else:
                        share_prize = total_prize / num_winners
                        winner_texts = []
                        for w in pending_claims:
                            w_res = wallets.find_one_and_update(
                                {"phone": w["phone"]}, 
                                {"$inc": {"balance": share_prize}}, 
                                return_document=True
                            )
                            if w_res:
                                gevent.spawn(notify_user_balance_update, w["phone"], w_res.get("balance", 0))
                            winner_texts.append(f"👤 {w['username']} (`{w['phone']}`) - 🎫 {w['ticket_num']}")
                        
                        success_msg = f"🏆 *WINNERS (Shared Prize on Ball {pending_claims[0]['winning_ball']})!* \n💰 Total Pot Share: {share_prize:.2f} ETB each ({num_winners} winners)\n" + "\n".join(winner_texts)
                        send_telegram(success_msg)
                        
                    broadcast_game_state()

                gevent.spawn(background_win_task)

                def countdown_and_reset():
                    global claim_lock_active, pending_claims
                    for t in range(10, -1, -1):
                        if game_state["status"] != "result":
                            return
                        game_state["timer"] = t
                        broadcast_game_state()
                        socketio.sleep(1)
                    reset_game()

                socketio.start_background_task(countdown_and_reset)

            socketio.start_background_task(process_claims_by_ball)
        else:
            already_exists = any(c["phone"] == db_phone for c in pending_claims)
            if not already_exists:
                pending_claims.append(claim_info)

    elif game_state["status"] == "result" and claim_lock_active:
        already_exists = any(c["phone"] == db_phone for c in pending_claims)
        if not already_exists:
            pending_claims.append(claim_info)

    return jsonify({"success": True})

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
