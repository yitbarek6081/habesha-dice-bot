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

# --- አዲስ፡ የግሩፕ እና የሳፖርት መረጃ ---
GROUP_LINK = "https://t.me/TombolaEthiopia" # <--- የእርሶን ግሩፕ ሊንክ እዚህ ያስገቡ
SUPPORT_ADMIN = "@TombolaEthiopia"     # <--- የእርሶን ዩዘር ኔም እዚህ ያስገቡ

app = Flask(__name__)
client = MongoClient(MONGO_URL)
db = client['tombola_game']
wallets = db['wallets']

bot = Bot(token=TOKEN)
dp = Dispatcher()

game_state = {
    "status": "lobby", "start_countdown": None, "drawn_numbers": [], 
    "players": {}, "pot": 0, "last_draw_time": 0, "winner": None,
    "available_numbers": list(range(1, 91))
}

@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    now = time.time()
    player_count = len(game_state["players"])
    if game_state["winner"] and (now - game_state["last_draw_time"] > 15):
        game_state.update({"status": "lobby", "players": {}, "pot": 0, "winner": None, "drawn_numbers": [], "available_numbers": list(range(1, 91))})
    
    if game_state["status"] == "lobby":
        if player_count >= 2:
            if game_state["start_countdown"] is None: game_state["start_countdown"] = now
            timer = max(0, 20 - int(now - game_state["start_countdown"]))
            if timer == 0:
                game_state.update({"status": "running", "last_draw_time": now})
                random.shuffle(game_state["available_numbers"])
        else: timer = 20
    else: timer = 0

    if game_state["status"] == "running" and not game_state["winner"]:
        if now - game_state["last_draw_time"] >= 4 and game_state["available_numbers"]:
            game_state["drawn_numbers"].append(game_state["available_numbers"].pop())
            game_state["last_draw_time"] = now

    return jsonify({"status": game_state["status"], "timer": timer, "pot": game_state["pot"], "player_count": player_count, "drawn_numbers": game_state["drawn_numbers"], "winner": game_state["winner"]})

@app.route('/user_data/<phone>')
def user_data(phone):
    u = wallets.find_one({"phone": str(phone)})
    if not u: return jsonify({"error": "not_registered"})
    return jsonify({"balance": u.get('balance', 0.0), "is_joined": str(phone) in game_state["players"], "ticket": game_state["players"].get(str(phone), {}).get("ticket")})

# --- ተስተካክሏል፡ ሪጅስተር ሲያደርጉ መልዕክት ይልካል ---
@app.route('/register', methods=['POST'])
def register():
    data = request.json
    phone, name = str(data['phone']), data['name']
    wallets.update_one({"phone": phone}, {"$set": {"name": name}}, upsert=True)
    
    # ተጫዋቹን ፈልጎ ማሳወቂያ መላክ
    user = wallets.find_one({"phone": phone})
    if user and user.get("tg_id"):
        welcome_text = (
            f"እንኳን ደህና መጡ {name} 👋!\n\n"
            f"✅ ምዝገባዎ ተጠናቋል።\n"
            f"📢 ግሩፓችን፡ {GROUP_LINK}\n"
            f"🛠 ድጋፍ፡ {SUPPORT_ADMIN}\n\n"
            "አሁኑኑ ተሳተፉና ያሸንፉ!"
        )
        asyncio.run_coroutine_threadsafe(bot.send_message(user["tg_id"], welcome_text), asyncio.get_event_loop())
    
    return jsonify({"success": True})

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
        wallets.update_one({"phone": p}, {"$inc": {"balance": game_state["pot"]}})
        game_state["last_draw_time"] = time.time()
        return jsonify({"success": True, "amount": game_state["pot"]})
    return jsonify({"success": False})

@app.route('/request_action', methods=['POST'])
def req_action():
    data = request.json
    msg = f"🔔 **አዲስ ጥያቄ**\n\nስልክ: `{data['phone']}`\nመጠን: `{data['amount']}`\nደረሰኝ: `{data.get('receipt')}`"
    asyncio.run_coroutine_threadsafe(bot.send_message(ADMIN_ID, msg, parse_mode="Markdown"), asyncio.get_event_loop())
    return jsonify({"success": True})
# --- ይህን ክፍል በኮድህ ውስጥ አድሚን ትዕዛዝ ጋር ጨምረው ---

@bot.message_handler(commands=['add'])
def admin_add_balance(message):
    # መጀመሪያ ትዕዛዙን የላከው ሰው እውነተኛው አድሚን መሆኑን ያረጋግጣል
    # ADMIN_ID የሚለውን በራስህ የቴሌግራም ID ቁጥር ቀይረው (ለምሳሌ 12345678)
    ADMIN_ID = "7956330391" # <--- ያንተን የቴሌግራም ID ቁጥር እዚህ አስገባ

    if str(message.from_user.id) == ADMIN_ID:
        try:
            # ትዕዛዙ እንዲህ መሆን አለበት: /add 0912345678 100
            args = message.text.split()
            if len(args) < 3:
                bot.reply_to(message, "❌ ስህተት! አጠቃቀም፦ /add [ስልክ] [መጠን]\nምሳሌ፦ /add 0945880474 100")
                return

            target_phone = args[1].strip()
            amount = float(args[2])

            # እዚህ ጋር በዳታቤዝህ (Database) ውስጥ ባላንሱን የማደስ ስራ ይሰራል
            # ለምሳሌ በ SQLite ከሆነ እንዲህ ይሆናል፡
            # cursor.execute("UPDATE users SET balance = balance + ? WHERE phone = ?", (amount, target_phone))
            # db.commit()

            bot.reply_to(message, f"✅ በተሳካ ሁኔታ ለ {target_phone} {amount} ETB ተጨምሯል!")
            
            # ለተጫዋቹ ኖቲፊኬሽን መላክ (አማራጭ)
            # bot.send_message(target_chat_id, f"💰 የ {amount} ETB ክፍያዎ ተረጋግጧል! አሁን መጫወት ይችላሉ።")

        except ValueError:
            bot.reply_to(message, "❌ ስህተት! የብር መጠኑ ቁጥር መሆን አለበት።")
        except Exception as e:
            bot.reply_to(message, f"❌ ችግር ተፈጥሯል፦ {str(e)}")
    else:
        bot.reply_to(message, "⚠️ ይህ ትዕዛዝ ለአድሚን ብቻ የተፈቀደ ነው።")
# --- BOT COMMANDS ---

@dp.message(Command("start"))
async def start(m: types.Message):
    # ተጫዋቹ ሲጀምር ID-ውን ለጊዜው እናስቀምጥለት (በኋላ ከስልክ ጋር ለማያያዝ)
    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🎮 Play Tombola", web_app=WebAppInfo(url=WEB_APP_URL)))
    await m.answer(f"ሰላም {m.from_user.first_name}! ለመጀመር መጀመሪያ ስልክዎን በ `/link` ያገናኙ ወይም Play የሚለውን ይጫኑ።", reply_markup=kb.as_markup())

@dp.message(Command("link"))
async def link_account(m: types.Message):
    args = m.text.split()
    if len(args) < 2: return await m.answer("⚠️ አጠቃቀም: `/link 09xxxxxxxx`")
    phone = args[1]
    wallets.update_one({"phone": phone}, {"$set": {"tg_id": m.from_user.id}})
    await m.answer("✅ አካውንትዎ ተገናኝቷል! አሁን ማሳወቂያዎች ይደርስዎታል።")

@dp.message(Command("add"))
async def add_money(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    args = m.text.split()
    if len(args) < 3: return
    phone, amount = args[1], float(args[2])
    user = wallets.find_one({"phone": phone})
    if user:
        wallets.update_one({"phone": phone}, {"$inc": {"balance": amount}})
        if user.get("tg_id"):
            try: await bot.send_message(user["tg_id"], f"💰 {amount} ETB ባላንስዎ ላይ ተጨምሯል!")
            except: pass
        await m.answer("ተጠናቋል።")

async def run_bot(): await dp.start_polling(bot)

if __name__ == "__main__":
    Thread(target=lambda: app.run(host='0.0.0.0', port=PORT), daemon=True).start()
    asyncio.run(run_bot())

