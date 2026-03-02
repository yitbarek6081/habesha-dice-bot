import os, asyncio, random, time
from flask import Flask, render_template, jsonify
from threading import Thread
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo, InlineKeyboardButton

app = Flask(__name__)

# --- የጨዋታው ሁኔታ (Game State) ---
game_state = {
    "status": "waiting",  # waiting, lobby, running
    "start_time": 0,
    "drawn_numbers": [],
}

def generate_ticket():
    """15 ቁጥሮች በ 3 ረድፍ ያመነጫል"""
    selected = random.sample(range(1, 91), 15)
    selected.sort()
    return [selected[0:5], selected[5:10], selected[10:15]]

@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_ticket')
def get_ticket():
    return jsonify({"ticket": generate_ticket()})

@app.route('/game_status')
def get_status():
    remaining = 0
    if game_state["status"] == "lobby":
        remaining = max(0, 20 - (time.time() - game_state["start_time"]))
    return jsonify({
        "status": game_state["status"],
        "timer": int(remaining),
        "drawn": game_state["drawn_numbers"]
    })

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- ቴሌግራም ቦት ---
TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL") # Render ላይ የሰጠኸው URL

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start_game"))
async def start_lobby(message: types.Message):
    global game_state
    if game_state["status"] != "waiting":
        return await message.answer("⚠️ ጨዋታው ቀድሞውኑ ተጀምሯል!")

    game_state["status"] = "lobby"
    game_state["start_time"] = time.time()
    game_state["drawn_numbers"] = []
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎮 ተቀላቀል (Join Game)", web_app=WebAppInfo(url=WEB_APP_URL)))
    
    await message.answer("🔔 **የቶምቦላ መመዝገቢያ ተከፍቷል!**\nለ20 ሰከንድ ቆይቶ ጨዋታው ይጀምራል። አሁኑኑ 'Join' ይበሉ!", reply_markup=builder.as_markup())

    # 20 ሰከንድ መጠበቅ
    await asyncio.sleep(20)
    
    game_state["status"] = "running"
    await message.answer("🚀 **ጊዜው አልቋል! ቁጥሮች መውጣት ጀመሩ።**")
    
    # Auto-Draw (በየ 10 ሰከንዱ)
    nums = list(range(1, 91))
    random.shuffle(nums)
    for n in nums:
        if game_state["status"] != "running": break
        game_state["drawn_numbers"].append(n)
        await message.answer(f"🔢 የወጣው ቁጥር: 🔴 **{n}**")
        await asyncio.sleep(10)

@dp.message(lambda m: m.web_app_data is not None)
async def handle_win(message: types.Message):
    global game_state
    game_state["status"] = "waiting" # ጨዋታውን ያቆማል
    await message.answer(f"🏆 ቶምቦላ! {message.from_user.full_name} አሸንፏል! 🎉")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    Thread(target=run_flask, daemon=True).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
