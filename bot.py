import os
import asyncio
import random
import time
import urllib.parse
from threading import Thread
from flask import Flask, render_template, jsonify, request
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo, InlineKeyboardButton
from pymongo import MongoClient

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL")
# MongoDB URI Error ለመከላከል parse እናደርገዋለን
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = 7956330391 
OWNER_PHONE = "0911223344" # ያንተ ስልክ ቁጥር

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = Flask(__name__)

# --- MONGODB CONNECTION FIX ---
try:
    client = MongoClient(MONGO_URL)
    db = client['tombola_game']
    wallets = db['wallets']
    # ግንኙነቱን ለመፈተሽ
    client.admin.command('ping')
except Exception as e:
    print(f"MongoDB Error: {e}")

def get_balance(phone):
    user = wallets.find_one({"phone": str(phone)})
    return user['balance'] if user else 0.0

def update_balance(phone, amount):
    wallets.update_one({"phone": str(phone)}, {"$inc": {"balance": amount}}, upsert=True)

# --- GAME STATE ---
game_state = {
    "status": "lobby", 
    "start_time": time.time(), 
    "drawn_numbers": [], 
    "players": {}, 
    "pot": 0, 
    "last_draw_time": 0, 
    "winner_name": None
}

# --- FLASK ROUTES ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    now = time.time()
    if game_state["status"] == "lobby":
        remaining = 20 - int((now - game_state["start_time"]) % 20)
        if remaining <= 1 and len(game_state["players"]) >= 2:
            game_state.update({"status": "running", "drawn_numbers": [], "last_draw_time": now, "winner_name": None})
    else: remaining = 0

    if game_state["status"] == "running" and not game_state["winner_name"]:
        if now - game_state["last_draw_time"] >= 4 and len(game_state["drawn_numbers"]) < 90:
            new_num = random.randint(1, 90)
            while new_num in game_state["drawn_numbers"]: new_num = random.randint(1, 90)
            game_state["drawn_numbers"].append(new_num)
            game_state["last_draw_time"] = now
    return jsonify({**game_state, "timer": int(remaining), "players_count": len(game_state["players"])})

@app.route('/user_data/<phone>')
def user_data(phone):
    player = game_state["players"].get(str(phone))
    return jsonify({
        "balance": get_balance(phone), 
        "is_joined": str(phone) in game_state["players"], 
        "ticket": player["ticket"] if player else None
    })

@app.route('/request_action', methods=['POST'])
async def request_action():
    data = request.json
    msg = f"🔔 **አዲስ ጥያቄ!**\n👤 ተጠቃሚ: `{data['phone']}`\n📝 አይነት: {data['type']}\n💰 መጠን: {data['amount']} ETB"
    await bot.send_message(ADMIN_ID, msg)
    return jsonify({"success": True})

@app.route('/join_game', methods=['POST'])
def join_game():
    data = request.json
    phone = str(data.get("phone"))
    if get_balance(phone) < 10 or game_state["status"] != "lobby": return jsonify({"success": False})
    update_balance(phone, -10)
    all_nums = random.sample(range(1, 91), 15)
    ticket = [sorted(all_nums[0:5]), sorted(all_nums[5:10]), sorted(all_nums[10:15])]
    game_state["players"][phone] = {"name": data.get("name", "Player"), "ticket": ticket}
    game_state["pot"] += 10
    return jsonify({"success": True})

@app.route('/win', methods=['POST'])
def win():
    phone = str(request.json.get("phone"))
    player = game_state["players"].get(phone)
    if not player or game_state["winner_name"]: return jsonify({"success": False})
    is_legit = any(all(n in game_state["drawn_numbers"] for n in row) for row in player["ticket"])
    if is_legit:
        prize, comm = game_state["pot"] * 0.8, game_state["pot"] * 0.2
        update_balance(phone, prize); update_balance(OWNER_PHONE, comm)
        game_state["winner_name"] = player["name"]
        Thread(target=lambda: (time.sleep(7), game_state.update({"status":"lobby", "start_time":time.time(), "drawn_numbers":[], "players":{}, "pot":0, "winner_name":None}))).start()
        return jsonify({"success": True})
    return jsonify({"success": False})

# --- RUNNER (RENDER FIX) ---
async def main():
    port = int(os.environ.get("PORT", 10000))
    def run_f():
        print(f"Server starting on port {port}")
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    
    Thread(target=run_f, daemon=True).start()
    await asyncio.sleep(2)
    print("Bot starting...")
    await dp.start_polling(bot)

@dp.message(Command("start"))
async def start(m: types.Message):
    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🎮 Play Tombola", web_app=WebAppInfo(url=WEB_APP_URL)))
    await m.answer("እንኳን ወደ ፕሪሚየም ቶምቦላ በደህና መጡ!", reply_markup=kb.as_markup())

if __name__ == "__main__":
    asyncio.run(main())
