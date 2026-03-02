import os, asyncio, random, time
from flask import Flask, render_template, jsonify
from threading import Thread
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo, InlineKeyboardButton

app = Flask(__name__)

# --- 1. CONFIGURATION ---
ADMIN_ID = 7956330391  # በ @userinfobot ያገኘኸውን ቁጥር እዚህ ተካ
TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL")

# --- 2. GAME STATE ---
game_state = {
    "status": "waiting",
    "start_time": 0,
    "drawn_numbers": [],
}

def generate_ticket():
    selected = random.sample(range(1, 91), 15)
    selected.sort()
    return [selected[0:5], selected[5:10], selected[10:15]]

# --- 3. FLASK ROUTES ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_ticket')
def get_ticket(): return jsonify({"ticket": generate_ticket()})

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
    # Render Port Fix: ሬንደር የሚሰጠውን PORT በራሱ ያነባል
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- 4. BOT HANDLERS ---
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎮 ቶምቦላ ተጫወት (Play)", web_app=WebAppInfo(url=WEB_APP_URL)))
    builder.row(
        InlineKeyboardButton(text="💰 Deposit", callback_data="deposit"),
        InlineKeyboardButton(text="💸 Withdraw", callback_data="withdraw")
    )
    await message.answer("እንኳን ወደ ሐበሻ ቶምቦላ መጡ! 🇪🇹\nለመጫወት Play የሚለውን ይጫኑ።", reply_markup=builder.as_markup())

@dp.message(Command("start_game"))
async def start_lobby(message: types.Message):
    global game_state
    if game_state["status"] != "waiting": return
    
    game_state["status"] = "lobby"
    game_state["start_time"] = time.time()
    game_state["drawn_numbers"] = []
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎮 ተቀላቀል (Join Game)", web_app=WebAppInfo(url=WEB_APP_URL)))
    await message.answer("🔔 መመዝገቢያ ተከፍቷል (20 ሰከንድ ቀርቷል)!", reply_markup=builder.as_markup())

    await asyncio.sleep(20)
    game_state["status"] = "running"
    await message.answer("🚀 መመዝገቢያ ተዘግቷል! ጨዋታው ተጀመረ።")
    
    nums = list(range(1, 91)); random.shuffle(nums)
    for n in nums:
        if game_state["status"] != "running": break
        game_state["drawn_numbers"].append(n)
        await message.answer(f"🔢 የወጣው ቁጥር: 🔴 **{n}**")
        await asyncio.sleep(10)

# --- 5. PAYMENT HANDLERS ---
@dp.callback_query(lambda c: c.data == "deposit")
async def dep_opts(c: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📱 telebirr", callback_data="p_tele"), InlineKeyboardButton(text="🏦 CBEBirr", callback_data="p_cbe"))
    await c.message.answer("ብር የሚያስገቡበትን ባንክ ይምረጡ:", reply_markup=builder.as_markup())

@dp.callback_query(lambda c: c.data.startswith("p_"))
async def p_inst(c: types.CallbackQuery):
    acc = "0911223344" if "tele" in c.data else "100012345678"
    await c.message.answer(f"📍 አካውንት: `{acc}`\nእባክዎ የደረሰኝ ፎቶ (Screenshot) እዚህ ይላኩ።")

@dp.message(lambda m: m.photo is not None)
async def handle_receipt(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Approve", callback_data=f"ok_{message.from_user.id}"))
    await bot.send_message(ADMIN_ID, f"📩 አዲስ ደረሰኝ ከ @{message.from_user.username}")
    await bot.forward_message(ADMIN_ID, message.chat.id, message.message_id)
    await bot.send_message(ADMIN_ID, "ደረሰኙን ያጸድቃሉ?", reply_markup=builder.as_markup())
    await message.answer("ደረሰኝዎ ለአድሚን ተልኳል፤ ሲረጋገጥ መልዕክት ይደርስዎታል።")

@dp.callback_query(lambda c: c.data.startswith("ok_"))
async def admin_ok(c: types.CallbackQuery):
    uid = int(c.data.split("_")[1])
    await bot.send_message(uid, "✅ ክፍያዎ ተረጋግጧል! አሁን መጫወት ይችላሉ።")
    await c.message.edit_text("ጸድቋል ✅")

async def main():
    # የቆዩ ግንኙነቶችን ያጸዳል
    await bot.delete_webhook(drop_pending_updates=True)
    # Flask ሰርቨሩን በሌላ Thread ያስጀምራል
    Thread(target=run_flask, daemon=True).start()
    # ቦቱን ያስጀምራል
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
