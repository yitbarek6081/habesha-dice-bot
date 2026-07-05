import os
import time
import random
import re
import threading
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from pymongo import MongoClient
from flask_cors import CORS
import requests

app = Flask(__name__, template_folder='templates')
CORS(app)
# SocketIOን ማካተት
socketio = SocketIO(app, cors_allowed_origins="*")

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

def sanitize_input(text):
    return re.sub(r'[^\w\s\-\+\.@]', '', str(text)).strip()

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except: pass

# የጨዋታው loop እና መረጃ መላኪያ
def game_loop():
    while True:
        with state_lock:
            # የጨዋታው ሎጂክ እንዳለ ሆኖ መረጃውን ለሁሉም ተጫዋች እንገፋለን
            socketio.emit('game_update', game_state)
        time.sleep(1)

@app.route('/')
def index(): return render_template('index.html')

# (የነበሩት routes እንዳሉ ይቀጥላሉ)
@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    # ... (የነበረው ኮድህ እዚህ ይግባ)
    return jsonify({"success": True})

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    # ... (የነበረው ኮድህ እዚህ ይግባ)
    return jsonify({"success": True})

if __name__ == '__main__':
    threading.Thread(target=game_loop, daemon=True).start()
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
