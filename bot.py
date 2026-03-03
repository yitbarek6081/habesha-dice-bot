import os
import asyncio
import random
import time
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
MONGO_URL = os.getenv("MONGO_URL")

# MongoDB Connection
client = MongoClient(MONGO_URL)
db = client['tombola_game']
wallets = db['wallets']

ADMIN_ID = 7956330391 
OWNER_PHONE = "0945880474"

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = Flask(__name__)
loop = asyncio.get_event_loop()

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

@app.route('/')
def index(): 
    return render_template('index.html')

@app.route('/get_status')
def get_status():
    now = time.time()
    if game_state["status"] == "lobby":
        remaining = 20 - int((now - game_state["start_time"]) % 20)
        if remaining <= 1 and len(game_state["players"]) >= 2:
            game_state.update({"status": "running", "drawn_numbers": [], "last_draw_time": now})
    
    if game_state["status"] == "running" and not game_state["winner_name"]:
        if now - game_state["last_draw_time"] >= 4 and len(game_state["drawn_numbers"]) < 90:
            new_num = random.randint(1, 90)
            while new_num in game_state["drawn_numbers"]: new_num = random.randint(1, 90)
            game_state["drawn_numbers"].append(new_num)
            game_state["last_draw_time"] = now
            
    return jsonify({**game_state, "timer": int(20 - ((now - game_state["start_time"]) % 20))})

@app.route('/user_data/<phone>')
def user_data(phone):
    user = wallets.find_one({"phone": str(phone)})
    balance = user['balance'] if user else 0.0
    is_joined = str(phone) in game_state["players"]
    ticket = game_state["players"].get(str(phone), {}).get("ticket")
    return jsonify({"balance": balance, "is_joined": is_joined, "ticket": ticket})

@app.route('/join_game', methods=['POST'])
def join_game():
    data = request.json
    phone = str(data.get("phone"))
    all_nums = random.sample(range(1, 91), 15)
    ticket = [sorted(all_nums[0:5]), sorted(all_nums[5:10]), sorted(all_nums[10:15])]
    game_state["players"][phone] = {"name": data.get("name", "Player"), "ticket": ticket}
    game_state["pot"] += 10
    return jsonify({"success": True})

@app.route('/request_action', methods=['POST'])
def request_action():
    data = request.json
    msg = f"🔔 ጥያቄ: {data['type']}\n👤 ስልክ: {data['phone']}\n💰 መጠን: {data['amount']}"
    loop.create_task(bot.send_message(ADMIN_ID, msg))
    return jsonify({"success": True})

async def main():
    port = int(os.environ.get("PORT", 10000))
    Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    await dp.start_polling(bot)

@dp.message(Command("start"))
async def start(m: types.Message):
    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🎮 Play Tombola", web_app=WebAppInfo(url=WEB_APP_URL)))
    await m.answer(f"እንኳን ደህና መጡ! ፕሪሚየም ቶምቦላ\n📞 Admin: {OWNER_PHONE}", reply_markup=kb.as_markup())

if __name__ == "__main__":
    asyncio.run(main())
