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

# --- ቁጥሮችን ከፊደላት ጋር ማዛመጃ (B-I-N-G-O) ---
def get_bingo_label(n):
    if 1 <= n <= 15: return f"B-{n}"
    if 16 <= n <= 30: return f"I-{n}"
    if 31 <= n <= 45: return f"N-{n}"
    if 46 <= n <= 60: return f"G-{n}"
    return f"O-{n}"

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
    return pool

TICKET_POOL = generate_fixed_tickets()

game_state = {
    "status": "lobby", 
    "sub_status": "open", 
    "start_countdown": None,
    "drawn_numbers": [], 
    "players": {}, 
    "pot": 0, 
    "winner": None,
    "available_numbers": list(range(1, 76)), 
    "last_draw_time": 0
}

@dp.message(filters.Command("add"))
async def add_balance(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        args = message.text.split()
        phone, amount = args[1], int(args[2])
        wallets.update_one({"phone": phone}, {"$inc": {"balance": amount}}, upsert=True)
        await message.answer(f"✅ ለ {phone} {amount} ብር ተጨምሯል!")
    except: await message.answer("ትክክለኛ አጠቃቀም: /add 0912345678 100")

@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    now = time.time()
    p_count = len(game_state["players"])
    
    if game_state["status"] == "lobby":
        if game_state["start_countdown"] is None: 
            game_state["start_countdown"] = now
        
        elapsed = now - game_state["start_countdown"]
        
        # ታይመሩ በየ 30 ሰከንዱ ራሱን እንዲደግም (Loop) ተደርጓል
        timer = 30 - (int(elapsed) % 30)
        sub = "open"
        
        # ተጫዋች ካለና ታይመሩ 0 ደርሶ ከሆነ ጨዋታውን ማስጀመር ትችላለህ
        # ለአሁኑ ግን ታይመሩ ብቻ እንዲደጋገም ተደርጓል
    else: 
        timer, sub = 0, "live"

    if game_state["status"] == "running" and not game_state["winner"]:
        if now - game_state["last_draw_time"] >= 4 and game_state["available_numbers"]:
            num = game_state["available_numbers"].pop()
            game_state["drawn_numbers"].append(get_bingo_label(num))
            game_state["last_draw_time"] = now

    # የተገዙ ቁጥሮችን ዝርዝር ማዘጋጀት (ለሁሉም እንዲታይ)
    bought_nums = []
    for p in game_state["players"].values():
        bought_nums.extend(p.get("selected_nums", []))

    return jsonify({
        "status": game_state["status"], 
        "sub_status": sub, 
        "timer": timer, 
        "pot": game_state["pot"], 
        "win_prize": game_state["pot"] * 0.8, 
        "player_count": p_count, 
        "drawn_numbers": game_state["drawn_numbers"],
        "bought_numbers": bought_nums
    })

@app.route('/join_game', methods=['POST'])
def join():
    data = request.json
    p, t_num = str(data['phone']), str(data['ticket_num'])
    user = wallets.find_one({"phone": p})
    
    if not user or user.get("balance", 0) < 10: 
        return jsonify({"success": False, "msg": "በቂ ብር የሎትም!"})
    
    # የተጫዋች ዳታ መኖሩን ማረጋገጥ
    if p not in game_state["players"]:
        game_state["players"][p] = {"tickets": [], "selected_nums": []}
    
    player_data = game_state["players"][p]
    
    # የአንድ ተጫዋች የ2 ካርቴላ ገደብ እዚህ ይከበራል
    if len(player_data["selected_nums"]) >= 2: 
        return jsonify({"success": False, "msg": "ከ2 በላይ ካርቴላ አይፈቀድም!"})

    # በካርቴላው ቁጥር ሌላ ሰው ቀድሞ ከገዛው መከልከል
    all_bought = [n for pl in game_state["players"].values() for n in pl["selected_nums"]]
    if t_num in all_bought:
        return jsonify({"success": False, "msg": "ይህ ካርቴላ ተይዟል!"})

    # ክፍያ (10 ብር)
    wallets.update_one({"phone": p}, {"$inc": {"balance": -10}})
    wallets.update_one({"phone": "ADMIN"}, {"$inc": {"balance": 2}}, upsert=True)
    
    player_data["tickets"].append(TICKET_POOL[t_num])
    player_data["selected_nums"].append(t_num)
    game_state["pot"] += 10 
    
    return jsonify({"success": True})

@app.route('/user_data/<phone>')
def user_data(phone):
    user = wallets.find_one({"phone": phone})
    # ተጫዋቹ የገዛቸው ካርቴላዎች ብዛት
    t_count = 0
    if phone in game_state["players"]:
        t_count = len(game_state["players"][phone]["selected_nums"])
        
    return jsonify({
        "balance": user.get("balance", 0) if user else 0, 
        "tickets": game_state["players"][phone]["tickets"] if phone in game_state["players"] else [],
        "ticket_count": t_count
    })

def run_flask(): app.run(host='0.0.0.0', port=10000)

async def main():
    Thread(target=run_flask).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == '__main__': 
    asyncio.run(main())
