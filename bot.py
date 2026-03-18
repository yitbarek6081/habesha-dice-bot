import os, time, random, requests, threading
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- CONFIG ---
ADMIN_ID = "7956330391" 
ADMIN_PHONE = "0945880474" 
BOT_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
MY_RENDER_URL = "https://habesha-dice-bot.onrender.com"

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

def send_telegram_msg(msg):
    if BOT_TOKEN:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": ADMIN_ID, "text": msg, "parse_mode": "HTML"}
        try: requests.post(url, json=payload)
        except: pass

def generate_bingo_card():
    card = []
    ranges = [(1,15), (16,30), (31,45), (46,60), (61,75)]
    for r in ranges:
        col = random.sample(range(r[0], r[1]+1), 5)
        card.append(col)
    flat_card = []
    for row in range(5):
        for col in range(5): flat_card.append(card[col][row])
    flat_card[12] = 0 # FREE
    return flat_card

@app.route('/')
def index(): return render_template('index.html')

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    data = request.json
    p, amt, tid = data.get('phone'), data.get('amount'), data.get('transaction_id')
    msg = f"🔔 <b>የተቀማጭ ጥያቄ!</b>\n\n👤 ስልክ: {p}\n💰 መጠን: {amt} ETB\n🧾 ID: {tid}\n\nማጽደቂያ: <code>/add {p} {amt}</code>"
    send_telegram_msg(msg)
    return jsonify({"success": True, "msg": "ጥያቄው ተልኳል!"})

@app.route('/get_status')
def get_status():
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone})
    p_data = game_state["players"].get(phone, {"active": False, "cards": []})
    return jsonify({**game_state, "balance": user['balance'] if user else 0, "my_cards": p_data["cards"], "is_player": p_data["active"]})

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    data = request.json
    phone, t_num, uname = data.get('phone'), str(data.get('ticket_num')), data.get('username', "ተጫዋች")
    if game_state["status"] != "lobby": return jsonify({"success":False, "msg":"ሽያጭ ተዘግቷል!"})
    if t_num in game_state["sold_tickets"]: return jsonify({"success":False, "msg":"ተይዟል!"})
    p_info = game_state["players"].get(phone, {"cards": []})
    if len(p_info["cards"]) >= 2: return jsonify({"success":False, "msg":"ቢበዛ 2 ካርቴላ!"})
    user = wallets.find_one({"phone": phone})
    if not user or user.get('balance', 0) < 10: return jsonify({"success":False, "msg":"በቂ ሂሳብ የሎትም!"})
    wallets.update_one({"phone": phone}, {"$inc": {"balance": -10}})
    game_state["sold_tickets"][t_num] = phone
    game_state["pot"] += 10
    card = generate_bingo_card()
    if phone not in game_state["players"]: game_state["players"][phone] = {"cards": [card], "active": True, "username": uname}
    else: game_state["players"][phone]["cards"].append(card)
    return jsonify({"success": True})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    phone = request.json.get('phone')
    if game_state["status"] != "playing": return jsonify({"success": False, "msg": "ጌሙ አልተጀመረም!"})
    player_data = game_state["players"].get(phone)
    if not player_data: return jsonify({"success": False})
    
    drawn_nums = {int(b[1:]) for b in game_state["drawn_balls"] if len(b) > 1}
    drawn_nums.add(0) # FREE space

    def check_win(card):
        # Lines: Horizontal, Vertical, Diagonal
        wins = [range(0,5), range(5,10), range(10,15), range(15,20), range(20,25), # H
                range(0,25,5), range(1,25,5), range(2,25,5), range(3,25,5), range(4,25,5), # V
                [0,6,12,18,24], [4,8,12,16,20]] # D
        return any(all(card[idx] in drawn_nums for idx in w) for w in wins)

    if any(check_win(c) for c in player_data["cards"]):
        win_amt = game_state["pot"] * 0.8
        wallets.update_one({"phone": phone}, {"$inc": {"balance": win_amt}})
        wallets.update_one({"phone": ADMIN_PHONE}, {"$inc": {"balance": game_state["pot"] * 0.2}}, upsert=True)
        game_state["winner"] = player_data["username"]
        game_state["status"] = "result"
        send_telegram_msg(f"🏆 <b>BINGO!</b>\nአሸናፊ: {game_state['winner']}\nሽልማት: {win_amt} ETB")
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "ገና ነዎት!"})

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        for i in range(30, -1, -1):
            game_state["timer"] = i
            time.sleep(1)
        if len(game_state["players"]) >= 2:
            for m in ["ዝግጁ?", "3", "2", "1"]:
                game_state["timer"] = m
                time.sleep(1)
            game_state["status"] = "playing"
            shuffled = balls.copy()
            random.shuffle(shuffled)
            for b in shuffled:
                if game_state["status"] != "playing": break
                game_state["current_ball"] = b
                game_state["drawn_balls"].append(b)
                time.sleep(5) # 5 ሰከንድ በኳስ መካከል
            time.sleep(7)
            game_state.update({"status":"lobby", "winner":None, "pot":0, "players":{}, "sold_tickets":{}, "drawn_balls":[], "current_ball":"--", "timer":30})
        else: game_state["timer"] = 30

threading.Thread(target=game_loop, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
