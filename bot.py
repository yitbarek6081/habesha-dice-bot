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
ADMIN_ID = 7956330391 # @userinfobot ላይ ያገኙትን ቁጥር እዚህ ይተኩ
TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL")

user_wallets = {}   # { "phone": balance }
used_receipts = set() # የደረሰኝ አሻራዎች (Hashes)

game_state = {
    "status": "lobby",
    "start_time": time.time(),
    "drawn_numbers": [],
    "players": {}, # { phone: name }
    "pot": 0,
    "winner_info": None
}

# --- 2. FLASK API (ለዌብ አፑ) ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status():
    remaining = max(0, 20 - (time.time() - game_state["start_time"]))
    if game_state["status"] == "lobby" and remaining <= 0 and len(game_state["players"]) >= 2:
        game_state["status"] = "running"
    elif game_state["status"] == "lobby" and remaining <= 0:
        game_state["start_time"] = time.time()
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

@app.route('/disjoin_game', methods=['POST'])
def disjoin_game():
    phone = request.json.get("phone")
    if phone in game_state["players"]:
        del game_state["players"][phone]
        user_wallets[phone] += 10
        game_state["pot"] -= 10
        return jsonify({"success": True, "balance": user_wallets[phone]})
    return jsonify({"success": False})

@app.route('/win', methods=['POST'])
def win():
    global game_state
    data = request.json
    phone = data.get("phone")
    winner_share = game_state["pot"] * 0.80 # 80% ለአሸናፊ
    user_wallets[phone] = user_wallets.get(phone, 0) + winner_share
    game_state["status"] = "winner_display"
    game_state["winner_info"] = {"name": data.get("name"), "row": data.get("row"), "amount": winner_share}
    def reset():
        time.sleep(5) # 5 ሰከንድ አሸናፊውን አሳይቶ ሪሴት ያደርጋል
        game_state.update({"status": "lobby", "start_time": time.time(), "drawn_numbers": [], "players": {}, "pot": 0, "winner_info": None})
    Thread(target=reset).start()
    return jsonify({"success": True})

# --- 3. BOT HANDLERS ---
bot = Bot(token=TOKEN)
dp = Dispatcher()

# አድሚን በስልክ ቁጥር ብር መሙያ (/add_credit 0911223344 100)
@dp.message(Command("add_credit"))
async def admin_add(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        parts = message.text.split()
        phone, amount = parts[1], float(parts[2])
        user_wallets[phone] = user_wallets.get(phone, 0) + amount
        await message.answer(f"✅ ለ {phone} {amount} ብር ተሞልቷል። ባላንስ: {user_wallets[phone]}")
    except: await message.answer("❌ አጠቃቀም: /add_credit ስልክ_ቁጥር መጠን")

@dp.message(Command("start"))
async def start(message: types.Message):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🎮 Play Tombola", web_app=WebAppInfo(url=WEB_APP_URL)))
    kb.row(InlineKeyboardButton(text="💰 Deposit", callback_data="dep"), InlineKeyboardButton(text="💸 Withdraw", callback_data="wd"))
    await message.answer(f"ሰላም {message.from_user.first_name}! እንኳን ደህና መጡ።", reply_markup=kb.as_markup())

# ደረሰኝ መቀበያ (Deposit)
@dp.callback_query(F.data == "dep")
async def dep_info(c: types.CallbackQuery):
    await c.message.answer("📍 ቴሌብር/CBE: `0945880474` (ስም)\nእባክዎ ከከፈሉ በኋላ የደረሰኝ ፎቶ (Screenshot) እዚህ ይላኩ።")

@dp.message(F.photo)
async def handle_receipt(message: types.Message):
    file = await bot.get_file(message.photo[-1].file_id)
    content = await bot.download_file(file.file_path)
    img_hash = str(imagehash.average_hash(Image.open(io.BytesIO(content.read()))))
    
    if img_hash in used_receipts:
        return await message.answer("⚠️ ይህ ደረሰኝ ቀድሞ ጥቅም ላይ ውሏል!")

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✅ Approve", callback_data=f"app_{message.from_user.id}_{img_hash}"))
    kb.row(InlineKeyboardButton(text="❌ Reject", callback_data=f"rej_{message.from_user.id}"))
    
    await bot.send_message(ADMIN_ID, f"📩 ደረሰኝ ከ @{message.from_user.username}")
    await bot.forward_message(ADMIN_ID, message.chat.id, message.message_id)
    await bot.send_message(ADMIN_ID, "ያጽድቁ?", reply_markup=kb.as_markup())
    await message.answer("ደረሰኝዎ ለAdmin ተልኳል፤ ሲረጋገጥ መልዕክት ይደርስዎታል።")

@dp.callback_query(F.data.startswith(("app_", "rej_")))
async def admin_decision(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    data = c.data.split("_")
    uid = int(data[1])
    if data[0] == "app":
        used_receipts.add(data[2])
        await bot.send_message(uid, "✅ ክፍያዎ ጸድቋል! አድሚኑ ባላንስዎ ላይ ብር እንዲጨምር ስልክ ቁጥርዎን ይላኩለት።")
        await c.message.edit_text("ጸድቋል ✅")
    else:
        await bot.send_message(uid, "❌ ደረሰኝዎ ውድቅ ተደርጓል።")
        await c.message.edit_text("ውድቅ ተደርጓል ❌")

# ዊዝድሮው (Withdraw)
@dp.callback_query(F.data == "wd")
async def withdraw_req(c: types.CallbackQuery):
    await c.message.answer("💸 ብር ለማውጣት የብር መጠን እና ስልክ ቁጥር ይላኩ።\nምሳሌ፦ `500 ብር በ 0911223344 ይላክልኝ`")

@dp.message(F.text.contains("ይላክልኝ"))
async def forward_wd(message: types.Message):
    await bot.send_message(ADMIN_ID, f"🚨 የዊዝድሮው ጥያቄ ከ @{message.from_user.username}:\n{message.text}")
    await message.answer("ጥያቄዎ ደርሷል፤ አድሚኑ ሲልክልዎ መልዕክት ይደርስዎታል።")

async def main():
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())


