import os, asyncio, random, time, json
from flask import Flask, render_template, jsonify, request
from threading import Thread
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo, InlineKeyboardButton

app = Flask(__name__)

# --- 1. CONFIGURATION (እነዚህን መረጃዎች ቀይር) ---
ADMIN_ID = 7956330391  # ያንተ የቴሌግራም ID ቁጥር
TELEBIRR_PHONE = "0945880474" # ያንተ የቴሌብር ቁጥር
CBEBIRR_PHONE = "0945880474"  # ያንተ የሲቢኢ ብር ቁጥር
ADMIN_NAME = "YITBAREK ABERA"      # ያንተ ስም (በባንክ አካውንትህ ያለ)

TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL")

# --- 2. DATA PERSISTENCE (ባላንስ እንዳይጠፋ በፋይል ሴቭ ያደርጋል) ---
user_wallets = {}
WALLETS_FILE = "wallets.json"

def load_data():
    global user_wallets
    try:
        if os.path.exists(WALLETS_FILE):
            with open(WALLETS_FILE, "r") as f:
                user_wallets = json.load(f)
    except: user_wallets = {}

def save_data():
    try:
        with open(WALLETS_FILE, "w") as f:
            json.dump(user_wallets, f)
    except: pass

load_data()

game_state = {
    "status": "lobby",
    "start_time": time.time(),
    "drawn_numbers": [],
    "players": {}, 
    "pot": 0,
    "winner_info": None
}

def generate_ticket():
    nums = random.sample(range(1, 91), 15)
    return [sorted(nums[0:5]), sorted(nums[5:10]), sorted(nums[10:15])]

# --- 3. FLASK API (Web App Communication) ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_ticket')
def get_ticket(): return jsonify({"ticket": generate_ticket()})

@app.route('/get_status')
def get_status():
    remaining = max(0, 25 - (time.time() - game_state["start_time"]))
    if game_state["status"] == "lobby" and remaining <= 0 and len(game_state["players"]) >= 2:
        game_state["status"] = "running"
    elif game_state["status"] == "lobby" and remaining <= 0:
        game_state["start_time"] = time.time()

    if game_state["status"] == "running" and len(game_state["drawn_numbers"]) < 90:
        new_num = random.randint(1, 90)
        if new_num not in game_state["drawn_numbers"]:
            game_state["drawn_numbers"].append(new_num)

    return jsonify({
        "status": game_state["status"], "timer": int(remaining),
        "drawn": game_state["drawn_numbers"], "pot": game_state["pot"],
        "players_count": len(game_state["players"]), "winner_info": game_state["winner_info"]
    })

@app.route('/user_data/<phone>')
def user_data(phone):
    p = phone.strip()
    return jsonify({"balance": user_wallets.get(p, 0.0), "is_joined": p in game_state["players"]})

@app.route('/join_game', methods=['POST'])
def join_game():
    data = request.json
    p = data.get("phone").strip()
    if user_wallets.get(p, 0) < 10: return jsonify({"success": False, "msg": "ባላንስ የሎትም!"})
    if p not in game_state["players"]:
        user_wallets[p] -= 10
        save_data()
        game_state["players"][p] = data.get("name", "Player")
        game_state["pot"] += 10
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/win', methods=['POST'])
def win():
    p = request.json.get("phone").strip()
    prize = game_state["pot"] * 0.80
    user_wallets[p] = user_wallets.get(p, 0) + prize
    save_data()
    game_state["winner_info"] = {"name": game_state["players"].get(p, "Player"), "amount": prize}
    game_state["status"] = "winner_display"
    def reset():
        time.sleep(7)
        game_state.update({"status":"lobby","start_time":time.time(),"drawn_numbers":[],"players":{},"pot":0,"winner_info":None})
    Thread(target=reset).start()
    return jsonify({"success": True})

# --- 4. TELEGRAM BOT HANDLERS ---
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🎮 Play Tombola", web_app=WebAppInfo(url=WEB_APP_URL)))
    kb.row(InlineKeyboardButton(text="💰 Deposit (ባላንስ ለመሙላት)", callback_data="deposit"))
    kb.row(InlineKeyboardButton(text="💸 Withdraw (ብር ለማውጣት)", callback_data="withdraw"))
    await m.answer(f"ሰላም {m.from_user.first_name}! እንኳን ወደ ቶምቦላ ጌም በደህና መጡ።", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "deposit")
async def dep_info(c: types.CallbackQuery):
    txt = (f"📍 **ባላንስ ለመሙላት የሚከተሉትን ይከተሉ፡**\n\n"
           f"📱 **Telebirr:** `{TELEBIRR_PHONE}`\n"
           f"🏦 **CBEBirr:** `{CBEBIRR_PHONE}`\n"
           f"👤 ስም: **{ADMIN_NAME}**\n\n"
           f"1️⃣ ብር ከላኩ በኋላ የደረሰኝ ፎቶ (Screenshot) እዚህ ይላኩ።\n"
           f"2️⃣ አድሚኑ ደረሰኙን አይቶ ባላንስ ይሞላሎታል።")
    await c.message.answer(txt, parse_mode="Markdown")
    await c.answer()

@dp.callback_query(F.data == "withdraw")
async def wit_info(c: types.CallbackQuery):
    txt = (f"📍 **ያሸነፉትን ብር ለማውጣት፡**\n\n"
           f"1. ማውጣት የሚፈልጉትን መጠን ይጻፉ።\n"
           f"2. የTelebirr ወይም የባንክ ቁጥርዎን ይጥቀሱ።\n\n"
           f"አድሚኑ መረጃውን አይቶ ብሩን ይልክልዎታል።")
    await c.message.answer(txt)
    await c.answer()

@dp.message(Command("add_credit"))
async def add(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    try:
        _, p, a = m.text.split()
        user_wallets[p.strip()] = user_wallets.get(p.strip(), 0) + float(a)
        save_data()
        await m.answer(f"✅ ለ {p} {a} ETB ተሞልቷል። አሁን ያለው ባላንስ: {user_wallets[p.strip()]}")
    except: await m.answer("አጠቃቀም: `/add_credit ስልክ መጠን` (ለምሳሌ: /add_credit 0925960226 50)")

async def main():
    port = int(os.environ.get("PORT", 10000))
    Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
