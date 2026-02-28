import os
import asyncio
import sqlite3
import random
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage

# --- 1. Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
except:
    ADMIN_ID = 0

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

# --- 2. Database ---
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
lobby_players = [] 
user_game_state = {}
is_counting_down = False

# --- 3. Main Menu ---
def get_main_menu_markup():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🎮 Be-Bingo Te-che-wat (Play)", callback_data="join_lobby"),
        types.InlineKeyboardButton("💰 Bir Mulla (Deposit)", callback_data="deposit"),
        types.InlineKeyboardButton("💳 Bir Awta (Withdraw)", callback_data="withdraw")
    )
    return markup

# --- 4. Multiplayer Engine (With Balance & Player Count) ---

async def start_multiplayer_round():
    global lobby_players, is_counting_down
    is_counting_down = True
    
    # 20 Second Lobby
    for i in range(20, 0, -5):
        p_count = len(lobby_players)
        for uid in lobby_players:
            try: await bot.send_message(uid, f"⏳ **Che-wa-ta-w le-me-je-mer {i} second ker-tual...**\n👥 Te-che-wachoch: **{p_count}**", parse_mode="Markdown")
            except: pass
        await asyncio.sleep(5)

    current_round_players = lobby_players.copy()
    lobby_players = []
    is_counting_down = False

    if not current_round_players: return
    p_total = len(current_round_players)

    # Board Dereder
    for uid in current_round_players:
        player_colors = random.sample(BINGO_COLORS, 9)
        cursor.execute("SELECT balance FROM users WHERE id=?", (uid,))
        current_bal = cursor.fetchone()[0]
        
        user_game_state[uid] = {"needed": player_colors, "hits": 0, "active": True, "total_p": p_total}
        
        markup = types.InlineKeyboardMarkup(row_width=3)
        btns = [types.InlineKeyboardButton(color, callback_data=f"hit_{color}") for color in player_colors]
        markup.add(*btns)
        
        board_text = (
            "━━━━━━━━━━━━━━\n"
            "🎮 **YE-BINGO KARTELA** 🎮\n"
            "━━━━━━━━━━━━━━\n"
            f"💰 Balance: **{current_bal} ETB**\n"
            f"👥 Players: **{p_total}**\n"
            "━━━━━━━━━━━━━━\n"
            "🎯 Status: *Ke 5 second behula ye-je-me-ral...*"
        )
        await bot.send_message(uid, board_text, reply_markup=markup, parse_mode="Markdown")
    
    await asyncio.sleep(5)

    # Shared Draw
    for _ in range(35):
        active_uids = [u for u in current_round_players if user_game_state.get(u, {}).get('active')]
        if not active_uids: break
        
        drawn = random.choice(BINGO_COLORS)
        for uid in active_uids:
            user_game_state[uid]["current"] = drawn
            call_text = f"🔔 **Te-le-ke-ke:** {drawn}"
            try: 
                msg = await bot.send_message(uid, call_text, parse_mode="Markdown")
                asyncio.create_task(delete_msg(msg, 3))
            except: pass
        await asyncio.sleep(3.5)

async def delete_msg(msg, delay):
    await asyncio.sleep(delay)
    try: await msg.delete()
    except: pass

# --- 5. Handlers ---

@dp.callback_query_handler(lambda c: c.data == "join_lobby")
async def join_lobby(c: types.CallbackQuery):
    u_id = c.from_user.id
    cursor.execute("SELECT balance FROM users WHERE id=?", (u_id,))
    balance = cursor.fetchone()[0]

    if balance < TICKET_PRICE:
        return await bot.answer_callback_query(c.id, "⚠️ Be-ki balance ye-lo-ti-m!", show_alert=True)
    
    if u_id in lobby_players:
        return await bot.answer_callback_query(c.id, "⏳ Te-me-ze-ge-be-wal, kotera-wun te-bi-ku.")

    cursor.execute("UPDATE users SET balance = balance - ? WHERE id=?", (TICKET_PRICE, u_id))
    cursor.execute("UPDATE pool SET prize = prize + ?", (TICKET_PRICE * 0.85,))
    conn.commit()

    lobby_players.append(u_id)
    await bot.send_message(u_id, f"✅ Te-me-ze-ge-be-wal! Ye-20 second kotera te-je-me-rual.\n👥 Players in Lobby: **{len(lobby_players)}**")

    if not is_counting_down:
        asyncio.create_task(start_multiplayer_round())

@dp.callback_query_handler(lambda c: c.data.startswith("hit_"))
async def handle_hit(c: types.CallbackQuery):
    u_id = c.from_user.id
    color = c.data.split("_")[1]
    state = user_game_state.get(u_id)

    if state and state.get("active") and color == state.get("current") and color in state["needed"]:
        state["needed"].remove(color)
        state["hits"] += 1
        
        hits = state["hits"]
        cursor.execute("SELECT balance FROM users WHERE id=?", (u_id,))
        c_bal = cursor.fetchone()[0]
        
        new_text = (
            "━━━━━━━━━━━━━━\n"
            "🎮 **YE-BINGO KARTELA** 🎮\n"
            "━━━━━━━━━━━━━━\n"
            f"💰 Balance: **{c_bal} ETB**\n"
            f"👥 Players: **{state['total_p']}**\n"
            "━━━━━━━━━━━━━━\n"
            f"✅ **{hits}/9** Hits!\n"
            f"🔥 Ber-ta! Ker-tua-l: **{9-hits}**"
        )
        try: await bot.edit_message_text(new_text, u_id, c.message.message_id, reply_markup=c.message.reply_markup, parse_mode="Markdown")
        except: pass
        
        if hits == 9:
            state["active"] = False
            cursor.execute("SELECT prize FROM pool WHERE id=1")
            prize = cursor.fetchone()[0]
            cursor.execute("UPDATE users SET balance = balance + ? WHERE id=?", (prize, u_id))
            cursor.execute("UPDATE pool SET prize = 0")
            conn.commit()
            await bot.send_message(u_id, f"🎊 **WIN!** {prize} ETB a-she-ne-fe-wal!")
    else:
        await bot.answer_callback_query(c.id, "❌ Keler-u ge-na al-we-ta-m!")

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT id, balance FROM users WHERE id=?", (user_id,))
    user_data = cursor.fetchone()

    if not user_data:
        cursor.execute("INSERT INTO users (id, name, balance) VALUES (?, ?, 0)", (user_id, message.from_user.full_name))
        conn.commit()
        balance = 0
    else:
        balance = user_data[1]

    await message.answer(f"👋 Welcome!\n💰 Balance: **{balance} ETB**", reply_markup=get_main_menu_markup(), parse_mode="Markdown")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
