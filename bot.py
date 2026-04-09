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

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except:
        print("Telegram Error")

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

@app.route('/')
def index():
    return "Bingo Server is Live!"

if __name__ == '__main__':
    # Webhook Set
    try:
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={RENDER_URL}/webhook")
    except:
        pass
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
