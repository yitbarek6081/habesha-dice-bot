import os
import asyncio
import random
from flask import Flask, render_template, jsonify
from threading import Thread
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo, InlineKeyboardButton

# --- 1. Flask & Game Data Setup ---
app = Flask(__name__)

# በጨዋታው ሂደት የወጡ ቁጥሮች እዚህ ይከማቻሉ
drawn_numbers = []
is_game_running = False

def generate_tombola_ticket():
    """ለአንድ ተጫዋች 15 ቁጥሮች በ 3 ረድፍ ያዘጋጃል"""
    all_nums = list(range(1, 91))
    selected = random.sample(all_nums, 15)
    selected.sort()
    return [selected[0:5], selected[5:10], selected[10:15]]

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_ticket')
def get_ticket():
    return jsonify({"ticket": generate_tombola_ticket()})

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- 2. Bot Setup ---
TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://habesha-dice-bot.onrender.com")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- 3. Game Handlers ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎮 ቶምቦላ ተጫወት", web_app=WebAppInfo(url=WEB_APP_URL)))
    await message.answer("እንኳን ወደ ሐበሻ ቶምቦላ በሰላም መጡ! 🇪🇹\nካርታዎን ለመቀበል ከታች ያለውን ቁልፍ ይጫኑ።", reply_markup=builder.as_markup())

@dp.message(Command("draw"))
async def cmd_draw(message: types.Message):
    global is_game_running, drawn_numbers
    if is_game_running:
        return await message.answer("ጨዋታው ቀድሞውኑ ተጀምሯል! ⏳")
    
    is_game_running = True
    drawn_numbers = []
    nums_to_draw = list(range(1, 91))
    random.shuffle(nums_to_draw)
    
    await message.answer("🎲 ቶምቦላ ተጀምሯል! ቁጥሮች መውጣት ጀመሩ...")
    
    for num in nums_to_draw:
        if not is_game_running: break
        drawn_numbers.append(num)
        await message.answer(f"የወጣው ቁጥር: 🔴 **{num}**")
        await asyncio.sleep(10) # በየ 10 ሴኮንዱ

@dp.message(Command("stop"))
async def cmd_stop(message: types.Message):
    global is_game_running
    is_game_running = False
    await message.answer("🛑 ጨዋታው ተቋርጧል።")

# --- 4. Win Verification (From Mini App) ---
@dp.message(lambda message: message.web_app_data is not None)
async def handle_webapp_data(message: types.Message):
    # ተጫዋቹ "አሸነፍኩ" ሲል ከ Mini App የሚመጣ ዳታ
    data = message.web_app_data.data # ለምሳሌ "claim_cinquina"
    user_name = message.from_user.full_name
    
    # እዚህ ጋር ተጫዋቹ በትክክል 5 ቁጥሮች በአንድ ረድፍ መያዙን በቦቱ በኩል እናረጋግጣለን
    # (ለአሁኑ ግን በቀጥታ አሸናፊነቱን እናውጃለን)
    if data == "claim_cinquina":
        global is_game_running
        is_game_running = False # አሸናፊ ሲገኝ ቁጥሮች ማውጣት ይቆማል
        await message.answer(f"🏆 ቶምቦላ! 🏆\n\nተጫዋች {user_name} የመጀመሪያውን ረድፍ (First Line) በመሙላት አሸንፏል! 🎊")

# --- 5. Main Execution ---
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    Thread(target=run_flask, daemon=True).start()
    print("✅ ቦቱ እና ዌብ አፑ ስራ ጀምረዋል!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
