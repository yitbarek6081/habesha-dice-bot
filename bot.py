import os, time, random, requests, threading
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient, ReturnDocument
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- CONFIG ---
ADMIN_ID = "7956330391" 
BOT_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
WEBHOOK_URL = "https://YOUR-APP-NAME.onrender.com/webhook" # እዚህ ጋር የRender ሊንክህን አስገባ

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']

game_state = {
    "status": "lobby", "timer": 30, "pot": 0, "players": {},
    "sold_tickets": {}, "current_ball": "--", "drawn_balls": [], "winner": None
}

def send_telegram(text):
    if BOT_TOKEN:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        try: requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"})
        except: print("Telegram Error")

# --- WEBHOOK SETUP ---
def set_webhook():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}"
    requests.get(url)

# --- TELEGRAM WEBHOOK ROUTE ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"]
        chat_id = str(data["message"]["chat"]["id"])
        
        # አድሚን ብቻ /add መጠቀም እንዲችል
        if chat_id == ADMIN_ID and msg.startswith("/add"):
            try:
                parts = msg.split()
                if len(parts) == 3:
                    target_phone = parts[1]
                    amount = float(parts[2])
                    
                    wallets.update_one(
                        {"phone": target_phone}, 
                        {"$inc": {"balance": amount}}, 
                        upsert=True
                    )
                    send_telegram(f"✅ ለ `{target_phone}` {amount} ETB ተጨምሯል!")
                else:
                    send_telegram("❌ ስህተት! ፎርማቱ: `/add phone amount` መሆን አለበት።")
            except Exception as e:
                send_telegram(f"❌ ስህተት: {str(e)}")
    return "OK", 200

# --- WINNING LOGIC ---
def is_winner(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0)
    for i in range(5):
        if all(card[i*5 + j] in drawn_set for j in range(5)): return True # Horizontal
        if all(card[j*5 + i] in drawn_set for i in range(5)): return True # Vertical
    if all(card[i*6] in drawn_set for i in range(5)): return True # Diagonal \
    if all(card[(i+1)*4] in drawn_set for i in range(5)): return True # Diagonal /
    return False

# --- GAME LOOP ---
def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        if game_state["status"] == "lobby":
            for i in range(30, -1, -1):
                if game_state["status"] != "lobby": break
                game_state["timer"] = i
                time.sleep(1)
            if len(game_state["players"]) >= 2:
                game_state["status"] = "playing"
                shuffled = balls.copy(); random.shuffle(shuffled)
                for b in shuffled:
                    if game_state["status"] != "playing": break
                    game_state["current_ball"] = b
                    game_state["drawn_balls"].append(b)
                    time.sleep(5) # 5 ሰከንድ እጣ
            else: game_state["timer"] = 30
        time.sleep(1)

@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    phone = request.args.get('phone')
    user = wallets.find_one({"phone": phone})
    p_data = game_state["players"].get(phone, {"cards": []})
    return jsonify({**game_state, "balance": user['balance'] if user else 0, "my_cards": p_data["cards"], "active_players": len(game_state["players"])})

# --- Buy, Withdraw, Claim Bingo Routes እዚህ ጋር ይቀጥላሉ ---
# ... (ከላይ የሰጠሁህ የቀድሞ ኮድ ላይ እንዳሉ ይቆያሉ)

if __name__ == '__main__':
    set_webhook() # ዌብሁኩን ለመቀስቀስ
    threading.Thread(target=game_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
