import os, time, random, requests, threading
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient, ReturnDocument
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- CONFIG ---
ADMIN_ID = "7956330391" 
BOT_TOKEN = "8708969585:AAE-MQTUle1g83tGTmL0pNBm7oJOYw0u5dc" 
MONGO_URL = os.getenv("MONGO_URL", "mongodb+srv://user:pass@cluster.mongodb.net/bingo_db")
RENDER_URL = "https://habesha-dice-bot.onrender.com" 

# MongoDB Connection
try:
    client = MongoClient(MONGO_URL)
    db = client['bingo_db']
    wallets = db['wallets']
except Exception as e:
    print(f"MongoDB Connection Error: {e}")

game_state = {
    "status": "lobby", "timer": 30, "pot": 0, "players": {},
    "sold_tickets": {}, "current_ball": "--", "drawn_balls": [], "winner": None
}

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: 
        requests.post(url, json={
            "chat_id": ADMIN_ID, 
            "text": text, 
            "parse_mode": "Markdown"
        })
    except: print("Telegram Error")

# --- አዲስ፡ ቦቱ በራሱ ዌብሁክ እንዲያስተካክል ---
def set_webhook():
    time.sleep(5) # ሰርቨሩ እስኪነሳ መጠበቅ
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={RENDER_URL}/webhook"
    try:
        requests.get(url)
        print("✅ Telegram Webhook Connected!")
    except:
        print("❌ Webhook Connection Failed")

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
                send_telegram(f"✅ ለ `{parts[1]}` {parts[2]} ETB ተጨምሯል!")
            elif msg.startswith("/sub") and len(parts) == 3:
                wallets.update_one({"phone": parts[1]}, {"$inc": {"balance": -float(parts[2])}})
                send_telegram(f"⚠️ ከ `{parts[1]}` {parts[2]} ETB ተቀንሷል!")
    return "OK", 200

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json
    ph, t_num, uname = d.get('phone'), str(d.get('ticket_num')), d.get('username')
    
    if ph in game_state["players"] and len(game_state["players"][ph]["cards"]) >= 2:
        return jsonify({"success": False, "msg": "ከ 2 በላይ መግዛት አይቻልም!"})
    
    if t_num in game_state["sold_tickets"]:
        return jsonify({"success": False, "msg": "ይህ ቁጥር ተይዟል!"})
        
    res = wallets.find_one_and_update(
        {"phone": ph, "balance": {"$gte": 10}}, 
        {"$inc": {"balance": -10}}, 
        return_document=ReturnDocument.AFTER
    )
    
    if res and game_state["status"] == "lobby":
        game_state["sold_tickets"][t_num] = ph
        game_state["pot"] += 8 # 20% commission
        
        card = []
        for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]:
            card.append(random.sample(range(r[0], r[1]+1), 5))
        flat = [card[c][r] for r in range(5) for c in range(5)]
        flat[12] = 0 # Center Free
        
        if ph not in game_state["players"]:
            game_state["players"][ph] = {"cards": [flat], "username": uname}
        else:
            game_state["players"][ph]["cards"].append(flat)
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "ባላንስ የለም!"})

# --- አዲስ፡ ካርተላ መመለሻ (Refund) ---
@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    d = request.json
    ph, t_num = d.get('phone'), str(d.get('ticket_num'))
    
    if game_state["status"] == "lobby" and game_state["sold_tickets"].get(t_num) == ph:
        # ገንዘብ መመለስ
        wallets.update_one({"phone": ph}, {"$inc": {"balance": 10}})
        # ከጨዋታው ማውጣት
        del game_state["sold_tickets"][t_num]
        game_state["pot"] -= 8
        
        # የዚህን ተጫዋች ካርተላ ከዝርዝር ውስጥ መቀነስ
        if ph in game_state["players"]:
            game_state["players"][ph]["cards"].pop()
            if not game_state["players"][ph]["cards"]:
                del game_state["players"][ph]
        
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "ትኬቱን መመለስ አልተቻለም!"})

def is_winner(card, drawn_balls):
    nums = {int(b[1:]) for b in drawn_balls if len(b) > 1}
    nums.add(0)
    for i in range(5):
        if all(card[i*5+j] in nums for j in range(5)): return True
        if all(card[j*5+i] in nums for j in range(5)): return True
    if all(card[i*6] in nums for i in range(5)): return True
    if all(card[(i+1)*4] in nums for i in range(5)): return True
    return False

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        if game_state["status"] == "lobby":
            for i in range(30, -1, -1):
                if game_state["status"] != "lobby": break
                game_state["timer"] = i
                time.sleep(1)
            
            if len(game_state["players"]) >= 2:
                game_state["status"] = "playing"
                game_state["drawn_balls"] = []
                shuffled = balls.copy(); random.shuffle(shuffled)
                for b in shuffled:
                    if game_state["status"] != "playing": break
                    game_state["current_ball"] = b
                    game_state["drawn_balls"].append(b)
                    time.sleep(4)
            else: game_state["timer"] = 30
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

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    d = request.json
    ph, amt, tid = d.get('phone'), d.get('amount'), d.get('transaction_id')
    send_telegram(f"💰 *Deposit Request*\n📞 Phone: `{ph}`\n💵 Amount: `{amt}`\n🆔 TXID: `{tid}`\n\nለመጨመር፦ `/add {ph} {amt}`")
    return jsonify({"success": True})

@app.route('/withdraw', methods=['POST'])
def withdraw():
    d = request.json
    try:
        ph, amt = d.get('phone'), float(d.get('amount'))
        if amt < 20: return jsonify({"success": False, "msg": "ቢያንስ 20 ብር!"})
        res = wallets.find_one_and_update({"phone": ph, "balance": {"$gte": amt}}, {"$inc": {"balance": -amt}}, return_document=ReturnDocument.AFTER)
        if res:
            send_telegram(f"📤 *Withdraw Request*\n📞 Phone: `{ph}`\n💵 Amount: `{amt}`\n\nለመቀነስ፦ `/sub {ph} {amt}`")
            return jsonify({"success": True})
        return jsonify({"success": False, "msg": "ባላንስ የለም!"})
    except:
        return jsonify({"success": False, "msg": "የተሳሳተ መረጃ!"})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    ph = request.json.get('phone')
    p_data = game_state["players"].get(ph)
    if game_state["status"] == "playing" and p_data and any(is_winner(c, game_state["drawn_balls"]) for c in p_data["cards"]):
        win_amt = game_state["pot"]
        wallets.update_one({"phone": ph}, {"$inc": {"balance": win_amt}})
        game_state["winner"], game_state["status"] = p_data["username"], "result"
        send_telegram(f"🏆 *Winner!* \n👤 {p_data['username']} \n💰 {win_amt} ETB")
        
        def reset():
            time.sleep(10)
            game_state.update({"status": "lobby", "winner": None, "pot": 0, "players": {}, "sold_tickets": {}, "drawn_balls": [], "current_ball": "--", "timer": 30})
        
        threading.Thread(target=reset).start()
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "ቢንጎ አልሞላም!"})

if __name__ == '__main__':
    # Webhook አውቶማቲክ እንዲሆን
    threading.Thread(target=set_webhook, daemon=True).start()
    # የጨዋታው ሉፕ
    threading.Thread(target=game_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
