import os, time, random, asyncio
from flask import Flask, render_template, jsonify, request
from threading import Thread
from pymongo import MongoClient

app = Flask(__name__)
client = MongoClient(os.getenv("MONGO_URL"))
db = client['bingo_db']
wallets = db['wallets']

# የጨዋታ ሁኔታ
game_state = {
    "status": "lobby",
    "timer": 30,
    "pot": 0,
    "players": {}, # {phone: {"card": [], "tickets": 0}}
    "current_ball": "--",
    "drawn_balls": [],
    "admin_id": "7956330391"
}

def generate_bingo_card():
    card = []
    ranges = [(1,15), (16,30), (31,45), (46,60), (61,75)]
    for r in ranges:
        col = random.sample(range(r[0], r[1]+1), 5)
        card.append(col)
    # ማትሪክሱን ወደ ዝርዝር መቀየር (Transpose)
    flat_card = []
    for row in range(5):
        for col in range(5):
            flat_card.append(card[col][row])
    flat_card[12] = 0 # FREE Space
    return flat_card

@app.route('/get_status')
def get_status():
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone})
    my_data = game_state["players"].get(phone, {"tickets": 0, "card": []})
    
    return jsonify({
        "status": game_state["status"],
        "timer": game_state["timer"],
        "pot": game_state["pot"],
        "player_count": len(game_state["players"]),
        "current_ball": game_state["current_ball"],
        "balance": user['balance'] if user else 0,
        "my_ticket_count": my_data['tickets'],
        "card": my_data['card']
    })

@app.route('/buy_ticket', methods=['POST'])
def buy_ticket():
    phone = request.json.get('phone')
    user = wallets.find_one({"phone": phone})
    
    if not user or user.get('balance', 0) < 10:
        return jsonify({"success": False, "msg": "በቂ ቀሪ ሂሳብ የሎትም!"})
    
    p_data = game_state["players"].get(phone, {"tickets": 0, "card": []})
    if p_data['tickets'] >= 2:
        return jsonify({"success": False, "msg": "ከ 2 ካርቴላ በላይ አይፈቀድም!"})
    
    wallets.update_one({"phone": phone}, {"$inc": {"balance": -10}})
    game_state["pot"] += 10
    
    if phone not in game_state["players"]:
        game_state["players"][phone] = {"tickets": 1, "card": generate_bingo_card()}
    else:
        game_state["players"][phone]["tickets"] += 1
        
    return jsonify({"success": True})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    phone = request.json.get('phone')
    if game_state["status"] != "playing":
        return jsonify({"success": False, "msg": "ጨዋታው አልተጀመረም!"})
    
    # ክፍያ ማከፋፈል (80/20)
    win_amt = game_state["pot"] * 0.8
    admin_amt = game_state["pot"] * 0.2
    
    wallets.update_one({"phone": phone}, {"$inc": {"balance": win_amt}})
    wallets.update_one({"phone": "ADMIN"}, {"$inc": {"balance": admin_amt}}, upsert=True)
    
    game_state["status"] = "lobby" # ጨዋታውን መመለስ
    game_state["pot"] = 0
    game_state["players"] = {}
    
    return jsonify({"success": True, "msg": f"ቢንጎ! {win_amt} ብር ወጥቶልዎታል።"})

def game_logic():
    balls = [f"{'BING O'[i//15]}{i+1}" for i in range(75)]
    while True:
        # Lobby Phase
        game_state["status"] = "lobby"
        for i in range(30, 0, -1):
            game_state["timer"] = i
            time.sleep(1)
        
        # Start Game if players > 1
        if len(game_state["players"]) >= 2:
            game_state["status"] = "playing"
            current_balls = balls.copy()
            random.shuffle(current_balls)
            
            while game_state["status"] == "playing" and current_balls:
                game_state["current_ball"] = current_balls.pop(0)
                time.sleep(4) # በየ 4 ሰከንዱ ኳስ ማውጣት
        else:
            time.sleep(2)

if __name__ == '__main__':
    Thread(target=game_logic).start()
    app.run(host='0.0.0.0', port=10000)
