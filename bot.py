import os, asyncio, random, time, json
from flask import Flask, render_template, jsonify, request
from threading import Thread
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo, InlineKeyboardButton, FSInputFile

# --- 1. CONFIGURATION (እነዚህን ብቻ ቀይር) ---
TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL")

ADMIN_ID = 7956330391            # <--- ያንተን የቴሌግራም ID ቁጥር እዚህ ተካ
TELEBIRR_PHONE = "0945880474"   # <--- ያንተን የቴሌብር ስልክ እዚህ ተካ
CBEBIRR_PHONE = "0945880474"    # <--- ያንተን የሲቢኢ ብር ስልክ እዚህ ተካ
ADMIN_NAME = "yitbarek abera"         # <--- ያንተን ስም እዚህ ተካ

# --- 2. INITIALIZE BOT & DISPATCHER (ስህተቱን ለመከላከል እዚህ መሆን አለባቸው) ---
bot = Bot(token=TOKEN)
dp = Dispatcher()
app = Flask(__name__)

# --- 3. DATA HANDLING ---
user_wallets = {}
WALLETS_FILE = "wallets.json"

def clean_phone(phone):
    if not phone: return ""
    p = str(phone).strip().replace(" ", "").replace("+", "").replace("-", "")
    if p.startswith("251"): p = "0" + p[3:]
    return p

def load_data():
    global user_wallets
    if os.path.exists(WALLETS_FILE):
        try:
            with open(WALLETS_FILE, "r") as f:
                user_wallets = json.load(f)
        except: user_wallets = {}

def save_data():
    with open(WALLETS_FILE, "w") as f:
        json.dump(user_wallets, f)

load_data()

game_state = {"status": "lobby", "start_time": time.time(), "drawn_numbers": [], "players": {}, "pot": 0}

# --- 4. TELEGRAM BOT HANDLERS ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    url = WEB_APP_URL if WEB_APP_URL.endswith("/") else f"{WEB_APP_URL}/"
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎮 Play Tombola", web_app=WebAppInfo(url=url)))
    builder.row(InlineKeyboardButton(text="💰 Deposit (ብር ለመሙላት)", callback_data="deposit"))
    await m.answer(f"ሰላም {m.from_user.first_name}! እንኳን ወደ ቶምቦላ በደህና መጡ።", reply_markup=builder.as_markup())

@dp.message(Command("add_credit"))
async def admin_add(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    try:
        _, p_input, amount = m.text.split()
        p = clean_phone(p_input)
        user_wallets[p] = user_wallets.get(p, 0) + float(amount)
        save_data()
        await m.answer(f"✅ ለ {p} {amount} ETB ተሞልቷል።\nአጠቃላይ ባላንስ: {user_wallets[p]}")
    except: await m.answer("አጠቃቀም: `/add_credit 09... 50`")

@dp.message(Command("list_players"))
async def list_p(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    if not user_wallets: return await m.answer("ምንም ተጫዋች የለም።")
    txt = "📊 **የተጫዋቾች ዝርዝር፡**\n"
    for p, b in user_wallets.items(): txt += f"📞 `{p}`: {b} ETB\n"
    await m.answer(txt, parse_mode="Markdown")

@dp.message(Command("backup"))
async def send_backup(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    save_data()
    if os.path.exists(WALLETS_FILE):
        await m.answer_document(FSInputFile(WALLETS_FILE), caption="የባላንስ ባክአፕ")

@dp.callback_query(F.data == "deposit")
async def dep_info(c: types.CallbackQuery):
    txt = (f"📱 **Telebirr:** `{TELEBIRR_PHONE}`\n"
           f"🏦 **CBEBirr:** `{CBEBIRR_PHONE}`\n"
           f"👤 ስም: **{ADMIN_NAME}**")
    await c.message.answer(txt, parse_mode="Markdown")
    await c.answer()

# --- 5. FLASK API ROUTES ---
@app.route('/')
def index(): return render_template('index.html')

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
    return jsonify({"status": game_state["status"], "timer": int(remaining), "drawn": game_state["drawn_numbers"], "pot": game_state["pot"], "players_count": len(game_state["players"])})

@app.route('/user_data/<phone>')
def user_data(phone):
    p = clean_phone(phone)
    return jsonify({"balance": user_wallets.get(p, 0.0), "is_joined": p in game_state["players"]})

@app.route('/join_game', methods=['POST'])
def join_game():
    p = clean_phone(request.json.get("phone"))
    if user_wallets.get(p, 0) < 10: return jsonify({"success": False, "msg": "ባላንስ የሎትም!"})
    if p not in game_state["players"]:
        user_wallets[p] -= 10
        save_data()
        game_state["players"][p] = request.json.get("name", "Player")
        game_state["pot"] += 10
        return jsonify({"success": True})
    return jsonify({"success": False})

# --- 6. RUN SERVER ---
async def main():
    port = int(os.environ.get("PORT", 10000))
    Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
