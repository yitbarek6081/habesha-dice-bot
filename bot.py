import os, time, random, requests
from flask import Flask, render_template, jsonify, request
from threading import Thread
from pymongo import MongoClient
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- CONFIG ---
ADMIN_ID = "7956330391"
ADMIN_PHONE = "0945880474"
BOT_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']

game_state = {
    "status": "lobby", # lobby, preparing, playing, result
    "timer": 30,
    "pot": 0,
    "players": {},
    "sold_tickets": {}, # {num: phone}
    "current_ball": "--",
    "drawn_balls": [],
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
def index(): return render_template('index.html')

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    data = request.json
    phone, t_num = data.get('phone'), str(data.get('ticket_num'))
    if game_state["status"] != "lobby": return jsonify({"success":False, "msg":"ሽያጭ ተዘግቷል!"})
    if t_num in game_state["sold_tickets"]: return jsonify({"success":False, "msg":"ተይዟል!"})
    
    user = wallets.find_one({"phone": phone})
    if not user or user.get('balance', 0) < 10: return jsonify({"success":False, "msg":"በቂ ሂሳብ የሎትም!"})

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
    if game_state["status"] != "playing": return jsonify({"success": False})
    
    win_amt = game_state["pot"] * 0.8
    admin_amt = game_state["pot"] * 0.2
    wallets.update_one({"phone": phone}, {"$inc": {"balance": win_amt}})
    wallets.update_one({"phone": ADMIN_PHONE}, {"$inc": {"balance": admin_amt}}, upsert=True)
    
    game_state["winner"] = phone
    game_state["status"] = "result"
    send_telegram_msg(f"🏆 አሸናፊ: {phone}\nሽልማት: {win_amt} ETB\nኮሚሽን: {admin_amt} ETB")
    return jsonify({"success": True})

@app.route('/get_status')
def get_status():
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone})
    p_data = game_state["players"].get(phone, {"active": False, "cards": []})
    return jsonify({**game_state, "balance": user['balance'] if user else 0, "my_cards": p_data["cards"], "is_player": p_data["active"]})

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.json
    if "message" in update and "text" in update["message"]:
        msg = update["message"]["text"]
        if str(update["message"]["chat"]["id"]) == ADMIN_ID and msg.startswith("/add"):
            p = msg.split()
            wallets.update_one({"phone": p[1]}, {"$inc": {"balance": float(p[2])}}, upsert=True)
            send_telegram_msg(f"✅ ለ {p[1]} {p[2]} ETB ተጨምሯል")
    return "ok"

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        game_state.update({"status": "lobby", "winner": None, "pot": 0, "players": {}, "sold_tickets": {}, "drawn_balls": []})
        for i in range(30, 0, -1):
            game_state["timer"] = i
            time.sleep(1)
        
        game_state["status"] = "preparing"
        for i in range(3, 0, -1):
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
            time.sleep(5) # Result display
        else: time.sleep(2)

Thread(target=game_loop, daemon=True).start()
if __name__ == '__main__': app.run(host='0.0.0.0', port=10000)
