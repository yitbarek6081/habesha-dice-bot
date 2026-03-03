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

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL")
MONGO_URL = os.getenv("MONGO_URL")
PORT = int(os.getenv("PORT", 10000)) # Render የሚሰጠውን PORT መቀበል

# Flask App
app = Flask(__name__)

# MongoDB Connection
client = MongoClient(MONGO_URL)
db = client['tombola_game']
wallets = db['wallets']

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- GAME STATE ---
game_state = {"status": "lobby", "start_time": time.time(), "drawn_numbers": [], "players": {}, "pot": 0}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_status')
def get_status():
    return jsonify({**game_state, "timer": int(20 - ((time.time() - game_state["start_time"]) % 20))})

# --- BOT HANDLERS ---
@dp.message(Command("start"))
async def start(m: types.Message):
    kb = InlineKeyboardBuilder().row(
        InlineKeyboardButton(text="🎮 Play Tombola", web_app=WebAppInfo(url=WEB_APP_URL))
    )
    await m.answer("እንኳን ደህና መጡ! ፕሪሚየም ቶምቦላን ለመጫወት Play ይበሉ", reply_markup=kb.as_markup())

# --- RUNNER ---
async def start_bot():
    print("🤖 Bot is starting...")
    await dp.start_polling(bot)

def run_flask():
    print(f"🌐 Flask is starting on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    # 1. Flaskን በሌላ Thread አስነሳ (Render Port እንዲያገኝ)
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

    # 2. ቦቱን በዋናው Thread አስነሳ
    asyncio.run(start_bot())
