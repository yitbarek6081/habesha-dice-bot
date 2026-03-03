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
MONGO_URL = os.getenv("MONGO_URL")
# Render የሚሰጠውን ፖርት ይጠቀማል፣ ካልሆነ 10000
PORT = int(os.environ.get("PORT", 10000))

# --- FLASK APP SETUP ---
app = Flask(__name__)

# --- MONGODB CONNECTION ---
try:
    # በ Password ውስጥ @ ካለ በ Render Env ላይ %40 ተጠቅመህ መቀየርህን አረጋግጥ
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = client['tombola_game']
    wallets = db['wallets']
    client.admin.command('ping')
    print("✅ MongoDB Connected Successfully!")
except Exception as e:
    print(f"❌ MongoDB Error: {e}")
    wallets = None

# --- AIOGRAM SETUP ---
bot = Bot(token=TOKEN)
dp = Dispatcher()

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
def index(): 
    return render_template('index.html')

@app.route('/get_status')
def get_status():
    now = time.time()
    # Lobby Logic
    if game_state["status"] == "lobby":
        remaining = 20 - int((now - game_state["start_time"]) % 20)
        if remaining <= 1 and len(game_state["players"]) >= 2:
            game_state.update({"status": "running", "drawn_numbers": [], "last_draw_time": now})
    
    # Running Logic (Draw numbers every 4 seconds)
    if game_state["status"] == "running" and not game_state["winner_name"]:
        if now - game_state["last_draw_time"] >= 4 and len(game_state["drawn_numbers"]) < 90:
            new_num = random.randint(1, 90)
            while new_num in game_state["drawn_numbers"]: 
                new_num = random.randint(1, 90)
            game_state["drawn_numbers"].append(new_num)
            game_state["last_draw_time"] = now
            
    return jsonify({**game_state, "timer": int(20 - ((now - game_state["start_time"]) % 20))})

@app.route('/user_data/<phone>')
def user_data(phone):
    balance = 0.0
    if wallets is not None:
        user = wallets.find_one({"phone": str(phone)})
        balance = user['balance'] if user else 0.0
    
    is_joined = str(phone) in game_state["players"]
    ticket = game_state["players"].get(str(phone), {}).get("ticket")
    return jsonify({"balance": balance, "is_joined": is_joined, "ticket": ticket})

@app.route('/join_game', methods=['POST'])
def join_game():
    data = request.json
    phone = str(data.get("phone"))
    # ቲኬት ማመንጫ
    all_nums = random.sample(range(1, 91), 15)
    ticket = [sorted(all_nums[0:5]), sorted(all_nums[5:10]), sorted(all_nums[10:15])]
    game_state["players"][phone] = {"name": data.get("name", "Player"), "ticket": ticket}
    game_state["pot"] += 10
    return jsonify({"success": True})

# --- BOT HANDLERS ---
@dp.message(Command("start"))
async def start_cmd(m: types.Message):
    kb = InlineKeyboardBuilder().row(
        InlineKeyboardButton(text="🎮 Play Tombola", web_app=WebAppInfo(url=WEB_APP_URL))
    )
    await m.answer("እንኳን ደህና መጡ! ፕሪሚየም ቶምቦላን ለመጫወት 'Play' የሚለውን ይጫኑ።", reply_markup=kb.as_markup())

# --- RUNNER THREADS ---
def run_bot():
    """ቦቱን በጀርባ thread ለማስነሳት"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    print("🤖 Bot is starting...")
    loop.run_until_complete(dp.start_polling(bot))

if __name__ == "__main__":
    # 1. ቦቱን በ Background Thread አስጀምር
    t = Thread(target=run_bot)
    t.daemon = True
    t.start()

    # 2. Flaskን በ Main Thread አስነሳ (Render Port Binding እንዲያገኝ)
    print(f"🌐 Flask starting on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT)
