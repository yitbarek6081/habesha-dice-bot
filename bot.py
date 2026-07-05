import os
import time
import random
import requests
import threading
import re
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient
from flask_cors import CORS
from flask_socketio import SocketIO, emit

app = Flask(__name__, template_folder='templates')
app.config['SECRET_KEY'] = 'habesha_bingo_secret_key'
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# --- CONFIG ---
ADMIN_ID = "7956330391" 
BOT_TOKEN = "8708969585:AAE-MQTUle1g83tGTmL0pNBm7oJOYw0u5dc" 
MONGO_URL = os.getenv("MONGO_URL")
RENDER_URL = "https://habesha-dice-bot.onrender.com" 

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']
wallets.create_index("phone", unique=True)

state_lock = threading.Lock()
game_state = {
    "status": "lobby", "timer": 30, "ball_timer": 3, "pot": 0, 
    "players": {}, "sold_tickets": {}, "current_ball": "--", 
    "drawn_balls": [], "winner": None, "winning_card": None  
}

def broadcast_state():
    socketio.emit('game_update', game_state)

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except: pass

# --- GAME LOOP ---
def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        with state_lock:
            if game_state["status"] == "lobby":
                if game_state["timer"] > 0:
                    game_state["timer"] -= 1
                    broadcast_state()
                elif len(game_state["players"]) >= 2:
                    game_state["status"] = "playing"
                    game_state["drawn_balls"] = []
                else:
                    game_state["timer"] = 30
            
            elif game_state["status"] == "playing":
                if game_state["ball_timer"] > 0:
                    game_state["ball_timer"] -= 1
                else:
                    if balls:
                        b = balls.pop(0)
                        game_state["current_ball"] = b
                        game_state["drawn_balls"].append(b)
                broadcast_state()
        time.sleep(1)

# --- ROUTES ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/buy_specific_ticket', methods=['POST'])
def buy():
    # ቲኬት ሲገዛ state አዘምን
    broadcast_state()
    return jsonify({"success": True})

if __name__ == '__main__':
    threading.Thread(target=game_loop, daemon=True).start()
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
