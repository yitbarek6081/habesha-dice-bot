import os, asyncio, random, time, json
from flask import Flask, render_template, jsonify, request
from threading import Thread
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo, InlineKeyboardButton

# --- 1. CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL")

ADMIN_ID = 7956330391            # ያንተ ቴሌግራም ID
TELEBIRR_PHONE = "0945880474"   
CBEBIRR_PHONE = "0945880474"    

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = Flask(__name__)

user_wallets = {}
WALLETS_FILE = "wallets.json"

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
    "last_draw_time": 0,
    "winner_name": None
}

# --- 2. FLASK API ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    now = time.time()
    if game_state["status"] == "lobby":
        remaining = 20 - int((now - game_state["start_time"]) % 20)
        if remaining == 1 and len(game_state["players"]) >= 2:
            game_state["status"] = "running"
            game_state["drawn_numbers"] = []
            game_state["last_draw_time"] = now
    else: remaining = 0

    if game_state["status"] == "running" and not game_state["winner_name"]:
        if now - game_state["last_draw_time"] >= 4 and len(game_state["drawn_numbers"]) < 90:
            new_num = random.randint(1, 90)
            while new_num in game_state["drawn_numbers"]: new_num = random.randint(1, 90)
            game_state["drawn_numbers"].append(new_num)
            game_state["last_draw_time"] = now

    return jsonify({
        "status": game_state["status"], "timer": int(remaining),
        "drawn": game_state["drawn_numbers"], "pot": game_state["pot"],
        "players_count": len(game_state["players"]), "winner": game_state["winner_name"]
    })

@app.route('/user_data/<phone>')
def user_data(phone):
    player = game_state["players"].get(phone)
    return jsonify({
        "balance": user_wallets.get(phone, 0.0),
        "is_joined": phone in game_state["players"],
        "ticket": player["ticket"] if player else None
    })

@app.route('/withdraw_request', methods=['POST'])
async def withdraw_request():
    phone = request.json.get("phone")
    bal = user_wallets.get(phone, 0)
    await bot.send_message(ADMIN_ID, f"⚠️ **የማውጫ ጥያቄ!**\n\nተጫዋች: {phone}\nባላንስ: {bal} ETB\nእባክዎ ለተጫዋቹ ይላኩ።")
    return jsonify({"success": True})

@app.route('/join_game', methods=['POST'])
def join_game():
    phone = request.json.get("phone")
    if user_wallets.get(phone, 0) < 10: return jsonify({"success": False, "msg": "ባላንስ የሎትም!"})
    if phone not in game_state["players"] and game_state["status"] == "lobby":
        user_wallets[phone] -= 10
        save_data()
        all_nums = random.sample(range(1, 91), 15)
        ticket = [sorted(all_nums[0:5]), sorted(all_nums[5:10]), sorted(all_nums[10:15])]
        game_state["players"][phone] = {"name": request.json.get("name", "Player"), "ticket": ticket}
        game_state["pot"] += 10
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/win', methods=['POST'])
def win():
    phone = request.json.
