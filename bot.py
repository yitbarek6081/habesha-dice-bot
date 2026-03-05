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
ADMIN_ID = 7956330391  # ያንተ የቴሌግራም መለያ ቁጥር
ADMIN_PHONE = "0945880474"
PORT = int(os.environ.get("PORT", 10000))

# የግሩፕ እና የሳፖርት መረጃ
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
    "status": "lobby", "start_countdown": None, "drawn_numbers": [], 
    "players": {}, "pot": 0, "last_draw_time": 0, "winner": None,
    "available_numbers": list(range(1, 91))
}

# --- FLASK ROUTES ---

@app.route('/')
def index(): 
    return render_template('index.html')

@app.route('/get_status')
def get_status():
    now = time.time()
    player_count = len(game_state["players"])
    
    # አሸናፊ ከተገኘ ከ15 ሰከንድ በኋላ ጌሙን ሪሴት ያደርጋል
    if game_state["winner"] and (now - game_state["last_draw_time"] > 15):
        game_state.update({
            "status": "lobby", "players": {}, "pot": 0, "winner": None, 
            "drawn_numbers": [], "available_numbers": list(range(1, 91)),
            "start_countdown": None
        })
    
    if game_state["status"] == "lobby":
        if player_count >= 2:
            if game_state["start_countdown"] is None: game_state["start_countdown"] = now
            timer = max(0, 20 - int(now - game_state["start_countdown"]))
            if timer == 0:
                game_state.update({"status": "running", "last_draw_time": now})
                random.shuffle(game_state["available_numbers"])
        else: 
            timer = 20
            game_state["start_countdown"] = None
    else: 
        timer = 0

    # ቁጥር የመጣል ሒደት
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
    phone, name = str(data['phone']), data['name']
    wallets.update_one({"phone": phone}, {"$set": {"name": name}}, upsert=True)
    return jsonify({"success": True})

@app.route('/join_game', methods=['POST'])
def join():
    p = str(request.json['phone'])
    user = wallets.find_one({"phone": p})
    if not user or user.get("balance", 0) < 10 or game_state["status"] != "lobby": 
        return jsonify({"success": False})
    
    wallets.update_one({"phone": p}, {"$inc": {"balance": -10}})
    all_n = random.sample(range(1, 91), 15)
    game_state["players"][p] = {
        "name": user.get("name", "Player"), 
        "ticket": [sorted(all_n[0:5]), sorted(all_n[5:10]), sorted(all_n[10:15])]
    }
    game_state["pot"] += 10
    return jsonify({"success": True})

# --- አዲስ፡ መውጫ እና ብር ተመላሽ ---
@app.route('/unjoin_game', methods=['POST'])
def unjoin():
    p = str(request.json['phone'])
    if p in game_state["players"] and game_state["status"] == "lobby":
        wallets.update_one({"phone": p}, {"$inc": {"balance": 10}})
        del game_state["players"][p]
        game_state["pot"] -= 10
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Cannot leave now"})

@app.route('/claim_win', methods=['POST'])
def claim_win():
    p = str(request.json['phone'])
    if p in game_state["players"] and not game_state["winner"]:
        game_state["winner"] = game_state["players"][p]["name"]
        wallets.update_one({"phone": p}, {"$inc": {"balance": game_state["pot"]}})
        game_state["last_draw_time"] = time.time()
        
        # ለአድሚኑ ማሳወቂያ መላክ
        if bot_loop:
            win_msg = f"🏆 **አሸናፊ ተገኝቷል!**\n\n👤 ስም: `{game_state['winner']}`\n💰 መጠን: `{game_state['pot']} ETB`"
            asyncio.run_coroutine_threadsafe(bot.send_message(ADMIN_ID, win_msg, parse_mode="Markdown"), bot_loop)
            
        return jsonify({"success": True, "amount": game_state["pot"]})
    return jsonify({"success": False})

@app.route('/request_action', methods=['POST'])
def request_action():
    data = request.json
    phone = data.get('phone')
    req_type = data.get('type', 'Deposit')
    amount = data.get('amount', '0')
    receipt = data.get('receipt', '').strip()

    if req_type == 'Deposit':
        if not receipt or len(receipt) < 10:
            return jsonify({"success": False, "error": "Invalid receipt"})

        receipt_id = hashlib.md5(receipt.encode()).hexdigest()
        if receipt_history.find_one({"receipt_id": receipt_id}):
            return jsonify({"success": False, "error": "Duplicate"})

        receipt_history.insert_one({"receipt_id": receipt_id, "phone": phone, "time": time.time()})
        msg = f"🔔 **አዲስ የዲፖዚት ጥያቄ!**\n\n📱 ስልክ: `{phone}`\n🧾 ደረሰኝ: \n`{receipt}`\n\nባላንስ ለመሙላት: `/add {phone} [መጠን]`"

    else: # Withdraw process
        user = wallets.find_one({"phone": phone})
        if not user or user.get("balance", 0) < float(amount):
            return jsonify({"success": False, "error": "Inadequate Balance"})
        
        msg = f"💸 **አዲስ የዊዝድሮው ጥያቄ!**\n\n📱 ስልክ: `{phone}`\n💰 መጠን: `{amount} ETB`\n\nባላንስ ለመቀነስ: `/add {phone} -{amount}`"
    
    if bot_loop:
        asyncio.run_coroutine_threadsafe(bot.send_message(ADMIN_ID, msg, parse_mode="Markdown"), bot_loop)
        return jsonify({"success": True})
    return jsonify({"success": False})

# --- BOT COMMANDS ---

@dp.message(Command("start"))
async def start(m: types.Message):
    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🎮 Play Tombola", web_app=WebAppInfo(url=WEB_APP_URL)))
    await m.answer(f"ሰላም {m.from_user.first_name}! እንኳን ደህና መጡ።\n\n📢 ግሩፓችን: {GROUP_LINK}\n🛠 ድጋፍ: {SUPPORT_ADMIN}", reply_markup=kb.as_markup())

@dp.message(Command("link"))
async def link_account(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return await m.answer("⚠️ አጠቃቀም: `/link 09xxxxxxxx`")
    phone = args[1].strip()
    wallets.update_one({"phone": phone}, {"$set": {"tg_id": m.from_user.id}})
    await m.answer("✅ አካውንትዎ ተገናኝቷል! አሁን ማሳወቂያዎች ይደርስዎታል።")

@dp.message(Command("users"))
async def list_users(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    all_u = wallets.find().sort("balance", -1)
    msg = "👥 **የተመዘገቡ ተጫዋቾች**\n\n"
    count = 0
    for u in all_u:
        count += 1
        msg += f"{count}. 👤 {u.get('name')} | 📱 `{u.get('phone')}` | 💰 `{u.get('balance', 0):.2f}`\n"
        if len(msg) > 3800:
            await m.answer(msg, parse_mode="Markdown")
            msg = ""
    if count == 0: await m.answer("❌ እስካሁን ምንም ተጫዋች አልተመዘገበም።")
    elif msg: await m.answer(msg, parse_mode="Markdown")

@dp.message(Command("add"))
async def add_money(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    try:
        args = m.text.split()
        if len(args) < 3: return await m.answer("❌ አጠቃቀም: `/add [ስልክ] [መጠን]`")
        phone, amount = args[1].strip(), float(args[2])
        user = wallets.find_one({"phone": phone})
        if user:
            wallets.update_one({"phone": phone}, {"$inc": {"balance": amount}})
            if user.get("tg_id"):
                try: 
                    n_msg = f"💰 {amount} ETB ባላንስዎ ላይ ተጨምሯል!" if amount > 0 else f"💸 {abs(amount)} ETB ከባላንስዎ ላይ ተቀንሷል።"
                    await bot.send_message(user["tg_id"], n_msg)
                except: pass
            await m.answer(f"✅ ለ {phone} {amount} ETB ተስተካክሏል።")
        else:
            await m.answer("❌ ስልኩ አልተገኘም።")
    except Exception as e:
        await m.answer(f"❌ ስህተት: {str(e)}")

# --- MAIN RUNNER ---

async def main():
    global bot_loop
    bot_loop = asyncio.get_running_loop()
    
    flask_thread = Thread(target=lambda: app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()
    
    print("Bot is starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped.")
