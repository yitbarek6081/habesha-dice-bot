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
    
    # አሸናፊ ከተገኘ ከ 5 ሰከንድ በኋላ ጌሙን ሪሴት ያደርጋል
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
        
        # ቢያንስ 2 ሰው ከሌለ ታይመሩ በየ 20 ሰከንዱ ራሱን ያድሳል
        if timer == 0:
            if player_count >= 2:
                game_state.update({"status": "running", "last_draw_time": now})
                random.shuffle(game_state["available_numbers"])
            else:
                game_state["start_countdown"] = now # ታይመሩን ደግሞ ይጀምረዋል
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

# --- አዲስ፡ ተጫዋቹ ሳይጀመር እንዲወጣ (Unjoin) ---
@app.route('/unjoin_game', methods=['POST'])
def unjoin():
    p = str(request.json['phone'])
    if p in game_state["players"] and game_state["status"] == "lobby":
        wallets.update_one({"phone": p}, {"$inc": {"balance": 10}}) # 10 ብር ይመለሳል
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
        
        # የኮሚሽን ስሌት (80% ለአሸናፊው)
        total_pot = game_state["pot"]
        win_amount = total_pot * 0.8  # 80%
        house_commission = total_pot * 0.2 # 20%
        
        wallets.update_one({"phone": p}, {"$inc": {"balance": win_amount}})
        game_state["last_draw_time"] = time.time()
        
        # ለአድሚኑ ማሳወቅ
        if bot_loop:
            msg = f"🏆 **አሸናፊ ተገኝቷል!**\n👤 ስም: `{game_state['winner']}`\n💰 ጠቅላላ: `{total_pot} ETB`\n💵 አሸናፊ ድርሻ (80%): `{win_amount} ETB`\n🏠 ኮሚሽን (20%): `{house_commission} ETB`"
            asyncio.run_coroutine_threadsafe(bot.send_message(ADMIN_ID, msg, parse_mode="Markdown"), bot_loop)
            
        return jsonify({"success": True, "amount": win_amount})
    return jsonify({"success": False})

# ... (ቀሪው request_action እና የBot Commands እንዳለ ይቀጥላል) ...
