import os
import asyncio
import sqlite3
import random
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage

# --- 1. ኮንፊገሬሽን ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
except:
    ADMIN_ID = 0

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

# --- 2. ዳታቤዝ ---
conn = sqlite3.connect('habesha_game.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, 
    name TEXT, 
    balance REAL DEFAULT 0,
    referred_by INTEGER)''')
cursor.execute('CREATE TABLE IF NOT EXISTS pool (id INTEGER PRIMARY KEY, prize REAL DEFAULT 0)')
cursor.execute('INSERT OR IGNORE INTO pool (id, prize) VALUES (1, 0)')
conn.commit()

BINGO_COLORS = ["🔴", "🔵", "🟢", "🟡", "🟣", "🟠", "🟤", "⚪", "⚫"]
TICKET_PRICE = 50.0
user_game_state = {}

# --- 3. ዋና ሜኑ (Main Menu Markup) ---
def get_main_menu_markup():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🎮 በቢንጎ ተጫወት (Play)", callback_data="start_bingo"),
        types.InlineKeyboardButton("💰 ብር ሙላ (Deposit)", callback_data="deposit"),
        types.InlineKeyboardButton("💳 ብር አውጣ (Withdraw)", callback_data="withdraw")
    )
    return markup

# --- 4. የ /start ትዕዛዝ እና ምዝገባ ---
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    args = message.get_args() # ለሪፈራል ሊንክ

    cursor.execute("SELECT id, balance FROM users WHERE id=?", (user_id,))
    user_data = cursor.fetchone()

    if not user_data:
        # አዲስ ተጠቃሚ ከሆነ መመዝገብ
        ref_id = int(args) if args and args.isdigit() else None
        cursor.execute("INSERT INTO users (id, name, balance, referred_by) VALUES (?, ?, 0, ?)", 
                       (user_id, message.from_user.full_name, ref_id))
        conn.commit()
        balance = 0
        welcome_msg = "🎉 **እንኳን ደስ አለዎት!**\nበተሳካ ሁኔታ ተመዝግበዋል።"
        if ref_id:
            try: await bot.send_message(ref_id, f"👤 አዲስ ሰው በእርስዎ ሊንክ ተመዝግቧል!")
            except: pass
    else:
        balance = user_data[1]
        welcome_msg = "👋 **እንኳን ደህና መጡ!**\nወደ COLOR BINGO ተመልሰዋል።"

    text = f"{welcome_msg}\n\n💵 ያሎት ባላንስ፦ **{balance} ETB**\n\nምን ማድረግ ይፈልጋሉ?"
    await message.answer(text, reply_markup=get_main_menu_markup(), parse_mode="Markdown")

# --- 5. የጨዋታ ሂደት (Game Logic) ---
async def start_bingo_logic(u_id, message_to_edit):
    cursor.execute("SELECT balance FROM users WHERE id=?", (u_id,))
    balance = cursor.fetchone()[0]
    
    if balance < TICKET_PRICE:
        return await bot.send_message(u_id, "⚠️ በቂ ባላንስ የለዎትም! እባክዎ መጀመሪያ ብር ይሙሉ::")

    cursor.execute("UPDATE users SET balance = balance - ? WHERE id=?", (TICKET_PRICE, u_id))
    cursor.execute("UPDATE pool SET prize = prize + ?", (TICKET_PRICE * 0.85,))
    conn.commit()

    player_colors = random.sample(BINGO_COLORS, 9)
    user_game_state[u_id] = {"needed": player_colors, "hits": 0, "active": True}

    # በቀጥታ መጫወቻ ሰሌዳውን (Grid) ማሳየት
    markup = types.InlineKeyboardMarkup(row_width=3)
    btns = [types.InlineKeyboardButton(color, callback_data=f"hit_{color}") for color in player_colors]
    markup.add(*btns)

    await bot.edit_message_text(
        chat_id=u_id,
        message_id=message_to_edit,
        text="🎯 **ቢንጎ ተጀምሯል!**\n\nየሚወጡትን ቀለሞች ከታች ካለው ሰሌዳዎ ላይ በፍጥነት ይጫኑ!",
        reply_markup=markup
    )
    asyncio.create_task(run_color_draw(u_id))

async def run_color_draw(user_id):
    for _ in range(25):
        if user_id not in user_game_state or not user_game_state[user_id]["active"]: break
        drawn = random.choice(BINGO_COLORS)
        user_game_state[user_id]["current"] = drawn
        msg = await bot.send_message(user_id, f"🎲 የወጣው ቀለም፦ {drawn}")
        await asyncio.sleep(3.5)
        try: await msg.delete()
        except: pass

@dp.callback_query_handler(lambda c: c.data.startswith("hit_"))
async def handle_hit(c: types.CallbackQuery):
    u_id = c.from_user.id
    color = c.data.split("_")[1]
    state = user_game_state.get(u_id)

    if state and color == state.get("current") and color in state["needed"]:
        state["needed"].remove(color)
        state["hits"] += 1
        await bot.answer_callback_query(c.id, f"✅ {state['hits']}/9")

        if state["hits"] == 9:
            state["active"] = False
            cursor.execute("SELECT prize FROM pool WHERE id=1")
            prize = cursor.fetchone()[0]
            cursor.execute("UPDATE users SET balance = balance + ? WHERE id=?", (prize, u_id))
            cursor.execute("UPDATE pool SET prize = 0")
            conn.commit()
            await bot.send_message(u_id, f"🎊 **BINGO!** {prize} ETB አሸንፈዋል!")
            user_game_state.pop(u_id, None)
    else:
        await bot.answer_callback_query(c.id, "❌ ቀለሙ አልወጣም!")

# --- 6. ሌሎች Callback Handlers ---
@dp.callback_query_handler(lambda c: c.data == "start_bingo")
async def btn_play(c: types.CallbackQuery):
    await start_bingo_logic(c.from_user.id, c.message.message_id)

@dp.callback_query_handler(lambda c: c.data == "deposit")
async def dep(c: types.CallbackQuery):
    await bot.send_message(c.from_user.id, "💰 **ብር ለመሙላት፦**\nበቴሌብር ብር ይላኩና የደረሰኙን ፎቶ እዚህ ይላኩ። አድሚኑ ሲያረጋግጥ ይጨምርልዎታል።")

@dp.callback_query_handler(lambda c: c.data == "withdraw")
async def wd_start(c: types.CallbackQuery):
    u_id = c.from_user.id
    cursor.execute("SELECT balance FROM users WHERE id=?", (u_id,))
    balance = cursor.fetchone()[0]
    if balance < 50: return await bot.answer_callback_query(c.id, "⚠️ አነስተኛው የማውጫ መጠን 50 ብር ነው!", show_alert=True)
    
    user_game_state[u_id] = {"step": "wd_amt"}
    await bot.send_message(u_id, "💵 ማውጣት የሚፈልጉትን መጠን ያስገቡ (Min 50):")

# (Withdrawal ሎጂክ እዚህ ይቀጥላል...)
@dp.message_handler(lambda m: user_game_state.get(m.from_user.id, {}).get("step") == "wd_amt")
async def wd_amt(m: types.Message):
    if not m.text.isdigit() or int(m.text) < 50: return await m.reply("❌ ትክክለኛ መጠን ያስገቡ (Min 50)።")
    user_game_state[m.from_user.id].update({"step": "wd_info", "amt": int(m.text)})
    await m.answer("📱 ብሩ የሚላክበትን ስልክ ቁጥር እና የባንክ ስም ይላኩ፦")

@dp.message_handler(lambda m: user_game_state.get(m.from_user.id, {}).get("step") == "wd_info")
async def wd_final(m: types.Message):
    u_id = m.from_user.id
    amt = user_game_state[u_id]["amt"]
    cursor.execute("UPDATE users SET balance = balance - ? WHERE id=?", (amt, u_id))
    conn.commit()
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("✅ ተከፈለ", callback_data=f"paid_{u_id}_{amt}"))
    await bot.send_message(ADMIN_ID, f"🚨 **ክፍያ ጥያቄ**\nሰው፦ {m.from_user.full_name}\nመጠን፦ {amt} ETB\nመረጃ፦ {m.text}", reply_markup=markup)
    await m.answer("✅ የክፍያ ጥያቄዎ ለአድሚን ደርሷል።")
    user_game_state.pop(u_id)

@dp.callback_query_handler(lambda c: c.data.startswith("paid_"))
async def admin_pay(c: types.CallbackQuery):
    _, uid, amt = c.data.split("_")
    await bot.send_message(uid, f"✅ የ {amt} ETB ክፍያዎ ተፈጽሟል።")
    await bot.edit_message_text(f"✅ ተከፍሏል ({amt} ETB)", c.message.chat.id, c.message.message_id)

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
