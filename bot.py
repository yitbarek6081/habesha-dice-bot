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
    "winner": None,
    "welcome_msg": "እንኳን ወደ BESH BINGO በሰላም መጡ!"
}

def send_telegram_msg(msg):
    if BOT_TOKEN:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        try: requests.post(url, json={"chat_id": ADMIN_ID, "text": msg}, timeout=5)
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
    flat_card[12] = 0 
    return flat_card

@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone})
    if not user and phone:
        wallets.insert_one({"phone": phone, "balance": 0})
        user = {"balance": 0}
    p_data = game_state["players"].get(phone, {"active": False, "cards": []})
    return jsonify({**game_state, "balance": user.get('balance', 0) if user else 0, "my_cards": p_data["cards"]})

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

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    data = request.json
    phone, t_num = data.get('phone'), str(data.get('ticket_num'))
    if game_state["status"] != "lobby": return jsonify({"success": False, "msg": "መሰረዝ አይቻልም!"})
    if t_num in game_state["sold_tickets"] and game_state["sold_tickets"][t_num] == phone:
        wallets.update_one({"phone": phone}, {"$inc": {"balance": 10}})
        del game_state["sold_tickets"][t_num]
        game_state["pot"] -= 10
        if phone in game_state["players"]:
            game_state["players"][phone]["cards"].pop()
            if not game_state["players"][phone]["cards"]: del game_state["players"][phone]
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    phone = request.json.get('phone')
    if game_state["status"] != "playing": return jsonify({"success": False, "msg": "ጌሙ አልተጀመረም!"})
    p_data = game_state["players"].get(phone)
    if not p_data: return jsonify({"success": False})
    drawn = {int(b[1:]) for b in game_state["drawn_balls"] if len(b) > 1}
    drawn.add(0)
    def check(c):
        for i in range(0,25,5): 
            if all(c[i+j] in drawn for j in range(5)): return True
        for i in range(5): 
            if all(c[i+j*5] in drawn for j in range(5)): return True
        if all(c[i*6] in drawn for i in range(5)) or all(c[(i+1)*4] in drawn for i in range(5)): return True
        return False
    if any(check(c) for c in p_data["cards"]):
        win, admin = game_state["pot"]*0.8, game_state["pot"]*0.2
        wallets.update_one({"phone": phone}, {"$inc": {"balance": win}})
        wallets.update_one({"phone": ADMIN_PHONE}, {"$inc": {"balance": admin}}, upsert=True)
        game_state["winner"], game_state["status"] = phone, "result"
        send_telegram_msg(f"🏆 BINGO! አሸናፊ: {phone}, ሽልማት: {win} ETB")
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "ገና ነዎት!"})

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        game_state.update({"status":"lobby","winner":None,"pot":0,"players":{},"sold_tickets":{},"drawn_balls":[],"current_ball":"--"})
        for i in range(30, 0, -1):
            # 1 ሰከንድ ሲቀረው 2 ሰው እስኪሞላ እንዲጠብቅ የማድረጊያ Logic
            while i == 1 and len(game_state["players"]) < 2:
                game_state["timer"] = "ሰው እየተጠበቀ ነው..."
                time.sleep(1)
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
                time.sleep(4)
            time.sleep(10) # ውጤት ለማሳየት

def keep_alive():
    while True:
        try: requests.get(MY_RENDER_URL)
        except: pass
        time.sleep(600)

threading.Thread(target=game_loop, daemon=True).start()
threading.Thread(target=keep_alive, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
