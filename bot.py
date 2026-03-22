import os, time, random, requests, threading
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

game_state = {
    "status": "lobby", "timer": 30, "pot": 0, "players": {},
    "sold_tickets": {}, "current_ball": "--", "drawn_balls": [], "winner": None
}

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"})
    except: print("Telegram Error")

def set_webhook():
    webhook_url = f"{RENDER_URL}/webhook"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}"
    try: requests.get(url)
    except: print("Webhook set failed")

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"]
        chat_id = str(data["message"]["chat"]["id"])
        
        if chat_id == ADMIN_ID and msg.startswith("/add"):
            try:
                parts = msg.split()
                if len(parts) == 3:
                    target_phone, amount = parts[1], float(parts[2])
                    wallets.update_one({"phone": target_phone}, {"$inc": {"balance": amount}}, upsert=True)
                    send_telegram(f"✅ ለ `{target_phone}` {amount} ETB ተጨምሯል።")
            except: send_telegram("❌ ስህተት! ፎርማቱ: /add ስልክ መጠን")
        
        elif chat_id == ADMIN_ID and msg.startswith("/sub"):
            try:
                parts = msg.split()
                if len(parts) == 3:
                    target_phone, amount = parts[1], float(parts[2])
                    wallets.update_one({"phone": target_phone}, {"$inc": {"balance": -amount}})
                    send_telegram(f"⚠️ ከ `{target_phone}` {amount} ETB ተቀንሷል።")
            except: send_telegram("❌ ስህተት! ፎርማቱ: /sub ስልክ መጠን")

    return "OK", 200

# --- የቢንጎ ህግ ማረጋገጫ ---
def is_winner(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0) # FREE space
    
    # Rows (አግድም)
    for i in range(0, 25, 5):
        if all(card[i + j] in drawn_set for j in range(5)): return True
    # Columns (ቁልቁል)
    for i in range(5):
        if all(card[i + j*5] in drawn_set for j in range(5)): return True
    # Diagonals
    if all(card[i*6] in drawn_set for i in range(5)): return True
    if all(card[(i+1)*4] in drawn_set for i in range(5)): return True
    
    return False

def game_loop():
    balls = [f"B{i}" for i in range(1, 16)] + [f"I{i}" for i in range(16, 31)] + \
            [f"N{i}" for i in range(31, 46)] + [f"G{i}" for i in range(46, 61)] + \
            [f"O{i}" for i in range(61, 76)]
    
    while True:
        if game_state["status"] == "lobby":
            for i in range(30, -1, -1):
                if game_state["status"] != "lobby": break
                game_state["timer"] = i
                time.sleep(1)
            
            if len(game_state["players"]) >= 2:
                game_state["status"] = "playing"
                game_state["drawn_balls"] = []
                shuffled = balls.copy()
                random.shuffle(shuffled)
                for b in shuffled:
                    if game_state["status"] != "playing": break
                    game_state["current_ball"] = b
                    game_state["drawn_balls"].append(b)
                    time.sleep(4) # የፍጥነት ማስተካከያ (4 ሰከንድ)
            else:
                game_state["timer"] = 30
        time.sleep(1)

@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone})
    p_data = game_state["players"].get(phone, {"cards": []})
    return jsonify({
        **game_state, 
        "balance": user['balance'] if user else 0, 
        "my_cards": p_data["cards"], 
        "active_players": len(game_state["players"])
    })

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json
    ph, t_num, uname = d.get('phone'), str(d.get('ticket_num')), d.get('username')
    
    # የ 2 ካርተላ ገደብ
    if ph in game_state["players"] and len(game_state["players"][ph]["cards"]) >= 2:
        return jsonify({"success": False, "msg": "በአንድ ጨዋታ ከ 2 ካርተላ በላይ መግዛት አይቻልም!"})
    
    # ባላንስ ቼክ
    res = wallets.find_one_and_update(
        {"phone": ph, "balance": {"$gte": 10}}, 
        {"$inc": {"balance": -10}}, 
        return_document=ReturnDocument.AFTER
    )
    
    if res and game_state["status"] == "lobby":
        game_state["sold_tickets"][t_num] = ph
        game_state["pot"] += 10
        
        # ካርተላ ማመንጫ (B-I-N-G-O በየረድፉ)
        col_b = random.sample(range(1, 16), 5)
        col_i = random.sample(range(16, 31), 5)
        col_n = random.sample(range(31, 46), 5)
        col_g = random.sample(range(46, 61), 5)
        col_o = random.sample(range(61, 76), 5)
        
        flat = []
        for i in range(5):
            flat.extend([col_b[i], col_i[i], col_n[i], col_g[i], col_o[i]])
        
        flat[12] = 0 # Center Free
        
        if ph not in game_state["players"]:
            game_state["players"][ph] = {"cards": [flat], "username": uname}
        else:
            game_state["players"][ph]["cards"].append(flat)
            
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም ወይም ጨዋታ ተጀምሯል!"})

@app.route('/withdraw', methods=['POST'])
def withdraw():
    d = request.json
    ph, amt = d.get('phone'), float(d.get('amount'))
    
    res = wallets.find_one_and_update(
        {"phone": ph, "balance": {"$gte": amt}},
        {"$inc": {"balance": -amt}},
        return_document=ReturnDocument.AFTER
    )
    
    if res:
        msg = f"📤 *Withdraw Request*\n📞 Phone: `{ph}`\n💵 Amount: `{amt}` ETB\n\n👇 ይክፈሉና ባላንሱን ለመመለስ ካስፈለገ `/add` ይጠቀሙ።"
        send_telegram(msg)
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    ph = request.json.get('phone')
    p_data = game_state["players"].get(ph)
    if game_state["status"] == "playing" and p_data and any(is_winner(c, game_state["drawn_balls"]) for c in p_data["cards"]):
        win_amt = game_state["pot"] * 0.8
        wallets.update_one({"phone": ph}, {"$inc": {"balance": win_amt}})
        game_state["winner"], game_state["status"] = p_data["username"], "result"
        send_telegram(f"🏆 *WINNER!* \n👤 Name: {p_data['username']} \n📞 Phone: `{ph}` \n💰 Prize: {win_amt} ETB")
        
        def reset():
            time.sleep(10)
            game_state.update({
                "status": "lobby", "winner": None, "pot": 0, "players": {}, 
                "sold_tickets": {}, "drawn_balls": [], "current_ball": "--", "timer": 30
            })
        threading.Thread(target=reset).start()
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "ቢንጎ አልሞላም!"})

if __name__ == '__main__':
    threading.Timer(5, set_webhook).start() 
    threading.Thread(target=game_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
