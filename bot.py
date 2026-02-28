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
    ADMIN_ID = 0

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

ALL_COLORS = ["🔴", "🟢", "🔵", "🟣", "🟡"]
ENTRY_FEE = 50.0      
PRIZE_PERCENT = 0.80   
current_target = []
round_winners = set()
user_steps = {}

# --- 3. Dashboard (Main Menu) ማሳያ ---
async def show_main_menu(chat_id, user_id):
    cursor.execute("SELECT balance FROM users WHERE id=?", (user_id,))
    row = cursor.fetchone()
    balance = row[0] if row else 0
    
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
        await show_main_menu(message.chat.id, user_id)
    else:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add(types.KeyboardButton("📲 በስልክ ቁጥር ይመዝገቡ", request_contact=True))
        await message.answer("እንኳን ወደ HABESHA COLOR RACE በሰላም መጡ! ለመቀጠል እባክዎ ይመዝገቡ።", reply_markup=markup)

@dp.message_handler(content_types=['contact'])
async def handle_registration(message: types.Message):
    user_id = message.from_user.id
    phone = message.contact.phone_number
    name = message.from_user.full_name
    
    cursor.execute("INSERT OR IGNORE INTO users (id, name, phone, balance) VALUES (?, ?, ?, 0)", (user_id, name, phone))
    conn.commit()
    
    if ADMIN_ID != 0:
        await bot.send_message(ADMIN_ID, f"📞 አዲስ ተመዝጋቢ፦ {phone}")

    await message.answer("✅ ምዝገባው ተሳክቷል! አሁን መጫወት ይችላሉ።", reply_markup=types.ReplyKeyboardRemove())
    await show_main_menu(message.chat.id, user_id)

# --- 5. የጨዋታ ቆጠራ (አሁን ባላንስ ባይኖርም ይሰራል) ---
async def start_game_round(msg, user_id):
    global current_target, round_winners
    round_winners.clear()
    current_target = random.sample(ALL_COLORS, len(ALL_COLORS))
    target_str = " ➔ ".join(current_target)
    
    for i in range(15, -1, -1):
        board_text = f"🎮 **የቀለም ፍጥነት ውድድር**\n━━━━━━━━━━━━━━━\n🎯 **ተልዕኮ:** `{target_str}`\n⏳ **ቀሪ ጊዜ:** {i}s\n━━━━━━━━━━━━━━━"
        try: await msg.edit_text(board_text, parse_mode="Markdown")
        except: pass
        await asyncio.sleep(1.2)
    
    markup = types.InlineKeyboardMarkup(row_width=3)
    btns = [types.InlineKeyboardButton(c, callback_data=f"hit_{c}") for c in ALL_COLORS]
    random.shuffle(btns)
    markup.add(*btns)
    await msg.edit_text("🚀 **START!** በፍጥነት ይጫኑ!", reply_markup=markup)

# --- 6. ቁልፎች (Callbacks) ---
@dp.callback_query_handler(lambda c: True)
async def handle_callbacks(c: types.CallbackQuery):
    u_id = c.from_user.id
    global round_winners

    if c.data == "btn_play":
        # ባላንስ ሳይረጋገጥ በቀጥታ ወደ ጨዋታው ቦርድ ይገባል
        msg = await bot.send_message(c.message.chat.id, "🔄 ዙሩ እየተዘጋጀ ነው...")
        asyncio.create_task(start_game_round(msg, u_id))

    elif c.data.startswith('hit_'):
        color = c.data.split("_")[1]
        if u_id not in user_steps: user_steps[u_id] = {"step": 0, "start": time.time()}
        
        if color == current_target[user_steps[u_id]["step"]]:
            user_steps[u_id]["step"] += 1
            if user_steps[u_id]["step"] == 5:
                # ተጫዋቹ ሲያሸንፍ ባላንሱን እዚህ ጋር እናረጋግጣለን
                cursor.execute("SELECT balance FROM users WHERE id=?", (u_id,))
                balance = cursor.fetchone()[0]
                
                if balance < ENTRY_FEE:
                    await bot.send_message(c.message.chat.id, "⚠️ ጨዋታውን ጨርሰሃል! ነገር ግን ለመወራረድ በቂ ባላንስ ስለሌለህ ሽልማቱን ማግኘት አትችልም። እባክህ ብር ሙላ።")
                    await show_main_menu(c.message.chat.id, u_id)
                else:
                    if not round_winners:
                        round_winners.add(u_id)
                        # ብር ቀንሶ ሽልማቱን መስጠት
                        cursor.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (ENTRY_FEE, u_id))
                        cursor.execute("UPDATE pool SET current_prize = current_prize + ?", (ENTRY_FEE * PRIZE_PERCENT,))
                        
                        cursor.execute("SELECT current_prize FROM pool WHERE id=1")
                        prize = cursor.fetchone()[0]
                        cursor.execute("UPDATE users SET balance = balance + ? WHERE id=?", (prize, u_id))
                        cursor.execute("UPDATE pool SET current_prize = 0")
                        conn.commit()
                        
                        finish = round(time.time() - user_steps[u_id]["start"], 3)
                        await bot.edit_message_text(f"🎊 **BINGO!** 🎊\n🏆 አሸናፊ፦ {c.from_user.first_name}\n⏱ ጊዜ፦ {finish}s\n💰 ሽልማት፦ {prize} ብር ተከፍሏል!", c.message.chat.id, c.message.message_id)
                    else:
                        await bot.answer_callback_query(c.id, "😔 ሌላ ሰው ቀድሞ ጨርሷል!")
                del user_steps[u_id]
        else:
            await bot.answer_callback_query(c.id, "❌ ተሳስተዋል!", show_alert=True)
            del user_steps[u_id]

    elif c.data == "btn_deposit":
        await bot.send_message(c.message.chat.id, "💰 ደረሰኝ እዚህ ይላኩ።")

    elif c.data == "btn_withdraw":
        await bot.send_message(c.message.chat.id, "💳 መጠን እና ስልክ ቁጥር ይላኩ (ምሳሌ፦ 500 - 0912...)")
    
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
        types.InlineKeyboardButton("✅ 500 አጽድቅ", callback_data=f"aprv_{message.from_user.id}_500")
    )
    if ADMIN_ID != 0:
        await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"💰 አዲስ ደረሰኝ ከ {message.from_user.full_name}", reply_markup=markup)
    await message.answer("📩 ደረሰኝዎ ተልኳል፤ አድሚኑ እስኪያጸድቅ ይጠብቁ።")

@dp.callback_query_handler(lambda c: c.data.startswith('aprv_'))
async def approve_payment(c: types.CallbackQuery):
    _, uid, amt = c.data.split('_')
    cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (float(amt), int(uid)))
    conn.commit()
    await bot.send_message(uid, f"✅ {amt} ብር ተጨምሮልዎታል።")
    await bot.edit_message_caption(c.message.chat.id, c.message.message_id, caption="✅ የጸደቀ")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
