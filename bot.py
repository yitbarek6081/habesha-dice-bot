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
ADMIN_ID = 123456789  # <--- የርስዎ የቴሌግራም ID ቁጥር እዚህ ይግባ
ADMIN_PHONE = "0945880474"
PORT = int(os.environ.get("PORT", 10000))

app = Flask(__name__)
client = MongoClient(MONGO_URL)
db = client['tombola_game']
wallets = db['wallets']

bot = Bot(token=TOKEN)
dp = Dispatcher()

game_state = {
    "status": "lobby", 
    "start_time": time.time(), 
    "drawn_numbers": [], 
    "players": {}, 
    "pot": 0
}

@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    now = time.time()
    if game_state["status"] == "lobby":
        rem = 20 - int((now - game_state["start_time"]) % 20)
        if rem <= 1 and len(game_state["players"]) >= 2:
            game_state.update({"status": "running", "drawn_numbers": [], "last_draw_time": now})
    
    if game_state["status"] == "running":
        if now - game_state.get("last_draw_time", 0) >= 4 and len(game_state["drawn_numbers"]) < 90:
            n = random.randint(1, 90)
            while n in game_state["drawn_numbers"]: n = random.randint(1, 90)
            game_state["drawn_numbers"].append(n)
            game_state["last_draw_time"] = now
            
    return jsonify({**game_state, "player_count": len(game_state["players"])})

@app.route('/user_data/<phone>')
def user_data(phone):
    u = wallets.find_one({"phone": str(phone)})
    if not u: return jsonify({"error": "not_registered"})
    return jsonify({
        "balance": u.get('balance', 0.0),
        "is_joined": str(phone) in game_state["players"],
        "ticket": game_state["players"].get(str(phone), {}).get("ticket")
    })

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    wallets.update_one({"phone": data['phone']}, {"$set": {"name": data['name'], "balance": 0.0}}, upsert=True)
    return jsonify({"success": True})

@app.route('/request_action', methods=['POST'])
def req_action():
    data = request.json
    msg = f"🔔 **አዲስ የ{data['type']} ጥያቄ!**\n\nስልክ: {data['phone']}\nመጠን: {data['amount']} ETB\nደረሰኝ: {data.get('receipt', 'የለም')}"
    asyncio.run_coroutine_threadsafe(bot.send_message(ADMIN_ID, msg), asyncio.get_event_loop())
    return jsonify({"success": True})

@app.route('/join_game', methods=['POST'])
def join():
    p = str(request.json['phone'])
    all_n = random.sample(range(1, 91), 15)
    game_state["players"][p] = {"name": "Player", "ticket": [sorted(all_n[0:5]), sorted(all_n[5:10]), sorted(all_n[10:15])]}
    game_state["pot"] += 10
    return jsonify({"success": True})

@dp.message(Command("start"))
async def start(m: types.Message):
    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🎮 Play Tombola", web_app=WebAppInfo(url=WEB_APP_URL)))
    await m.answer("እንኳን ደህና መጡ! ለመጫወት ከታች ያለውን ይጫኑ።", reply_markup=kb.as_markup())

if __name__ == "__main__":
    Thread(target=lambda: app.run(host='0.0.0.0', port=PORT), daemon=True).start()
    asyncio.run(dp.start_polling(bot))
