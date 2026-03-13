import os, asyncio, random, time
from threading import Thread
from flask import Flask, render_template, jsonify, request
from aiogram import Bot, Dispatcher
from pymongo import MongoClient

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = 7956330391 
ADMIN_PHONE = "0945880474"

app = Flask(__name__)
client = MongoClient(MONGO_URL)
db = client['tombola_game']
wallets = db['wallets']

bot = Bot(token=TOKEN)
dp = Dispatcher()
bot_loop = None

# --- ቋሚ (Static) 500 ካርቴላዎችን ማመንጫ ---
def generate_fixed_tickets():
    pool = {}
    for i in range(1, 501):
        random.seed(i)
        ticket = []
        ranges = [(1,15), (16,30), (31,45), (46,60), (61,75)]
        cols = [random.sample(range(r[0], r[1]+1), 5) for r in ranges]
        for r in range(5):
            row = [cols[c][r] for c in range(5)]
            if r == 2: row[2] = 0 
            ticket.append(row)
        pool[str(i)] = ticket
    random.seed(time.time())
    return pool

TICKET_POOL = generate_fixed_tickets()

game_state = {
    "status": "lobby", "sub_status": "open", "start_countdown": None,
    "drawn_numbers": [], "players": {}, "pot": 0, "winner": None,
    "available_numbers": list(range(1, 76)), "last_draw_time": 0
}

@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    now = time.time()
    player_count = len(game_state["players"])
    bought_nums = []
    for p in game_state["players"].values(): bought_nums.extend(p.get("selected_nums", []))

    if game_state["status"] == "lobby":
        if game_state["start_countdown"] is None: game_state["start_countdown"] = now
        elapsed = now - game_state["start_countdown"]
        if elapsed < 30: timer, sub = 30 - int(elapsed), "open"
        elif elapsed < 35:
            if player_count >= 2: timer, sub = 35 - int(elapsed), "closed"
            else: game_state["start_countdown"] = now; timer, sub = 30, "open"
        else:
            game_state.update({"status": "running", "sub_status": "live", "last_draw_time": now})
            random.shuffle(game_state["available_numbers"])
            timer, sub = 0, "live"
        game_state["sub_status"] = sub
    else: timer = 0

    if game_state["status"] == "running" and not game_state["winner"]:
        if now - game_state["last_draw_time"] >= 4 and game_state["available_numbers"]:
            game_state["drawn_numbers"].append(game_state["available_numbers"].pop())
            game_state["last_draw_time"] = now

    return jsonify({
        "status": game_state["status"], "sub_status": game_state["sub_status"],
        "timer": timer, "pot": game_state["pot"], "bought_numbers": bought_nums,
        "drawn_numbers": game_state["drawn_numbers"], "winner": game_state["winner"]
    })

@app.route('/join_game', methods=['POST'])
def join():
    data = request.json
    p, t_num = str(data['phone']), str(data['ticket_num'])
    user = wallets.find_one({"phone": p})
    for pd in game_state["players"].values():
        if t_num in pd.get("selected_nums", []): return jsonify({"success": False, "msg": "ተይዟል!"})
    if not user or user.get("balance", 0) < 10 or game_state["sub_status"] != "open": return jsonify({"success": False})

    player_data = game_state["players"].get(p, {"name": user.get("name", "Player"), "tickets": [], "selected_nums": []})
    player_data["tickets"].append(TICKET_POOL[t_num])
    player_data["selected_nums"].append(t_num)
    game_state["players"][p] = player_data
    game_state["pot"] += 10
    wallets.update_one({"phone": p}, {"$inc": {"balance": -10}})
    return jsonify({"success": True})

@app.route('/claim_win', methods=['POST'])
def claim_win():
    data = request.json
    p = str(data['phone'])
    marked = set(data['marked_numbers'])
    marked.add(0) # FREE space
    
    if p in game_state["players"] and not game_state["winner"]:
        drawn = set(game_state["drawn_numbers"])
        drawn.add(0)
        
        # ቼክ፡ ተጫዋቹ ያጠቆረው ያልተጠራ ቁጥር ካለ ውድቅ አድርግ
        if not marked.issubset(drawn):
            return jsonify({"success": False, "msg": "ያልተጠራ ቁጥር አጥቁረዋል!"})

        def check_bingo(t):
            s = 5
            for i in range(s):
                if all(t[i][j] in marked for j in range(s)): return True
                if all(t[j][i] in marked for j in range(s)): return True
            if all(t[i][i] in marked for i in range(s)): return True
            if all(t[i][s-1-i] in marked for i in range(s)): return True
            return False

        if any(check_bingo(t) for t in game_state["players"][p]["tickets"]):
            game_state["winner"] = game_state["players"][p]["name"]
            win_amt = game_state["pot"] * 0.8
            wallets.update_one({"phone": p}, {"$inc": {"balance": win_amt}})
            return jsonify({"success": True})
            
    return jsonify({"success": False, "msg": "ገና ነዎት ወይም ተበልተዋል!"})

@app.route('/request_action', methods=['POST'])
def request_action():
    data = request.json
    if bot_loop:
        msg = f"💰 **Deposit**\nPh: `{data['phone']}`\nDetail: {data['receipt']}" if data['type'] == 'Deposit' else f"💸 **Withdraw**\nPh: `{data['phone']}`\nAmt: `{data['amount']} ETB`"
        asyncio.run_coroutine_threadsafe(bot.send_message(ADMIN_ID, msg), bot_loop)
    return jsonify({"success": True})

@app.route('/user_data/<phone>')
def user_data(phone):
    user = wallets.find_one({"phone": phone})
    return jsonify({"balance": user.get("balance", 0) if user else 0, "tickets": game_state["players"][phone]["tickets"] if phone in game_state["players"] else []})

def run_flask(): app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
async def main():
    global bot_loop
    bot_loop = asyncio.get_running_loop()
    Thread(target=run_flask).start()
    await dp.start_polling(bot)

if __name__ == '__main__': asyncio.run(main())
