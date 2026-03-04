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

# --- የጨዋታ ሁኔታ ---
game_state = {
    "status": "lobby", 
    "start_countdown": None, 
    "drawn_numbers": [], 
    "players": {}, 
    "pot": 0,
    "last_draw_time": 0,
    "winner": None
}

@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    now = time.time()
    player_count = len(game_state["players"])
    timer_display = 20

    # ዙሩ አልቆ አሸናፊ ከተገኘ ከ10 ሰከንድ በኋላ ወደ lobby ይመለሳል (Reset)
    if game_state["winner"] and (now - game_state["last_draw_time"] > 10):
        game_state.update({
            "status": "lobby", "start_countdown": None, "drawn_numbers": [], 
            "players": {}, "pot": 0, "winner": None
        })

    if game_state["status"] == "lobby":
        if player_count >= 2:
            if game_state["start_countdown"] is None:
                game_state["start_countdown"] = now
            elapsed = now - game_state["start_countdown"]
            timer_display = max(0, 20 - int(elapsed))
            if elapsed >= 20:
                game_state.update({"status": "running", "drawn_numbers": [], "last_draw_time": now})
        else:
            game_state["start_countdown"] = None
            timer_display = 20

    elif game_state["status"] == "running" and not game_state["winner"]:
        if now - game_state.get("last_draw_time", 0) >= 4 and len(game_state["drawn_numbers"]) < 90:
            n = random.randint(1, 90)
            while n in game_state["drawn_numbers"]: n = random.randint(1, 90)
            game_state["drawn_numbers"].append(n)
            game_state["last_draw_time"] = now
            
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
    wallets.update_one({"phone": data['phone']}, {"$set": {"name": data['name']}}, upsert=True)
    return jsonify({"success": True})

@app.route('/join_game', methods=['POST'])
def join():
    p = str(request.json['phone'])
    
    # 1. አስቀድሞ ገብቶ ከሆነ መከልከል
    if p in game_state["players"]:
        return jsonify({"success": False, "message": "አስቀድመው ገብተዋል!"})

    # 2. ባላንስ ማረጋገጥ (10 ብር)
    user = wallets.find_one({"phone": p})
    if not user or user.get("balance", 0) < 10:
        return jsonify({"success": False, "message": "በቂ ባላንስ የሎትም! እባክዎ Deposit ያድርጉ።"})

    # 3. 10 ብር ቀንሶ ማስገባት
    wallets.update_one({"phone": p}, {"$inc": {"balance": -10}})
    
    all_n = random.sample(range(1, 91), 15)
    game_state["players"][p] = {
        "name": user.get("name", "Player"), 
        "ticket": [sorted(all_n[0:5]), sorted(all_n[5:10]), sorted(all_n[10:15])]
    }
    game_state["pot"] += 10
    return jsonify({"success": True})

@app.route('/claim_win', methods=['POST'])
def claim_win():
    p = str(request.json['phone'])
    if p in game_state["players"] and not game_state["winner"]:
        game_state["winner"] = p
        win_amt = game_state["pot"]
        wallets.update_one({"phone": p}, {"$inc": {"balance": win_amt}})
        game_state["last_draw_time"] = time.time() # ለ Reset መጠበቂያ
        return jsonify({"success": True, "amount": win_amt})
    return jsonify({"success": False})

@app.route('/request_action', methods=['POST'])
def req_action():
    data = request.json
    msg = f"🔔 **አዲስ የ{data['type']} ጥያቄ!**\n\nስልክ: {data['phone']}\nመጠን: {data['amount']} ETB\nደረሰኝ: {data.get('receipt', 'የለም')}"
    asyncio.run_coroutine_threadsafe(bot.send_message(ADMIN_ID, msg), asyncio.get_event_loop())
    return jsonify({"success": True})

@dp.message(Command("start"))
async def start(m: types.Message):
    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🎮 Play Tombola", web_app=WebAppInfo(url=WEB_APP_URL)))
    await m.answer("እንኳን ደህና መጡ! ለመጫወት ከታች ያለውን ይጫኑ።", reply_markup=kb.as_markup())

if __name__ == "__main__":
    Thread(target=lambda: app.run(host='0.0.0.0', port=PORT), daemon=True).start()
    asyncio.run(dp.start_polling(bot))
