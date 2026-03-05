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
import hashlib

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = 7956330391 
ADMIN_PHONE = "0945880474"
PORT = int(os.environ.get("PORT", 10000))

GROUP_LINK = "https://t.me/TombolaEthiopia" 
SUPPORT_ADMIN = "@TombolaEthiopia"

app = Flask(__name__)
client = MongoClient(MONGO_URL)
db = client['tombola_game']
wallets = db['wallets']
receipt_history = db['receipt_history'] 

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

@app.route('/')
def index(): 
    return render_template('index.html')

@app.route('/get_status')
def get_status():
    now = time.time()
    player_count = len(game_state["players"])
    
    # 1. አሸናፊ ከተገኘ ከ 5 ሰከንድ በኋላ ጌሙን ሪሴት ያደርጋል (ለአኒሜሽን ማሳያ)
    if game_state["winner"] and (now - game_state["last_draw_time"] > 5):
        game_state.update({
            "status": "lobby", "players": {}, "pot": 0, "winner": None, 
            "drawn_numbers": [], "available_numbers": list(range(1, 91)),
            "start_countdown": None
        })

    if game_state["status"] == "lobby":
        if game_state["start_countdown"] is None:
            game_state["start_countdown"] = now
        
        timer = max(0, 20 - int(now - game_state["start_countdown"]))
        
        # 2. ቢያንስ 2 ሰው ከሌለ ታይመሩ በየ 20 ሰከንዱ ራሱን ያድሳል
        if timer == 0:
            if player_count >= 2:
                game_state.update({"status": "running", "last_draw_time": now})
                random.shuffle(game_state["available_numbers"])
            else:
                game_state["start_countdown"] = now # ታይመሩን ዳግመኛ ይጀምረዋል
                timer = 20
    else:
        timer = 0

    if game_state["status"] == "running" and not game_state["winner"]:
        if now - game_state["last_draw_time"] >= 4 and game_state["available_numbers"]:
            game_state["drawn_numbers"].append(game_state["available_numbers"].pop())
            game_state["last_draw_time"] = now

    return jsonify({
        "status": game_state["status"], 
        "timer": timer, 
        "pot": game_state["pot"], 
        "player_count": player_count, 
        "drawn_numbers": game_state["drawn_numbers"], 
        "winner": game_state["winner"]
    })

@app.route('/unjoin_game', methods=['POST'])
def unjoin():
    p = str(request.json['phone'])
    if p in game_state["players"] and game_state["status"] == "lobby":
        wallets.update_one({"phone": p}, {"$inc": {"balance": 10}}) 
        del game_state["players"][p]
        game_state["pot"] -= 10
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/join_game', methods=['POST'])
def join():
    p = str(request.json['phone'])
    user = wallets.find_one({"phone": p})
    if not user or user.get("balance", 0) < 10 or game_state["status"] != "lobby": return jsonify({"success": False})
    
    wallets.update_one({"phone": p}, {"$inc": {"balance": -10}})
    all_n = random.sample(range(1, 91), 15)
    game_state["players"][p] = {"name": user.get("name", "Player"), "ticket": [sorted(all_n[0:5]), sorted(all_n[5:10]), sorted(all_n[10:15])]}
    game_state["pot"] += 10
    return jsonify({"success": True})

@app.route('/claim_win', methods=['POST'])
def claim_win():
    p = str(request.json['phone'])
    if p in game_state["players"] and not game_state["winner"]:
        game_state["winner"] = game_state["players"][p]["name"]
        
        # 3. የኮሚሽን ስሌት (80% ለአሸናፊው፣ 20% ለቤት)
        total_pot = game_state["pot"]
        win_amount = total_pot * 0.8 
        house_commission = total_pot * 0.2 
        
        # ለአሸናፊው 80% መጨመር
        wallets.update_one({"phone": p}, {"$inc": {"balance": win_amount}})
        
        # ኮሚሽኑን ለአድሚን ዋሌት ገቢ ማድረግ
        wallets.update_one({"phone": ADMIN_PHONE}, {"$inc": {"balance": house_commission}}, upsert=True)
        
        game_state["last_draw_time"] = time.time() # 5 ሰከንዱ ከዚህ ይቆጠራል
        
        if bot_loop:
            msg = (f"🏆 **አሸናፊ ተገኝቷል!**\n\n"
                   f"👤 ስም: `{game_state['winner']}`\n"
                   f"💰 ጠቅላላ ፖት: `{total_pot} ETB`\n"
                   f"💵 ለአሸናፊው (80%): `{win_amount:.2f} ETB`\n"
                   f"🏠 የቤት ኮሚሽን (20%): `{house_commission:.2f} ETB`")
            asyncio.run_coroutine_threadsafe(bot.send_message(ADMIN_ID, msg, parse_mode="Markdown"), bot_loop)
            
        return jsonify({"success": True, "amount": win_amount})
    return jsonify({"success": False})

# --- USER DATA ---
@app.route('/user_data/<phone>')
def user_data(phone):
    user = wallets.find_one({"phone": phone})
    if not user: return jsonify({"balance": 0, "is_joined": False})
    return jsonify({
        "balance": user.get("balance", 0),
        "is_joined": phone in game_state["players"],
        "ticket": game_state["players"][phone]["ticket"] if phone in game_state["players"] else []
    })

# --- REGISTER ---
@app.route('/register', methods=['POST'])
def register():
    data = request.json
    if not wallets.find_one({"phone": data['phone']}):
        wallets.insert_one({"phone": data['phone'], "name": data['name'], "balance": 0})
    return jsonify({"success": True})

# --- REQUEST ACTION ---
@app.route('/request_action', methods=['POST'])
def request_action():
    data = request.json
    if bot_loop:
        if data['type'] == 'Deposit':
            msg = f"💰 **የተቀማጭ ጥያቄ (Deposit)**\n👤 ስልክ: `{data['phone']}`\n📄 ዝርዝር: {data['receipt']}\n\nለመሙላት: `/add {data['phone']} መጠን`"
        else:
            msg = f"💸 **የወጪ ጥያቄ (Withdraw)**\n👤 ስልክ: `{data['phone']}`\n💵 መጠን: `{data['amount']} ETB`"
        asyncio.run_coroutine_threadsafe(bot.send_message(ADMIN_ID, msg), bot_loop)
    return jsonify({"success": True})

# --- BOT COMMANDS ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎮 ጨዋታውን ክፈት", web_app=WebAppInfo(url=WEB_APP_URL)))
    await message.answer("እንኳን ወደ ፕሪሚየም ቶምቦላ በሰላም መጡ! ለመጫወት ከታች ያለውን ቁልፍ ይጫኑ።", reply_markup=builder.as_markup())

@dp.message(Command("add"))
async def cmd_add(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        _, ph, amt = message.text.split()
        wallets.update_one({"phone": ph}, {"$inc": {"balance": float(amt)}}, upsert=True)
        await message.answer(f"✅ ለ {ph} {amt} ብር ተጨምሯል!")
    except:
        await message.answer("ስህተት! አጠቃቀም: `/add 0912345678 100`")

# --- SERVER START ---
def run_flask():
    app.run(host='0.0.0.0', port=PORT)

async def main():
    global bot_loop
    bot_loop = asyncio.get_running_loop()
    Thread(target=run_flask).start()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
