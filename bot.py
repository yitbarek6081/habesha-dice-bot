import os
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
import threading

app = Flask(__name__, template_folder='templates')
CORS(app)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")

# 🔒 ለካርቴላ ግዢ Concurrency ደህንነት የሚሆን ሎክ (Lock)
cartela_lock = threading.Lock()

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
    "winners": [],       # 1. ባለብዙ አሸናፊዎችን ለመያዝ (List) ተደርጓል
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
        "winner": game_state["winners"][0]["username"] if game_state["winners"] else game_state.get("winner"),
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

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data:
        return "OK", 200
    # [የቀድሞው የዌብሁክ ኮድ እንዳለ ይቀጥላል...]
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
            broadcast_game_state()
            return jsonify({"success": True, "msg": "እንኳን ደህና መጡ!", "balance": updated_user.get("balance", 0)})
        else:
            new_user = {"phone": clean_phone, "username": input_username, "balance": 0}
            wallets.insert_one(new_user)
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
        "status": "lobby", "winner": None, "winners": [], "winning_card": None, "winning_ticket_num": None, 
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
                    
                    # በጨዋታ ሰዓት ተጫዋቾች ቢንጎ መምታታቸውን በየሰኮንዱ መፈተሽ (ባለብዙ አሸናፊዎችን ለማካተት)
                    # (እዚህ ጋር የclaim ሎጂክ በተጨማሪ በራስ-ሰር ሊፈትሽ ይችላል)

                    broadcast_game_state() 
                    socketio.sleep(4) 
            
            if game_state["status"] == "playing":
                game_state["status"] = "result"
                game_state["winner"] = "No Winner (House)"
                send_telegram("ℹ️ ጨዋታው ያለ አሸናፊ ተጠናቋል።")
                socketio.start_background_task(lambda: (socketio.sleep(10), reset_game()))
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
    
    status_copy = {
        "status": game_state["status"],
        "timer": game_state["timer"],
        "ball_timer": game_state["ball_timer"],
        "pot": game_state["pot"],
        "sold_tickets": game_state["sold_tickets"],
        "current_ball": game_state["current_ball"],
        "drawn_balls": game_state["drawn_balls"],
        "winner": game_state["winners"][0]["username"] if game_state["winners"] else game_state.get("winner"),
        "winning_card": game_state["winning_card"],
        "winning_ticket_num": game_state["winning_ticket_num"],
        "winning_indices": game_state.get("winning_indices"),
        "winning_line_name": game_state.get("winning_line_name"),
        "all_cards": game_state.get("all_cards", {}),
        "players": clean_players, 
        "balance": user['balance'] if user else 0, 
        "my_cards": cards_list, 
        "active_players": len(game_state["players"])
    }
    return jsonify(status_copy)

# 2. ካርቴላ ሲገዛ በአንድ ጊዜ ማስተናገድ (Thread-safe / Concurrency Lock)
@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json or {}
    ph, t_num, uname = sanitize_input(d.get('phone')), str(d.get('ticket_num')), sanitize_input(d.get('username'))
    
    if not ph or not t_num:
        return jsonify({"success": False, "msg": "የተሳሳተ መረጃ!"})

    with cartela_lock:
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
                    
            notify_user_balance_update(db_phone, res.get("balance", 0))
            broadcast_game_state() 
            return jsonify({"success": True, "balance": res.get("balance", 0)})
        
        if game_state["sold_tickets"].get(t_num) == "RESERVED_LOCK":
            del game_state["sold_tickets"][t_num]
                
        return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    d = request.json or {}
    ph, t_num = sanitize_input(d.get('phone')), str(d.get('ticket_num'))
    
    with cartela_lock:
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
                notify_user_balance_update(db_phone, res.get("balance", 0))
            broadcast_game_state() 
            return jsonify({"success": True})
                
    return jsonify({"success": False, "msg": "ካርተላውን መሰረዝ አይቻልም!"})

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    d = request.json or {}
    ph = sanitize_input(str(d.get('phone')))
    amt = d.get('amount')
    t_id = sanitize_input(d.get('transaction_id', 'N/A'))
    user = wallets.find_one({"$or": [{"phone": ph}, {"telegram_id": ph}]})
    db_phone = user["phone"] if user else ph
    send_telegram(f"💰 *Deposit Request*\n📞 Phone: `{db_phone}`\n💵 Amount: `{amt}` ETB\n🆔 ID: `{t_id}`")
    return jsonify({"success": True})

@app.route('/request_withdrawal', methods=['POST']) 
def withdraw():
    d = request.json or {}
    ph, amt = sanitize_input(d.get('phone')), float(d.get('amount'))
    user = wallets.find_one({"$or": [{"phone": ph}, {"telegram_id": ph}]})
    if not user:
        return jsonify({"success": False, "msg": "ተጠቃሚው አልተገኘም!"})
    db_phone = user["phone"]
    res = wallets.find_one_and_update({"phone": db_phone, "balance": {"$gte": amt}}, {"$inc": {"balance": -amt}}, return_document=True)
    if res:
        notify_user_balance_update(db_phone, res.get("balance", 0))
        broadcast_game_state() 
        return jsonify({"success": True, "msg": "የውዝድሮው ጥያቄዎ በተሳካ ሁኔታ ተልኳል!"})
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

# 1 & 3. ባለብዙ ካርቴላ ማረጋገጫ እና የሽልማት እኩል ማካፈል (Multi-Card Validation & Equal Prize Distribution)
@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    d = request.json or {}
    ph = sanitize_input(d.get('phone'))
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
    winning_card_found = False
    winning_info = None
    
    # 1. የተጫዋቹን ሁሉንም ካርቴላዎች (እስከ 2 ካርቴላዎች) መፈተሽ
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
                        if b_num == num and idx_drawn > max_drawn_index:
                            max_drawn_index = idx_drawn
                    except ValueError:
                        pass
            
            total_drawn = len(game_state["drawn_balls"])
            if max_drawn_index != -1 and (total_drawn - 1 - max_drawn_index) >= 3:
                return jsonify({"success": False, "msg": "⚠️ አልፎሃል! ቢንጎ ያሰኘህ ቁጥር ከወጣ 3 ኳስ አልፎታል።"})

            winning_card_found = True
            winning_info = {
                "phone": db_phone,
                "username": p_data["username"],
                "card": card,
                "ticket_num": str(t_num),
                "indices": win_indices,
                "line_name": line_type
            }
            break # አንዱ ካርቴላ ማሸነፉ በቂ ነው

    if not winning_card_found:
        return jsonify({"success": False, "msg": "ቢንጎ አልሞላም!"})

    # 3. የሁለት ወይም ከዚያ በላይ አሸናፊዎች መኖርን ማረጋገጥ እና ሽልማቱን በእኩል ማካፈል
    if game_state["status"] != "result":
        game_state["status"] = "result"
        game_state["winners"] = []

    game_state["winners"].append(winning_info)
    game_state["timer"] = 10
    game_state["winner"] = winning_info["username"]
    game_state["winning_card"] = winning_info["card"]
    game_state["winning_ticket_num"] = winning_info["ticket_num"]
    game_state["winning_indices"] = winning_info["indices"]
    game_state["winning_line_name"] = winning_info["line_name"] 

    total_pool = game_state["pot"] * 0.8
    num_winners = len(game_state["winners"])
    share_per_winner = total_pool / num_winners  # ሽልማቱን በእኩል ማካፈል

    # ሁሉንም አሸናፊዎች ማካፈል
    for win in game_state["winners"]:
        wallets.find_one_and_update({"phone": win["phone"]}, {"$inc": {"balance": share_per_winner}})
        notify_user_balance_update(win["phone"], wallets.find_one({"phone": win["phone"]}).get("balance", 0))

    success_msg = f"🏆 *WINNER(S)!* አሸናፊዎች ብዛት: {num_winners} | እያንዳንዳቸው የደረሰባቸው ድርሻ: {share_per_winner} ETB"
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
