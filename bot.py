import os
import asyncio
import sqlite3
import random
import time
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryContextStorage

# --- 1. ኮንፊገሬሽን ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) # ያንተ የቴሌግራም ID ቁጥር

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryContextStorage())

# --- 2. ዳታቤዝ ማዘጋጀት ---
conn = sqlite3.connect('habesha_game_pro.db', check_same_thread=False)
cursor = conn.cursor()

# ሰንጠረዦችን መፍጠር
cursor.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, phone TEXT, balance REAL DEFAULT 0)')
cursor.execute('CREATE TABLE IF NOT EXISTS receipts (file_id TEXT PRIMARY KEY, user_id INTEGER)')
cursor.execute('CREATE TABLE IF NOT EXISTS pool (id INTEGER PRIMARY KEY, current_prize REAL DEFAULT 0)')
cursor.execute('INSERT OR IGNORE INTO pool (id, current_prize) VALUES (1, 0)')
conn.commit()

# የጨዋታ ተለዋዋጮች
ALL_COLORS = ["🔴", "🟢", "🔵", "🟣", "🟡"]
ENTRY_FEE = 50.0  # የመግቢያ ዋጋ
PRIZE_PERCENT = 0.80 # 80% ለአሸናፊው (20% ያንተ ኮሚሽን)
current_target = []
round_winners = set()
user_steps = {}

# --- 3. ማስጀመሪያ (Start & Register) ---
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT * FROM users WHERE id=?", (user_id,))
    if cursor.fetchone():
        await show_main_menu(message)
    else:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add(types.KeyboardButton("📲 በስልክ ቁጥር ይመዝገቡ", request_contact=True))
        await message.answer("እንኳን ወደ ጨዋታው በሰላም መጡ! ለመቀጠል እባክዎ ይመዝገቡ።", reply_markup=markup)

@dp.message_handler(content_types=['contact'])
async def handle_registration(message: types.Message):
    user_id = message.from_user.id
    name = message.from_user.full_name
    phone = message.contact.phone_number
    cursor.execute("INSERT OR IGNORE INTO users (id, name, phone, balance) VALUES (?, ?, ?, 0)", (user_id, name, phone))
    conn.commit()
    await message.answer("✅ ምዝገባው ተሳክቷል!", reply_markup=types.ReplyKeyboardRemove())
    await show_main_menu(message)

# --- 4. ዋና ሜኑ ---
async def show_main_menu(message: types.Message):
    cursor.execute("SELECT balance FROM users WHERE id=?", (message.from_user.id,))
    balance = cursor.fetchone()[2]
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🎮 PLAY (ወደ ጨዋታው)", callback_data="btn_play"),
        types.InlineKeyboardButton("💰 DEPOSIT (ብር ለመሙላት)", callback_data="btn_deposit"),
        types.InlineKeyboardButton("💳 WITHDRAW (ብር ለማውጣት)", callback_data="btn_withdraw")
    )
    await bot.send_message(message.chat.id, f"🏆 **HABESHA GAME CENTER**\n\n💵 ባላንስዎ፦ {balance} ብር\n\nምን ማድረግ ይፈልጋሉ?", reply_markup=markup)

# --- 5. DEPOSIT (ከደረሰኝ ክትትል ጋር) ---
@dp.callback_query_handler(lambda c: c.data == "btn_deposit")
async def deposit_info(c: types.CallbackQuery):
    msg = "💰 **ብር ለመሙላት**\n\n1. ቴሌብር፦ `09xxxxxxxx` (ስም)\n2. ሲቤኢ ብር፦ `1000xxxxxxx` (ስም)\n\nከከፈሉ በኋላ ደረሰኙን (Screenshot) እዚህ ይላኩ።"
    await bot.send_message(c.message.chat.id, msg, parse_mode="Markdown")
    await bot.answer_callback_query(c.id)

@dp.message_handler(content_types=['photo'])
async def handle_receipt(message: types.Message):
    photo_id = message.photo[-1].file_unique_id
    cursor.execute("SELECT user_id FROM receipts WHERE file_id=?", (photo_id,))
    if cursor.fetchone():
        await message.reply("⚠️ ይህ ደረሰኝ ቀድሞ ጥቅም ላይ ውሏል! ማጭበርበር አይቻልም።")
        return
    
    cursor.execute("INSERT INTO receipts (file_id, user_id) VALUES (?, ?)", (photo_id, message.from_user.id))
    conn.commit()
    
    admin_markup = types.InlineKeyboardMarkup()
    admin_markup.add(
        types.InlineKeyboardButton("✅ 100 አጽድቅ", callback_data=f"aprv_{message.from_user.id}_100"),
        types.InlineKeyboardButton("✅ 500 አጽድቅ", callback_data=f"aprv_{message.from_user.id}_500"),
        types.InlineKeyboardButton("❌ ውድቅ አድርግ", callback_data=f"rejt_{message.from_user.id}")
    )
    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"💰 አዲስ ደረሰኝ\nከ፦ {message.from_user.full_name}", reply_markup=admin_markup)
    await message.answer("📩 ደረሰኝዎ ተልኳል፤ አድሚኑ እስኪያጸድቅ ይጠብቁ።")

# --- 6. አጨዋወት (Play & Auto-Payout) ---
async def start_game_round(msg, user_id):
    global current_target, round_winners
    round_winners.clear()
    current_target = random.sample(ALL_COLORS, len(ALL_COLORS))
    target_str = " ➔ ".join(current_target)
    
    for i in range(15, -1, -1):
        board_text = (
            f"🎮 **የፍጥነት ውድድር**\n━━━━━━━━━━━━━━━\n"
            f"🎯 **ተልዕኮ:** `{target_str}`\n"
            f"⏳ **ቀሪ ጊዜ:** {i}s\n━━━━━━━━━━━━━━━\n"
            f"0 ሲደርስ በፍጥነት ይደርድሩ!"
        )
        try: await msg.edit_text(board_text, parse_mode="Markdown")
        except: pass
        await asyncio.sleep(1.2)
    
    markup = types.InlineKeyboardMarkup(row_width=3)
    btns = [types.InlineKeyboardButton(c, callback_data=f"hit_{c}") for c in ALL_COLORS]
    random.shuffle(btns)
    markup.add(*btns)
    await msg.edit_text("🚀 **START!** አሁን ይጫኑ!", reply_markup=markup)

@dp.callback_query_handler(lambda c: c.data == "btn_play")
async def play_init(c: types.CallbackQuery):
    cursor.execute("SELECT balance FROM users WHERE id=?", (c.from_user.id,))
    balance = cursor.fetchone()[2]
    if balance < ENTRY_FEE:
        await bot.answer_callback_query(c.id, "⚠️ በቂ ባላንስ የለዎትም!", show_alert=True)
        return
    
    cursor.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (ENTRY_FEE, c.from_user.id))
    cursor.execute("UPDATE pool SET current_prize = current_prize + ?", (ENTRY_FEE * PRIZE_PERCENT,))
    conn.commit()
    msg = await bot.send_message(c.message.chat.id, "🔄 ዙሩ እየተዘጋጀ ነው...")
    asyncio.create_task(start_game_round(msg, c.from_user.id))

@dp.callback_query_handler(lambda c: c.data.startswith('hit_'))
async def handle_hits(c: types.CallbackQuery):
    u_id = c.from_user.id
    color = c.data.split("_")[1]
    if u_id not in user_steps: user_steps[u_id] = {"step": 0, "start": time.time()}
    
    if color == current_target[user_steps[u_id]["step"]]:
        user_steps[u_id]["step"] += 1
        if user_steps[u_id]["step"] == 5:
            if not round_winners:
                round_winners.add(u_id)
                cursor.execute("SELECT current_prize FROM pool WHERE id=1")
                prize = cursor.fetchone()[0]
                cursor.execute("UPDATE users SET balance = balance + ? WHERE id=?", (prize, u_id))
                cursor.execute("UPDATE pool SET current_prize = 0")
                conn.commit()
                finish = round(time.time() - user_steps[u_id]["start"], 3)
                await bot.edit_message_text(f"🎊 **BINGO!** 🎊\n🏆 አሸናፊ፦ {c.from_user.first_name}\n⏱ ጊዜ፦ {finish}s\n💰 ሽልማት፦ {prize} ብር ተከፍሏል!", c.message.chat.id, c.message.message_id)
            else: await bot.answer_callback_query(c.id, "😔 ሌላ ሰው ቀድሞ ጨርሷል!")
            del user_steps[u_id]
    else:
        await bot.answer_callback_query(c.id, "❌ ተሳስተዋል!", show_alert=True)
        del user_steps[u_id]

# --- 7. አድሚን APPROVAL & WITHDRAW ---
@dp.callback_query_handler(lambda c: c.data.startswith(('aprv_', 'rejt_', 'btn_withdraw')))
async def admin_and_withdraw(c: types.CallbackQuery):
    if c.data.startswith('aprv_'):
        _, uid, amt = c.data.split('_')
        cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (float(amt), int(uid)))
        conn.commit()
        await bot.send_message(uid, f"✅ {amt} ብር ተጨምሮልዎታል።")
        await bot.edit_message_caption(c.message.chat.id, c.message.message_id, caption="✅ የጸደቀ")
    
    elif c.data == "btn_withdraw":
        await bot.send_message(c.message.chat.id, "💳 **ገንዘብ ለማውጣት**\n\nመጠን እና ስልክ ቁጥርዎን እንዲህ ይላኩ፦\n`500 - 0912345678`")

if __name__ == '__main__':
    executor.start_polling(dp)
