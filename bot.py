import os, asyncio, random, time
from flask import Flask, render_template, jsonify, request
from threading import Thread
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo, InlineKeyboardButton

app = Flask(__name__)

# --- 1. CONFIG (የእርስዎን ID እዚህ ያስገቡ) ---
ADMIN_ID =7956330391  # በ @userinfobot ያገኙትን ቁጥር እዚህ ይተኩ
TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL")

# --- 2. GAME STATE ---
game_state = {
    "status": "lobby", 
    "start_time": time.time(),
    "drawn_numbers": [],
    "players": [],
    "balance": 0.00,
    "winner_info": None
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

@app.route('/join_action', methods=['POST'])
def join_action():
    game_state["players"].append(time.time())
    return jsonify({"success": True})

@app.route('/declare_winner', methods=['POST'])
def declare_winner():
    global game_state
    data = request.json
    game_state["status"] = "winner_display"
    game_state["winner_info"] = {"row_index": data.get("row_index"), "user_name": data.get("user_name")}
    
    def reset_loop():
        time.sleep(5) # 5 ሰከንድ አሳይቶ ወደ ሎቢ ይመለሳል
        game_state.update({"status": "lobby", "start_time": time.time(), "drawn_numbers": [], "players": [], "winner_info": None})
    
    Thread(target=reset_loop).start()
    return jsonify({"success": True})

@app.route('/game_status')
def get_status():
    remaining = max(0, 20 - (time.time() - game_state["start_time"]))
    if game_state["status"] == "lobby" and remaining <= 0 and len(game_state["players"]) >= 2:
        game_state["status"] = "running"
    elif game_state["status"] == "lobby" and remaining <= 0:
        game_state["start_time"] = time.time()
    return jsonify({
        "status": game_state["status"], "timer": int(remaining), "drawn": game_state["drawn_numbers"],
        "players_count": len(game_state["players"]), "balance": game_state["balance"], "winner_info": game_state["winner_info"]
    })

# --- 4. BOT HANDLERS ---
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎮 ተጫወት (Play)", web_app=WebAppInfo(url=WEB_APP_URL)))
    builder.row(
        InlineKeyboardButton(text="💰 Deposit", callback_data="deposit"),
        InlineKeyboardButton(text="💸 Withdraw", callback_data="withdraw")
    )
    await message.answer("እንኳን ወደ ሐበሻ ቶምቦላ መጡ! 🇪🇹\nለመጫወት Play የሚለውን ይጫኑ።", reply_markup=builder.as_markup())

# --- 5. MANUAL PAYMENT SYSTEM (Deposit/Withdraw) ---
@dp.callback_query(lambda c: c.data == "deposit")
async def dep_opts(c: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📱 telebirr", callback_data="p_tele"), InlineKeyboardButton(text="🏦 CBEBirr", callback_data="p_cbe"))
    await c.message.answer("ብር የሚያስገቡበትን ባንክ ይምረጡ:", reply_markup=builder.as_markup())

@dp.callback_query(lambda c: c.data.startswith("p_"))
async def p_inst(c: types.CallbackQuery):
    acc = "0945880474" if "tele" in c.data else "0945880474"
    await c.message.answer(f"📍 አካውንት: `{acc}`\nእባክዎ የደረሰኝ ፎቶ (Screenshot) እዚህ ይላኩ።")

@dp.message(lambda m: m.photo is not None)
async def handle_receipt(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Approve", callback_data=f"ok_{message.from_user.id}"))
    builder.row(InlineKeyboardButton(text="❌ Reject", callback_data=f"no_{message.from_user.id}"))
    
    await bot.send_message(ADMIN_ID, f"📩 አዲስ ደረሰኝ ከ @{message.from_user.username}")
    await bot.forward_message(ADMIN_ID, message.chat.id, message.message_id)
    await bot.send_message(ADMIN_ID, "ደረሰኙን ያጸድቃሉ?", reply_markup=builder.as_markup())
    await message.answer("ደረሰኝዎ ለአድሚን ተልኳል፤ ሲረጋገጥ መልዕክት ይደርስዎታል።")

@dp.callback_query(lambda c: c.data.startswith(("ok_", "no_")))
async def admin_decision(c: types.CallbackQuery):
    uid = int(c.data.split("_")[1])
    if "ok_" in c.data:
        await bot.send_message(uid, "✅ ክፍያዎ ተረጋግጧል! አሁን መጫወት ይችላሉ።")
        await c.message.edit_text("ጸድቋል ✅")
    else:
        await bot.send_message(uid, "❌ ደረሰኝዎ ውድቅ ተደርጓል። እባክዎ ደግመው ይላኩ።")
        await c.message.edit_text("ውድቅ ተደርጓል ❌")

@dp.callback_query(lambda c: c.data == "withdraw")
async def withdraw_req(c: types.CallbackQuery):
    await c.message.answer("💸 **ብር ለማውጣት**\nየብር መጠን እና የባንክ አካውንትዎን በዚህ መልኩ ይላኩ፡\nምሳሌ፦ `500 ብር በ 0911223344 telebirr ይላክልኝ`")

@dp.message(lambda m: "ይላክልኝ" in m.text)
async def forward_wd(message: types.Message):
    await bot.send_message(ADMIN_ID, f"🚨 የብር ማውጫ ጥያቄ ከ @{message.from_user.username}፡\n\n{message.text}")
    await message.answer("ጥያቄዎ ለAdmin ደርሷል።")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
