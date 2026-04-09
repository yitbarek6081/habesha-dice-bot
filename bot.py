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

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']
game_db = db['game_state_new'] # አዲስ ስም ተጠቅሜያለሁ ስህተት እንዳይመጣ

initial_state = {
    "id": "global", "status": "lobby", "timer": 30, "pot": 0, "players": {},
    "sold_tickets": {}, "current_ball": "--", "drawn_balls": [], "winner": None
}

def get_db_state():
    try:
        state = game_db.find_one({"id": "global"})
        if not state:
            game_db.insert_one(initial_state.copy())
            return initial_state
        return state
    except:
        return initial_state

def update_db_state(update_data):
    game_db.update_one({"id": "global"}, {"$set": update_data}, upsert=True)

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        try:
            state = get_db_state()
            if state.get("status") == "lobby":
                t = state.get("timer", 30)
                if t > 0:
                    game_db.update_one({"id": "global"}, {"$inc": {"timer": -1}})
                else:
                    players = state.get("players", {})
                    if len(players) >= 2:
                        update_db_state({"status": "playing", "drawn_balls": []})
                        shuffled = balls.copy()
                        random.shuffle(shuffled)
                        drawn = []
                        for b in shuffled:
                            curr = get_db_state()
                            if curr.get("status") != "playing": break
                            drawn.append(b)
                            update_db_state({"current_ball": b, "drawn_balls": drawn})
                            time.sleep(5)
                    else:
                        update_db_state({"timer": 30})
            time.sleep(1)
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(5)

@app.route('/get_status')
def get_status():
    # ሉፑ ካልጀመረ እዚህ ጋር ይቀሰቀሳል
    loop_active = any(t.name == "BingoLoop" for t in threading.enumerate())
    if not loop_active:
        threading.Thread(target=game_loop, name="BingoLoop", daemon=True).start()
        
    state = get_db_state()
    phone = request.args.get('phone', '')
    user = wallets.find_one({"phone": phone}) if phone else None
    
    # ሰርቨሩ እንዳይቆም የምንመልሰው ዳታ (IDን በማጥፋት JSON Error እንዳይመጣ)
    state.pop('_id', None)
    
    return jsonify({
        **state,
        "balance": user['balance'] if user else 0,
        "my_cards": state.get("players", {}).get(phone, {}).get("cards", []) if phone else [],
        "active_players": len(state.get("players", {}))
    })

@app.route('/')
def index():
    return "Bingo Server is Running!" # 404 እንዳይመጣ

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
