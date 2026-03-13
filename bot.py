import os, time, random
from flask import Flask, render_template, jsonify, request
from threading import Thread
from pymongo import MongoClient
from flask_cors import CORS

# Flask አፕሊኬሽን ከ CORS ጋር
app = Flask(__name__, template_folder='templates')
CORS(app)

# MongoDB ግንኙነት (ከ Environment Variable)
MONGO_URL = os.getenv("MONGO_URL")
client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']

# የጨዋታው ሁኔታ (Game State)
game_state = {
    "status": "lobby",
    "timer": 30,
    "pot": 0,
    "players": {},
    "current_ball": "--",
    "drawn_balls": []
}

def generate_bingo_card():
    """በ B-I-N-G-O ህግ 5x5 ካርቴላ ያመነጫል"""
    card = []
    ranges = [(1,15), (16,30), (31,45), (46,60), (61,75)]
    for r in ranges:
        col = random.sample(range(r[0], r[1]+1), 5)
        card.append(col)
    
    flat_card = []
    for row in range(5):
        for col in range(5):
            flat_card.append(card[col][row])
    flat_card[12] = 0 # መካከለኛው FREE Space
    return flat_card

@app.route('/')
def index():
    return render_template('index.html')

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
    data = request.json
    phone = data.get('phone')
    user = wallets.find_one({"phone": phone})
    
    if not user or user.get('balance', 0) < 10:
        return jsonify({"success": False, "msg": "በቂ ቀሪ ሂሳብ የሎትም!"})
    
    p_data = game_state["players"].get(phone, {"tickets": 0, "card": []})
    if p_data['tickets'] >= 2:
        return jsonify({"success": False, "msg": "በአንድ ዙር ከ 2 ካርቴላ በላይ አይፈቀድም!"})
    
    wallets.update_one({"phone": phone}, {"$inc": {"balance": -10}})
    game_state["pot"] += 10
    
    if phone not in game_state["players"]:
        game_state["players"][phone] = {"tickets": 1, "card": generate_bingo_card()}
    else:
        game_state["players"][phone]["tickets"] += 1
        
    return jsonify({"success": True})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    data = request.json
    phone = data.get('phone')
    
    if game_state["status"] != "playing":
        return jsonify({"success": False, "msg": "ጨዋታው ገና አልተጀመረም!"})
    
    # የገንዘብ ክፍያ (80% ለአሸናፊ፣ 20% ለአድሚን)
    pot = game_state["pot"]
    win_amt = pot * 0.8
    admin_amt = pot * 0.2
    
    wallets.update_one({"phone": phone}, {"$inc": {"balance": win_amt}})
    wallets.update_one({"phone": "ADMIN"}, {"$inc": {"balance": admin_amt}}, upsert=True)
    
    game_state["status"] = "lobby"
    game_state["pot"] = 0
    game_state["players"] = {}
    
    return jsonify({"success": True, "msg": f"ቢንጎ! {int(win_amt)} ብር ገቢ ሆኖልዎታል።"})

def game_logic():
    """የታይመሩን እና የኳስ አወጣጡን ዑደት ይቆጣጠራል"""
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        game_state["status"] = "lobby"
        for i in range(30, -1, -1):
            game_state["timer"] = i
            time.sleep(1)
        
        if len(game_state["players"]) >= 2:
            game_state["status"] = "playing"
            current_balls = balls.copy()
            random.shuffle(current_balls)
            
            while game_state["status"] == "playing" and current_balls:
                game_state["current_ball"] = current_balls.pop(0)
                time.sleep(4)
        else:
            time.sleep(2)

if __name__ == '__main__':
    Thread(target=game_logic, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
