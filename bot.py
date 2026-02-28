import os
import asyncio
import sqlite3
import random
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))  # Your Telegram ID
if not BOT_TOKEN or not ADMIN_ID:
    raise ValueError("⚠️ BOT_TOKEN or ADMIN_ID not set in environment!")

START_BALANCE = 500
BINGO_COLORS = ["🔴","🔵","🟢","🟡","🟣","🟠","🟤","⚪","⚫"]
ROUND_COST = 10
ADMIN_PERCENT = 0.2
DRAW_INTERVAL = 3
JOIN_COUNTDOWN = 15

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

# ================= DATABASE =================
conn = sqlite3.connect("bingo_tournament.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute(f"""CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT,
    balance INTEGER DEFAULT {START_BALANCE},
    phone TEXT
)""")
conn.commit()

# ================= GAME STATE =================
waiting_players = {}  # {user_id: full_name}
game_players = {}     # {user_id: {"needed": [...], "hits":0, "message_id":id}}
current_draw = ""
round_pool = 0
game_active = False

# ================= MAIN MENU =================
def get_main_menu(balance):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🎮 Join Bingo Round", callback_data="join_game"),
        types.InlineKeyboardButton(f"💵 Balance: {balance}", callback_data="check_balance"),
        types.InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard"),
        types.InlineKeyboardButton("📱 Register Phone", callback_data="register_phone")
    )
    return markup

# ================= /START =================
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT id, balance FROM users WHERE id=?", (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.execute("INSERT INTO users (id,name,balance) VALUES (?,?,?)",
                       (user_id,message.from_user.full_name,START_BALANCE))
        conn.commit()
        balance = START_BALANCE
        welcome = f"🎉 Welcome {message.from_user.full_name}! You have {START_BALANCE} points to play Bingo!"
    else:
        balance = user[1]
        welcome = f"👋 Welcome back {message.from_user.full_name}! Your balance: {balance} points."
    await message.answer(welcome, reply_markup=get_main_menu(balance))

# ================= REGISTER PHONE =================
@dp.callback_query_handler(lambda c: c.data=="register_phone")
async def register_phone_button(c: types.CallbackQuery):
    await bot.send_message(c.from_user.id,"📱 Send your phone number using this format:\n`/phone 0912345678`", parse_mode="Markdown")

@dp.message_handler(commands=["phone"])
async def register_phone(message: types.Message):
    args = message.get_args().strip()
    if not args.isdigit():
        return await message.reply("⚠️ Usage: /phone <number>")
    phone = args
    user_id = message.from_user.id
    cursor.execute("UPDATE users SET phone=? WHERE id=?", (phone,user_id))
    conn.commit()
    await message.reply(f"✅ Phone number {phone} registered successfully!")

# ================= JOIN GAME =================
@dp.callback_query_handler(lambda c: c.data=="join_game")
async def join_game(c: types.CallbackQuery):
    global waiting_players
    user_id = c.from_user.id
    cursor.execute("SELECT balance FROM users WHERE id=?",(user_id,))
    balance = cursor.fetchone()[0]
    if balance < ROUND_COST:
        return await bot.answer_callback_query(c.id,"⚠️ Not enough points!")

    if user_id in waiting_players or user_id in game_players:
        return await bot.answer_callback_query(c.id,"✅ Already joined!")

    waiting_players[user_id] = c.from_user.full_name
    await bot.answer_callback_query(c.id,f"🎮 You joined! Players joined: {len(waiting_players)}")

    if not game_active:
        asyncio.create_task(join_countdown())

# ================= JOIN COUNTDOWN WITH LIVE UPDATES =================
async def join_countdown():
    global waiting_players, game_players, round_pool, game_active

    # Repeat until 2+ players
    while len(waiting_players) < 2:
        if waiting_players:
            for user_id in waiting_players:
                try:
                    await bot.send_message(
                        user_id,
                        f"⏳ Waiting for at least 2 players...\nCurrently joined: {len(waiting_players)}"
                    )
                except:
                    pass
        await asyncio.sleep(2)

    # Countdown for all players
    countdown = JOIN_COUNTDOWN
    live_msgs = {}
    for user_id in waiting_players:
        try:
            msg = await bot.send_message(
                user_id,
                f"⏳ Round starting in {countdown} seconds!\nPlayers joined: {len(waiting_players)}"
            )
            live_msgs[user_id] = msg.message_id
        except:
            pass

    while countdown > 0 and len(waiting_players) >= 2:
        text = f"⏳ Round starting in {countdown} seconds!\nPlayers joined: {len(waiting_players)}"
        for user_id, msg_id in live_msgs.items():
            try:
                await bot.edit_message_text(
                    text,
                    chat_id=user_id,
                    message_id=msg_id
                )
            except:
                pass
        await asyncio.sleep(1)
        countdown -= 1

    # Deduct points & prepare pool
    global round_pool
    round_pool = 0
    for user_id in waiting_players:
        cursor.execute("UPDATE users SET balance = balance - ? WHERE id=?", (ROUND_COST,user_id))
        round_pool += ROUND_COST
    conn.commit()

    # Assign random colors to each player
    for user_id in waiting_players:
        colors = random.sample(BINGO_COLORS, 9)
        markup = types.InlineKeyboardMarkup(row_width=3)
        markup.add(*[types.InlineKeyboardButton(color, callback_data=f"hit_{color}") for color in colors])
        try:
            msg = await bot.send_message(user_id,f"🎯 Your Bingo Card (Colors left: {len(colors)})",reply_markup=markup)
            game_players[user_id] = {"needed": colors, "hits":0, "message_id": msg.message_id}
        except:
            pass

    waiting_players.clear()
    asyncio.create_task(run_bingo_round())

# ================= RUN ROUND WITH LIVE DRAW =================
async def run_bingo_round():
    global current_draw, game_active, round_pool
    game_active = True

    while game_players:
        # Draw random color
        current_draw = random.choice(BINGO_COLORS)

        # Notify all players about drawn color
        for user_id, player in list(game_players.items()):
            if current_draw in player["needed"]:
                player["needed"].remove(current_draw)
                player["hits"] += 1

                # Update player card
                if player["needed"]:
                    buttons = [types.InlineKeyboardButton(c, callback_data=f"hit_{c}") for c in player["needed"]]
                else:
                    buttons = [types.InlineKeyboardButton("🎉 WIN!", callback_data="win")]

                try:
                    await bot.edit_message_reply_markup(
                        user_id,
                        player["message_id"],
                        reply_markup=types.InlineKeyboardMarkup().add(*buttons)
                    )
                    await bot.edit_message_text(
                        f"🎯 Your Bingo Card (Colors left: {len(player['needed'])})",
                        user_id,
                        player["message_id"],
                        reply_markup=types.InlineKeyboardMarkup().add(*buttons)
                    )
                except:
                    pass

        await asyncio.sleep(DRAW_INTERVAL)
        # Stop if all players cleared
        if all(len(player["needed"])==0 for player in game_players.values()):
            break

    current_draw = ""
    game_active = False

# ================= HANDLE HITS =================
@dp.callback_query_handler(lambda c: c.data.startswith("hit_"))
async def handle_hit(c: types.CallbackQuery):
    user_id = c.from_user.id
    if user_id not in game_players:
        return await bot.answer_callback_query(c.id,"⚠️ Not in game!")
    await bot.answer_callback_query(c.id,"⏳ Wait for the system to draw colors", show_alert=True)

# ================= HANDLE WIN =================
@dp.callback_query_handler(lambda c: c.data=="win")
async def handle_win(c: types.CallbackQuery):
    global game_players, round_pool
    user_id = c.from_user.id
    if user_id not in game_players:
        return await bot.answer_callback_query(c.id,"⚠️ Not in game!")

    winner_points = int(round_pool * 0.8)
    admin_points = round_pool - winner_points

    cursor.execute("SELECT balance FROM users WHERE id=?",(user_id,))
    balance = cursor.fetchone()[0] + winner_points
    cursor.execute("UPDATE users SET balance=? WHERE id=?",(balance,user_id))
    conn.commit()

    await bot.send_message(user_id,
        f"🎊 BINGO! You won {winner_points} points!\n💵 Balance: {balance}\n💼 Admin keeps {admin_points} points")

    for other_id in list(game_players.keys()):
        if other_id != user_id:
            try:
                await bot.send_message(other_id,f"🏆 {c.from_user.full_name} won this round!")
            except:
                pass

    game_players.clear()
    round_pool = 0

# ================= CHECK BALANCE =================
@dp.callback_query_handler(lambda c: c.data=="check_balance")
async def check_balance(c: types.CallbackQuery):
    user_id = c.from_user.id
    cursor.execute("SELECT balance FROM users WHERE id=?",(user_id,))
    balance = cursor.fetchone()[0]
    await bot.answer_callback_query(c.id,f"💵 Balance: {balance} points", show_alert=True)

# ================= LEADERBOARD =================
@dp.callback_query_handler(lambda c: c.data=="leaderboard")
async def leaderboard(c: types.CallbackQuery):
    cursor.execute("SELECT name,balance FROM users ORDER BY balance DESC LIMIT 5")
    top = cursor.fetchall()
    text = "🏆 Top 5 Players:\n\n" + "\n".join([f"{i+1}. {p[0]}: {p[1]} pts" for i,p in enumerate(top)])
    await bot.answer_callback_query(c.id,text, show_alert=True)

# ================= ADMIN GIVE POINTS BY PHONE =================
@dp.message_handler(commands=["givepoints"])
async def admin_give_points(message: types.Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return await message.reply("❌ You are not admin!")

    args = message.get_args().split()
    if len(args) != 2 or not args[1].isdigit():
        return await message.reply("⚠️ Usage: /givepoints <phone_number> <points>")

    phone = args[0]
    points = int(args[1])

    cursor.execute("SELECT id, balance FROM users WHERE phone=?", (phone,))
    user = cursor.fetchone()
    if not user:
        return await message.reply("❌ User with this phone number not found!")

    target_id = user[0]
    new_balance = user[1] + points
    cursor.execute("UPDATE users SET balance=? WHERE id=?", (new_balance, target_id))
    conn.commit()
    await message.reply(f"✅ {points} points added to user with phone {phone}. New balance: {new_balance} pts")

    try:
        await bot.send_message(target_id, f"💰 Admin added {points} points to your balance! New balance: {new_balance} pts")
    except:
        pass

# ================= RUN BOT =================
if __name__=="__main__":
    executor.start_polling(dp, skip_updates=True)
