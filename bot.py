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

game_state = {
    "status": "lobby", "timer": 30, "pot": 0, "players": {},
    "sold_tickets": {}, "current_ball": "--", "drawn_balls": [], "winner": None
}

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"})
    except: print("Telegram Error")

def set_webhook():
    webhook_url = f"{RENDER_URL}/webhook"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}"
    try: requests.get(url)
    except: print("Webhook set failed")

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"]
        chat_id = str(data["message"]["chat"]["id"])
        if chat_id == ADMIN_ID:
            parts = msg.split()
            if msg.startswith("/add") and len(parts) == 3:
                wallets.update_one({"phone": parts[1]}, {"$inc": {"balance": float(parts[2])}}, upsert=True)
                send_telegram(f"✅ ለ `{parts[1]}` {parts[2]} ETB ተጨምሯል")
            elif msg.startswith("/sub") and len(parts) == 3:
                wallets.update_one({"phone": parts[1]}, {"$inc": {"balance": -float(parts[2])}})
                send_telegram(f"⚠️ ከ `{parts[1]}` {parts[2]} ETB ተቀንሷል")
    return "OK", 200

def is_winner(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0) 
    for i in range(5):
        if all(card[i*5 + j] in drawn_set for j in range(5)): return True 
        if all(card[j*5 + i] in drawn_set for i in range(5)): return True 
    if all(card[i*6] in drawn_set for i in range(5)): return True 
    if all(card[4 + i*4] in drawn_set for i in range(5)): return True 
    return False

def game_loop():
    balls = []
    for i, l in enumerate("BINGO"):
        for n in range(i*15 + 1, (i+1)*15 + 1): balls.append(f"{l}{n}")
        
    while True:
        if game_state["status"] == "lobby":
            for i in range(30, -1, -1):
                if game_state["status"] != "lobby": break
                game_state["timer"] = i
                time.sleep(1)
            
            if len(game_state["players"]) >= 2:
                game_state["status"] = "playing"
                game_state["drawn_balls"] = []
                shuffled = balls.copy(); random.shuffle(shuffled)
                for b in shuffled:
                    if game_state["status"] != "playing": break
                    game
