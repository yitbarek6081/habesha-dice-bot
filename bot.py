import os
import random
import time
import threading
from flask import Flask, jsonify, request, render_template_string
from pymongo import MongoClient

app = Flask(__name__)

# --- MONGO DB CONNECTION ---
# በ Render Environment Variable ላይ MONGODB_URI ካልተገኘ local ይጠቀማል
MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URI)
db = client["besh_bingo_db"]

# --- GAME CONFIGURATION ---
TICKET_PRICE = 10
LOBBY_DURATION = 30  # የሎቢ ሰዓት በሰከንድ

# --- GLOBAL GAME STATE ---
game_state = {
    "status": "lobby",       # lobby, playing, result
    "timer": LOBBY_DURATION,
    "pot": 0,
    "drawn_balls": [],
    "current_ball": "--",
    "winner": "",
    "winning_card": []
}

# --- BINGO CARD GENERATOR (1-75) ---
def generate_bingo_card():
    card = []
    # B: 1-15, I: 16-30, N: 31-45, G: 46-60, O: 61-75
    ranges = [range(1, 16), range(16, 31), range(31, 46), range(46, 61), range(61, 75)]
    columns = [random.sample(r, 5) for r in ranges]
    
    # ወደ Row ፎርማት መቀየር (5x5)
    for row in range(5):
        for col in range(5):
            if row == 2 and col == 2:
                card.append("FREE")
            else:
                card.append(columns[col][row])
    return card

# --- BACKGROUND GAME LOOP ---
def game_loop():
    global game_state
    all_balls = [f"{'B' if b<=15 else 'I' if b<=30 else 'N' if b<=45 else 'G' if b<=60 else 'O'}{b}" for b in range(1, 76)]
    
    while True:
        if game_state["status"] == "lobby":
            if game_state["timer"] > 0:
                time.sleep(1)
                game_state["timer"] -= 1
            else:
                # በቂ ተጫዋች ካለ ወደ ጨዋታ ይቀይራል
                tickets_count = db.tickets.count_documents({})
                if tickets_count > 0:
                    game_state["status"] = "playing"
                    game_state["drawn_balls"] = []
                    game_state["current_ball"] = "--"
                    random.shuffle(all_balls)
                    
                    # ለተገዙት ቲኬቶች በሙሉ የቢንጎ ካርቴላ ማደል
                    for t in db.tickets.find({}):
                        card_data = generate_bingo_card()
                        db.tickets.update_one({"_id": t["_id"]}, {"$set": {"card": card_data}})
                else:
                    game_state["timer"] = LOBBY_DURATION  # ተጫዋች ከሌለ ሰዓቱን እንደገና ማስጀመር

        elif game_state["status"] == "playing":
            if len(game_state["drawn_balls"]) < 75 and game_state["status"] == "playing":
                time.sleep(3)  # በየ 3 ሰከንዱ እጣ ማውጣት
                # ሌላ ተጫዋች Claim አድርጎ ሁኔታው ከተቀየረ ለማቆም
                if game_state["status"] != "playing":
                    continue
                    
                next_ball = all_balls[len(game_state["drawn_balls"])]
                game_state["drawn_balls"].append(next_ball)
                game_state["current_ball"] = next_ball
            else:
                # ሁሉም ኳስ ካለቀና አሸናፊ ከሌለ ወደ ሎቢ ይመለሳል
                time.sleep(5)
                reset_game()

        elif game_state["status"] == "result":
            time.sleep(10)  # ውጤቱን ለ10 ሰከንድ አሳይቶ ማጽዳት
            reset_game()

def reset_game():
    global game_state
    db.tickets.delete_many({})  # ያለፉትን ቲኬቶች ማጽዳት
    game_state = {
        "status": "lobby",
        "timer": LOBBY_DURATION,
        "pot": 0,
        "drawn_balls": [],
        "current_ball": "--",
        "winner": "",
        "winning_card": []
    }

# የጨዋታውን ሉፕ በ Background Thread ማስጀመር
threading.Thread(target=game_loop, daemon=True).start()


# --- API ENDPOINTS ---

@app.route('/')
def home():
    return "Besh Bingo Game Server is Running!"

@app.route('/get_status', methods=['GET'])
def get_status():
    phone = request.args.get('phone', '')
    
    # የተጫዋቹን ሂሳብ ማግኘት
    user = db.users.find_one({"phone": phone})
    balance = user["balance"] if user else 0
    
    # የዚሁ ተጫዋች የተገዙ ካርቴላዎች ካሉ ማዘጋጀት
    my_cards = []
    if game_state["status"] == "playing":
        for t in db.tickets.find({"phone": phone}):
            if "card" in t:
                my_cards.append(t["card"])
                
    # በአጠቃላይ በሎቢው ያሉ ንቁ ተጫዋቾች ብዛት
    active_players = len(db.tickets.distinct("phone"))
    
    # የጃክፖት (የበሽ-ደራሽ) መጠን ስሌት
    tickets_count = db.tickets.count_documents({})
    game_state["pot"] = tickets_count * TICKET_PRICE

    return jsonify({
        "status": game_state["status"],
        "timer": game_state["timer"],
        "pot": game_state["pot"],
        "current_ball": game_state["current_ball"],
        "drawn_balls": game_state["drawn_balls"],
        "winner": game_state["winner"],
        "winning_card": game_state["winning_card"],
        "balance": balance,
        "active_players": active_players if active_players > 0 else 0,
        "my_cards": my_cards,
        "sold_tickets": {str(t["ticket_num"]): t["phone"] for t in db.tickets.find({})}
    })

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_specific_ticket():
    data = request.json
    clean_phone = str(data.get('phone', '')).strip()
    ticket_num = int(data.get('ticket_num', 0))
    input_username = str(data.get('username', 'Player')).strip()
    
    if game_state["status"] != "lobby":
        return jsonify({"success": False, "msg": "ጨዋታ ተጀምሯል! ቀጣዩን ይጠብቁ::"})
        
    if ticket_num < 1 or ticket_num > 500:
        return jsonify({"success": False, "msg": "የሳጥን ቁጥር ስህተት ነው!"})

    # ተጠቃሚው ቀድሞ መኖሩን ማረጋገጥ ወይስ አዲስ መፍጠር
    user = db.users.find_one({"phone": clean_phone})
    if not user:
        db.users.insert_one({"phone": clean_phone, "username": input_username, "balance": 0})
        user = {"balance": 0}
    else:
        # ስም ከተቀየረ ማሻሻል (እዚህ ጋ ነው ስህተቱ የተስተካከለው 🛠)
        db.users.update_one({"phone": clean_phone}, {"$set": {"username": input_username}})

    if user["balance"] < TICKET_PRICE:
        return jsonify({"success": False, "msg": "በቂ ሂሳብ የሎትም! እባክዎ መጀመሪያ 📥 DEPOSIT ያድርጉ::"})

    # ሳጥኑ ቀድሞ መገዛቱን ቼክ ማድረግ
    existing = db.tickets.find_one({"ticket_num": ticket_num})
    if existing:
        return jsonify({"success": False, "msg": "ይህ ሳጥን ቀድሞ ተይዟል!"})

    # የአንድ ሰው የካርቴላ ብዛት ከ 2 እንዳይበልጥ መገደብ
    my_count = db.tickets.count_documents({"phone": clean_phone})
    if my_count >= 2:
        return jsonify({"success": False, "msg": "በአንድ ጨዋታ ከ 2 ካርቴላ በላይ መግዛት አይቻልም!"})

    # ብር ቀንሶ ቲኬቱን መመዝገብ
    db.users.update_one({"phone": clean_phone}, {"$inc": {"balance": -TICKET_PRICE}})
    db.tickets.insert_one({
        "phone": clean_phone,
        "username": input_username,
        "ticket_num": ticket_num
    })
    return jsonify({"success": True})

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    data = request.json
    clean_phone = str(data.get('phone', '')).strip()
    ticket_num = int(data.get('ticket_num', 0))
    
    if game_state["status"] != "lobby":
        return jsonify({"success": False, "msg": "ጨዋታ ሊጀምር ስለሆነ መሰረዝ አይቻልም!"})
        
    ticket = db.tickets.find_one({"phone": clean_phone, "ticket_num": ticket_num})
    if ticket:
        db.tickets.delete_one({"_id": ticket["_id"]})
        db.users.update_one({"phone": clean_phone}, {"$inc": {"balance": TICKET_PRICE}})
        return jsonify({"success": True})
        
    return jsonify({"success": False, "msg": "ቲኬቱ አልተገኘም!"})

def verify_bingo(card, drawn_balls):
    # ከኳስ ላይ ፊደላቱን አውጥቶ ወደ ቁጥር መቀየር (ለምሳሌ "B12" -> 12)
    drawn_numbers = set()
    for b in drawn_balls:
        try:
            num = int(''.join(filter(str.isdigit, str(b))))
            drawn_numbers.add(num)
        except:
            pass

    def is_hit(idx):
        if idx == 12 or card[idx] == "FREE":
            return True
        return card[idx] in drawn_numbers

    # የማሸነፊያ መስመሮች (አግድም፣ ቀጥታ፣ ሰያፍ፣ 4 ማዕዘን)
    winning_combinations = [
        [0, 1, 2, 3, 4], [5, 6, 7, 8, 9], [10, 11, 12, 13, 14], [15, 16, 17, 18, 19], [20, 21, 22, 23, 24], # Rows
        [0, 5, 10, 15, 20], [1, 6, 11, 16, 21], [2, 7, 12, 17, 22], [3, 8, 13, 18, 23], [4, 9, 14, 19, 24], # Columns
        [0, 6, 12, 18, 24], [4, 8, 12, 16, 20], # Diagonals
        [0, 4, 20, 24] # 4 Corners
    ]
    
    for combo in winning_combinations:
        if all(is_hit(i) for i in combo):
            return True
    return False

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    global game_state
    data = request.json
    clean_phone = str(data.get('phone', '')).strip()
    
    if game_state["status"] != "playing":
        return jsonify({"success": False, "msg": "በአሁን ሰዓት ንቁ ጨዋታ የለም!"})
        
    # የተጫዋቹን ካርቴላዎች መፈተሽ
    my_tickets = list(db.tickets.find({"phone": clean_phone}))
    
    for t in my_tickets:
        if "card" in t and verify_bingo(t["card"], game_state["drawn_balls"]):
            # አሸናፊ ከተገኘ የአሸናፊነት ክፍያ (80% ለተጫዋች)
            win_amount = Math.floor(game_state["pot"] * 0.8)
            db.users.update_one({"phone": clean_phone}, {"$inc": {"balance": win_amount}})
            
            game_state["status"] = "result"
            game_state["winner"] = t.get("username", "Unknown")
            game_state["winning_card"] = t["card"]
            return jsonify({"success": True})
            
    return jsonify({"success": False, "msg": "ትክክለኛ ቢንጎ አልተሰራም! እባክዎ ቁጥሮቹን በደንብ ይፈትሹ::"})

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    data = request.json
    phone = str(data.get('phone', '')).strip()
    amount = int(data.get('amount', 0))
    tid = str(data.get('transaction_id', '')).strip()
    
    if amount >= 10 and len(tid) >= 4:
        # ለጊዜው አውቶማቲክ እንዲሆን ወዲያውኑ ብሩን አካውንቱ ላይ ይጨምረዋል
        db.users.update_one({"phone": phone}, {"$inc": {"balance": amount}}, upsert=True)
        db.deposits.insert_one({"phone": phone, "amount": amount, "transaction_id": tid, "status": "approved"})
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "የመረጃ ስህተት!"})

@app.route('/withdraw', methods=['POST'])
def withdraw():
    data = request.json
    phone = str(data.get('phone', '')).strip()
    amount = int(data.get('amount', 0))
    
    if amount < 20:
        return jsonify({"success": False, "msg": "ቢያንስ 20 ብር ማውጣት ይችላሉ!"})
        
    user = db.users.find_one({"phone": phone})
    if user and user["balance"] >= amount:
        db.users.update_one({"phone": phone}, {"$inc": {"balance": -amount}})
        db.withdrawals.insert_one({"phone": phone, "amount": amount, "status": "pending"})
        return jsonify({"success": True})
        
    return jsonify({"success": False, "msg": "በቂ ሂሳብ የሎትም!"})

if __name__ == '__main__':
    # ሬንደር ላይ የሚሰጠውን PORT ተጠቅሞ ይነሳል
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
