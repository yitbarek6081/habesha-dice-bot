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
ADMIN_ID = 7956330391 
ADMIN_PHONE = "0945880474"
PORT = int(os.environ.get("PORT", 10000))

app = Flask(__name__)
client = MongoClient(MONGO_URL)
db = client['tombola_game']
wallets = db['wallets']

bot = Bot(token=TOKEN)
dp = Dispatcher()
bot_loop = None

game_state = {
    "status": "lobby", 
    "start_countdown": None, 
    "drawn_numbers": [], 
    "players": {}, 
    "pot": 0, 
    "last_draw_time": 0, 
    "winner": None,
    "available_numbers": list(range(1, 91))
}

def get_bingo_label(n):
    if 1 <= n <= 18: return f"B{n}"
    if 19 <= n <= 36: return f"I{n}"
    if 37 <= n <= 54: return f"N{n}"
    if 55 <= n <= 72: return f"G{n}"
    return f"O{n}"

@app.route('/')
def index(): 
    return render_template('index.html')

@app.route('/get_status')
def get_status():
    now = time.time()
    player_count = len(game_state["players"])
    
    # ድል ከተመዘገበ በኋላ ጨዋታውን ዳግም ማስጀመር
    if game_state["winner"] and (now - game_state["last_draw_time"] > 7):
        game_state.update({
            "status": "lobby", "players": {}, "pot": 0, "winner": None, 
            "drawn_numbers": [], "available_numbers": list(range(1, 91)),
            "start_countdown": now
        })

    if game_state["status"] == "lobby":
        if game_state["start_countdown"] is None:
            game_state["start_countdown"] = now
        
        elapsed = now - game_state["start_countdown"]
        # 30 ሰከንድ መግዣ + 5 ሰከንድ ዝግጅት = 35 ሰከንድ
        timer = max(0, 35 - int(elapsed))
        
        if timer == 0:
            if player_count >= 2:
                game_state.update({"status": "running", "last_draw_time": now})
                random.shuffle(game_state["available_numbers"])
            else:
                game_state["start_countdown"] = now # ተጫዋች እስኪሞላ ድጋሚ መቁጠር

    if game_state["status"] == "running" and not game_state["winner"]:
        if now - game_state["last_draw_time"] >= 4 and game_state["available_numbers"]:
            game_state["drawn_numbers"].append(game_state["available_numbers"].pop())
            game_state["last_draw_time"] = now

    return jsonify({
        "status": game_state["status"], 
        "timer": timer if game_state["status"] == "lobby" else 0, 
        "pot": game_state["pot"], 
        "player_count": player_count, 
        "drawn_numbers": game_state["drawn_numbers"], 
        "winner": game_state["winner"],
        "last_num_label": get_bingo_label(game_state["drawn_numbers"][-1]) if game_state["drawn_numbers"] else "?"
    })

@app.route('/join_game', methods=['POST'])
def join():
    p = str(request.json['phone'])
    user = wallets.find_one({"phone": p})
    if not user or user.get("balance", 0) < 10 or game_state["status"] != "lobby": 
        return jsonify({"success": False, "msg": "Balance low or game started"})
    
    # ቢበዛ 2 ካርታ ብቻ
    current_tickets = game_state["players"].get(p, {}).get("tickets", [])
    if len(current_tickets) >= 2:
        return jsonify({"success": False, "msg": "Max 2 tickets allowed!"})

    wallets.update_one({"phone": p}, {"$inc": {"balance": -10}})
    
    # አዲስ ትኬት ማመንጨት
    all_n = random.sample(range(1, 91), 15)
    new_ticket = [sorted(all_n[0:5]), sorted(all_n[5:10]), sorted(all_n[10:15])]
    
    if p not in game_state["players"]:
        game_state["players"][p] = {"name": user.get("name", "Player"), "tickets": [new_ticket]}
    else:
        game_state["players"][p]["tickets"].append(new_ticket)
        
    game_state["pot"] += 10
    return jsonify({"success": True})

@app.route('/unjoin_game', methods=['POST'])
def unjoin():
    p = str(request.json['phone'])
    if p in game_state["players"] and game_state["status"] == "lobby":
        count = len(game_state["players"][p]["tickets"])
        wallets.update_one({"phone": p}, {"$inc": {"balance": 10 * count}}) 
        del game_state["players"][p]
        game_state["pot"] -= (10 * count)
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/claim_win', methods=['POST'])
def claim_win():
    p = str(request.json['phone'])
    if p in game_state["players"] and not game_state["winner"]:
        game_state["winner"] = game_state["players"][p]["name"]
        total_pot = game_state["pot"]
        win_amount = total_pot * 0.8 
        house_commission = total_pot * 0.2 
        wallets.update_one({"phone": p}, {"$inc": {"balance": win_amount}})
        wallets.update_one({"phone": ADMIN_PHONE}, {"$inc": {"balance": house_commission}}, upsert=True)
        game_state["last_draw_time"] = time.time()
        
        if bot_loop:
            msg = (f"🏆 **BINGO!**\n\n👤 አሸናፊ: `{game_state['winner']}`\n💰 የድል መጠን: `{win_amount} ETB`")
            asyncio.run_coroutine_threadsafe(bot.send_message(ADMIN_ID, msg), bot_loop)
        return jsonify({"success": True, "amount": win_amount})
    return jsonify({"success": False})

@app.route('/user_data/<phone>')
def user_data(phone):
    user = wallets.find_one({"phone": phone})
    if not user: return jsonify({"balance": 0, "tickets": []})
    return jsonify({
        "balance": user.get("balance", 0),
        "is_joined": phone in game_state["players"],
        "tickets": game_state["players"][phone]["tickets"] if phone in game_state["players"] else []
    })

# --- Flask & Bot Runner ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎮 BINGO ክፈት", web_app=WebAppInfo(url=WEB_APP_URL)))
    await message.answer("እንኳን ወደ BINGO ኢትዮጵያ በሰላም መጡ!", reply_markup=builder.as_markup())

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

async def main():
    global bot_loop
    bot_loop = asyncio.get_running_loop()
    Thread(target=run_flask).start()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
