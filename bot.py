# ይህ ኮድ ከፍተኛ ባህሪያት ያካትታል።
import os
import asyncio
import sqlite3
import random
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

conn = sqlite3.connect("habesha_game.db", check_same_thread=False)
cursor = conn.cursor()

# --- Tables ---
cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT,
    balance REAL DEFAULT 0,
    wins INTEGER DEFAULT 0
)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS pool (
    id INTEGER PRIMARY KEY,
    prize REAL DEFAULT 0
)''')
cursor.execute('INSERT OR IGNORE INTO pool (id, prize) VALUES (1,0)')
conn.commit()

BINGO_COLORS = ["🔴","🔵","🟢","🟡","🟣","🟠","🟤","⚪","⚫"]
lobby_players = []
user_game_state = {}
game_running = False
TICKET_PRICE = 0  # free start

# --- Helpers ---
def get_pool():
    cursor.execute("SELECT prize FROM pool WHERE id=1")
    return cursor.fetchone()[0]

def get_main_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🎮 Play", callback_data="join_lobby"),
        types.InlineKeyboardButton("💰 Deposit", callback_data="deposit"),
        types.InlineKeyboardButton("💳 Withdraw", callback_data="withdraw")
    )
    return markup

async def update_player_board(uid):
    state = user_game_state[uid]
    cursor.execute("SELECT balance FROM users WHERE id=?", (uid,))
    balance = cursor.fetchone()[0]
    pool = get_pool()
    text = (
        f"━━━━━━━━━━━━━━\n"
        f"🎮 Habesha Win Board 🎮\n"
        f"━━━━━━━━━━━━━━\n"
        f"💰 Balance: {balance} ETB\n"
        f"👥 Players: {state['total_p']}\n💰 Pool: {pool}\n"
        f"━━━━━━━━━━━━━━\n"
        f"✅ Hits: {state['hits']}/9\n"
        f"🔥 Remaining: {9 - state['hits']}"
    )
    try:
        await bot.edit_message_text(text, uid, state["msg_id"], reply_markup=state["markup"])
    except: pass

async def animate_hit(uid, color):
    try:
        for _ in range(2):
            await bot.edit_message_text(f"🎯 HIT! {color}", uid, user_game_state[uid]["msg_id"])
            await asyncio.sleep(0.3)
            await update_player_board(uid)
            await asyncio.sleep(0.3)
    except: pass

async def end_game(uid):
    state = user_game_state[uid]
    prize = get_pool()
    cursor.execute("UPDATE users SET balance = balance + ?, wins = wins + 1 WHERE id=?", (prize, uid))
    cursor.execute("UPDATE pool SET prize = 0 WHERE id=1")
    conn.commit()
    await bot.send_message(uid, f"🎉 YOU WIN! Prize: {prize} ETB")
    await update_player_board(uid)

# --- Game loop ---
async def start_multiplayer_round():
    global lobby_players, game_running
    game_running = True
    current_players = lobby_players.copy()
    lobby_players = []

    for uid in current_players:
        player_colors = random.sample(BINGO_COLORS, 9)
        markup = types.InlineKeyboardMarkup(row_width=3)
        btns = [types.InlineKeyboardButton(c, callback_data=f"hit_{c}") for c in player_colors]
        markup.add(*btns)
        msg = await bot.send_message(uid, "🎮 Game Start! Hit the colors as they appear!", reply_markup=markup)
        user_game_state[uid] = {
            "needed": player_colors.copy(),
            "hits":0,
            "active":True,
            "total_p":len(current_players),
            "msg_id": msg.message_id,
            "markup": markup
        }

    available_colors = BINGO_COLORS * 5
    random.shuffle(available_colors)
    drop_count = 0

    for drop_color in available_colors:
        if drop_count >= 40:
            break
        drop_count += 1

        for uid, state in user_game_state.items():
            if not state["active"]:
                continue
            if drop_color in state["needed"]:
                state["needed"].remove(drop_color)
                state["hits"] += 1
                asyncio.create_task(animate_hit(uid, drop_color))
                await update_player_board(uid)
                if state["hits"] == 9:
                    state["active"] = False
                    await end_game(uid)
        # Notify drop
        for uid in user_game_state.keys():
            try:
                tmp_msg = await bot.send_message(uid, f"🔔 Drop: {drop_color}")
                asyncio.create_task(bot.delete_message(uid, tmp_msg.message_id))
            except: pass
        await asyncio.sleep(3)

    game_running = False

# --- Handlers ---
@dp.callback_query_handler(lambda c: c.data=="join_lobby")
async def join_lobby(c: types.CallbackQuery):
    uid = c.from_user.id
    if uid not in lobby_players:
        lobby_players.append(uid)
        await c.answer(f"✅ Joined! Players: {len(lobby_players)}")
    else:
        await c.answer("⏳ Already joined")

    if not game_running:
        asyncio.create_task(start_multiplayer_round())

@dp.callback_query_handler(lambda c: c.data.startswith("hit_"))
async def hit_color(c: types.CallbackQuery):
    uid = c.from_user.id
    color = c.data.split("_")[1]
    state = user_game_state.get(uid)
    if state and state["active"] and color in state["needed"]:
        state["needed"].remove(color)
        state["hits"] += 1
        await animate_hit(uid, color)
        await update_player_board(uid)
        if state["hits"] == 9:
            state["active"] = False
            await end_game(uid)
    else:
        await c.answer("❌ Not your color!")

@dp.message_handler(commands=['start'])
async def cmd_start(m: types.Message):
    uid = m.from_user.id
    cursor.execute("SELECT id FROM users WHERE id=?", (uid,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (id, name) VALUES (?,?)", (uid, m.from_user.full_name))
        conn.commit()
    await m.answer("👋 Welcome! Start playing free!", reply_markup=get_main_menu())

@dp.callback_query_handler(lambda c: c.data=="deposit")
async def deposit(c: types.CallbackQuery):
    text = (
        "💰 Deposit Instructions:\n\n"
        "CBE: 09XXXXXXXX\n"
        "Telebirr: 09YYYYYYYY\n\n"
        "Send screenshot with your user ID to Admin for approval."
    )
    await c.message.answer(text)

@dp.message_handler(lambda m: m.chat.id == ADMIN_ID, content_types=['photo','document'])
async def admin_approve_screenshot(m: types.Message):
    try:
        caption = m.caption
        user_id = int(caption.split("user:")[1].split()[0])
        amount = float(caption.split("amount:")[1].split()[0])
        cursor.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, user_id))
        conn.commit()
        await bot.send_message(user_id, f"✅ Deposit Approved! +{amount} ETB")
        await m.reply("✅ User balance updated successfully")
    except Exception as e:
        await m.reply(f"❌ Failed: {str(e)}")

@dp.callback_query_handler(lambda c: c.data=="withdraw")
async def withdraw(c: types.CallbackQuery):
    text = "💳 Withdraw Instructions:\nSend /withdraw <amount>, Admin will approve."
    await c.message.answer(text)

@dp.message_handler(commands=['withdraw'])
async def withdraw_request(m: types.Message):
    try:
        user_id = m.from_user.id
        amount = float(m.text.split()[1])
        cursor.execute("SELECT balance FROM users WHERE id=?", (user_id,))
        balance = cursor.fetchone()[0]
        if balance < amount:
            await m.reply("❌ Insufficient balance")
            return
        await bot.send_message(ADMIN_ID, f"Withdraw request: User {user_id} Amount {amount}")
        await m.reply("✅ Withdraw request sent to admin for approval")
    except:
        await m.reply("❌ Invalid format. Use /withdraw <amount>")

@dp.message_handler(commands=['leaderboard'])
async def leaderboard(m: types.Message):
    cursor.execute("SELECT name, balance, wins FROM users ORDER BY balance DESC LIMIT 10")
    top = cursor.fetchall()
    text = "🏆 Leaderboard 🏆\n"
    for i, (name,balance,wins) in enumerate(top,1):
        text += f"{i}. {name} - 💰{balance} ETB - 🏆{wins} wins\n"
    await m.answer(text)

if __name__=="__main__":
    executor.start_polling(dp, skip_updates=True)
