import os
import random
import asyncio
from flask import Flask
from threading import Thread
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# --- 1. Render Keep-Alive (Flask) ---
server = Flask('')
@server.route('/')
def home(): return "Aiogram Tombola is Live!"
def run_flask(): server.run(host='0.0.0.0', port=8080)

# --- 2. Translations & Logic ---
STRINGS = {
    'am': {
        'welcome': "እንኳን ወደ ቶምቦላ መጡ! ቋንቋ ይምረጡ:",
        'get_ticket': "🎫 ካርታ ውሰድ",
        'start_draw': "🚀 ቁጥር ማውጣት ጀምር",
        'bingo_btn': "🏆 ቢንጎ! (አረጋግጥ)",
        'winner': "🎉 እንኳን ደስ አለዎት {name}! አሸንፈዋል! 🏆",
        'not_yet': "ገና ነዎት! {num} ቁጥሮች ይቀሩዎታል"
    },
    'en': {
        'welcome': "Welcome to Tombola! Choose language:",
        'get_ticket': "🎫 Get Ticket",
        'start_draw': "🚀 Start Drawing",
        'bingo_btn': "🏆 Bingo! (Verify)",
        'winner': "🎉 Congratulations {name}! You won! 🏆",
        'not_yet': "Not yet! You still need {num} numbers"
    }
}

# ዳታዎችን ለማስቀመጥ (በአጭሩ)
user_tickets = {} # {user_id: [numbers]}
drawn_numbers = []
user_lang = {} # {user_id: 'am'}

# --- 3. Handlers ---
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="አማርኛ 🇪🇹", callback_data="lang_am"))
    builder.add(InlineKeyboardButton(text="English 🇺🇸", callback_data="lang_en"))
    await message.answer(STRINGS['am']['welcome'], reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    user_lang[callback.from_user.id] = lang
    
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text=STRINGS[lang]['get_ticket'], callback_data="get_ticket"))
    builder.add(InlineKeyboardButton(text=STRINGS[lang]['start_draw'], callback_data="start_draw"))
    await callback.message.edit_text(STRINGS[lang]['welcome'], reply_markup=builder.as_markup())

@dp.callback_query(F.data == "get_ticket")
async def send_ticket(callback: types.CallbackQuery):
    lang = user_lang.get(callback.from_user.id, 'am')
    nums = random.sample(range(1, 91), 15)
    nums.sort()
    user_tickets[callback.from_user.id] = nums
    
    ticket_text = f"🎫 **{STRINGS[lang]['get_ticket']}**\n\n"
    for i in range(0, 15, 5):
        ticket_text += " | ".join(f"`{n:02d}`" for n in nums[i:i+5]) + "\n"
    
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text=STRINGS[lang]['bingo_btn'], callback_data="check_bingo"))
    
    await callback.message.answer(ticket_text, reply_markup=builder.as_markup(), parse_mode="MarkdownV2")
    await callback.answer()

@dp.callback_query(F.data == "start_draw")
async def start_drawing(callback: types.CallbackQuery):
    drawn_numbers.clear()
    await callback.message.answer("🚀 ቁጥሮች መውጣት ጀምረዋል!")
    
    # ቁጥሮችን በየ 10 ሴኮንዱ ማውጣት (Loop)
    all_nums = list(range(1, 91))
    random.shuffle(all_nums)
    
    for num in all_nums:
        drawn_numbers.append(num)
        await callback.message.answer(f"🔢 ቁጥር: **{num}**", parse_mode="Markdown")
        await asyncio.sleep(10) # 10 ሴኮንድ ይጠብቃል

@dp.callback_query(F.data == "check_bingo")
async def verify_bingo(callback: types.CallbackQuery):
    lang = user_lang.get(callback.from_user.id, 'am')
    my_nums = user_tickets.get(callback.from_user.id, [])
    
    missing = [n for n in my_nums if n not in drawn_numbers]
    
    if not missing:
        await callback.message.answer(STRINGS[lang]['winner'].format(name=callback.from_user.first_name))
    else:
        await callback.answer(STRINGS[lang]['not_yet'].format(num=len(missing)), show_alert=True)

# --- 4. Main Entry ---
async def main():
    Thread(target=run_flask).start()
    # የድሮውን ፖሊንግ ለማጽዳት
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
