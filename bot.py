import os, asyncio, random, time, io, imagehash
from flask import Flask, render_template, jsonify, request
from threading import Thread
from PIL import Image
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo, InlineKeyboardButton

app = Flask(__name__)

# --- 1. CONFIG & DATABASE ---
ADMIN_ID = 7956330391  # በ @userinfobot ያገኘኸውን ቁጥር እዚህ ተካ
ADMIN_PHONE = "0945880474" # ያንተ የቴሌብር/ሲቢኢ ቁጥር
ADMIN_NAME = "Y A"
TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL ="https://habesha-dice-bot.onrender.com"
user_wallets = {}   # { "phone": balance }
used_receipts = set() 

game_state = {
    "status": "lobby",
    "start_time": time.time(),
    "drawn_numbers": [],
    "players": {}, # { phone: name }
    "pot": 0,
    "winner_info": None
}

def generate_ticket():
    nums = random.sample(range(1, 91), 15)
    return [sorted(nums[0:5]), sorted(nums[5:10]), sorted(nums[10:15])]

# --- 2. FLASK API ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_ticket')
def get_ticket():
    return jsonify({"ticket": generate_ticket()})

@app.route('/get_status')
def get_status():
    remaining = max(0, 20 - (time.time() - game_state["start_time"]))
    
    # ቢያንስ 2 ሰው ካለ ጨዋታው ይጀምራል
    if game_state["status"] == "lobby" and remaining <= 0 and len(game_state["players"]) >= 2:
        game_state["status"] = "running"
    elif game_state["status"] == "lobby" and remaining <= 0:
        game_state["start_time"] = time.time() # ሰው እስኪሞላ ሰዓቱን ያድሳል

    # ቁጥር የመጣል ሂደት
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
    return jsonify({"balance": user_wallets.get(phone, 0.0), "is_joined": phone in game_state["players"]})

@app.route('/join_game', methods=['POST'])
def join_game():
    data = request.json
    phone = data.get("phone")
    if user_wallets.get(phone, 0) < 10:
        return jsonify({"success": False, "msg": "Low Balance! 10 ብር የሎትም።"})
    
    if phone not in game_state["players"]:
        user_wallets[phone] -= 10
        game_state["players"][phone] = data.get("name")
        game_state["pot"] += 10
        return jsonify({"success": True, "balance": user_wallets[phone]})
    return jsonify({"success": False, "msg": "ቀድሞውኑ ገብተዋል።"})

@app.route('/win', methods=['POST'])
def win():
    global game_state
    data = request.json
    phone = data.get("phone")
    
    total = game_state["pot"]
    winner_share = total * 0.80 # 80% ለአሸናፊ (20% ኮሚሽን)
    user_wallets[phone] = user_wallets.get(phone, 0) + winner_share
    
    game_state["status"] = "winner_display"
    game_state["winner_info"] = {"name": data.get("name"), "row": data.get("row"), "amount": winner_share}
    
    def reset():
        time.sleep(5)
        game_state.update({"status": "lobby", "start_time": time.time(), "drawn_numbers": [], "players": {}, "pot": 0, "winner_info": None})
    Thread(target=reset).start()
    return jsonify({"success": True})

# --- 3. BOT LOGIC ---
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("add_credit"))
async def admin_add(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        _, phone, amount = message.text.split()
        user_wallets[phone] = user_wallets.get(phone, 0) + float(amount)
        await message.answer(f"✅ ለ {phone} {amount} ብር ተሞልቷል። ባላንስ: {user_wallets[phone]} ETB")
    except: await message.answer("❌ ስህተት! አጠቃቀም: `/add_credit ስልክ መጠን` (ለምሳሌ: /add_credit 0911223344 100)")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎮 Play Tombola", web_app=WebAppInfo(url=WEB_APP_URL)))
    builder.row(InlineKeyboardButton(text="💰 Deposit", callback_data="dep"), InlineKeyboardButton(text="💸 Withdraw", callback_data="wd"))
    await message.answer(f"ሰላም {message.from_user.first_name}! 🇪🇹\n\nባላንስ ለመሙላት Deposit ይጫኑ።", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "dep")
async def dep_info(c: types.CallbackQuery):
    msg = (f"📍 **የክፍያ መረጃ**\n\n"
           f"📞 ስልክ: `{ADMIN_PHONE}`\n"
           f"👤 ስም: {ADMIN_NAME}\n\n"
           f"እባክዎ ከከፈሉ በኋላ የደረሰኝ ፎቶ እዚህ ይላኩ።")
    await c.message.answer(msg, parse_mode="Markdown")

@dp.message(F.photo)
async def handle_receipt(message: types.Message):
    file = await bot.get_file(message.photo[-1].file_id)
    content = await bot.download_file(file.file_path)
    img_hash = str(imagehash.average_hash(Image.open(io.BytesIO(content.read()))))
    
    if img_hash in used_receipts:
        return await message.answer("⚠️ ይህ ደረሰኝ ቀድሞ ጥቅም ላይ ውሏል!")
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✅ Approve", callback_data=f"app_{message.from_user.id}_{img_hash}"))
    await bot.send_message(ADMIN_ID, f"📩 ደረሰኝ ከ @{message.from_user.username}")
    await bot.forward_message(ADMIN_ID, message.chat.id, message.message_id)
    await bot.send_message(ADMIN_ID, "ያጽድቁ?", reply_markup=kb.as_markup())
    await message.answer("ደረሰኙ ለAdmin ተልኳል...")

@dp.callback_query(F.data.startswith("app_"))
async def approve(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    _, uid, h = c.data.split("_")
    used_receipts.add(h)
    await bot.send_message(int(uid), "✅ ደረሰኝዎ ጸድቋል! አድሚኑ ባላንስዎን እስኪሞላ ይጠብቁ።")
    await c.message.edit_text("ጸድቋል ✅")

async def main():
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
