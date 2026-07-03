# MUST BE THE ABSOLUTE FIRST LINES OF THE FILE BEFORE ANY OTHER IMPORTS
import eventlet
eventlet.monkey_patch()

import os
import time
import random
import requests
import threading
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient, ASCENDING
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- CONFIG (ደህንነቱ የተጠበቀ ውቅር) ---
ADMIN_ID = "7956330391" 
BOT_TOKEN = "8708969585:AAE-MQTUle1g83tGTmL0pNBm7oJOYw0u5dc" 
MONGO_URL = os.getenv("MONGO_URL")
RENDER_URL = "https://habesha-dice-bot.onrender.com" 

# አስገዳጅ መረጃዎች መኖራቸውን ማረጋገጥ
if not MONGO_URL:
    raise ValueError("CRITICAL ERROR: MONGO_URL environment variable is missing!")

# --- DATABASE SETUP ---
client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']

# የስልክ ቁጥሮች በዳታቤዝ ደረጃ ልዩ (Unique) እንዲሆኑ ኢንዴክስ መፍጠር (ባላንስ እንዳይጠፋ ይረዳል)
wallets.create_index([("phone", ASCENDING)], unique=True)

# Thread lock to prevent race conditions across requests & background loop
state_lock = threading.Lock()

game_state = {
    "status": "lobby", 
    "timer": 30, 
    "pot": 0, 
    "players": {},       
    "sold_tickets": {},  
    "current_ball": "--", 
    "drawn_balls": [], 
    "winner": None,
    "winning_card": None  
}

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
    if not data or "message" not in data or "text" not in data["message"]:
        return "OK", 200
        
    msg = data["message"]["text"]
    chat_id = str(data["message"]["chat"]["id"])

    # --- Referral tracking (ባላንስ ሳይነካ አዲስ ሰው ብቻ መመዝገብ) ---
    if msg.startswith("/start"):
        parts = msg.split()
        if len(parts) > 1:
            agent_phone = parts[1]
            try:
                # $setOnInsert አዲስ ተጠቃሚ ሲሆን ብቻ ነው ዳታ የሚያስገባው፤ ነባር ከሆነ ባላንስ አይነካም!
                wallets.update_one(
                    {"phone": chat_id},
                    {"$setOnInsert": {
                        "phone": chat_id, 
                        "balance": 0, 
                        "referred_by": agent_phone
                    }},
                    upsert=True
                )
            except Exception as e:
                print(f"Referral db handling error: {e}")

    # --- Admin controls ---
    if chat_id == str(ADMIN_ID):
        if msg.startswith("/add") or msg.startswith("/sub"):
            try:
                parts = msg.split()
                if len(parts) == 3:
                    target_phone, amount_str = parts[1], parts[2]
                    amount = float(amount_str)
                    
                    if amount <= 0:
                        send_telegram("❌ ስህተት! መጠን ከ 0 በላይ መሆን አለበት።")
                        return "OK", 200

                    if msg.startswith("/sub"):
                        amount = -amount

                    wallets.update_one({"phone": target_phone}, {"$inc": {"balance": amount}}, upsert=True)
                    action = "ተጨምሯል" if amount > 0 else "ተቀንሷል"
                    send_telegram(f"✅ ለ `{target_phone}` {abs(amount)} ETB {action}።")
            except Exception:
                send_telegram("❌ ስህተት! ፎርማቱ: /add ወይም /sub ስልክ መጠን")

    return "OK", 200

def is_winner(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0)  
    
    for i in range(5):
        row = [card[i*5 + j] for j in range(5)]
        if all(num in drawn_set for num in row): return True 
        
    for j in range(5):
        col = [card[i*5 + j] for i in range(5)]
        if all(num in drawn_set for num in col): return True 
        
    diag1 = [card[0], card[6], card[12], card[18], card[24]]
    diag2 = [card[4], card[8], card[12], card[16], card[20]]
    if all(num in drawn_set for num in diag1): return True
    if all(num in drawn_set for num in diag2): return True
    
    corners = [card[0], card[4], card[20], card[24]]
    if all(num in drawn_set for num in corners): return True
    
    return False

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
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone}) if phone else None
    
    with state_lock:
        p_data = game_state["players"].get(phone, {"cards": {}})
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
    ph = d.get('phone')
    t_num = str(d.get('ticket_num')) if d.get('ticket_num') is not None else None
    uname = d.get('username')
    
    if not ph or not t_num:
        return jsonify({"success": False, "msg": "የተሳሳተ መረጃ!"})

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
        return_document=True
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
    ph = d.get('phone')
    t_num = str(d.get('ticket_num')) if d.get('ticket_num') is not None else None
    
    if not ph or not t_num:
         return jsonify({"success": False, "msg": "የተሳሳተ መረጃ!"})

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
    ph = str(d.get('phone'))
    
    try:
        amt = float(d.get('amount', 0))
        if amt <= 0: raise ValueError
    except (ValueError, TypeError):
        return jsonify({"success": False, "msg": "የተሳሳተ የገንዘብ መጠን!"})

    t_id = d.get('transaction_id', 'N/A')
    user = wallets.find_one({"phone": ph})
    
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
    ph = d.get('phone')
    
    try:
        amt = float(d.get('amount', 0))
    except (ValueError, TypeError):
        return jsonify({"success": False, "msg": "የተሳሳተ የገንዘብ መጠን!"})
    
    if not ph or amt <= 0:
        return jsonify({"success": False, "msg": "የተሳሳተ መረጃ!"})
    
    res = wallets.find_one_and_update(
        {"phone": ph, "balance": {"$gte": amt}},
        {"$inc": {"balance": -amt}},
        return_document=True
    )
    if res:
        msg = f"📤 *Withdraw Request*\n📞 Phone: `{ph}`\n💵 Amount: `{amt}` ETB\n\n⚠️ ብሩን በቴሌብር ላክና ባላንሱን ለመመለስ ካስፈለገ `/add` ተጠቀም።"
        send_telegram(msg)
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    d = request.json or {}
    ph = d.get('phone')
    
    with state_lock:
        if game_state["status"] != "playing":
            return jsonify({"success": False, "msg": "ጨዋታው በሂደት ላይ አይደለም ወይም ሌላ አሸናፊ ተገኝቷል!"})
            
        p_data = game_state["players"].get(ph)
        if not p_data:
            return jsonify({"success": False, "msg": "ይገባኛል ጥያቄው ውድቅ ተደርጓል!"})
            
        cards_to_check = p_data["cards"]
        
        for t_num, card in cards_to_check.items():
            if is_winner(card, game_state["drawn_balls"]):
                game_state["status"] = "result"
                game_state["winner"] = p_data["username"]
                game_state["winning_card"] = card  
                
                win_amt = game_state["pot"] * 0.8
                wallets.update_one({"phone": ph}, {"$inc": {"balance": win_amt}})
                
                card_rows = []
                for r in range(5):
                    row_vals = [str(card[r*5 + c]) if card[r*5 + c] != 0 else "FREE" for c in range(5)]
                    card_rows.append(" | ".join(row_vals))
                card_text = "\n".join(card_rows)
                
                send_telegram(f"🏆 *WINNER!* \n👤 Name: {p_data['username']} \n📞 Phone: `{ph}` \n🎫 Ticket No: {t_num} \n💰 Prize: {win_amt} ETB\n\n📊 *Winning Card:* \n`{card_text}`")
                
                threading.Thread(target=lambda: (time.sleep(10), reset_game())).start()
                return jsonify({"success": True})
            
    return jsonify({"success": False, "msg": "ቢንጎ አልሞላም!"})

if __name__ == '__main__':
    threading.Timer(5, set_webhook).start() 
    threading.Thread(target=game_loop, daemon=True).start()
    
    # 🚀 Gunicorn ስህተት እንዳይፈጥር በ Eventlet ሰርቨር ቀጥታ ማስነሻ
    import eventlet.wsgi
    eventlet.wsgi.server(eventlet.listen(('0.0.0.0', int(os.environ.get("PORT", 10000)))), app)
