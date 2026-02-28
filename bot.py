import os
import asyncio
import sqlite3
import random
import time
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage

# --- 1. ኮንፊገሬሽን ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID"))
except (TypeError, ValueError):
    ADMIN_ID = 0  # አድሚን ID ካልተገኘ

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

# --- 2. ዳታቤዝ ማዘጋጀት ---
conn = sqlite3.connect('habesha_game_pro.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, phone TEXT, balance REAL DEFAULT 0)')
cursor.execute('CREATE TABLE IF NOT EXISTS receipts (file_id TEXT PRIMARY KEY, user_id INTEGER)')
cursor.execute('CREATE TABLE IF NOT EXISTS pool (id INTEGER PRIMARY KEY, current_prize REAL DEFAULT 0)')
cursor.execute('INSERT OR IGNORE INTO pool (id, current_prize) VALUES (1, 0)')
conn.commit()

# የጨዋታ ተለዋዋጮች
ALL_COLORS = ["🔴", "🟢", "🔵", "🟣", "🟡"]
ENTRY_FEE = 50.0      # መወራረጃ 50 ብር
PRIZE_PERCENT = 0.80   # 80% ለአሸናፊው (20% ለአንተ ኮሚሽን)
current_target = []
round_winners = set()
user_steps = {}

# --- 3. ሜኑ ማሳያ ፈንክሽን ---
async def show_main_menu(message_or_id, user_id=None):
    if isinstance(message_or_id, types.Message):
        chat_id = message_or_id.chat.id
        u_id = message_or_id.from_user.id
    else:
        chat_id = message_or_id
        u_id = user_id

    cursor.execute("SELECT balance FROM users WHERE id=?", (u_id,))
    row = cursor.fetchone()
    balance = row[0] if row else 0 # 0 if not registered
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🎮 PLAY (ውድድሩን ጀምር)", callback_data="btn_play"),
        types.InlineKeyboardButton("💰 DEPOSIT (ብር ሙላ)", callback_data="btn_deposit"),
        types.InlineKeyboardButton("💳 WITHDRAW (ብር አውጣ)", callback_data="btn_withdraw")
    )
    
    text = f"🏆 **HABESHA COLOR RACE**\n\n💵 ያሎት ባላንስ፦ **{balance}** ብር\n\nምን ማድረግ ይፈልጋሉ?"
    await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

# --- 4. ማስጀመሪያ እና ምዝገባ ---
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT * FROM users WHERE id=?", (user_id,))
    if cursor.fetchone():
        await show_main_menu(message)
    else:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add(types.KeyboardButton("📲 በስልክ ቁጥር ይመዝገቡ", request_contact=True))
        await message.answer("እንኳን ወደ HABESHA COLOR RACE በሰላም መጡ! ለመቀጠል እባክዎ ይመዝገቡ።", reply_markup=markup)

@dp.message_handler(content_types=['contact'])
async def handle_registration(message: types.Message):
    user_id = message.from_user.id
    phone = message.contact.phone_number
    name = message.from_user.full_name
    
    # በዳታቤዝ መመዝገብ
    cursor.execute("INSERT OR IGNORE INTO users (id, name, phone, balance) VALUES (?, ?, ?, 0)", (user_id, name, phone))
    conn.commit()
    
    # ለአድሚኑ ስልክ ቁጥሩን ብቻ መላክ
    if ADMIN_ID != 0:
        await bot.send_message(ADMIN_ID, f"📞 አዲስ ተመዝጋቢ፦ {phone}")

    await message.answer("✅ ምዝገባው ተሳክቷል! አሁን መጫወት ይችላሉ።", reply_markup=types.ReplyKeyboardRemove())
    
    # ወዲያውኑ ሜኑውን ማምጣት
    await show_main_menu(message.chat.id, user_id)

# --- 5. የጨዋታ ውድድር (15 ሰከንድ ቆጠራ) ---
async def start_game_round(msg, user_id):
    global current_target, round_winners
    round_winners.clear()
    current_target = random.sample(ALL_COLORS, len(ALL_COLORS))
    target_str = " ➔ ".join(current_target)
    
    for i in range(15, -1, -1):
        cursor.execute("SELECT balance FROM users WHERE id=?", (user_id,))
        row = cursor.fetchone()
        balance = row[0] if row else 0
        board_text = (
            f"🎮 **የቀለም ፍጥነት ውድድር**\n━━━━━━━━━━━━━━━\n"
            f"🎯 **ተልዕኮ:** `{target_str}`\n"
            f"💰 **ባላንስ:** {balance} ብር\n"
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
    await msg.edit_text("🚀 **START!** አሁን በፍጥነት ይጫኑ!", reply_markup=markup)

# --- 6. ቁልፎች (Callbacks) ---
@dp.callback_query_handler(lambda c: True)
async def handle_callbacks(c: types.CallbackQuery):
    u_id = c.from_user.id
    global round_winners

    if c.data == "btn_play":
        cursor.execute("SELECT balance FROM users WHERE id=?", (u_id,))
        row = cursor.fetchone()
        if not row or row[0] < ENTRY_FEE:
            await bot.answer_callback_query(c.id, "⚠️ በቂ ባላንስ የለዎትም!", show_alert=True)
            return
        
        cursor.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (ENTRY_FEE, u_id))
        cursor.execute("UPDATE pool SET current_prize = current_prize + ?", (ENTRY_FEE * PRIZE_PERCENT,))
        conn.commit()
        msg = await bot.send_message(c.message.chat.id, "🔄 ዙሩ እየተዘጋጀ ነው...")
        asyncio.create_task(start_game_round(msg, u_id))

    elif c.data.startswith('hit_'):
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

    elif c.data == "btn_deposit":
        await bot.send_message(c.message.chat.id, "💰 **ብር ለመሙላት**\n\nቴሌብር፦ `09xxxxxxxx` (ስም)\nሲቤኢ፦ `1000xxxxxxx` (ስም)\n\nደረሰኙን (Screenshot) እዚህ ይላኩ።")

    elif c.data == "btn_withdraw":
        await bot.send_message(c.message.chat.id, "💳 **ገንዘብ ለማውጣት**\n\nመጠን እና ስልክ ቁጥርዎን በዚህ መልኩ ይላኩ፦\n`500 - 0912345678`")
    
    await bot.answer_callback_query(c.id)

# --- 7. የደረሰኝ መቀበያ እና አድሚን Approval ---
@dp.message_handler(content_types=['photo'])
async def handle_receipt(message: types.Message):
    photo_id = message.photo[-1].file_unique_id
    cursor.execute("SELECT user_id FROM receipts WHERE file_id=?", (photo_id,))
    if cursor.fetchone():
        await message.reply("⚠️ ይህ ደረሰኝ ቀድሞ ጥቅም ላይ ውሏል!")
        return
    
    cursor.execute("INSERT INTO receipts (file_id, user_id) VALUES (?, ?)", (photo_id, message.from_user.id))
    conn.commit()
    
    markup = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("✅ 100 አጽድቅ", callback_data=f"aprv_{message.from_user.id}_100"),
        types.InlineKeyboardButton("✅ 500 አጽድቅ", callback_data=f"aprv_{message.from_user.id}_500"),
        types.InlineKeyboardButton("❌ ውድቅ አድርግ", callback_data=f"rejt_{message.from_user.id}")
    )
    if ADMIN_ID != 0:
        await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"💰 አዲስ ደረሰኝ ከ {message.from_user.full_name}", reply_markup=markup)
    await message.answer("📩 ደረሰኝዎ ተልኳል። አድሚኑ እስኪያጸድቅ ይጠብቁ።")

@dp.callback_query_handler(lambda c: c.data.startswith(('aprv_', 'rejt_')))
async def admin_action(c: types.CallbackQuery):
    data = c.data.split('_')
    action = data[0]
    uid = int(data[1])
    
    if action == "aprv":
        amt = float(data[2])
        cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amt, uid))
        conn.commit()
        await bot.send_message(uid, f"✅ {amt} ብር ባላንስዎ ላይ ተጨምሯል።")
        await bot.edit_message_caption(c.message.chat.id, c.message.message_id, caption="✅ የጸደቀ")
    elif action == "rejt":
        await bot.send_message(uid, "❌ የላኩት ደረሰኝ ውድቅ ተደርጓል።")
        await bot.edit_message_caption(c.message.chat.id, c.message.message_id, caption="❌ ውድቅ የተደረገ")
    await bot.answer_callback_query(c.id)

# --- 8. Withdraw Request ---
@dp.message_handler(lambda message: "-" in message.text and message.text.split("-")[0].strip().isdigit())
async def handle_withdraw(message: types.Message):
    parts = message.text.split("-")
    amt = float(parts[0].strip())
    phone = parts[1].strip()
    u_id = message.from_user.id
    
    cursor.execute("SELECT balance FROM users WHERE id=?", (u_id,))
    row = cursor.fetchone()
    if not row or row[0] < amt:
        await message.reply("❌ በቂ ባላንስ የለዎትም!")
        return

    cursor.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (amt, u_id))
    conn.commit()

    if ADMIN_ID != 0:
        await bot.send_message(ADMIN_ID, f"🚨 **Withdraw Request**\n💰 መጠን፦ {amt}\n📞 ስልክ፦ {phone}\n🆔 ID፦ `{u_id}`")
    await message.answer("📩 የክፍያ ጥያቄዎ ደርሶናል።")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
