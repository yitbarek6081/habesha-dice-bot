import os
import random
import requests
import re
import threading
import time
from flask import Flask, render_template, request, jsonify
from pymongo import MongoClient
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- CONFIG ---
ADMIN_ID = os.getenv("ADMIN_ID", "7956330391") 
BOT_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']
wallets.create_index("phone", unique=True)

# የአጠቃላይ ጨዋታው ስቴት (በሚሞሪ ላይ የሚቀመጥ)
game_state = {
    "status": "lobby",       # lobby, playing, result
    "timer": 30,             # የሎቢ መቆያ ሰከንድ
    "ball_timer": 3,         # ጨዋታ ከመጀመሩ በፊት ዝግጅት
    "pot": 0,                # አጠቃላይ የተሰበሰበ ብር
    "players": {},           # {"phone": {"cards": {t_num: [25 ቁጥሮች]}, "username": "name"}}
    "sold_tickets": {},      # {"ticket_num": "phone"}
    "current_ball": "--", 
    "drawn_balls": [], 
    "winner": None,
    "winning_card": None,
    "winning_ticket_num": None  
}

# በጀርባ ሰርቨሩን እንዳያጨናንቅ Thread Lock መጠቀም
state_lock = threading.Lock()

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

def sanitize_input(text):
    if not text: return ""
    return re.sub(r'[^\w\s\-\+\.@]', '', str(text)).strip()

def check_winning_line(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0) # FREE space

    # 1. Rows
    for i in range(5):
        row_indices = [i*5 + j for j in range(5)]
        if all(card[idx] in drawn_set for idx in row_indices):
            return row_indices, f"አግድም (Row {i+1})"

    # 2. Columns
    for i in range(5):
        col_indices = [i + j*5 for j in range(5)]
        if all(card[idx] in drawn_set for idx in col_indices):
            return col_indices, f"ቁልቁል (Column {i+1})"

    # 3. Diagonals
    if all(card[idx] in drawn_set for idx in [0, 6, 12, 18, 24]):
        return [0, 6, 12, 18, 24], "ዲያጎናል (Diagonal 📉)"
    if all(card[idx] in drawn_set for idx in [4, 8, 12, 16, 20]):
        return [4, 8, 12, 16, 20], "ዲያጎናል (Diagonal 📈)"

    # 4. Corners
    if all(card[idx] in drawn_set for idx in [0, 4, 20, 24]):
        return [0, 4, 20, 24], "አራቱ ማዕዘናት (4 Corners)"

    return None, None

# --- TELEGRAM WEBHOOK ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json or {}
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"].strip()
        chat_id = str(data["message"]["chat"]["id"])

        if msg.startswith("/start"):
            parts = msg.split()
            agent_phone = sanitize_input(parts[1]) if len(parts) > 1 else None
            already_registered = wallets.find_one({"$or": [{"telegram_id": chat_id}, {"phone": chat_id}]})
            if already_registered:
                return "OK", 200

            wallets.delete_one({"telegram_id": chat_id, "reg_status": {"$exists": True}})
            reg_session = {"phone": f"TEMP_{chat_id}", "telegram_id": chat_id, "reg_status": "awaiting_phone", "balance": 0}
            if agent_phone: reg_session["referred_by"] = agent_phone
            wallets.insert_one(reg_session)
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "👋 እባክዎ የስልክ ቁጥርዎን ያስገቡ፦"})
            return "OK", 200

        session = wallets.find_one({"telegram_id": chat_id, "reg_status": {"$exists": True}})
        if session:
            current_status = session.get("reg_status")
            if current_status == "awaiting_phone":
                clean_phone = msg.replace("+", "").replace(" ", "")
                if wallets.find_one({"phone": clean_phone}):
                    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "❌ ይህ ቁጥር ተመዝግቧል።"})
                    return "OK", 200
                wallets.update_one({"telegram_id": chat_id}, {"$set": {"phone": clean_phone, "reg_status": "awaiting_name"}})
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "✅ ቀጥሎ የተጫዋች ስምዎን ያስገቡ፦"})
                return "OK", 200
            elif current_status == "awaiting_name":
                player_name = sanitize_input(msg)
                wallets.update_one({"telegram_id": chat_id}, {"$set": {"username": player_name}, "$unset": {"reg_status": ""}})
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "🎉 ምዝገባዎ ተጠናቋል! አፕሊኬሽኑን መክፈት ይችላሉ።"})
                send_telegram(f"🎉 አዲስ ተጫዋች ተመዘገበ: {player_name}")
                return "OK", 200

        # Admin Controls (/add, /sub)
        if chat_id == ADMIN_ID:
            if msg.startswith("/add") or msg.startswith("/sub"):
                parts = msg.split()
                if len(parts) == 3:
                    target_phone, amount = sanitize_input(parts[1]), float(parts[2])
                    final_amt = amount if msg.startswith("/add") else -amount
                    wallets.update_one({"$or": [{"phone": target_phone}, {"telegram_id": target_phone}]}, {"$inc": {"balance": final_amt}}, upsert=True)
                    send_telegram(f"⚖️ ዋሌት ተስተካክሏል ለ `{target_phone}`: {final_amt} ETB")
    return "OK", 200

# --- WEB APP API ENDPOINTS ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register_or_login', methods=['POST'])
def register_or_login():
    data = request.json or {}
    phone = sanitize_input(data.get('phone')).replace("+", "").replace(" ", "")
    username = sanitize_input(data.get('username'))
    
    if not phone or not username:
        return jsonify({"error": "ልክ ያልሆነ መረጃ"}), 400
        
    user = wallets.find_one({"phone": phone})
    if not user:
        user = {"phone": phone, "username": username, "balance": 0}
        wallets.insert_one(user)
    else:
        wallets.update_one({"phone": phone}, {"$set": {"username": username}})
        
    return jsonify({"success": True, "balance": user.get("balance", 0)})

@app.route('/get_status', methods=['GET'])
def get_status():
    phone = request.args.get('phone', '').strip()
    user_balance = 0
    my_cards = []
    
    if phone:
        user = wallets.find_one({"phone": phone})
        if user: user_balance = user.get("balance", 0)
        if phone in game_state["players"]:
            my_cards = list(game_state["players"][phone]["cards"].values())
            
    with state_lock:
        response_data = {
            "status": game_state["status"],
            "timer": game_state["timer"],
            "ball_timer": game_state["ball_timer"],
            "pot": game_state["pot"],
            "current_ball": game_state["current_ball"],
            "drawn_balls": game_state["drawn_balls"],
            "winner": game_state["winner"],
            "winning_card": game_state["winning_card"],
            "winning_ticket_num": game_state["winning_ticket_num"],
            "active_players": len(game_state["players"]),
            "balance": user_balance,
            "my_cards": my_cards
        }
    return jsonify(response_data)

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_specific_ticket():
    data = request.json or {}
    phone = sanitize_input(data.get('phone'))
    ticket_num = str(data.get('ticket_num'))
    username = sanitize_input(data.get('username'))
    
    with state_lock:
        if game_state["status"] != "lobby":
            return jsonify({"success": False, "message": "ጨዋታ ተጀምሯል!"})
        if ticket_num in game_state["sold_tickets"]:
            return jsonify({"success": False, "message": "ይህ ካርቴላ ተይዟል!"})
            
        user = wallets.find_one({"phone": phone})
        if not user or user.get("balance", 0) < 10:
            return jsonify({"success": False, "message": "በቂ ባላንስ የለም!"})
            
        wallets.update_one({"phone": phone}, {"$inc": {"balance": -10}})
        
        # 25 የቢንጎ ቁጥሮች ማመንጨት (B, I, N, G, O)
        columns = [random.sample(range(r[0], r[1]+1), 5) for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]]
        flat_card = []
        for row in range(5):
            for col in range(5): flat_card.append(columns[col][row])
        flat_card[12] = 0 # FREE Space
        
        game_state["sold_tickets"][ticket_num] = phone
        game_state["pot"] += 10
        
        if phone not in game_state["players"]:
            game_state["players"][phone] = {"cards": {ticket_num: flat_card}, "username": username}
        else:
            game_state["players"][phone]["cards"][ticket_num] = flat_card
            
    return jsonify({"success": True, "message": "ካርቴላ በተሳካ ሁኔታ ተገዝቷል!"})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    data = request.json or {}
    phone = sanitize_input(data.get('phone'))
    
    with state_lock:
        if game_state["status"] != "playing":
            return jsonify({"success": False, "message": "ጨዋታው ሂደት ላይ አይደለም!"})
            
        p_data = game_state["players"].get(phone)
        if not p_data:
            return jsonify({"success": False, "message": "ካርቴላ አልገዙም!"})
            
        for t_num, card in p_data["cards"].items():
            win_indices, line_type = check_winning_line(card, game_state["drawn_balls"])
            if win_indices:
                game_state["status"] = "result"
                game_state["winner"] = p_data["username"]
                game_state["winning_card"] = card
                game_state["winning_ticket_num"] = str(t_num)
                
                win_amt = game_state["pot"] * 0.8
                wallets.update_one({"phone": phone}, {"$inc": {"balance": win_amt}})
                send_telegram(f"🏆 BINGO! {p_data['username']} ({phone}) በካርቴላ ቁጥር {t_num} በ {line_type} አሸንፏል። ሽልማት: {win_amt} ETB")
                
                threading.Thread(target=reset_game_delayed).start()
                return jsonify({"success": True, "message": "እንኳን ደስ አለዎት! አሸንፈዋል!"})
                
    return jsonify({"success": False, "message": "ቢንጎዎ የተሳሳተ ነው ወይም አልሞላም!"})

def reset_game_delayed():
    time.sleep(10) # ውጤቱ ለ 10 ሰከንድ ስክሪን ላይ እንዲቆይ
    with state_lock:
        game_state.update({
            "status": "lobby", "winner": None, "winning_card": None, "winning_ticket_num": None, "pot": 0,
            "players": {}, "sold_tickets": {}, "drawn_balls": [], "current_ball": "--", "timer": 30, "ball_timer": 3
        })

# --- BACKGROUND AUTOMATIC GAME LOOP ---
def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        if game_state["status"] == "lobby":
            while game_state["timer"] > 0:
                time.sleep(1)
                with state_lock: game_state["timer"] -= 1
                
            with state_lock:
                # ቢያንስ 2 እና ከዚያ በላይ ሰው ሲኖር ጨዋታው ይጀምራል
                can_start = len(game_state["players"]) >= 2
                
            if can_start:
                with state_lock: game_state["status"] = "playing"
                shuffled_balls = balls.copy()
                random.shuffle(shuffled_balls)
                
                while game_state["ball_timer"] > 0:
                    time.sleep(1)
                    with state_lock: game_state["ball_timer"] -= 1
                    
                for ball in shuffled_balls:
                    with state_lock:
                        if game_state["status"] != "playing": break
                        game_state["current_ball"] = ball
                        game_state["drawn_balls"].append(ball)
                    time.sleep(4) # እያንዳንዱ ኳስ በ 4 ሰከንድ ልዩነት ይወጣል
            else:
                with state_lock: game_state["timer"] = 30 # ሰው ከሌለ መልሶ 30 ሰከንድ ይቆጥራል
        time.sleep(1)

# የጀርባ ሉፑን ማስጀመር
bg_thread = threading.Thread(target=game_loop, daemon=True)
bg_thread.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
