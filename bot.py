import os, time, random, requests
from flask import Flask, render_template, jsonify, request
from threading import Thread
from pymongo import MongoClient
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- ADMIN CONFIG ---
ADMIN_ID = "7956330391"
ADMIN_PHONE = "0945880474"
BOT_TOKEN = os.getenv("BOT_TOKEN") 

# --- MONGODB SETUP ---
MONGO_URL = os.getenv("MONGO_URL")
client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']

game_state = {
    "status": "lobby",
    "timer": 30,
    "pot": 0,
    "players": {},
    "current_ball": "--",
    "winner": None
}

def send_telegram_msg(msg):
    if BOT_TOKEN:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": ADMIN_ID, "text": msg})

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

@app.route('/buy_ticket', methods=['POST'])
def buy_ticket():
    data = request.json
    phone, count = data.get('phone'), int(data.get('count', 1))
    if game_state["status"] != "lobby":
        return jsonify({"success": False, "msg": "ጨዋታ ተጀምሯል! ቀጣይ ዙር ይጠብቁ።"})
    
    p_data = game_state["players"].get(phone, {"count": 0})
    if p_data["count"] + count > 2:
        return jsonify({"success": False, "msg": "ቢበዛ 2 ካርቴላ ብቻ ይፈቀዳል!"})

    user = wallets.find_one({"phone": phone})
    price = count * 10
    if not user or user.get('balance', 0) < price:
        return jsonify({"success": False, "msg": "በቂ ሂሳብ የሎትም!"})

    wallets.update_one({"phone": phone}, {"$inc": {"balance": -price}})
    game_state["pot"] += price
    new_cards = [generate_bingo_card() for _ in range(count)]
    
    if phone not in game_state["players"]:
        game_state["players"][phone] = {"cards": new_cards, "count": count, "active": True}
    else:
        game_state["players"][phone]["count"] += count
        game_state["players"][phone]["cards"].extend(new_cards)
    return jsonify({"success": True})

@app.route('/request_action', methods=['POST'])
def request_action():
    data = request.json
    msg = f"🔔 አዲስ ጥያቄ!\nዓይነት: {data['type']}\nስልክ: {data['phone']}\nመረጃ: {data['info']}"
    send_telegram_msg(msg)
    return jsonify({"success": True, "msg": "ጥያቄው ለአድሚን ተልኳል!"})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    phone = request.json.get('phone')
    if game_state["status"] != "playing": return jsonify({"success": False})
    
    win_amt = game_state["pot"] * 0.8
    admin_amt = game_state["pot"] * 0.2
    wallets.update_one({"phone": phone}, {"$inc": {"balance": win_amt}})
    wallets.update_one({"phone": ADMIN_PHONE}, {"$inc": {"balance": admin_amt}}, upsert=True)
    
    game_state["winner"] = phone
    game_state["status"] = "result"
    send_telegram_msg(f"🏆 አሸናፊ: {phone}\nሽልማት: {win_amt} ETB\nአድሚን ኮሚሽን: {admin_amt} ETB")
    return jsonify({"success": True})

@app.route('/get_status')
def get_status():
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone})
    p_data = game_state["players"].get(phone, {"active": False, "cards": []})
    return jsonify({**game_state, "balance": user['balance'] if user else 0, "cards": p_data["cards"], "is_player": p_data["active"]})

# --- TELEGRAM WEBHOOK FOR /ADD COMMAND ---
@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    update = request.json
    if "message" in update and "text" in update["message"]:
        msg_text = update["message"]["text"]
        user_id = str(update["message"]["chat"]["id"])
        
        if user_id == ADMIN_ID and msg_text.startswith("/add"):
            try:
                parts = msg_text.split()
                target_phone = parts[1]
                amount = float(parts[2])
                wallets.update_one({"phone": target_phone}, {"$inc": {"balance": amount}}, upsert=True)
                send_telegram_msg(f"✅ ለ {target_phone} {amount} ETB ተጨምሯል።")
            except:
                send_telegram_msg("❌ ስህተት! አጻጻፍ፡ /add 0911223344 100")
    return jsonify({"status": "ok"})

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        game_state.update({"status": "lobby", "winner": None, "pot": 0, "players": {}})
        for i in range(30, -1, -1):
            game_state["timer"] = i
            time.sleep(1)
        
        if len(game_state["players"]) >= 2:
            game_state["status"] = "playing"
            shuffled_balls = balls.copy()
            random.shuffle(shuffled_balls)
            for b in shuffled_balls:
                if game_state["status"] != "playing": break
                game_state["current_ball"] = b
                time.sleep(5) 
            if game_state["status"] == "result": time.sleep(5)
        else: time.sleep(2)

Thread(target=game_loop, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
