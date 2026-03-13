import os, asyncio, random, time
from threading import Thread
from flask import Flask, render_template, jsonify, request
from aiogram import Bot, Dispatcher, types, filters
from pymongo import MongoClient

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = 7956330391 

app = Flask(__name__)
client = MongoClient(MONGO_URL)
db = client['tombola_game']
wallets = db['wallets']

bot = Bot(token=TOKEN)
dp = Dispatcher()
bot_loop = None

# --- ቋሚ 500 ካርቴላዎችን ማመንጫ ---
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
    "status": "lobby", "start_countdown": None,
    "drawn_numbers": [], "players": {}, "pot": 0, "winner": None,
    "available_numbers": list(range(1, 76)), "last_draw_time": 0
}

# --- የአድሚን ትዕዛዝ: /add phone amount ---
@dp.message(filters.Command("add"))
async def add_balance(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        args = message.text.split()
        phone, amount = args[1], int(args[2])
        wallets.update_one({"phone": phone}, {"$inc": {"balance": amount}}, upsert=True)
        await message.answer(f"✅ ለ {phone} {amount} ብር ተጨምሯል!")
    except:
        await message.answer("ትክክለኛ አጠቃቀም: /add 0912345678 100")

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
        
        if elapsed < 30:
            timer, sub = 30 - int(elapsed), "open"
        elif elapsed < 35:
            # ቢያንስ 2 ተጫዋች ካለ ጨዋታው ይጀምራል
            if player_count >= 2: timer, sub = 35 - int(elapsed), "closed"
            else: # ተጫዋች ካልመጣ ታይመሩ ይደገማል
                game_state["start_countdown"] = now
                timer, sub = 30, "open"
        else:
            game_state.update({"status": "running", "last_draw_time": now})
            random.shuffle(game_state["available_numbers"])
            timer, sub = 0, "live"
    else: timer = 0

    if game_state["status"] == "running" and not game_state["winner"]:
        if now - game_state["last_draw_time"] >= 4 and game_state["available_numbers"]:
            game_state["drawn_numbers"].append(game_state["available_numbers"].pop())
            game_state["last_draw_time"] = now

    return jsonify({
        "status": game_state["status"], "timer": timer, "pot": game_state["pot"],
        "win_prize": game_state["pot"] * 0.8, "player_count": player_count,
        "bought_numbers": bought_nums, "drawn_numbers": game_state["drawn_numbers"],
        "winner": game_state["winner"]
    })

@app.route('/join_game', methods=['POST'])
def join():
    data = request.json
    p, t_num = str(data['phone']), str(data['ticket_num'])
    user = wallets.find_one({"phone": p})
    
    # የካርታ ገደብ (Max 2)
    player_data = game_state["players"].get(p, {"tickets": [], "selected_nums": []})
    if len(player_data["tickets"]) >= 2: return jsonify({"success": False, "msg": "ከ2 በላይ አይፈቀድም!"})
    
    if not user or user.get("balance", 0) < 10: return jsonify({"success": False, "msg": "በቂ ብር የሎትም!"})

    wallets.update_one({"phone": p}, {"$inc": {"balance": -10}})
    player_data["tickets"].append(TICKET_POOL[t_num])
    player_data["selected_nums"].append(t_num)
    game_state["players"][p] = player_data
    game_state["pot"] += 10
    return jsonify({"success": True})

@app.route('/user_data/<phone>')
def user_data(phone):
    user = wallets.find_one({"phone": phone})
    return jsonify({"balance": user.get("balance", 0) if user else 0, "tickets": game_state["players"][phone]["tickets"] if phone in game_state["players"] else []})

def run_flask(): app.run(host='0.0.0.0', port=10000)
async def main():
    global bot_loop
    bot_loop = asyncio.get_running_loop()
    Thread(target=run_flask).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == '__main__': asyncio.run(main())
