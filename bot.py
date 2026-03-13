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

game_state = {
    "status": "lobby", "start_countdown": None,
    "players": {}, "pot": 0
}

# --- FLASK ROUTES ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    now = time.time()
    if game_state["start_countdown"] is None: game_state["start_countdown"] = now
    timer = 30 - (int(now - game_state["start_countdown"]) % 30)
    bought_nums = [n for p in game_state["players"].values() for n in p.get("selected_nums", [])]
    return jsonify({
        "timer": timer, "pot": game_state["pot"], 
        "win_prize": game_state["pot"] * 0.8, 
        "player_count": len(game_state["players"]), 
        "bought_numbers": bought_nums
    })

@app.route('/request_deposit', methods=['POST'])
def request_deposit():
    phone = request.json.get('phone')
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bot.send_message(ADMIN_ID, f"💰 የገንዘብ ማስገቢያ ጥያቄ!\nስልክ: {phone}\nእባክዎ ደረሰኝ ይጠይቁ።"))
    return jsonify({"msg": "ጥያቄው ለአድሚን ተልኳል!"})

@app.route('/request_withdraw', methods=['POST'])
def request_withdraw():
    data = request.json
    p, amt = data.get('phone'), data.get('amount')
    user = wallets.find_one({"phone": p})
    if user and user.get("balance", 0) >= int(amt):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(bot.send_message(ADMIN_ID, f"🔔 የገንዘብ ማውጫ ጥያቄ!\nስልክ: {p}\nመጠን: {amt} ብር"))
        return jsonify({"msg": "ጥያቄው ተልኳል! አድሚን ሲያጸድቀው ይላክለታል።"})
    return jsonify({"msg": "በቂ ቀሪ ሂሳብ የሎትም!"})

@app.route('/join_game', methods=['POST'])
def join():
    data = request.json
    p, t_num = str(data['phone']), str(data['ticket_num'])
    user = wallets.find_one({"phone": p})
    if not user or user.get("balance", 0) < 10: return jsonify({"success": False, "msg": "በቂ ብር የሎትም!"})
    if p not in game_state["players"]: game_state["players"][p] = {"selected_nums": []}
    if len(game_state["players"][p]["selected_nums"]) >= 2: return jsonify({"success": False, "msg": "ገደብ አልፏል!"})
    
    wallets.update_one({"phone": p}, {"$inc": {"balance": -10}})
    game_state["players"][p]["selected_nums"].append(t_num)
    game_state["pot"] += 10
    return jsonify({"success": True})

@app.route('/user_data/<phone>')
def user_data(phone):
    user = wallets.find_one({"phone": phone})
    t_count = len(game_state["players"][phone]["selected_nums"]) if phone in game_state["players"] else 0
    return jsonify({"balance": user.get("balance", 0) if user else 0, "ticket_count": t_count})

@dp.message(filters.Command("add"))
async def add_balance(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        _, ph, am = message.text.split()
        wallets.update_one({"phone": ph}, {"$inc": {"balance": int(am)}}, upsert=True)
        await message.answer(f"✅ {am} ብር ለ {ph} ተጨምሯል!")
    except: await message.answer("/add 09... 100")

def run_flask(): app.run(host='0.0.0.0', port=10000)

async def main():
    Thread(target=run_flask).start()
    await dp.start_polling(bot)

if __name__ == '__main__': asyncio.run(main())
