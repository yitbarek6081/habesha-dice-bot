import os, asyncio, random, time, json
from flask import Flask, render_template, jsonify, request
from threading import Thread
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo, InlineKeyboardButton
from pymongo import MongoClient

# --- 1. CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL")
MONGO_URL = os.getenv("MONGO_URL") 
ADMIN_ID = 7956330391 # የእርስዎ ቴሌግራም ID

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = Flask(__name__)

# --- 2. MONGODB SETUP ---
client = MongoClient(MONGO_URL)
db = client['tombola_game']
wallets = db['wallets']

def get_balance(phone):
    user = wallets.find_one({"phone": phone})
    return user['balance'] if user else 0.0

def update_balance(phone, amount):
    wallets.update_one(
        {"phone": phone},
        {"$inc": {"balance": amount}},
        upsert=True
    )

game_state = {
    "status": "lobby",
    "start_time": time.time(),
    "drawn_numbers": [],
    "players": {},
    "pot": 0,
    "last_draw_time": 0,
    "winner_name": None
}

# --- 3. FLASK API ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    now = time.time()
    if game_state["status"] == "lobby":
        remaining = 20 - int((now - game_state["start_time"]) % 20)
        if remaining == 1 and len(game_state["players"]) >= 2:
            game_state["status"] = "running"
            game_state["drawn_numbers"] = []
            game_state["last_draw_time"] = now
    else: remaining = 0

    if game_state["status"] == "running" and not game_state["winner_name"]:
        if now - game_state["last_draw_time"] >= 4 and len(game_state["drawn_numbers"]) < 90:
            new_num = random.randint(1, 90)
            while new_num in game_state["drawn_numbers"]: new_num = random.randint(1, 90)
            game_state["drawn_numbers"].append(new_num)
            game_state["last_draw_time"] = now
    return jsonify({"status": game_state["status"], "timer": int(remaining), "drawn": game_state["drawn_numbers"], "pot": game_state["pot"], "players_count": len(game_state["players"]), "winner": game_state["winner_name"]})

@app.route('/user_data/<phone>')
def user_data(phone):
    player = game_state["players"].get(phone)
    return jsonify({"balance": get_balance(phone), "is_joined": phone in game_state["players"], "ticket": player["ticket"] if player else None})

@app.route('/request_action', methods=['POST'])
async def request_action():
    data = request.json
    action_type = data.get("type") # "deposit" ወይም "withdraw"
    phone = data.get("phone")
    amount = data.get("amount", "ያልተጠቀሰ")
    
    msg = f"🔔 **አዲስ ጥያቄ!**\n\n👤 ተጠቃሚ: `{phone}`\n📝 አይነት: **{action_type.upper()}**\n💰 መጠን: {amount} ETB"
    await bot.send_message(ADMIN_ID, msg, parse_mode="Markdown")
    return jsonify({"success": True})

@app.route('/join_game', methods=['POST'])
def join_game():
    data = request.json
    phone = data.get("phone")
    if get_balance(phone) < 10: return jsonify({"success": False, "msg": "ባላንስ የሎትም!"})
    if phone not in game_state["players"] and game_state["status"] == "lobby":
        update_balance(phone, -10)
        all_nums = random.sample(range(1, 91), 15)
        ticket = [sorted(all_nums[0:5]), sorted(all_nums[5:10]), sorted(all_nums[10:15])]
        game_state["players"][phone] = {"name": data.get("name", "Player"), "ticket": ticket}
        game_state["pot"] += 10
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/win', methods=['POST'])
def win():
    phone = request.json.get("phone")
    player = game_state["players"].get(phone)
    if not player or game_state["winner_name"]: return jsonify({"success": False})
    is_legit = any(all(num in game_state["drawn_numbers"] for num in row) for row in player["ticket"])
    if is_legit:
        prize = game_state["pot"] * 0.85
        update_balance(phone, prize)
        game_state["winner_name"] = player["name"]
        Thread(target=lambda: (time.sleep(7), game_state.update({"status":"lobby", "start_time":time.time(), "drawn_numbers":[], "players":{}, "pot":0, "winner_name":None}))).start()
        return jsonify({"success": True, "prize": prize})
    return jsonify({"success": False})

# --- 4. TELEGRAM HANDLERS ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🎮 ቶምቦላ ይጫወቱ", web_app=WebAppInfo(url=WEB_APP_URL)))
    await m.answer(f"ሰላም {m.from_user.first_name}! ለመጫወት ከታች ያለውን ቁልፍ ይጫኑ።", reply_markup=kb.as_markup())

@dp.message(Command("add_credit"))
async def add_c(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    try:
        parts = m.text.split()
        p, amt = parts[1], float(parts[2])
        update_balance(p, amt)
        await m.answer(f"✅ ተሞልቷል!\nስልክ: {p}\nባላንስ: {get_balance(p)} ETB")
    except: await m.answer("አጠቃቀም: `/add_credit 09... 100`")

# --- 5. RENDER PORT FIX ---
async def main():
    port = int(os.environ.get("PORT", 10000))
    Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
