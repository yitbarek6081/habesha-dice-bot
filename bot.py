import os
import asyncio
import sqlite3
import random
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
if not BOT_TOKEN or not ADMIN_ID:
    raise ValueError("⚠️ BOT_TOKEN or ADMIN_ID not set!")

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
waiting_players = {}  # {user_id: name}
game_players = {}     # {user_id: {"needed":[colors], "hits":0, "message_id":id, "clicked":[]}}
current_draw = ""
drawn_colors = []
round_pool = 0
game_active = False

# ================= MAIN MENU =================
def main_menu(balance):
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
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT id,balance FROM users WHERE id=?",(user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.execute("INSERT INTO users(id,name,balance) VALUES(?,?,?)",
                       (user_id,message.from_user.full_name,START_BALANCE))
        conn.commit()
        balance = START_BALANCE
        welcome = f"🎉 Welcome {message.from_user.full_name}! You have {START_BALANCE} points to play Bingo!"
    else:
        balance = user[1]
        welcome = f"👋 Welcome back {message.from_user.full_name}! Your balance: {balance} points."
    await message.answer(welcome, reply_markup=main_menu(balance))

# ================= REGISTER PHONE =================
@dp.callback_query_handler(lambda c: c.data=="register_phone")
async def register_phone_button(c: types.CallbackQuery):
    await bot.send_message(c.from_user.id,"📱 Send your phone number using:\n`/phone 0912345678`", parse_mode="Markdown")

@dp.message_handler(commands=["phone"])
async def register_phone(message: types.Message):
    args = message.get_args().strip()
    if not args.isdigit():
        return await message.reply("⚠️ Usage: /phone <number>")
    phone = args
    user_id = message.from_user.id
    cursor.execute("UPDATE users SET phone=? WHERE id=?",(phone,user_id))
    conn.commit()
    await message.reply(f"✅ Phone {phone} registered!")

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
    await bot.answer_callback_query(c.id,f"🎮 You joined! Players: {len(waiting_players)}")

    if not game_active:
        asyncio.create_task(join_countdown())

# ================= COUNTDOWN =================
async def join_countdown():
    global waiting_players, game_players, round_pool, game_active
    # Wait for at least 2 players
    while len(waiting_players) < 2:
        if waiting_players:
            for uid in waiting_players:
                try:
                    await bot.send_message(uid,f"⏳ Waiting for at least 2 players... Currently: {len(waiting_players)}")
                except: pass
        await asyncio.sleep(2)

    countdown = JOIN_COUNTDOWN
    live_msgs = {}
    for uid in waiting_players:
        try:
            msg = await bot.send_message(uid,f"⏳ Round starting in {countdown} seconds! Players: {len(waiting_players)}")
            live_msgs[uid] = msg.message_id
        except: pass

    while countdown>0 and len(waiting_players)>=2:
        text = f"⏳ Round starting in {countdown} seconds! Players: {len(waiting_players)}"
        for uid,msg_id in live_msgs.items():
            try: await bot.edit_message_text(text,chat_id=uid,message_id=msg_id)
            except: pass
        await asyncio.sleep(1)
        countdown -= 1

    # Deduct points & pool
    global round_pool
    round_pool = 0
    for uid in waiting_players:
        cursor.execute("UPDATE users SET balance=balance-? WHERE id=?",(ROUND_COST,uid))
        round_pool += ROUND_COST
    conn.commit()

    # Assign 9 colors per player
    for uid in waiting_players:
        colors = random.sample(BINGO_COLORS,9)
        markup = types.InlineKeyboardMarkup(row_width=5)
        markup.add(*[types.InlineKeyboardButton(c,callback_data=f"hit_{c}") for c in colors])
        try:
            msg = await bot.send_message(uid,f"🎯 Your Bingo Card (Click the color when it matches CURRENT)",reply_markup=markup)
            game_players[uid] = {"needed":colors,"hits":0,"message_id":msg.message_id,"clicked":[]}
        except: pass

    waiting_players.clear()
    asyncio.create_task(run_bingo_round())

# ================= GRID SYSTEM =================
def generate_bingo_card(player_colors, clicked_colors):
    grid = ""
    cols = ["C","O","L","O","R"]
    grid += " | ".join(cols) + "\n" + "-"*25 + "\n"

    all_cells = []
    color_cells = player_colors.copy()
    empty_cells = ["⚪"] * (30 - len(color_cells))
    all_cells = color_cells + empty_cells
    random.shuffle(all_cells)

    for i,cell in enumerate(all_cells):
        if cell in clicked_colors:
            all_cells[i] = "✅"
    for row in range(6):
        grid += " | ".join(all_cells[row*5:(row+1)*5]) + "\n"
    return grid

# ================= RUN ROUND =================
async def run_bingo_round():
    global current_draw, game_active, drawn_colors
    drawn_colors = []
    game_active = True

    while game_players:
        current_draw = random.choice(BINGO_COLORS)
        drawn_colors.append(current_draw)

        for uid,player in game_players.items():
            header = f"DERASH: {round_pool} | BALLS: {' '.join(drawn_colors[-10:])} | PLAYERS: {len(game_players)} | CURRENT: {current_draw}\n{'-'*40}\n"
            grid = generate_bingo_card(player["needed"], player["clicked"])
            text = header + grid
            buttons = [types.InlineKeyboardButton(c, callback_data=f"hit_{c}") for c in player["needed"]]
            if not buttons: buttons = [types.InlineKeyboardButton("🎉 WIN!",callback_data="win")]
            markup = types.InlineKeyboardMarkup(row_width=5).add(*buttons)
            try:
                await bot.edit_message_text(text, chat_id=uid, message_id=player["message_id"], reply_markup=markup)
            except: pass

        await asyncio.sleep(DRAW_INTERVAL)

    current_draw=""
    game_active=False

# ================= HANDLE CLICK =================
@dp.callback_query_handler(lambda c: c.data.startswith("hit_"))
async def handle_hit(c: types.CallbackQuery):
    uid = c.from_user.id
    color_clicked = c.data.split("_")[1]
    player = game_players.get(uid)
    if not player: return await bot.answer_callback_query(c.id,"⚠️ Not in game!")

    if color_clicked == current_draw:
        if color_clicked not in player["clicked"]:
            player["clicked"].append(color_clicked)
            player["needed"].remove(color_clicked)
            player["hits"] += 1
        await bot.answer_callback_query(c.id,f"✅ Correct! {player['hits']}/9")
    else:
        await bot.answer_callback_query(c.id,"❌ Not the current color!", show_alert=True)

# ================= HANDLE WIN =================
@dp.callback_query_handler(lambda c: c.data=="win")
async def handle_win(c: types.CallbackQuery):
    global game_players, round_pool
    uid = c.from_user.id
    player = game_players.get(uid)
    if not player: return await bot.answer_callback_query(c.id,"⚠️ Not in game!")

    winner_points = int(round_pool*0.8)
    admin_points = round_pool - winner_points

    cursor.execute("SELECT balance FROM users WHERE id=?",(uid,))
    balance = cursor.fetchone()[0]+winner_points
    cursor.execute("UPDATE users SET balance=? WHERE id=?",(balance,uid))
    conn.commit()

    await bot.send_message(uid,f"🎊 BINGO! You won {winner_points} points!\n💵 Balance: {balance}\n💼 Admin keeps {admin_points} points")
    for other in list(game_players.keys()):
        if other!=uid:
            try: await bot.send_message(other,f"🏆 {c.from_user.full_name} won this round!")
            except: pass

    game_players.clear()
    round_pool=0
    await asyncio.sleep(5)
    if waiting_players: asyncio.create_task(join_countdown())

# ================= CHECK BALANCE =================
@dp.callback_query_handler(lambda c: c.data=="check_balance")
async def check_balance(c: types.CallbackQuery):
    uid = c.from_user.id
    cursor.execute("SELECT balance FROM users WHERE id=?",(uid,))
    balance = cursor.fetchone()[0]
    await bot.answer_callback_query(c.id,f"💵 Balance: {balance}",show_alert=True)

# ================= LEADERBOARD =================
@dp.callback_query_handler(lambda c: c.data=="leaderboard")
async def leaderboard(c: types.CallbackQuery):
    cursor.execute("SELECT name,balance FROM users ORDER BY balance DESC LIMIT 5")
    top = cursor.fetchall()
    text = "🏆 Top 5 Players:\n\n" + "\n".join([f"{i+1}. {p[0]}: {p[1]} pts" for i,p in enumerate(top)])
    await bot.answer_callback_query(c.id,text,show_alert=True)

# ================= ADMIN GIVE POINTS =================
@dp.message_handler(commands=["givepoints"])
async def admin_give_points(message: types.Message):
    if message.from_user.id!=ADMIN_ID: return await message.reply("❌ Not admin!")
    args = message.get_args().split()
    if len(args)!=2 or not args[1].isdigit(): return await message.reply("⚠️ Usage: /givepoints <phone> <points>")
    phone, pts = args[0], int(args[1])
    cursor.execute("SELECT id,balance FROM users WHERE phone=?",(phone,))
    user = cursor.fetchone()
    if not user: return await message.reply("❌ Phone not found!")
    new_balance = user[1]+pts
    cursor.execute("UPDATE users SET balance=? WHERE id=?",(new_balance,user[0]))
    conn.commit()
    await message.reply(f"✅ {pts} points added to phone {phone}. New balance: {new_balance}")
    try: await bot.send_message(user[0],f"💰 Admin added {pts} points! New balance: {new_balance}")
    except: pass

# ================= RUN BOT =================
if __name__=="__main__":
    executor.start_polling(dp, skip_updates=True)
