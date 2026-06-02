import os
import time
import random
import requests
import threading
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient, ReturnDocument
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

# Thread lock to prevent race conditions across requests & background loop
state_lock = threading.Lock()

game_state = {
    "status": "lobby", 
    "timer": 30, 
    "pot": 0, 
    "players": {},       # chat_id -> {"cards": {ticket_num: flat_card}, "username": uname}
    "sold_tickets": {},  # ticket_num -> chat_id
    "current_ball": "--", 
    "drawn_balls": [], 
    "winner": None
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
    if not data:
        return "OK", 200
        
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"]
        chat_id = str(data["message"]["chat"]["id"])

        # --- Referral tracking (New players only) ---
        if msg.startswith("/start"):
            parts = msg.split()
            if len(parts) > 1:
                agent_phone = parts[1]
                existing_user = wallets.find_one({"phone": chat_id})
                
                if not existing_user:
                    wallets.update_one(
                        {"phone": chat_id},
                        {"$set": {
                            "phone": chat_id, 
                            "balance": 0, 
                            "referred_by": agent_phone
                        }},
                        upsert=True
                    )

        # --- Admin controls ---
        if chat_id == ADMIN_ID and msg.startswith("/add"):
            try:
                parts = msg.split()
                if len(parts) == 3:
                    target_phone, amount = parts[1], float(parts[2])
                    wallets.update_one({"phone": target_phone}, {"$inc": {"balance": amount}}, upsert=True)
                    send_telegram(f"✅ ለ `{target_phone}` {amount} ETB ተጨምሯል።")
            except:
                send_telegram("❌ ስህተት! ፎርማቱ: /add ስልክ መጠን")
        
        elif chat_id == ADMIN_ID and msg.startswith("/sub"):
            try:
                parts = msg.split()
                if len(parts) == 3:
                    target_phone, amount = parts[1], float(parts[2])
                    wallets.update_one({"phone": target_phone}, {"$inc": {"balance": -amount}})
                    send_telegram(f"⚠️ ከ `{target_phone}` {amount} ETB ተቀንሷል።")
            except:
                send_telegram("❌ ስህተት! ፎርማቱ: /sub ስልክ መጠን")

    return "OK", 200

def is_winner(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0)  # Free space center mark
    for i in range(5):
        if all(card[i*5 + j] in drawn_set for j in range(5)): return True 
        if all(card[j*5 + i] in drawn_set for i in range(5)): return True 
    if all(card[i*6] in drawn_set for i in range(5)): return True 
    if all(card[(i+1)*4] in drawn_set for i in range(5)): return True 
    return False

def reset_game():
    with state_lock:
        game_state.update({
            "status": "lobby", "winner": None, "pot": 0, "players": {}, 
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

            # Ball drawing process
            for b in shuffled:
                with state_lock:
                    if game_state["status"] != "playing": 
                        break
                    game_state["current_ball"] = b
                    game_state["drawn_balls"].append(b)
                time.sleep(5)
            
            # Handle scenario where 75 balls pass without a winner declaration
            with state_lock:
                if game_state["status"] == "playing":
                    game_state["status"] = "result"
                    game_state["winner"] = "No Winner (House)"
                    send_telegram("ℹ️ ጨዋታው ያለ አሸናፊ ተጠናቋል። ሁሉም ኳሶች አልቀዋል።")
                    threading.Thread(target=lambda: (time.sleep(10), reset_game())).start()

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
    ph, t_num, uname = d.get('phone'), str(d.get('ticket_num')), d.get('username')
    
    if not ph or not t_num:
        return jsonify({"success": False, "msg": "የተሳሳተ መረጃ!"})

    # --- ATOMIC STATE DOUBLE-CLICK PROTECTION ---
    with state_lock:
        if game_state["status"] != "lobby":
            return jsonify({"success": False, "msg": "ጨዋታ ተጀምሯል!"})
        if t_num in game_state["sold_tickets"]:
            return jsonify({"success": False, "msg": "ይህ ካርተላ ቀድሞ ተይዟል!"})
        if ph in game_state["players"] and len(game_state["players"][ph]["cards"]) >= 2:
            return jsonify({"success": False, "msg": "ከ 2 ካርተላ በላይ መግዛት አይቻልም!"})

    # Deduct balance securely using MongoDB atomic query mechanics
    res = wallets.find_and_modify(
        query={"phone": ph, "balance": {"$gte": 10}}, 
        update={"$inc": {"balance": -10}}, 
        new=True
    )
    
    if res:
        # Generate Bingo Card structural properties
        card = []
        for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]:
            card.append(random.sample(range(r[0], r[1]+1), 5))
        flat = [card[c][r] for r in range(5) for c in range(5)]
        flat[12] = 0  # Center element Free Space marker
        
        with state_lock:
            # Re-verify lobby state status before modifying game tracking map variables
            if game_state["status"] != "lobby" or t_num in game_state["sold_tickets"]:
                wallets.update_one({"phone": ph}, {"$inc": {"balance": 10}}) # Rollback balance
                return jsonify({"success": False, "msg": "ስህተት ተከስቷል ወይም ጨዋታው ተጀምሯል!"})
                
            game_state["sold_tickets"][t_num] = ph
            game_state["pot"] += 10
            
            if ph not in game_state["players"]:
                game_state["players"][ph] = {"cards": {t_num: flat}, "username": uname}
            else:
                game_state["players"][ph]["cards"][t_num] = flat
                
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    d = request.json or {}
    ph, t_num = d.get('phone'), str(d.get('ticket_num'))
    
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
    amt = d.get('amount')
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
    ph, amt = d.get('phone'), float(d.get('amount'))
    
    res = wallets.find_and_modify(
        query={"phone": ph, "balance": {"$gte": amt}},
        update={"$inc": {"balance": -amt}},
        new=True
    )
    if res:
        msg = f"📤 *Withdraw Request*\n📞 Phone: `{ph}`\n💵 Amount: `{amt}` ETB\n\n⚠️ ብሩን በቴሌብር ላክና ባላንሱን ለመመለስ ካስፈለገ `/add` ተጠቀም።"
        send_telegram(msg)
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    ph = request.json.get('phone') if request.json else None
    if not ph:
        return jsonify({"success": False, "msg": "የተሳሳተ መረጃ!"})
    
    # --- ATOMIC WINNER PROCESSING DOUBLE-CLICK PROTECTION ---
    with state_lock:
        p_data = game_state["players"].get(ph)
        
        # If status already changed from "playing" to "result" by the first execution click,
        # consecutive duplicate operations drop cleanly right here.
        if game_state["status"] != "playing" or not p_data:
            return jsonify({"success": False, "msg": "ይገባኛል ጥያቄው ውድቅ ተደርጓል!"})
            
        cards_to_check = list(p_data["cards"].values())
        
        if any(is_winner(c, game_state["drawn_balls"]) for c in cards_to_check):
            win_amt = game_state["pot"] * 0.8
            
            # Change state parameters immediately inside lock context window 
            game_state["winner"] = p_data["username"]
            game_state["status"] = "result"
            
            # Issue payload updates atomically inside persistence tier
            wallets.update_one({"phone": ph}, {"$inc": {"balance": win_amt}})
            
            send_telegram(f"🏆 *WINNER!* \n👤 Name: {p_data['username']} \n📞 Phone: `{ph}` \n💰 Prize: {win_amt} ETB")
            threading.Thread(target=lambda: (time.sleep(10), reset_game())).start()
            return jsonify({"success": True})
            
    return jsonify({"success": False, "msg": "ቢንጎ አልሞላም!"})

if __name__ == '__main__':
    threading.Timer(5, set_webhook).start() 
    threading.Thread(target=game_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
