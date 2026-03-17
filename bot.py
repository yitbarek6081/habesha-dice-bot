import os, time, random, requests, threading
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- CONFIG ---
ADMIN_ID = "7956330391" # ያንተ ቴሌግራም ID
ADMIN_PHONE = "0945880474" # ኮሚሽን የሚገባበት ስልክ
BOT_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
MY_RENDER_URL = "https://habesha-dice-bot.onrender.com"

# MongoDB Connection
client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']

game_state = {
    "status": "lobby", 
    "timer": 30,
    "pot": 0,
    "players": {},
    "sold_tickets": {}, 
    "current_ball": "--",
    "drawn_balls": [],
    "winner": None,
    "welcome_msg": "እንኳን ወደ BESH BINGO በሰላም መጡ!"
}

def send_telegram_msg(msg):
    """መልዕክቱን በ HTML Format ለባለቤቱ ይልካል"""
    if BOT_TOKEN:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": ADMIN_ID, 
            "text": msg, 
            "parse_mode": "HTML"
        }
        try:
            requests.post(url, json=payload)
        except Exception as e:
            print(f"Telegram Error: {e}")

def generate_bingo_card():
    card = []
    ranges = [(1,15), (16,30), (31,45), (46,60), (61,75)]
    for r in ranges:
        col = random.sample(range(r[0], r[1]+1), 5)
        card.append(col)
    flat_card = []
    for row in range(5):
        for col in range(5):
            flat_card.append(card[col][row])
    flat_card[12] = 0 # FREE Space
    return flat_card

@app.route('/')
def index():
    return render_template('index.html')

# --- WEBHOOK & ADMIN COMMANDS ---
@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.json
    if "message" in update and "text" in update["message"]:
        msg_text = update["message"]["text"]
        sender_id = str(update["message"]["chat"]["id"])
        
        if sender_id == ADMIN_ID:
            if msg_text.startswith("/add"):
                try:
                    parts = msg_text.split()
                    p, a = parts[1], float(parts[2])
                    wallets.update_one({"phone": p}, {"$inc": {"balance": a}}, upsert=True)
                    send_telegram_msg(f"✅ ተሳክቷል! ለ {p} <b>{a} ETB</b> ተጨምሯል።")
                except:
                    send_telegram_msg("⚠️ ስህተት! እባክህ በዚህ መልኩ ጻፍ: <code>/add 09******** 50</code>")
            
            elif msg_text.startswith("/minus"):
                try:
                    parts = msg_text.split()
                    p, a = parts[1], float(parts[2])
                    wallets.update_one({"phone": p}, {"$inc": {"balance": -a}})
                    send_telegram_msg(f"✅ ተሳክቷል! ከ {p} <b>{a} ETB</b> ተቀንሷል።")
                except:
                    send_telegram_msg("⚠️ ስህተት! እባክህ በዚህ መልኩ ጻፍ: <code>/minus 09******** 50</code>")
    return "ok", 200

# --- WALLET REQUESTS ---
@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    data = request.json
    p, amt, tid = data.get('phone'), data.get('amount'), data.get('transaction_id')
    msg = (f"🔔 <b>የተቀማጭ ጥያቄ!</b>\n\n"
           f"👤 ስልክ: {p}\n"
           f"💰 መጠን: {amt} ETB\n"
           f"🧾 ID: {tid}\n\n"
           f"ለማጽደቅ ተጫነው:\n<code>/add {p} {amt}</code>")
    send_telegram_msg(msg)
    return jsonify({"success": True, "msg": "ጥያቄው ተልኳል! አስተዳዳሪው ሲያጸድቅልዎ ይገባል።"})

@app.route('/request_withdraw', methods=['POST'])
def request_withdraw():
    data = request.json
    p, amt, target = data.get('phone'), float(data.get('amount')), data.get('target_phone')
    user = wallets.find_one({"phone": p})
    if not user or user.get('balance', 0) < amt:
        return jsonify({"success": False, "msg": "በቂ ሂሳብ የሎትም!"})
    
    msg = (f"📤 <b>የገንዘብ ማውጫ ጥያቄ!</b>\n\n"
           f"👤 ተጫዋች: {p}\n"
           f"💰 መጠን: {amt} ETB\n"
           f"📱 መላኪያ ስልክ: {target}\n\n"
           f"ከዋሌቱ ለመቀነስ ተጫነው:\n<code>/minus {p} {amt}</code>")
    send_telegram_msg(msg)
    return jsonify({"success": True, "msg": "ጥያቄው ተልኳል።"})

# --- GAME LOGIC ---
@app.route('/get_status')
def get_status():
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone})
    if not user and phone:
        wallets.insert_one({"phone": phone, "balance": 0})
        user = {"balance": 0}
    
    p_data = game_state["players"].get(phone, {"active": False, "cards": []})
    
    return jsonify({
        **game_state, 
        "balance": user['balance'] if user else 0, 
        "my_cards": p_data["cards"], 
        "is_player": p_data["active"]
    })

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    data = request.json
    phone, t_num = data.get('phone'), str(data.get('ticket_num'))
    
    if game_state["status"] != "lobby": 
        return jsonify({"success":False, "msg":"ሽያጭ ተዘግቷል!"})
    
    if t_num in game_state["sold_tickets"]: 
        return jsonify({"success":False, "msg":"ይህ ካርቴላ ተይዟል!"})

    # ማሻሻያ 1: የአንድ ሰው የካርቴላ ብዛት ከ 2 እንዳይበልጥ መገደብ
    player_info = game_state["players"].get(phone, {"cards": []})
    if len(player_info["cards"]) >= 2:
        return jsonify({"success":False, "msg":"ከ 2 ካርቴላ በላይ መግዛት አይቻልም!"})

    user = wallets.find_one({"phone": phone})
    if not user or user.get('balance', 0) < 10: 
        return jsonify({"success":False, "msg":"በቂ ሂሳብ የሎትም!"})
    
    wallets.update_one({"phone": phone}, {"$inc": {"balance": -10}})
    game_state["sold_tickets"][t_num] = phone
    game_state["pot"] += 10
    
    card = generate_bingo_card()
    if phone not in game_state["players"]:
        game_state["players"][phone] = {"cards": [card], "active": True}
    else:
        game_state["players"][phone]["cards"].append(card)
    
    return jsonify({"success": True})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    phone = request.json.get('phone')
    if game_state["status"] != "playing": 
        return jsonify({"success": False, "msg": "ጌሙ አልተጀመረም!"})
    
    player_data = game_state["players"].get(phone)
    if not player_data: 
        return jsonify({"success": False})

    drawn_numbers = {int(b[1:]) for b in game_state["drawn_balls"] if len(b) > 1}
    drawn_numbers.add(0)

    def is_winner(card):
        for i in range(0, 25, 5): 
            if all(card[i+j] in drawn_numbers for j in range(5)): return True
        for i in range(5): 
            if all(card[i+j*5] in drawn_numbers for i in range(5)): return True
        if all(card[i*6] in drawn_numbers for i in range(5)) or all(card[(i+1)*4] in drawn_numbers for i in range(5)): return True
        return False

    if any(is_winner(c) for c in player_data["cards"]):
        win_amt = game_state["pot"] * 0.8
        admin_amt = game_state["pot"] * 0.2
        wallets.update_one({"phone": phone}, {"$inc": {"balance": win_amt}})
        wallets.update_one({"phone": ADMIN_PHONE}, {"$inc": {"balance": admin_amt}}, upsert=True)
        game_state["winner"] = phone
        game_state["status"] = "result"
        send_telegram_msg(f"🏆 <b>ቢንጎ!</b>\nአሸናፊ: {phone}\nሽልማት: <b>{win_amt} ETB</b>")
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "ገና ነዎት!"})

def game_loop():
    # ማሻሻያ 2: B I N G O ፊደላትን ከቁጥሮች ጋር ማጣመር
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    
    while True:
        for i in range(30, -1, -1):
            game_state["timer"] = i
            time.sleep(1)
        
        if len(game_state["players"]) >= 2:
            game_state["status"] = "playing"
            shuffled = balls.copy()
            random.shuffle(shuffled)
            for b in shuffled:
                if game_state["status"] != "playing": break
                game_state["current_ball"] = b
                game_state["drawn_balls"].append(b)
                time.sleep(5)
            
            time.sleep(10)

            # Reset State after game ends
            game_state.update({
                "status": "lobby", "winner": None, "pot": 0, "players": {},
                "sold_tickets": {}, "drawn_balls": [], "current_ball": "--", "timer": 30
            })
        else:
            game_state["timer"] = 30
            continue

def keep_alive():
    while True:
        try: 
            requests.get(MY_RENDER_URL)
        except: 
            pass
        time.sleep(600)

threading.Thread(target=game_loop, daemon=True).start()
threading.Thread(target=keep_alive, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
