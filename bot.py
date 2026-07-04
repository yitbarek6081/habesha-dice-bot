import os
import time
import random
import secrets
import requests
import threading
import re
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

# --- CONFIG ---
ADMIN_ID = os.getenv("ADMIN_ID", "7956330391") 
BOT_TOKEN = os.getenv("BOT_TOKEN", "8708969585:AAE-MQTUle1g83tGTmL0pNBm7oJOYw0u5dc") 
MONGO_URL = os.getenv("MONGO_URL")
RENDER_URL = "https://habesha-dice-bot.onrender.com" 

client = MongoClient(MONGO_URL)
db = client['bingo_db']
wallets = db['wallets']

wallets.create_index("phone", unique=True)

state_lock = threading.Lock()

game_state = {
    "status": "lobby", 
    "timer": 30, 
    "pot": 0, 
    "players": {},       # phone -> {"cards": {ticket_num: flat_card}, "username": uname}
    "sold_tickets": {},  # ticket_num -> phone
    "current_ball": "--", 
    "drawn_balls": [], 
    "winner": None,
    "winning_card": None  
}

def sanitize_input(text):
    if not text:
        return ""
    return re.sub(r'[^\w\s\-\+\.@]', '', str(text)).strip()

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

def set_webhook():
    webhook_url = f"{RENDER_URL}/webhook/{BOT_TOKEN}"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}"
    try:
        requests.get(url, timeout=5)
    except Exception as e:
        print(f"Webhook set failed: {e}")

@app.route(f'/webhook/{BOT_TOKEN}', methods=['POST'])
def webhook():
    data = request.json
    if not data:
        return "OK", 200
        
    if "message" in data and "text" in data["message"]:
        msg = data["message"]["text"].strip()
        chat_id = str(data["message"]["chat"]["id"])

        if msg.startswith("/start"):
            parts = msg.split()
            if len(parts) > 1:
                agent_phone = sanitize_input(parts[1])
                if agent_phone != chat_id:  # ራሱን እንዳይጋብዝ መከላከል
                    wallets.update_one(
                        {"phone": chat_id},
                        {"$setOnInsert": {
                            "phone": chat_id, 
                            "balance": 0, 
                            "referred_by": agent_phone,
                            "username": f"Player_{chat_id[-4:]}"
                        }},
                        upsert=True
                    )

        if chat_id == ADMIN_ID:
            if msg == "/all_balances" or msg == "/all":
                try:
                    all_users = list(wallets.find({}))
                    if not all_users:
                        send_telegram("📭 በዳታቤዝ ውስጥ ምንም ተጠቃሚ አልተገኘም።")
                    else:
                        report_lines = ["📋 *የሁሉንም ተጠቃሚዎች ባላንስ ዝርዝር:*\n"]
                        total_system_balance = 0
                        for idx, user in enumerate(all_users, 1):
                            phone = user.get("phone", "N/A")
                            uname = user.get("username", "የማይታወቅ")
                            bal = user.get("balance", 0)
                            total_system_balance += bal
                            report_lines.append(f"{idx}. 👤 `{uname}` | 📞 `{phone}` | 💵 *{bal} ETB*")
                        report_lines.append(f"\n💰 *በሲስተሙ ላይ ያለ ጠቅላላ የብር ድምር:* `{total_system_balance} ETB`")
                        full_report = "\n".join(report_lines)
                        if len(full_report) > 4000:
                            for chunk in [full_report[i:i+4000] for i in range(0, len(full_report), 4000)]:
                                send_telegram(chunk)
                        else:
                            send_telegram(full_report)
                except Exception as e:
                    send_telegram(f"❌ ስህተት: {e}")

            elif msg.startswith("/balance") or msg.startswith("/check"):
                try:
                    parts = msg.split()
                    if len(parts) == 2:
                        target_phone = sanitize_input(parts[1])
                        user = wallets.find_one({"phone": target_phone})
                        if user:
                            send_telegram(f"🔍 *የተጠቃሚ መረጃ:*\n\n👤 ስም: `{user.get('username')}`\n📞 ስልክ: `{target_phone}`\n💵 ባላንስ: *{user.get('balance', 0)} ETB*")
                        else:
                            send_telegram(f"❌ ስህተት! `{target_phone}` አልተገኘም።")
                except:
                    send_telegram("❌ ፎርማት: /balance ስልክ")

            elif msg.startswith("/add"):
                try:
                    parts = msg.split()
                    if len(parts) == 3:
                        target_phone, amount = sanitize_input(parts[1]), float(parts[2])
                        if amount > 0:
                            wallets.update_one({"phone": target_phone}, {"$inc": {"balance": amount}}, upsert=True)
                            send_telegram(f"✅ ለ `{target_phone}` {amount} ETB ተጨምሯል።")
                except:
                    send_telegram("❌ ፎርማት: /add ስልክ መጠን")
            
            elif msg.startswith("/sub"):
                try:
                    parts = msg.split()
                    if len(parts) == 3:
                        target_phone, amount = sanitize_input(parts[1]), float(parts[2])
                        if amount > 0:
                            wallets.update_one({"phone": target_phone}, {"$inc": {"balance": -amount}})
                            send_telegram(f"⚠️ ከ `{target_phone}` {amount} ETB ተቀንሷል።")
                except:
                    send_telegram("❌ ፎርማት: /sub ስልክ መጠን")

            elif msg.startswith("/kick") or msg.startswith("/remove"):
                try:
                    parts = msg.split()
                    if len(parts) == 2:
                        target_phone = sanitize_input(parts[1])
                        wallets.find_one_and_delete({"phone": target_phone})
                        with state_lock:
                            if target_phone in game_state["players"]:
                                p_tickets = list(game_state["players"][target_phone]["cards"].keys())
                                for t in p_tickets:
                                    if game_state["sold_tickets"].get(t) == target_phone:
                                        del game_state["sold_tickets"][t]
                                        game_state["pot"] -= 10
                                del game_state["players"][target_phone]
                        send_telegram(f"❌ ተጠቃሚ 📞 `{target_phone}` ተወግዷል።")
                except Exception as e:
                    send_telegram(f"❌ ስህተት: {e}")

    return "OK", 200

def check_winning_line(card, drawn_numbers):
    drawn_set = {int(b[1:]) for b in drawn_numbers if len(b) > 1}
    drawn_set.add(0) # FREE Space

    for i in range(5):
        row_indices = [i * 5 + j for j in range(5)]
        if all(card[idx] in drawn_set for idx in row_indices):
            return row_indices, f"አግድም (Row {i+1})"

    for i in range(5):
        col_indices = [i + j * 5 for j in range(5)]
        if all(card[idx] in drawn_set for idx in col_indices):
            return col_indices, f"ቁልቁል (Column {i+1})"

    diag1_indices = [0, 6, 12, 18, 24]
    if all(card[idx] in drawn_set for idx in diag1_indices):
        return diag1_indices, "ዲያጎናል (Diagonal 📉)"

    diag2_indices = [20, 16, 12, 8, 4]
    if all(card[idx] in drawn_set for idx in diag2_indices):
        return diag2_indices, "ዲያጎናል (Diagonal 📈)"

    corner_indices = [0, 4, 20, 24]
    if all(card[idx] in drawn_set for idx in corner_indices):
        return corner_indices, "አራቱ ማዕዘናት (4 Corners)"

    return None, None

def reset_game():
    with state_lock:
        game_state.update({
            "status": "lobby", "winner": None, "winning_card": None, "pot": 0, "players": {}, 
            "sold_tickets": {}, "drawn_balls": [], "current_ball": "--", "timer": 30
        })

def game_loop():
    balls = [f"{'BINGO'[i//15]}{i+1}" for i in range(75)]
    while True:
        with state_lock:
            current_status = game_state["status"]

        if current_status == "lobby":
            for i in range(30, -1, -1):
                with state_lock:
                    if game_state["status"] != "lobby": 
                        break
                    game_state["timer"] = i
                time.sleep(1)
            
            with state_lock:
                if game_state["status"] == "lobby" and len(game_state["players"]) >= 2:
                    game_state["status"] = "playing"
                    game_state["drawn_balls"] = []
                    shuffled = balls.copy()
                    random.shuffle(shuffled)
                else:
                    game_state["timer"] = 30
                    shuffled = []

            for b in shuffled:
                with state_lock:
                    if game_state["status"] != "playing": 
                        break
                    game_state["current_ball"] = b
                    game_state["drawn_balls"].append(b)
                time.sleep(5)
            
            with state_lock:
                if game_state["status"] == "playing":
                    game_state["status"] = "result"
                    game_state["winner"] = "No Winner (House)"
                    game_state["winning_card"] = None
                    send_telegram("ℹ️ ጨዋታው ያለ አሸናፊ ተጠናቋል።")
                    threading.Thread(target=lambda: (time.sleep(5), reset_game())).start()

        time.sleep(1)

@app.route('/')
def index(): 
    return render_template('index.html')

@app.route('/get_status')
def get_status():
    phone = sanitize_input(request.args.get('phone'))
    user = wallets.find_one({"phone": phone}) if phone else None
    
    with state_lock:
        # ተጠቃሚው ወደ ሌላ አፕ ሄዶ ሲመለስ የነበሩት ካርተላዎች እንዳይጠፉ/ስታክ እንዳይሆኑ ከስቴት ላይ ፈልጎ ማውጣት
        p_data = game_state["players"].get(phone, {"cards": {}})
        my_cards_dict = p_data.get("cards", {})
        
        # የካርተላ ቁጥሩን (Ticket Number) ጨምሮ ለግንባር ቀደም (Frontend) መላክ
        cards_list = [{"ticket_num": k, "numbers": v} for k, v in my_cards_dict.items()]
        
        status_copy = {
            "status": game_state["status"],
            "timer": game_state["timer"],
            "pot": game_state["pot"],
            "current_ball": game_state["current_ball"],
            "drawn_balls": game_state["drawn_balls"],
            "winner": game_state["winner"],
            "winning_card": game_state["winning_card"],
            "balance": user['balance'] if user else 0, 
            "my_cards": cards_list, 
            "active_players": len(game_state["players"])
        }
    return jsonify(status_copy)

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_ticket():
    d = request.json or {}
    ph, t_num, uname = sanitize_input(d.get('phone')), str(d.get('ticket_num')), sanitize_input(d.get('username'))
    
    if not ph or not t_num:
        return jsonify({"success": False, "msg": "የተሳሳተ መረጃ!"})

    with state_lock:
        if game_state["status"] != "lobby":
            return jsonify({"success": False, "msg": "ጨዋታ ተጀምሯል!"})
        if t_num in game_state["sold_tickets"]:
            return jsonify({"success": False, "msg": "ይህ ካርተላ ቀድሞ ተይዟል!"})
        if ph in game_state["players"] and len(game_state["players"][ph]["cards"]) >= 2:
            return jsonify({"success": False, "msg": "ከ 2 ካርተላ በላይ መግዛት አይቻልም!"})
        
        game_state["sold_tickets"][t_num] = "RESERVED_LOCK"

    res = wallets.find_one_and_update(
        {"phone": ph, "balance": {"$gte": 10}}, 
        {"$inc": {"balance": -10}},
        return_document=True
    )
    
    if res:
        columns = []
        for r in [(1,15), (16,30), (31,45), (46,60), (61,75)]:
            columns.append(random.sample(range(r[0], r[1]+1), 5))
            
        flat = []
        for row_idx in range(5):
            for col_idx in range(5):
                flat.append(columns[col_idx][row_idx])
                
        flat[12] = 0  
        
        with state_lock:
            if game_state["status"] != "lobby":
                if game_state["sold_tickets"].get(t_num) == "RESERVED_LOCK":
                    del game_state["sold_tickets"][t_num]
                wallets.update_one({"phone": ph}, {"$inc": {"balance": 10}})
                return jsonify({"success": False, "msg": "ጨዋታ ተጀምሯል!"})
                
            game_state["sold_tickets"][t_num] = ph
            game_state["pot"] += 10
            
            p_uname = uname if uname else res.get("username", f"User_{ph[-4:]}")
            if ph not in game_state["players"]:
                game_state["players"][ph] = {"cards": {t_num: flat}, "username": p_uname}
            else:
                game_state["players"][ph]["cards"][t_num] = flat
                
        return jsonify({"success": True})
    
    with state_lock:
        if game_state["sold_tickets"].get(t_num) == "RESERVED_LOCK":
            del game_state["sold_tickets"][t_num]
            
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    d = request.json or {}
    ph, t_num = sanitize_input(d.get('phone')), str(d.get('ticket_num'))
    
    with state_lock:
        if game_state["status"] == "lobby" and game_state["sold_tickets"].get(t_num) == ph:
            wallets.update_one({"phone": ph}, {"$inc": {"balance": 10}}) 
            game_state["pot"] -= 10
            del game_state["sold_tickets"][t_num]
            
            if ph in game_state["players"]:
                if t_num in game_state["players"][ph]["cards"]:
                    del game_state["players"][ph]["cards"][t_num]
                if not game_state["players"][ph]["cards"]: 
                    del game_state["players"][ph]
            return jsonify({"success": True})
            
    return jsonify({"success": False, "msg": "መሰረዝ አይቻልም!"})

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    d = request.json or {}
    ph = sanitize_input(str(d.get('phone')))
    amt = d.get('amount')
    t_id = sanitize_input(d.get('transaction_id', 'N/A'))
    
    user = wallets.find_one({"phone": ph})
    if user and "referred_by" in user:
        agent_phone = user["referred_by"]
        msg = (f"👤 **አዲስ ተመዝጋቢ በኤጀንት!**\n\n📝 ስም: `{ph}`\n💵 መጠን: `{amt}` ETB\n📲 ኤጀንት (ስልክ): **{agent_phone}**\n\n👇 Approve:\n`/add {ph} {amt}`")
    else:
        msg = (f"💰 *Deposit Request*\n📞 Phone: `{ph}`\n💵 Amount: `{amt}` ETB\n🆔 ID: `{t_id}`\n\n👇 Approve:\n`/add {ph} {amt}`")
               
    send_telegram(msg)
    return jsonify({"success": True})

@app.route('/withdraw', methods=['POST'])
def withdraw():
    d = request.json or {}
    ph, amt = sanitize_input(d.get('phone')), float(d.get('amount'))
    
    res = wallets.find_one_and_update(
        {"phone": ph, "balance": {"$gte": amt}},
        {"$inc": {"balance": -amt}},
        return_document=True
    )
    if res:
        msg = f"📤 *Withdraw Request*\n📞 Phone: `{ph}`\n💵 Amount: `{amt}` ETB"
        send_telegram(msg)
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "በቂ ባላንስ የለም!"})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    d = request.json or {}
    ph = sanitize_input(d.get('phone'))
    
    with state_lock:
        if game_state["status"] != "playing":
            return jsonify({"success": False, "msg": "ጨዋታው በሂደት ላይ አይደለም!"})
            
        p_data = game_state["players"].get(ph)
        if not p_data:
            return jsonify({"success": False, "msg": "ይገባኛል ጥያቄው ውድቅ ተደርጓል!"})
            
        cards_to_check = p_data["cards"]
        
        for t_num, card in cards_to_check.items():
            win_indices, line_type = check_winning_line(card, game_state["drawn_balls"])
            
            if win_indices is not None:
                game_state["status"] = "result"
                game_state["winner"] = p_data["username"]
                game_state["winning_card"] = card  
                
                # የሽልማት ክፍፍል
                total_pot = game_state["pot"]
                win_amt = total_pot * 0.8
                
                # አሸናፊው ዋናውን ብር እንዲያገኝ ማድረግ
                wallets.update_one({"phone": ph}, {"$inc": {"balance": win_amt}})
                
                # --- ✨ የ 5% ሪፈራል (የሊንክ ጋባዥ) ኮሚሽን ስሌት ---
                ref_msg = ""
                user_record = wallets.find_one({"phone": ph})
                if user_record and "referred_by" in user_record:
                    agent_phone = user_record["referred_by"]
                    ref_bonus = total_pot * 0.05
                    
                    # ለጋባዡ (ኤጀንት) 5% በራስ-ሰር መጨመር
                    wallets.update_one({"phone": agent_phone}, {"$inc": {"balance": ref_bonus}}, upsert=True)
                    ref_msg = f"\n🎁 *ሪፈራል ኮሚሽን (5%):* {ref_bonus} ETB ለኤጀንት `{agent_phone}` ገቢ ሆኗል!"
                
                card_rows = []
                for r in range(5):
                    row_vals = []
                    for c in range(5):
                        idx = r * 5 + c
                        val = card[idx]
                        val_str = "FREE" if val == 0 else str(val)
                        if idx in win_indices:
                            row_vals.append(f"⭐{val_str}⭐")
                        else:
                            row_vals.append(val_str)
                    card_rows.append(" | ".join(row_vals))
                card_text = "\n".join(card_rows)
                
                success_msg = (
                    f"🏆 *WINNER!* \n"
                    f"👤 Name: {p_data['username']} \n"
                    f"📞 Phone: `{ph}` \n"
                    f"🎫 Ticket No: {t_num} \n"
                    f"🎯 መስመር: *{line_type}*\n"
                    f"💰 Prize: {win_amt} ETB\n"
                    f"{ref_msg}\n\n"
                    f"📊 *Winning Card:* \n"
                    f"`{card_text}`"
                )
                
                send_telegram(success_msg)
                threading.Thread(target=lambda: (time.sleep(10), reset_game())).start()
                return jsonify({"success": True})
            
    return jsonify({"success": False, "msg": "ቢንጎ አልሞላም!"})

if __name__ == '__main__':
    threading.Timer(5, set_webhook).start() 
    threading.Thread(target=game_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
