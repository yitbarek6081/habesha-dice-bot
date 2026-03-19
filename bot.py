import os, time, random, requests, threading, re
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

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']

game_state = {
    "status": "lobby", "timer": 30, "pot": 0, "players": {},
    "sold_tickets": {}, "current_ball": "--", "drawn_balls": [], "winner": None
}

def send_telegram_msg(msg):
    if BOT_TOKEN:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": ADMIN_ID, "text": msg, "parse_mode": "HTML"}
        try: requests.post(url, json=payload)
        except: pass

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    data = request.json
    ph, t_num = data.get('phone'), str(data.get('ticket_num'))
    if game_state["status"] != "lobby": return jsonify({"success": False, "msg": "ጨዋታ ተጀምሯል!"})
    if game_state["sold_tickets"].get(t_num) == ph:
        del game_state["sold_tickets"][t_num]
        game_state["pot"] -= 10
        if ph in game_state["players"]:
            if len(game_state["players"][ph]["cards"]) > 1: game_state["players"][ph]["cards"].pop()
            else: del game_state["players"][ph]
        wallets.update_one({"phone": ph}, {"$inc": {"balance": 10}})
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "የእርስዎ ቁጥር አይደለም!"})

def is_winner(card, drawn_numbers):
    for i in range(0, 25, 5): # Rows
        if all(card[i+j] in drawn_numbers for j in range(5)): return True
    for i in range(5): # Columns
        if all(card[i+j*5] in drawn_numbers for i in range(5)): return True
    if all(card[i*6] in drawn_numbers for i in range(5)): return True # Diagonals
    if all(card[(i+1)*4] in drawn_numbers for i in range(5)): return True
    return False

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        if game_state["status"] == "lobby":
            for i in range(30, -1, -1):
                if game_state["status"] != "lobby": break
                game_state["timer"] = i
                time.sleep(1)
            
            if len(game_state["players"]) >= 2 and game_state["status"] == "lobby":
                game_state["status"] = "playing"
                shuffled = balls.copy()
                random.shuffle(shuffled)
                for b in shuffled:
                    if game_state["status"] != "playing": break
                    game_state["current_ball"] = b
                    game_state["drawn_balls"].append(b)
                    time.sleep(5)
            else: game_state["timer"] = 30
        time.sleep(1)

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    ph = request.json.get('phone')
    if game_state["status"] != "playing": return jsonify({"success": False})
    p_data = game_state["players"].get(ph)
    drawn = {int(b[1:]) for b in game_state["drawn_balls"] if len(b) > 1}
    drawn.add(0) # FREE Space
    
    if p_data and any(is_winner(c, drawn) for c in p_data["cards"]):
        win_amt = game_state["pot"] * 0.8
        wallets.update_one({"phone": ph}, {"$inc": {"balance": win_amt}})
        wallets.update_one({"phone": ADMIN_PHONE}, {"$inc": {"balance": game_state["pot"]*0.2}}, upsert=True)
        
        game_state["winner"], game_state["status"] = p_data["username"], "result"
        
        # Reset after 5 seconds
        def delayed_reset():
            time.sleep(5)
            game_state.update({"status": "lobby", "winner": None, "pot": 0, "players": {}, "sold_tickets": {}, "drawn_balls": [], "current_ball": "--", "timer": 30})
        threading.Thread(target=delayed_reset).start()
        
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "ቢንጎ አልሞላም!"})

@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone})
    p_data = game_state["players"].get(phone, {"cards": []})
    return jsonify({**game_state, "balance": user['balance'] if user else 0, "my_cards": p_data["cards"]})

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    data = request.json
    ph, t_num, uname = data.get('phone'), str(data.get('ticket_num')), data.get('username')
    if game_state["status"] != "lobby" or t_num in game_state["sold_tickets"]: return jsonify({"success":False})
    p_info = game_state["players"].get(ph, {"cards": []})
    if len(p_info["cards"]) >= 3: return jsonify({"success": False, "msg": "ቢበዛ 3 ካርቴላ!"})
    user = wallets.find_one({"phone": ph})
    if not user or user.get('balance', 0) < 10: return jsonify({"success":False, "msg":"በቂ ሂሳብ የሎትም!"})
    wallets.update_one({"phone": ph}, {"$inc": {"balance": -10}})
    game_state["sold_tickets"][t_num] = ph
    game_state["pot"] += 10
    card = []
    for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]:
        card.append(random.sample(range(r[0], r[1]+1), 5))
    flat = [card[c][r] for r in range(5) for c in range(5)]; flat[12] = 0
    if ph not in game_state["players"]: game_state["players"][ph] = {"cards": [flat], "username": uname}
    else: game_state["players"][ph]["cards"].append(flat)
    return jsonify({"success": True})

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    d = request.json
    send_telegram_msg(f"💰 Deposit: {d['phone']} | {d['amount']} ETB\nID: {d['transaction_id']}")
    return jsonify({"success": True})

@app.route('/request_withdraw', methods=['POST'])
def request_withdraw():
    d = request.json
    user = wallets.find_one({"phone": d['phone']})
    amt = float(d['amount'])
    if user and user.get('balance', 0) >= amt:
        wallets.update_one({"phone": d['phone']}, {"$inc": {"balance": -amt}})
        send_telegram_msg(f"💸 Withdraw Request: {d['phone']} | {amt} ETB")
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "በቂ ሂሳብ የሎትም!"})

if __name__ == '__main__':
    threading.Thread(target=game_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
