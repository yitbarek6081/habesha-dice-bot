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

ADMIN_ID = 7956330391             # <--- ያንተን የቴሌግራም ID ቁጥር እዚህ ተካ
TELEBIRR_PHONE = "0945880474"   # <--- ያንተን የቴሌብር ስልክ እዚህ ተካ
CBEBIRR_PHONE = "0945880474"    # <--- ያንተን የሲቢኢ ብር ስልክ እዚህ ተካ
ADMIN_NAME = "yitbarek abera"         # <--- ያንተን ስም እዚህ ተካ

# --- 2. INITIALIZE ---
bot = Bot(token=TOKEN)
dp = Dispatcher()
app = Flask(__name__)

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
            with open(WALLETS_FILE, "r") as f: user_wallets = json.load(f)
        except: user_wallets = {}

def save_data():
    with open(WALLETS_FILE, "w") as f: json.dump(user_wallets, f)

load_data()

game_state = {
    "status": "lobby",
    "start_time": time.time(),
    "drawn_numbers": [],
    "players": {},
    "pot": 0,
    "last_draw_time": 0
}

def generate_ticket():
    all_nums = random.sample(range(1, 91), 15)
    return [sorted(all_nums[0:5]), sorted(all_nums[5:10]), sorted(all_nums[10:15])]

# --- 3. FLASK API ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    now = time.time()
    remaining = max(0, 25 - (now - game_state["start_time"]))
    
    if game_state["status"] == "lobby" and remaining <= 0 and len(game_state["players"]) >= 2:
        game_state["status"] = "running"
        game_state["drawn_numbers"] = []
        game_state["last_draw_time"] = now
    elif game_state["status"] == "lobby" and remaining <= 0:
        game_state["start_time"] = now

    if game_state["status"] == "running" and len(game_state["drawn_numbers"]) < 90:
        if now - game_state["last_draw_time"] >= 3:
            new_num = random.randint(1, 90)
            while new_num in game_state["drawn_numbers"]:
                new_num = random.randint(1, 90)
            game_state["drawn_numbers"].append(new_num)
            game_state["last_draw_time"] = now

    return jsonify({
        "status": game_state["status"], "timer": int(remaining),
        "drawn": game_state["drawn_numbers"], "pot": game_state["pot"],
        "players_count": len(game_state["players"])
    })

@app.route('/user_data/<phone>')
def user_data(phone):
    p = clean_phone(phone)
    player = game_state["players"].get(p)
    return jsonify({
        "balance": user_wallets.get(p, 0.0),
        "is_joined": p in game_state["players"],
        "ticket": player["ticket"] if player else None
    })

@app.route('/join_game', methods=['POST'])
def join_game():
    p = clean_phone(request.json.get("phone"))
    if user_wallets.get(p, 0) < 10: return jsonify({"success": False, "msg": "ባላንስ የሎትም!"})
    if p not in game_state["players"]:
        user_wallets[p] -= 10
        save_data()
        ticket = generate_ticket()
        game_state["players"][p] = {"name": request.json.get("name", "Player"), "ticket": ticket}
        game_state["pot"] += 10
        return jsonify({"success": True, "ticket": ticket})
    return jsonify({"success": False, "msg": "ገብተዋል!"})

@app.route('/win', methods=['POST'])
def win():
    p = clean_phone(request.json.get("phone"))
    player_data = game_state["players"].get(p)
    if not player_data: return jsonify({"success": False, "msg": "ተጫዋቹ አልተገኘም!"})

    ticket = player_data["ticket"]
    drawn = game_state["drawn_numbers"]
    
    is_legit = False
    for row in ticket:
        if all(num in drawn for num in row):
            is_legit = True
            break
    
    if is_legit:
        prize = game_state["pot"] * 0.80
        user_wallets[p] = user_wallets.get(p, 0) + prize
        save_data()
        name = player_data["name"]
        game_state.update({"status":"lobby", "start_time":time.time(), "drawn_numbers":[], "players":{}, "pot":0})
        return jsonify({"success": True, "winner": name, "prize": prize})
    return jsonify({"success": False, "msg": "ገና አልጨረሱም!"})

# --- 4. TELEGRAM BOT ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    url = WEB_APP_URL if WEB_APP_URL.endswith("/") else f"{WEB_APP_URL}/"
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🎮 Play Tombola", web_app=WebAppInfo(url=url)))
    await m.answer(f"ሰላም {m.from_user.first_name}!", reply_markup=kb.as_markup())

@dp.message(Command("add_credit"))
async def add_c(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    try:
        _, p, amt = m.text.split()
        p = clean_phone(p); user_wallets[p] = user_wallets.get(p, 0) + float(amt)
        save_data(); await m.answer(f"✅ ተሞልቷል። ባላንስ: {user_wallets[p]}")
    except: await m.answer("አጠቃቀም: /add_credit 09... 50")

async def main():
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
