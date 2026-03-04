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
ADMIN_ID = 5606497334  # <--- የርስዎ ትክክለኛ የቴሌግራም ID እዚህ ይግባ
ADMIN_PHONE = "0945880474"
PORT = int(os.environ.get("PORT", 10000))

app = Flask(__name__)
# MongoDB Connection
client = MongoClient(MONGO_URL)
db = client['tombola_game']
wallets = db['wallets']

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- የጨዋታ ሁኔታ (Game State) ---
game_state = {
    "status": "lobby", 
    "start_countdown": None, 
    "drawn_numbers": [], 
    "players": {}, 
    "pot": 0,
    "last_draw_time": 0,
    "winner": None,
    "available_numbers": list(range(1, 91)) # ለመሳብ የተዘጋጁ ቁጥሮች
}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_status')
def get_status():
    now = time.time()
    player_count = len(game_state["players"])
    timer_display = 20

    # 1. ጨዋታው አልቆ አሸናፊ ከተገኘ ከ15 ሰከንድ በኋላ ሪሴት ያደርጋል
    if game_state["winner"] and (now - game_state["last_draw_time"] > 15):
        game_state.update({
            "status": "lobby", "start_countdown": None, "drawn_numbers": [], 
            "players": {}, "pot": 0, "winner": None, "available_numbers": list(range(1, 91))
        })

    # 2. ሎቢ ውስጥ ቢያንስ 2 ሰው ሲኖር ታይመሩ ይጀምራል
    if game_state["status"] == "lobby":
        if player_count >= 2:
            if game_state["start_countdown"] is None:
                game_state["start_countdown"] = now
            elapsed = now - game_state["start_countdown"]
            timer_display = max(0, 20 - int(elapsed))
            if elapsed >= 20:
                game_state.update({
                    "status": "running", 
                    "drawn_numbers": [], 
                    "last_draw_time": now,
                    "available_numbers": list(range(1, 91))
                })
                random.shuffle(game_state["available_numbers"])
        else:
            game_state["start_countdown"] = None
            timer_display = 20

    # 3. ጨዋታው እየሄደ ከሆነ በየ 4 ሰከንዱ ቁጥር ይወጣል
    elif game_state["status"] == "running" and not game_state["winner"]:
        if now - game_state.get("last_draw_time", 0) >= 4:
            if game_state["available_numbers"]:
                n = game_state["available_numbers"].pop()
                game_state["drawn_numbers"].append(n)
                game_state["last_draw_time"] = now
            else:
                game_state["winner"] = "No one (Board Empty)"

    return jsonify({
        "status": game_state["status"],
        "timer": timer_display,
        "pot": game_state["pot"],
        "player_count": player_count,
        "drawn_numbers": game_state["drawn_numbers"],
        "winner": game_state["winner"]
    })

@app.route('/user_data/<phone>')
def user_data(phone):
    phone_str = str(phone)
    u = wallets.find_one({"phone": phone_str})
    if not u:
        return jsonify({"error": "not_registered"})
    
    return jsonify({
        "balance": u.get('balance', 0.0),
        "is_joined": phone_str in game_state["players"],
        "ticket": game_state["players"].get(phone_str, {}).get("ticket")
    })

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    wallets.update_one(
        {"phone": str(data['phone'])}, 
        {"$set": {"name": data['name']}, "$setOnInsert": {"balance": 0.0}}, 
        upsert=True
    )
    return jsonify({"success": True})

@app.route('/join_game', methods=['POST'])
def join():
    p = str(request.json['phone'])
    
    if game_state["status"] != "lobby":
        return jsonify({"success": False, "message": "ጨዋታ ተጀምሯል! ቀጣዩን ዙር ይጠብቁ።"})

    if p in game_state["players"]:
        return jsonify({"success": False, "message": "አስቀድመው ገብተዋል!"})

    user = wallets.find_one({"phone": p})
    if not user or user.get("balance", 0) < 10:
        return jsonify({"success": False, "message": "በቂ ባላንስ የሎትም! (10 ETB ያስፈልጋል)"})

    # 10 ብር ቀንሶ ማስገባት
    wallets.update_one({"phone": p}, {"$inc": {"balance": -10}})
    
    # ለተጫዋቹ የተለየ (Unique) 15 ቁጥሮች መስጠት
    all_n = random.sample(range(1, 91), 15)
    # 3 ረድፍ፣ እያንዳንዱ 5 ቁጥሮች
    ticket = [
        sorted(all_n[0:5]), 
        sorted(all_n[5:10]), 
        sorted(all_n[10:15])
    ]
    
    game_state["players"][p] = {
        "name": user.get("name", "Player"), 
        "ticket": ticket
    }
    game_state["pot"] += 10
    return jsonify({"success": True})

@app.route('/claim_win', methods=['POST'])
def claim_win():
    p = str(request.json['phone'])
    if p in game_state["players"] and not game_state["winner"]:
        # እዚህ ጋር ሰርቨሩ ላይም ቁጥሮቹን ቼክ ማድረግ ይቻላል (Security)
        game_state["winner"] = game_state["players"][p]["name"]
        win_amt = game_state["pot"]
        wallets.update_one({"phone": p}, {"$inc": {"balance": win_amt}})
        game_state["last_draw_time"] = time.time()
        return jsonify({"success": True, "amount": win_amt})
    return jsonify({"success": False})

@app.route('/request_action', methods=['POST'])
def req_action():
    data = request.json
    msg = f"🔔 **አዲስ የ{data['type']} ጥያቄ!**\n\nስልክ: `{data['phone']}`\nመጠን: `{data['amount']} ETB`\nደረሰኝ: `{data.get('receipt', 'የለም')}`"
    
    loop = asyncio.get_event_loop()
    loop.create_task(bot.send_message(ADMIN_ID, msg, parse_mode="Markdown"))
    return jsonify({"success": True})

@dp.message(Command("start"))
async def start(m: types.Message):
    kb = InlineKeyboardBuilder().row(
        InlineKeyboardButton(text="🎮 Play Tombola", web_app=WebAppInfo(url=WEB_APP_URL))
    )
    await m.answer("እንኳን ደህና መጡ! ቶምቦላ ለመጫወት ከታች ያለውን አዝራር ይጫኑ።", reply_markup=kb.as_markup())

async def run_bot():
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Flaskን በ Thread ውስጥ ማስጀመር
    Thread(target=lambda: app.run(host='0.0.0.0', port=PORT, use_reloader=False), daemon=True).start()
    # Telegram Botን በዋናው Loop ማስጀመር
    asyncio.run(run_bot())
