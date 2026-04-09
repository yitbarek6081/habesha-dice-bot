import os, time, random, requests, threading
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient, ReturnDocument
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- CONFIG ---
ADMIN_ID = "7956330391" 
BOT_TOKEN = "8708969585:AAE-MQTUle1g83tGTmL0pNBm7oJOYw0u5dc" 
MONGO_URL = os.getenv("MONGO_URL")
RENDER_URL = "https://habesha-dice-bot.onrender.com" 

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']
game_db = db['game_state_v4']

def get_initial_state():
    return {
        "id": "global", "status": "lobby", "timer": 30, "pot": 0, "players": {},
        "sold_tickets": {}, "current_ball": "--", "drawn_balls": [], "winner": None
    }

def get_db_state():
    try:
        state = game_db.find_one({"id": "global"})
        if not state:
            game_db.insert_one(get_initial_state())
            return get_initial_state()
        return state
    except:
        return get_initial_state()

def update_db_state(update_data):
    game_db.update_one({"id": "global"}, {"$set": update_data}, upsert=True)

# --- GAME LOOP (ሰዓት እና ኳስ ማውጫ) ---
def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        try:
            state = get_db_state()
            if state["status"] == "lobby":
                if state["timer"] > 0:
                    game_db.update_one({"id": "global"}, {"$inc": {"timer": -1}})
                else:
                    if len(state["players"]) >= 2:
                        update_db_state({"status": "playing", "drawn_balls": []})
                        shuffled = balls.copy(); random.shuffle(shuffled)
                        drawn = []
                        for b in shuffled:
                            curr = get_db_state()
                            if curr["status"] != "playing": break
                            drawn.append(b)
                            update_db_state({"current_ball": b, "drawn_balls": drawn})
                            time.sleep(5)
                    else:
                        update_db_state({"timer": 30})
            time.sleep(1)
        except:
            time.sleep(5)

# --- ROUTES ---

@app.route('/')
def index():
    # የቀድሞውን ጽሁፍ በ render_template ቀይረነዋል
    return render_template('index.html')

@app.route('/get_status')
def get_status():
    state = get_db_state()
    phone = request.args.get('phone', '')
    user = wallets.find_one({"phone": phone})
    state.pop('_id', None)
    return jsonify({
        **state,
        "balance": user['balance'] if user else 0,
        "my_cards": state.get("players", {}).get(phone, {}).get("cards", []),
        "active_players": len(state.get("players", {}))
    })

# --- WEBHOOK & TELEGRAM ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"]
        chat_id = str(data["message"]["chat"]["id"])
        # የኤጀንት ሲስተም
        if msg.startswith("/start"):
            parts = msg.split()
            if len(parts) > 1 and not wallets.find_one({"phone": chat_id}):
                wallets.update_one({"phone": chat_id}, {"$set": {"phone": chat_id, "balance": 0, "referred_by": parts[1]}}, upsert=True)
    return "OK", 200

if __name__ == '__main__':
    # Webhook Set
    try: requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={RENDER_URL}/webhook")
    except: pass
    
    # Game Loop ጀምር
    threading.Thread(target=game_loop, daemon=True).start()
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
