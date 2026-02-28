import os
import asyncio
import sqlite3
import random
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("⚠️ BOT_TOKEN environment variable is not set!")

START_BALANCE = 500
BINGO_COLORS = ["🔴","🔵","🟢","🟡","🟣","🟠","🟤","⚪","⚫"]
TICKET_COST = 0         # Free play
ROUND_PRIZE = 100       # Prize points per round
DRAW_INTERVAL = 3       # Seconds per color draw
ROUND_DURATION = 60     # Seconds per round

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

# ================== DATABASE ==================
conn = sqlite3.connect("bingo_tournament.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT,
    balance INTEGER DEFAULT ?
)""", (START_BALANCE,))
conn.commit()

# ================== GAME STATE ==================
game_players = {}  # {user_id: {"needed": [...], "hits": 0}}
current_draw = ""
game_active = False
prize_pool = 0

# ================== MAIN MENU ==================
def get_main_menu_markup(balance):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🎮 Join Bingo Tournament", callback_data="join_game"),
        types.InlineKeyboardButton(f"💵 Balance: {balance}", callback_data="check_balance"),
        types.InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")
    )
    return markup

# ================== /START ==================
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT id, balance FROM users WHERE id=?", (user_id,))
    user = cursor.fetchone()
    
    if not user:
        cursor.execute("INSERT INTO users (id, name, balance) VALUES (?, ?, ?)",
                       (user_id, message.from_user.full_name, START_BALANCE))
        conn.commit()
        balance = START_BALANCE
        welcome = f"🎉 Welcome {message.from_user.full_name}! You have {START_BALANCE} free points to play Bingo Tournament!"
    else:
        balance = user[1]
        welcome = f"👋 Welcome back {message.from_user.full_name}! Your balance: {balance} points."
    
    await message.answer(welcome, reply_markup=get_main_menu_markup(balance))

# ================== JOIN GAME ==================
@dp.callback_query_handler(lambda c: c.data=="join_game")
async def join_game(c: types.CallbackQuery):
    global game_active, prize_pool
    user_id = c.from_user.id
    if user_id in game_players:
        return await bot.answer_callback_query(c.id,"✅ Already joined!")

    colors = random.sample(BINGO_COLORS, 9)
    msg = await bot.send_message(user_id, "🎯 Your Bingo Card:",
                                 reply_markup=types.InlineKeyboardMarkup(row_width=3).add(
                                     *[types.InlineKeyboardButton(color, callback_data=f"hit_{color}") for color in colors]
                                 ))
    game_players[user_id] = {"needed": colors, "hits": 0, "message_id": msg.message_id}

    # Optional: add ticket points to pool
    # prize_pool += TICKET_COST

    await bot.answer_callback_query(c.id, "🎮 You joined the Bingo Tournament!")

    # Start shared draw if not active
    if not game_active:
        asyncio.create_task(run_bingo_round())

# ================== RUN ROUND ==================
async def run_bingo_round():
    global current_draw, game_active, prize_pool
    game_active = True
    round_time = 0
    while round_time < ROUND_DURATION and game_players:
        current_draw = random.choice(BINGO_COLORS)
        for user_id in list(game_players.keys()):
            try:
                await bot.send_message(user_id,f"🎲 Drawn Color: {current_draw}")
            except:
                pass
        await asyncio.sleep(DRAW_INTERVAL)
        round_time += DRAW_INTERVAL
    # Round ends
    if game_players:  # No winner?
        for user_id in list(game_players.keys()):
            await bot.send_message(user_id,"⏰ Round ended! No winner this time.")
        game_players.clear()
    current_draw = ""
    game_active = False
    prize_pool = 0

# ================== HANDLE HITS ==================
@dp.callback_query_handler(lambda c: c.data.startswith("hit_"))
async def handle_hit(c: types.CallbackQuery):
    global game_players, current_draw, prize_pool
    user_id = c.from_user.id
    color = c.data.split("_")[1]
    player = game_players.get(user_id)
    if not player:
        return await bot.answer_callback_query(c.id,"⚠️ Not in game!")

    if color==current_draw and color in player["needed"]:
        player["needed"].remove(color)
        player["hits"] += 1
        await bot.answer_callback_query(c.id,f"✅ Hits: {player['hits']}/9")

        if player["hits"]==9:
            # Winner!
            cursor.execute("SELECT balance FROM users WHERE id=?",(user_id,))
            balance = cursor.fetchone()[0] + ROUND_PRIZE
            cursor.execute("UPDATE users SET balance=? WHERE id=?",(balance,user_id))
            conn.commit()
            await bot.send_message(user_id,f"🎊 BINGO! You won {ROUND_PRIZE} points!\n💵 Balance: {balance}")
            # Announce to others
            for other_id in list(game_players.keys()):
                if other_id!=user_id:
                    try:
                        await bot.send_message(other_id,f"🏆 {c.from_user.full_name} completed Bingo first!")
                    except:
                        pass
            game_players.clear()
    else:
        await bot.answer_callback_query(c.id,"❌ Wrong color!")

# ================== CHECK BALANCE ==================
@dp.callback_query_handler(lambda c: c.data=="check_balance")
async def check_balance(c: types.CallbackQuery):
    user_id = c.from_user.id
    cursor.execute("SELECT balance FROM users WHERE id=?",(user_id,))
    balance = cursor.fetchone()[0]
    await bot.answer_callback_query(c.id,f"💵 Balance: {balance} points",show_alert=True)

# ================== LEADERBOARD ==================
@dp.callback_query_handler(lambda c: c.data=="leaderboard")
async def leaderboard(c: types.CallbackQuery):
    cursor.execute("SELECT name,balance FROM users ORDER BY balance DESC LIMIT 5")
    top = cursor.fetchall()
    text = "🏆 Top 5 Players:\n\n" + "\n".join([f"{i+1}. {p[0]}: {p[1]} pts" for i,p in enumerate(top)])
    await bot.answer_callback_query(c.id,text,show_alert=True)

# ================== RUN BOT ==================
if __name__=="__main__":
    executor.start_polling(dp, skip_updates=True)
