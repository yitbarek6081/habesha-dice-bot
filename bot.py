import os, time, random, requests, threading
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient, ReturnDocument
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- CONFIG ---
# እነዚህን መረጃዎች በ Render Environment Variables ውስጥ ማስገባት ይመከራል
ADMIN_ID = "7956330391" 
BOT_TOKEN = "8708969585:AAE-MQTUle1g83tGTmL0pNBm7oJOYw0u5dc" 
MONGO_URL = os.getenv("MONGO_URL")
RENDER_URL = "https://habesha-dice-bot.onrender.com" 

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']

# የጨዋታው ሁኔታ (Global State)
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

def send_telegram(text):
    """ለአድሚኑ በቴሌግራም መልእክት መላኪያ"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"})
    except:
        print("Telegram Error")

def set_webhook():
    """ቦቱን ከዌብሳይቱ ጋር ማገናኛ"""
    webhook_url = f"{RENDER_URL}/webhook"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}"
    try:
        requests.get(url)
    except:
        print("Webhook set failed")

@app.route('/webhook', methods=['POST'])
def webhook():
    """ከቴሌግራም የሚመጡ የአድሚን ትዕዛዞች መቀበያ"""
    data = request.json
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"]
        chat_id = str(data["message"]["chat"]["id"])
        
        if chat_id == ADMIN_ID:
            parts = msg.split()
            # /add ስልክ መጠን
            if msg.startswith("/add") and len(parts) == 3:
                wallets.update_one({"phone": parts[1]}, {"$inc": {"balance": float(parts[2])}}, upsert=True)
                send_telegram(f"✅ ለ `{parts[1]}` {parts[2]} ETB ተጨምሯል")
            # /sub ስልክ መጠን
            elif msg.startswith("/sub") and len(parts) == 3:
                wallets.update_one({"phone": parts[1]}, {"$inc": {"balance": -float(parts[2])}})
                send_telegram(f"⚠️ ከ `{parts[1]}` {parts[2]} ETB ተቀንሷል")
    return "OK", 200

def is_winner(card, drawn_numbers):
    """የቢንጎ ህግ ማረጋገጫ (Rows, Cols, Diagonals)"""
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0) # FREE space
    
    # አግድም እና ቁልቁል ማረጋገጫ
    for i in range(5):
        if all(card[i*5 + j] in drawn_set for j in range(5)): return True 
        if all(card[j*5 + i] in drawn_set for j in range(5)): return True 
    # ሰያፍ ማረጋገጫ
    if all(card[i*6] in drawn_set for i in range(5)): return True 
    if all(card[4 + i*4] in drawn_set for i in range(5)): return True 
    return False

def game_loop():
    """ዋናው የጨዋታ ሂደት - በየ 5 ሰከንዱ ኳስ ማውጣት"""
    balls = []
    for i, l in enumerate("BINGO"):
        for n in range(i*15 + 1, (i+1)*15 + 1):
            balls.append(f"{l}{n}")
            
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
                    time.sleep(5) # ኳስ የሚወጣበት ፍጥነት
            else: 
                game_state["timer"] = 30
        time.sleep(1)

@app.route('/')
def index():
    return render_template('index.html')

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
    
    if ph in game_state["players"] and len(game_state["players"][ph]["cards"]) >= 2:
        return jsonify({"success": False, "msg": "Max 2 tickets!"})
    
    res = wallets.find_one_and_update(
        {"phone": ph, "balance": {"$gte": 10}}, 
        {"$inc": {"balance": -10}}, 
        return_document=ReturnDocument.AFTER
    )
    
    if res and game_state["status"] == "lobby":
        game_state["sold_tickets"][t_num] = ph
        game_state["pot"] += 10
        
        # ትክክለኛ የቢንጎ ካርድ ማመንጫ (B-I-N-G-O ክልል የጠበቀ)
        card_cols = [random.sample(range(i*15+1, (i+1)*15+1), 5) for i in range(5)]
        flat = [card_cols[c][r] for r in range(5) for c in range(5)]
        flat[12] = 0 # FREE Space
        
        if ph not in game_state["players"]:
            game_state["players"][ph] = {"cards": [flat], "username": uname}
        else:
            game_state["players"][ph]["cards"].append(flat)
        return jsonify({"success": True})
    return jsonify({"success": False})

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
            
            # ለአድሚኑ ስልኩን ኮፒ በሚሆን መልኩ መላክ
            send_telegram(f"🏆 *Winner!* \n👤 {p_data['username']} \n📞 `{ph}`\n💰 {win_amt} ETB")
            
            def reset():
                time.sleep(5)
                game_state.update({
                    "status": "lobby", "winner": None, "pot": 0, "players": {}, 
                    "sold_tickets": {}, "drawn_balls": [], "current_ball": "--", "timer": 30
                })
            threading.Thread(target=reset).start()
            return jsonify({"success": True})
    return jsonify({"success": False})

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
        # ስልክ ቁጥሩን ለመቅዳት እንዲመች በ ` ` ምልክት ተደርጓል
        send_telegram(f"📤 *Withdraw Request*\n📞 `{ph}`\n💵 `{amt}` ETB")
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    d = request.json
    ph, amt = d.get('phone'), d.get('amount')
    # በቀጥታ ኮፒ ተደርጎ እንዲላክ አድሚን ትዕዛዙን ጨምሮ ይላካል
    send_telegram(f"📥 *Deposit Request*\n📞 `{ph}`\n💵 `{amt}` ETB\n🆔 `{d.get('transaction_id')}`\n\n✅ `/add {ph} {amt}`")
    return jsonify({"success": True})

if __name__ == '__main__':
    # ዌብሁክ ቦቱ ከሰራ ከ 5 ሰከንድ በኋላ ይገናኛል
    threading.Timer(5, set_webhook).start()
    # የጨዋታው ሂደት በጀርባ (Background) እንዲሰራ
    threading.Thread(target=game_loop, daemon=True).start()
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
