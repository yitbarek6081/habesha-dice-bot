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
    "status": "lobby", 
    "timer": 30, 
    "pot": 0, 
    "players": {},
    "sold_tickets": {}, 
    "current_ball": "--", 
    "drawn_balls": [], 
    "winner": None
}

# --- ሰርቨር ሳይድ ብር መመላሻ (REFUND) ---
@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    d = request.json
    ph = d.get('phone')
    t_num = str(d.get('ticket_num'))
    
    if game_state["status"] == "lobby" and game_state["sold_tickets"].get(t_num) == ph:
        # 1. ብር መመለስ
        wallets.update_one({"phone": ph}, {"$inc": {"balance": 10}})
        # 2. ከፖት መቀነስ
        game_state["pot"] -= 10
        # 3. ቲኬቱን ማጥፋት
        del game_state["sold_tickets"][t_num]
        
        # 4. ካርዱን ከተጫዋቹ ዝርዝር ማጥፋት
        if ph in game_state["players"]:
            # ተጫዋቹ ሁለት ቲኬት ሊኖረው ስለሚችል የመጨረሻውን እናጥፋለን
            if len(game_state["players"][ph]["cards"]) > 0:
                game_state["players"][ph]["cards"].pop()
                if len(game_state["players"][ph]["cards"]) == 0:
                    del game_state["players"][ph]
                    
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "መመለስ አልተቻለም!"})

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"})
    except: print("Telegram Error")

# --- GAME LOGIC ---
def is_winner(card, drawn_numbers):
    # ካርዱ 5x5 Grid ነው (25 ቁጥሮች)
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0) # FREE space id is 0
    
    # Rows & Columns check
    for i in range(5):
        if all(card[i*5 + j] in drawn_set for j in range(5)): return True # Row
        if all(card[j*5 + i] in drawn_set for j in range(5)): return True # Column
    
    # Diagonals
    if all(card[i*6] in drawn_set for i in range(5)): return True 
    if all(card[4 + i*4] in drawn_set for i in range(5)): return True 
    return False

def game_loop():
    # ኳሶችን ማዘጋጀት (B1-15, I16-30...)
    balls = [f"{'BINGO'[i]}{n}" for i in range(5) for n in range(i*15+1, (i+1)*15+1)]
    
    while True:
        if game_state["status"] == "lobby":
            # ቢያንስ 2 ሰው እስኪገባ ሰዓቱ 30 ላይ ይቆማል
            if len(game_state["players"]) < 2:
                game_state["timer"] = 30
                time.sleep(1)
                continue
            
            # ቆጠራ መጀመር
            for i in range(30, -1, -1):
                if game_state["status"] != "lobby": break
                game_state["timer"] = i
                time.sleep(1)
            
            # ጨዋታ ማስጀመር
            if len(game_state["players"]) >= 2:
                game_state["status"] = "playing"
                game_state["drawn_balls"] = []
                shuffled = balls.copy()
                random.shuffle(shuffled)
                
                for b in shuffled:
                    if game_state["status"] != "playing": break
                    game_state["current_ball"] = b
                    game_state["drawn_balls"].append(b)
                    time.sleep(4) # የኳስ ፍጥነት (4 ሰከንድ)
            else:
                game_state["timer"] = 30
        
        time.sleep(1)

# --- ROUTES ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone})
    p_data = game_state["players"].get(phone, {"cards": []})
    return jsonify({
        "status": game_state["status"],
        "timer": game_state["timer"],
        "pot": game_state["pot"],
        "sold_tickets": game_state["sold_tickets"],
        "current_ball": game_state["current_ball"],
        "drawn_balls": game_state["drawn_balls"],
        "winner": game_state["winner"],
        "balance": user['balance'] if user else 0,
        "my_cards": p_data["cards"],
        "active_players": len(game_state["players"])
    })

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json
    ph, t_num, uname = d.get('phone'), str(d.get('ticket_num')), d.get('username')
    
    if ph in game_state["players"] and len(game_state["players"][ph]["cards"]) >= 2:
        return jsonify({"success": False, "msg": "Max 2 tickets!"})
    
    # ገንዘብ መቀነስ
    res = wallets.find_one_and_update(
        {"phone": ph, "balance": {"$gte": 10}}, 
        {"$inc": {"balance": -10}}, 
        return_document=ReturnDocument.AFTER
    )
    
    if res and game_state["status"] == "lobby":
        game_state["sold_tickets"][t_num] = ph
        game_state["pot"] += 10
        
        # የቢንጎ ካርድ ማመንጨት (Correct Column Ranges)
        card_cols = [random.sample(range(i*15+1, (i+1)*15+1), 5) for i in range(5)]
        flat = []
        for r in range(5):
            for c in range(5):
                flat.append(card_cols[c][r])
        flat[12] = 0 # FREE Space
        
        if ph not in game_state["players"]:
            game_state["players"][ph] = {"cards": [flat], "username": uname}
        else:
            game_state["players"][ph]["cards"].append(flat)
            
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "Insufficient balance or game started"})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    ph = request.json.get('phone')
    p_data = game_state["players"].get(ph)
    
    if game_state["status"] == "playing" and p_data:
        if any(is_winner(c, game_state["drawn_balls"]) for c in p_data["cards"]):
            win_amt = game_state["pot"] * 0.8
            wallets.update_one({"phone": ph}, {"$inc": {"balance": win_amt}})
            
            game_state["winner"] = p_data["username"]
            game_state["status"] = "result"
            
            send_telegram(f"🏆 *Winner!* \n👤 {p_data['username']} \n📞 `{ph}`\n💰 {win_amt} ETB")
            
            # Reset Game after 7 seconds
            def reset():
                time.sleep(7)
                game_state.update({
                    "status": "lobby", "winner": None, "pot": 0, 
                    "players": {}, "sold_tickets": {}, 
                    "drawn_balls": [], "current_ball": "--", "timer": 30
                })
            threading.Thread(target=reset).start()
            return jsonify({"success": True})
            
    return jsonify({"success": False})

# --- Webhook & Main ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"]
        chat_id = str(data["message"]["chat"]["id"])
        if chat_id == ADMIN_ID:
            parts = msg.split()
            if msg.startswith("/add") and len(parts) == 3:
                wallets.update_one({"phone": parts[1]}, {"$inc": {"balance": float(parts[2])}}, upsert=True)
                send_telegram(f"✅ ተጨምሯል፡ `{parts[1]}` +{parts[2]}")
    return "OK", 200

if __name__ == '__main__':
    threading.Thread(target=game_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
