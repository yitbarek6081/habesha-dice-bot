import os
import asyncio
import sqlite3
import random
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# ==================== CONFIG ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

# ==================== DATABASE ====================
conn = sqlite3.connect('habesha_game.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, 
    name TEXT, 
    phone TEXT,
    balance REAL DEFAULT 0,
    wins INTEGER DEFAULT 0
)''')
cursor.execute('CREATE TABLE IF NOT EXISTS pool (id INTEGER PRIMARY KEY, prize REAL DEFAULT 0)')
cursor.execute('INSERT OR IGNORE INTO pool (id, prize) VALUES (1, 0)')
conn.commit()

# ==================== GAME SETTINGS ====================
BINGO_COLORS = ["🔴", "🔵", "🟢", "🟡", "🟣", "🟠", "🟤", "⚪", "⚫"]
TICKET_PRICE = 10.0  # per round
lobby_players = [] 
user_game_state = {}
current_draw = None
is_counting_down = False
game_running = False

# ==================== FSM ====================
class Registration(StatesGroup):
    waiting_for_phone = State()

# ==================== MAIN MENU ====================
def get_main_menu_markup():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🎮 Play", callback_data="join_lobby"),
        types.InlineKeyboardButton("💰 Deposit", callback_data="deposit"),
        types.InlineKeyboardButton("💳 Withdraw", callback_data="withdraw")
    )
    return markup

# ==================== USER REGISTRATION ====================
@dp.message_handler(commands=['start'])
async def start_register(message: types.Message):
    cursor.execute("SELECT id FROM users WHERE id=?", (message.from_user.id,))
    if cursor.fetchone():
        cursor.execute("SELECT balance FROM users WHERE id=?", (message.from_user.id,))
        balance = cursor.fetchone()[0]
        await message.answer(f"👋 Welcome back!\n💰 Balance: {balance} ETB", reply_markup=get_main_menu_markup())
        return

    await message.answer("📱 Please send your phone number to register:")
    await Registration.waiting_for_phone.set()

@dp.message_handler(state=Registration.waiting_for_phone)
async def phone_received(message: types.Message, state: FSMContext):
    phone = message.text
    await state.update_data(phone=phone)

    admin_text = f"👤 New Registration\nName: {message.from_user.full_name}\nPhone: {phone}\nID: {message.from_user.id}"
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_{message.from_user.id}_{phone}"),
        types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_{message.from_user.id}")
    )
    await bot.send_message(ADMIN_ID, admin_text, reply_markup=markup)
    await message.answer("✅ Registration request sent to admin for approval.")
    await state.finish()

# ==================== ADMIN APPROVE/REJECT ====================
@dp.callback_query_handler(lambda c: c.data.startswith("approve_"))
async def admin_approve(c: types.CallbackQuery):
    _, user_id, phone = c.data.split("_")
    user_id = int(user_id)
    cursor.execute(
        "INSERT INTO users (id, name, phone, balance) VALUES (?, ?, ?, 10)",  # starting balance 10 ETB
        (user_id, "Player", phone)
    )
    conn.commit()
    await bot.send_message(user_id, "✅ Registration approved! You can now Play / Deposit / Withdraw.", reply_markup=get_main_menu_markup())
    await c.answer("User approved!")

@dp.callback_query_handler(lambda c: c.data.startswith("reject_"))
async def admin_reject(c: types.CallbackQuery):
    _, user_id = c.data.split("_")
    user_id = int(user_id)
    await bot.send_message(user_id, "❌ Registration rejected by admin.")
    await c.answer("User rejected!")

# ==================== DEPOSIT ====================
@dp.callback_query_handler(lambda c: c.data == "deposit")
async def deposit_menu(c: types.CallbackQuery):
    text = (
        "💰 Deposit Options:\n\n"
        "1️⃣ CBE: 091XXXXXXX\n"
        "2️⃣ TeleBirr: 092XXXXXXX\n\n"
        "📸 After deposit, send SMS screenshot to admin for approval.\n"
        "Include your user_id in the caption."
    )
    await bot.send_message(c.from_user.id, text)

@dp.message_handler(lambda m: m.chat.id == ADMIN_ID, content_types=types.ContentType.PHOTO)
async def admin_deposit_approve(message: types.Message):
    caption = message.caption or ""
    if "user_id:" in caption:
        user_id = int(caption.split("user_id:")[1])
        amount = 10  # or parse from caption/screenshot
        cursor.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, user_id))
        conn.commit()
        await bot.send_message(user_id, f"✅ Deposit approved! Your new balance is updated.")
        await message.reply("✅ Approved and balance updated.")

# ==================== MULTIPLAYER ROUND ====================
async def start_multiplayer_round():
    global lobby_players, is_counting_down, current_draw, game_running

    if len(lobby_players) < 1:
        return

    is_counting_down = True
    game_running = True
    current_round_players = lobby_players.copy()
    lobby_players.clear()

    # countdown
    for i in range(20,0,-5):
        for uid in current_round_players:
            try:
                cursor.execute("SELECT prize FROM pool WHERE id=1")
                pool = cursor.fetchone()[0]
                await bot.send_message(uid, f"⏳ Game starts in {i} seconds\n👥 Players: {len(current_round_players)}\n💰 Pool: {pool} ETB")
            except: pass
        await asyncio.sleep(5)
    is_counting_down = False

    # generate boards
    for uid in current_round_players:
        player_colors = random.sample(BINGO_COLORS, 9)
        cursor.execute("SELECT balance FROM users WHERE id=?", (uid,))
        current_bal = cursor.fetchone()[0]

        markup = types.InlineKeyboardMarkup(row_width=3)
        btns = [types.InlineKeyboardButton(color, callback_data=f"hit_{color}") for color in player_colors]
        markup.add(*btns)

        msg = await bot.send_message(uid,
            f"━━━━━━━━━━━━━━\n🎮 Habesha Win Board 🎮\n━━━━━━━━━━━━━━\n💰 Balance: {current_bal} ETB\n👥 Players: {len(current_round_players)}\n━━━━━━━━━━━━━━\n🎯 Status: Preparing...",
            reply_markup=markup
        )

        user_game_state[uid] = {
            "needed": player_colors,
            "hits": 0,
            "active": True,
            "total_p": len(current_round_players),
            "msg_id": msg.message_id,
            "markup": markup
        }

    # shared draw loop
    for _ in range(35):
        active_uids = [u for u in current_round_players if user_game_state.get(u, {}).get('active')]
        if not active_uids: break

        current_draw = random.choice(BINGO_COLORS)
        for uid in active_uids:
            try: 
                tmp_msg = await bot.send_message(uid, f"🔔 Draw: {current_draw}")
                asyncio.create_task(delete_msg(tmp_msg, 3))
            except: pass
        await asyncio.sleep(3.5)

async def delete_msg(msg, delay):
    await asyncio.sleep(delay)
    try: await msg.delete()
    except: pass

# ==================== PLAYER HIT ====================
@dp.callback_query_handler(lambda c: c.data.startswith("hit_"))
async def handle_hit(c: types.CallbackQuery):
    global current_draw, game_running
    uid = c.from_user.id
    color = c.data.split("_")[1]
    state = user_game_state.get(uid)

    if not game_running or not state or not state["active"]:
        return await c.answer("❌ Game not running or already finished!")

    if color == current_draw and color in state["needed"]:
        state["needed"].remove(color)
        state["hits"] += 1
        await update_player_board(uid)

        if state["hits"] == 9:
            state["active"] = False
            await end_game(uid)
    else:
        await c.answer("❌ Wrong color!")

async def update_player_board(uid):
    state = user_game_state.get(uid)
    if not state: return
    cursor.execute("SELECT balance FROM users WHERE id=?", (uid,))
    balance = cursor.fetchone()[0]

    new_text = (
        "━━━━━━━━━━━━━━\n"
        "🎮 Habesha Win Board 🎮\n"
        "━━━━━━━━━━━━━━\n"
        f"💰 Balance: {balance} ETB\n"
        f"👥 Players: {state['total_p']}\n"
        "━━━━━━━━━━━━━━\n"
        f"✅ Hits: {state['hits']}/9\n"
        f"🔥 Remaining: {9-state['hits']}"
    )
    try:
        await bot.edit_message_text(new_text, uid, state["msg_id"], reply_markup=state["markup"])
    except: pass

async def end_game(winner_id):
    global game_running
    cursor.execute("SELECT prize FROM pool WHERE id=1")
    prize = cursor.fetchone()[0]

    reward = prize * 0.8
    admin_commission = prize * 0.2

    cursor.execute("UPDATE users SET balance = balance + ?, wins = wins + 1 WHERE id=?", (reward, winner_id))
    cursor.execute("UPDATE pool SET prize = 0 WHERE id=1")
    conn.commit()

    for uid in user_game_state.keys():
        try:
            if uid == winner_id:
                await bot.send_message(uid, f"🏆 YOU WIN {reward} ETB!")
            else:
                await bot.send_message(uid, "❌ Game Over. Someone won!")
        except: pass

    game_running = False
    user_game_state.clear()

# ==================== JOIN LOBBY ====================
@dp.callback_query_handler(lambda c: c.data == "join_lobby")
async def join_lobby(c: types.CallbackQuery):
    u_id = c.from_user.id
    cursor.execute("SELECT balance FROM users WHERE id=?", (u_id,))
    balance = cursor.fetchone()[0]

    if balance < TICKET_PRICE:
        return await c.answer("⚠️ Not enough balance!", show_alert=True)

    cursor.execute("UPDATE users SET balance = balance - ? WHERE id=?", (TICKET_PRICE, u_id))
    cursor.execute("UPDATE pool SET prize = prize + ?", (TICKET_PRICE * 0.85,))
    conn.commit()

    if u_id in lobby_players:
        return await c.answer("⏳ Already joined the lobby.")
    lobby_players.append(u_id)
    await c.answer(f"✅ Joined! Players: {len(lobby_players)}")

    if not is_counting_down:
        asyncio.create_task(start_multiplayer_round())

# ==================== LEADERBOARD ====================
@dp.message_handler(commands=['leaderboard'])
async def leaderboard(message: types.Message):
    cursor.execute("SELECT name, balance, wins FROM users ORDER BY balance DESC LIMIT 10")
    top_players = cursor.fetchall()

    text = "🏆 **Global Leaderboard** 🏆\n\n"
    for i, (name, balance, wins) in enumerate(top_players, 1):
        text += f"{i}. {name} - 💰 {balance} ETB - 🏆 {wins} Wins\n"
    await message.answer(text, parse_mode="Markdown")

# ==================== RUN BOT ====================
if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
