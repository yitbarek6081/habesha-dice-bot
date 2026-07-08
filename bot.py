import os
import time
import random
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

# --- IN-MEMORY DATASTORE (Lightweight for 512MB RAM) ---
# ለአነስተኛ ሚሞሪ ሲባል ዳታቤዝ ሳይሆን ሚሞሪ ላይ በፍጥነት እንዲሰራ ተደርጓል
USERS = {}          # { phone: { "username": str, "balance": int, "cards": [] } }
TICKETS_SOLD = {}   # { ticket_num: phone }
DRAWN_BALLS = []    # የአሁኑ ዙር የወጡ ኳሶች
GAME_STATUS = "lobby" # lobby, playing, result
WINNER_INFO = {}

# --- GAME CONFIGURATION ---
LOBBY_DURATION = 30  # የሎቢ ሰዓት (ሰከንድ)
PREP_DURATION = 3    # የጨዋታ መጀመሪያ ዝግጅት ሰዓት (ሰከንድ)
BALL_INTERVAL = 4    # ኳስ በየስንት ሰከንዱ ይጣላል
TOTAL_BALLS = 75     # ጠቅላላ የቢንጎ ኳሶች
ROUND_CYCLE = 500    # ጠቅላላ የአንድ ዙር ዑደት ሰከንድ (ዳግም ለመጀመርያ ማረጋገጫ)

# የጨዋታውን ሰዓት እና ሁኔታ በጊዜ ማህተም (Timestamp) የሚቆጣጠር መካኒዝም
GAME_START_TS = time.time()

def get_current_game_state():
    """
    ይህ ፋንክሽን በ 0.1 vCPU ላይ ሉፕ ሳይሰራ በሰከንድ ስሌት ብቻ ጨዋታውን ይመራል።
    """
    global GAME_STATUS, DRAWN_BALLS, TICKETS_SOLD, WINNER_INFO, GAME_START_TS
    
    now = time.time()
    elapsed = int(now - GAME_START_TS)
    
    # 1. ሎቢ ስቴጅ (ካርቴላ መግዣ ሰዓት)
    if elapsed < LOBBY_DURATION:
        if GAME_STATUS != "lobby":
            # አዲስ ዙር ሲጀምር ዳታዎችን ማጽጃ
            GAME_STATUS = "lobby"
            TICKETS_SOLD.clear()
            DRAWN_BALLS.clear()
            WINNER_INFO.clear()
            for p in USERS:
                USERS[p]["cards"] = []
        return {"status": "lobby", "timer": LOBBY_DURATION - elapsed}
    
    # 2. የዝግጅት ስቴጅ (ጌም ሊጀምር ሲል 3 ሰከንድ)
    prep_end = LOBBY_DURATION + PREP_DURATION
    if elapsed < prep_end:
        GAME_STATUS = "playing"
        return {"status": "playing", "ball_timer": prep_end - elapsed, "current_ball": "--", "drawn": []}
    
    # 3. የጨዋታ ስቴጅ (ኳስ መጣል)
    GAME_STATUS = "playing"
    game_elapsed = elapsed - prep_end
    ball_count = (game_elapsed // BALL_INTERVAL) + 1
    
    # ኳሶችን በቅደም ተከተል በዘፈቀደ ማመንጫ (Deterministic Random Seed per Round)
    random.seed(int(GAME_START_TS))
    all_possible_balls = list(range(1, TOTAL_BALLS + 1))
    random.shuffle(all_possible_balls)
    
    # እስከአሁን የወጡ ኳሶች ሊስት
    DRAWN_BALLS = all_possible_balls[:min(ball_count, TOTAL_BALLS)]
    
    # ጌሙ ካለቀ ወይም አሸናፊ አስቀድሞ ከተገኘ
    if WINNER_INFO:
        GAME_STATUS = "result"
        res_elapsed = now - WINNER_INFO.get("time", now)
        rem_result_time = max(0, 10 - int(res_elapsed))
        if rem_result_time <= 0:
            GAME_START_TS = time.time() # አዲስ ዙር ቀጥታ መጀመርያ
        return {"status": "result", "timer": rem_result_time}
        
    if ball_count >= TOTAL_BALLS:
        # ማንም ሳያሸንፍ ኳስ ካለቀ ዙሩን በ result መዝጋት
        WINNER_INFO = {"winner": "ማንም", "ticket_num": "-", "card": [], "time": time.time()}
        return {"status": "result", "timer": 10}
        
    current_ball = DRAWN_BALLS[-1] if DRAWN_BALLS else "--"
    return {"status": "playing", "ball_timer": 0, "current_ball": current_ball, "drawn": DRAWN_BALLS}

def generate_bingo_card(ticket_num):
    """ለእያንዳንዱ ቲኬት ቁጥር የማይቀያየር ቋሚ የቢንጎ ማትሪክስ ያመነጫል"""
    random.seed(int(ticket_num))
    card = []
    ranges = [(1,15), (16,30), (31,45), (46,60), (61,75)]
    columns = []
    for r in ranges:
        col = random.sample(range(r[0], r[1]+1), 5)
        columns.append(col)
    
    # ማትሪክሱን ወደ Row መቀየር
    for row in range(5):
        for col in range(5):
            if row == 2 and col == 2:
                card.append("FREE")
            else:
                card.append(columns[col][row])
    return card

@app.route('/')
def index():
    # HTML ኮዱን ከታች ካለው ሊስት ላይ ያነባል
    return render_template_string(HTML_LAYOUT)

@app.route('/register_or_login', f"methods=['POST']")
def register_or_login():
    data = request.json or {}
    phone = data.get("phone", "").strip()
    username = data.get("username", "").strip()
    
    if not phone or not username:
        return jsonify({"success": False, "msg": "እባክዎ መረጃ በትክክል ያስገቡ!"})
        
    if phone not in USERS:
        USERS[phone] = {"username": username, "balance": 100, "cards": []} # ለሙከራ 100 ብር ቦነስ
        
    return jsonify({"success": True})

@app.route('/get_status')
def get_status():
    phone = request.args.get("phone", "")
    state = get_current_game_state()
    
    user = USERS.get(phone, {"balance": 0, "cards": []})
    my_cards_data = [generate_bingo_card(t) for t in user.get("cards", [])]
    
    # ጠቅላላ የተሸጡ ካርቴላዎች ብዛት
    pot = len(TICKETS_SOLD) * 10
    
    res = {
        "status": state["status"],
        "balance": user["balance"],
        "active_players": len(USERS),
        "pot": pot,
        "sold_tickets": TICKETS_SOLD,
        "my_cards": my_cards_data
    }
    
    if state["status"] == "lobby":
        res["timer"] = state["timer"]
    elif state["status"] == "playing":
        res["ball_timer"] = state["ball_timer"]
        res["current_ball"] = state["current_ball"]
        res["drawn_balls"] = state["drawn"]
    elif state["status"] == "result":
        res["timer"] = state["timer"]
        res["winner"] = WINNER_INFO.get("winner", "Player")
        res["winning_ticket_num"] = WINNER_INFO.get("ticket_num", "-")
        res["winning_card"] = WINNER_INFO.get("card", [])
        res["drawn_balls"] = DRAWN_BALLS
        
    return jsonify(res)

@app.route('/buy_specific_ticket', methods=['POST'])
def buy_specific_ticket():
    global TICKETS_SOLD
    state = get_current_game_state()
    if state["status"] != "lobby":
        return jsonify({"success": False, "msg": "የካርቴላ መግዣ ሰዓት አልፏል!"})
        
    data = request.json or {}
    phone = data.get("phone", "")
    ticket_num = int(data.get("ticket_num", 0))
    
    if phone not in USERS:
        return jsonify({"success": False, "msg": "ተጫዋቹ አልተገኘም!"})
    if ticket_num < 1 or ticket_num > 500:
        return jsonify({"success": False, "msg": "የተሳሳተ የካርቴላ ቁጥር!"})
        
    user = USERS[phone]
    if len(user["cards"]) >= 2:
        return jsonify({"success": False, "msg": "ከ 2 ካርቴላ በላይ መግዛት አይቻልም!"})
    if user["balance"] < 10:
        return jsonify({"success": False, "msg": "በቂ ባላንስ የለም! እባክዎ ዲፖዚት ያድርጉ።"})
        
    # Race-Condition መከላከያ (አንድ ካርቴላ ለሁለት ሰው እንዳይሸጥ)
    if ticket_num in TICKETS_SOLD:
        return jsonify({"success": False, "msg": "ይህ ካርቴላ ተሽጧል! ሌላ ይምረጡ።"})
        
    TICKETS_SOLD[ticket_num] = phone
    user["cards"].append(ticket_num)
    user["balance"] -= 10
    return jsonify({"success": True})

@app.route('/cancel_ticket', methods=['POST'])
def cancel_ticket():
    data = request.json or {}
    phone = data.get("phone", "")
    ticket_num = int(data.get("ticket_num", 0))
    
    if ticket_num in TICKETS_SOLD and TICKETS_SOLD[ticket_num] == phone:
        del TICKETS_SOLD[ticket_num]
        if phone in USERS and ticket_num in USERS[phone]["cards"]:
            USERS[phone]["cards"].remove(ticket_num)
            USERS[phone]["balance"] += 10
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "መሰረዝ አይቻልም!"})

@app.route('/claim_bingo', methods=['POST'])
def claim_bingo():
    global WINNER_INFO, GAME_STATUS
    data = request.json or {}
    phone = data.get("phone", "")
    
    if phone not in USERS or not USERS[phone]["cards"]:
        return jsonify({"success": False, "msg": "ካርቴላ የለዎትም!"})
        
    state = get_current_game_state()
    if state["status"] != "playing" or WINNER_INFO:
        return jsonify({"success": False, "msg": "BINGO ለማለት አልተፈቀደም!"})
        
    drawn_set = set(DRAWN_BALLS)
    user = USERS[phone]
    
    # የተጫዋቹን ካርቴላዎች በሙሉ ቼክ ማድረግ
    for t_num in user["cards"]:
        card = generate_bingo_card(t_num)
        
        # የቢንጎ ህግ ማረጋገጫ (Rows, Columns, Diagonals, 4 Corners)
        def check_hit(idx):
            if idx == 12 or card[idx] == "FREE": return True
            return card[idx] in drawn_set
            
        is_bingo = False
        # Rows
        for i in range(5):
            if all(check_hit(i*5 + j) for j in range(5)): is_bingo = True
        # Columns
        for j in range(5):
            if all(check_hit(i*5 + j) for i in range(5)): is_bingo = True
        # Diagonals
        if all(check_hit(i*6) for i in range(5)): is_bingo = True
        if all(check_hit(4 + i*4) for i in range(5)): is_bingo = True
        # 4 Corners
        if all(check_hit(x) for x in [0, 4, 20, 24]): is_bingo = True
        
        if is_bingo:
            # አሸናፊ ሲገኝ ሂሳብ ማሰራጫ (80% ለአሸናፊው ገቢ ይደረጋል)
            prize = int(len(TICKETS_SOLD) * 10 * 0.8)
            user["balance"] += prize
            WINNER_INFO = {
                "winner": user["username"],
                "ticket_num": t_num,
                "card": card,
                "time": time.time()
            }
            return jsonify({"success": True})
            
    return jsonify({"success": False, "msg": "ካርቴላዎ ገና አልሞላም! የተሳሳተ ቢንጎ ማስታወቅ ቅጣት ያስቀጣል!"})

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    data = request.json or {}
    phone = data.get("phone", "")
    amount = int(data.get("amount", 0))
    if phone in USERS and amount > 0:
        USERS[phone]["balance"] += amount # ለቀላል አሰራር ወዲያው አውቶማቲክ ባላንስ ይጨምራል
    return jsonify({"success": True})

@app.route('/request_withdraw', methods=['POST'])
def request_withdraw():
    data = request.json or {}
    phone = data.get("phone", "")
    amount = int(data.get("amount", 0))
    if phone in USERS and USERS[phone]["balance"] >= amount:
        USERS[phone]["balance"] -= amount
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "የሂሳብ ስህተት!"})

# --- HTML TEMPLATE INLINE FOR ONE-FILE DEPLOYMENT ---
HTML_LAYOUT = """...""" # የፍሮንትኤንድ ኮድ እዚህ ውስጥ ይገባል
