# 🔴 እጅግ በጣም ወሳኝ፡ የ Gevent ማስተካከያ ከማንኛውም Import በፊት 1ኛ መስመር ላይ መሆን አለበት!
from gevent import monkey
monkey.patch_all()

import os
import time
import random
import gevent
from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient

app = Flask(__name__)
CORS(app)

# --- CONFIG (ከRender ሰርቨር ቁልፎች ጋር የተጣጣመ) ---
ADMIN_ID = os.getenv("ADMIN_ID", "7956330391") 
BOT_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017/bingo_db")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://habesha-dice-bot.onrender.com")

# --- DATABASE CONNECTION ---
# Memory ለመቆጠብ PoolSize ተገድቧል
client = MongoClient(MONGO_URL, maxPoolSize=10, minPoolSize=1)
db = client.get_database()

# --- GAME STATE (IN-MEMORY FOR 0.1 CPU SPEED) ---
game_state = {
    "status": "lobby",       
    "timer": 45,             
    "ball_timer": 3,         
    "current_ball": "--",
    "drawn_balls": [],
    "pot": 0,
    "tickets": {},           
    "player_names": {},      
    "player_balances": {},   
    "winner": None,
    "winning_ticket_num": None,
    "winning_card": None,
    "winning_line": []       
}

def generate_bingo_card():
    col_ranges = [(1,15), (16,30), (31,45), (46,60), (61,75)]
    card = [0] * 25
    for col in range(5):
        start, end = col_ranges[col]
        nums = random.sample(range(start, end + 1), 5)
        for row in range(5):
            card[row * 5 + col] = nums[row]
    card[12] = "FREE"
    return card

def check_bingo_win(card, drawn_set):
    # 1. አግድም መስመሮች
    for r in range(5):
        idxs = [r*5 + c for c in range(5)]
        if all(idx == 12 or card[idx] in drawn_set for idx in idxs):
            return True, idxs
    # 2. ቁልቁል መስመሮች
    for c in range(5):
        idxs = [r*5 + c for r in range(5)]
        if all(idx == 12 or card[idx] in drawn_set for idx in idxs):
            return True, idxs
    # 3. ዲያጎናል (ግራ ወደ ቀኝ)
    d1 = [0, 6, 12, 18, 24]
    if all(idx == 12 or card[idx] in drawn_set for idx in d1):
        return True, d1
    # 4. ዲያጎናል (ቀኝ ወደ ግራ)
    d2 = [4, 8, 12, 16, 20]
    if all(idx == 12 or card[idx] in drawn_set for idx in d2):
        return True, d2
    # 5. 4ቱ ማዕዘናት
    corners = [0, 4, 20, 24]
    if all(card[idx] in drawn_set for idx in corners):
        return True, corners
    return False, []

# --- API ENDPOINTS ---

@app.route('/register_or_login', methods=['POST'])
def register_or_login():
    data = request.json or {}
    phone = data.get('phone', '').strip()
    username = data.get('username', '').strip()
    if not phone or not username:
        return jsonify({"success": False, "msg": "መረጃው አልተሟላም!"}), 400
    
    game_state["player_names"][phone] = username
    
    user_doc = db.users.find_one({"phone": phone})
    if not user_doc:
        db.users.insert_one({"phone": phone, "username": username, "balance": 100.0})
        balance = 100.0
    else:
        balance = user_doc.get("balance", 0.0)
        db.users.update_one({"phone": phone}, {"$set": {"username": username}})
        
    game_state["player_balances"][phone] = balance
    return jsonify({"success": True, "balance": balance})

@app.route('/get_status', methods=['GET'])
def get_status():
    phone = request.args.get('phone', '').strip()
    balance = game_state["player_balances"].get(phone, 0.0)
    
    my_cards = []
    for t_num, owner_phone in game_state["tickets"].items():
        if owner_phone == phone:
            random.seed(int(t_num) + 999) 
            my_cards.append(generate_bingo_card())
            
    active_players = len(set(game_state["tickets"].values()))
    
    return jsonify({
        "status": game_state["status"],
        "timer": game_state["timer"],
        "ball_timer": game_state["ball_timer"],
        "current_ball": game_state["current_ball"],
        "drawn_balls": game_state["drawn_balls"],
        "pot": game_state["pot"],
        "balance": balance,
        "active_players": max(active_players, len(game_state["player_names"])),
        "sold_tickets": game_state["tickets"],
        "my_cards": my_cards,
        "winner": game_state["winner"],
        "winning_ticket_num": game_state["winning_ticket_num"],
        "winning_card": game_state["winning_card"],
        "winning_line": game_state["winning_line"]
    })

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_specific_ticket():
    if game_state["status"] != "lobby":
        return jsonify({"success": False, "msg": "ጨዋታው ጀምሯል! መግዛት አይቻልም።"}), 400
        
    data = request.json or {}
    phone = data.get('phone', '').strip()
    t_num = str(data.get('ticket_num', ''))
    
    if not phone or not t_num:
        return jsonify({"success": False, "msg": "መረጃው የተሳሳተ ነው"}), 400
        
    if t_num in game_state["tickets"]:
        return jsonify({"success": False, "msg": "ይህ ካርቴላ ተሽጧል!"}), 400
        
    mine_count = sum(1 for p in game_state["tickets"].values() if p == phone)
    if mine_count >= 2:
        return jsonify({"success": False, "msg": "ከ 2 ካርቴላ በላይ መግዛት አይቻልም!"}), 400
        
    user_doc = db.users.find_one({"phone": phone})
    bal = user_doc.get("balance", 0.0) if user_doc else 0.0
    if bal < 10:
        return jsonify({"success": False, "msg": "በቂ ባላንስ የለዎትም!"}), 400
        
    db.users.update_one({"phone": phone}, {"$inc": {"balance": -10.0}})
    game_state["player_balances"][phone] = bal - 10.0
    game_state["tickets"][t_num] = phone
    game_state["pot"] += 10
    
    return jsonify({"success": True})

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    if game_state["status"] != "lobby":
        return jsonify({"success": False, "msg": "ጨዋታው ስለጀመረ መሰረዝ አይቻልም"}), 400
    data = request.json or {}
    phone = data.get('phone', '')
    t_num = str(data.get('ticket_num', ''))
    
    if game_state["tickets"].get(t_num) == phone:
        del game_state["tickets"][t_num]
        db.users.update_one({"phone": phone}, {"$inc": {"balance": 10.0}})
        game_state["player_balances"][phone] = game_state["player_balances"].get(phone, 0.0) + 10.0
        game_state["pot"] -= 10
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "ካርቴላው የእርስዎ አይደለም!"}), 400

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    if game_state["status"] != "playing":
        return jsonify({"success": False, "msg": "ጨዋታው አሁን ላይ አክቲቭ አይደለም!"}), 400
        
    data = request.json or {}
    phone = data.get('phone', '')
    
    drawn_set = set()
    for b in game_state["drawn_balls"]:
        num_part = ''.join(filter(str.isdigit, str(b)))
        if num_part:
            drawn_set.add(int(num_part))
            
    for t_num, owner_phone in game_state["tickets"].items():
        if owner_phone == phone:
            random.seed(int(t_num) + 999)
            card = generate_bingo_card()
            has_won, win_line = check_bingo_win(card, drawn_set)
            if has_won:
                w_name = game_state["player_names"].get(phone, "ተጫዋች")
                game_state["winner"] = w_name
                game_state["winning_ticket_num"] = t_num
                game_state["winning_card"] = card
                game_state["winning_line"] = win_line
                game_state["status"] = "result"
                game_state["timer"] = 10 
                
                net_prize = float(int(game_state["pot"] * 0.8))
                db.users.update_one({"phone": phone}, {"$inc": {"balance": net_prize}})
                game_state["player_balances"][phone] += net_prize
                return jsonify({"success": True})
                
    return jsonify({"success": False, "msg": "ካርቴላዎ ገና አልሞላም! እባክዎ በትክክል ያረጋግጡ።"}), 400

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    data = request.json or {}
    db.deposits.insert_one({
        "phone": data.get('phone'),
        "amount": data.get('amount'),
        "transaction_id": data.get('transaction_id'),
        "status": "pending",
        "time": time.time()
    })
    return jsonify({"success": True})

@app.route('/request_withdrawal', methods=['POST'])
def request_withdrawal():
    data = request.json or {}
    phone = data.get('phone')
    amt = float(data.get('amount', 0))
    user_doc = db.users.find_one({"phone": phone})
    if user_doc and user_doc.get("balance", 0) >= amt:
        db.users.update_one({"phone": phone}, {"$inc": {"balance": -amt}})
        game_state["player_balances"][phone] -= amt
        db.withdrawals.insert_one({"phone": phone, "amount": amt, "status": "pending", "time": time.time()})
        return jsonify({"success": True, "msg": "የማውጣት ጥያቄዎ ተመዝግቧል!"})
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለዎትም!"})

def game_background_loop():
    all_balls = []
    letters = ['B', 'I', 'N', 'G', 'O']
    for i, l in enumerate(letters):
        for n in range(i*15+1, i*15+16):
            all_balls.append(f"{l}{n}")

    while True:
        gevent.sleep(1) 
        if game_state["status"] == "lobby":
            if len(game_state["tickets"]) > 0: 
                game_state["timer"] -= 1
                if game_state["timer"] <= 0:
                    game_state["status"] = "playing"
                    game_state["ball_timer"] = 3
                    game_state["drawn_balls"] = []
                    game_state["current_ball"] = "--"
                    random.shuffle(all_balls)
                    ball_index = 0
            else:
                game_state["timer"] = 45 
                
        elif game_state["status"] == "playing":
            if game_state["ball_timer"] > 0:
                game_state["ball_timer"] -= 1
            else:
                if ball_index < len(all_balls):
                    new_ball = all_balls[ball_index]
                    game_state["current_ball"] = new_ball
                    game_state["drawn_balls"].append(new_ball)
                    ball_index += 1
                    game_state["ball_timer"] = 5 
                else:
                    game_state["status"] = "lobby"
                    game_state["tickets"] = {}
                    game_state["pot"] = 0
                    game_state["timer"] = 45
                    
        elif game_state["status"] == "result":
            game_state["timer"] -= 1
            if game_state["timer"] <= 0:
                game_state["status"] = "lobby"
                game_state["tickets"] = {}
                game_state["pot"] = 0
                game_state["timer"] = 45
                game_state["winner"] = None
                game_state["winning_ticket_num"] = None
                game_state["winning_card"] = None
                game_state["winning_line"] = []

# የጀርባ ሉፑን በ gevent ስፓውን ማድረግ
gevent.spawn(game_background_loop)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
