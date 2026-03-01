import os
import random
import asyncio
from flask import Flask
from threading import Thread
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

# --- 1. Render Keep-Alive ---
server = Flask('')
@server.route('/')
def home(): return "Tombola Group Bot is Live!"
def run_flask(): server.run(host='0.0.0.0', port=8080)

# --- 2. Translations ---
STRINGS = {
    'am': {
        'welcome': "እንኳን ወደ ቶምቦላ መጡ! ቋንቋ ይምረጡ / Choose language:",
        'get_ticket': "🎫 ካርታ በ Inbox ውሰድ",
        'start_draw': "🚀 ቁጥር ማውጣት ጀምር",
        'bingo_btn': "🏆 ቢንጎ! (አረጋግጥ)",
        'winner': "🎉🎉🎉 ቢንጎ! 🎉🎉🎉\nእንኳን ደስ አለዎት {name}! አሸንፈዋል! 🏆",
        'not_yet': "ገና ነዎት! {num} ቁጥሮች ይቀሩዎታል",
        'game_started': "🚀 ጨዋታው ተጀምሯል! ቁጥሮች እዚህ ይወጣሉ።"
    },
    'en': {
        'welcome': "Welcome to Tombola! Choose language:",
        'get_ticket': "🎫 Get Ticket in Inbox",
        'start_draw': "🚀 Start Drawing",
        'bingo_btn': "🏆 Bingo! (Verify)",
        'winner': "🎉🎉🎉 BINGO! 🎉🎉🎉\nCongratulations {name}! You won! 🏆",
        'not_yet': "Not yet! You still need {num} numbers",
        'game_started': "🚀 Game started! Watch for numbers here."
    }
}

# ግሎባል ዳታዎች
user_tickets = {} 
drawn_numbers = []
user_lang = {}

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- 3. Handlers ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # በግል Inbox ውስጥ ካርታ ለመስጠት
    if message.chat.type == "private":
        lang = user_lang.get(message.from_user.id, 'am')
        nums = random.sample(range(1, 91), 15)
        nums.sort()
        user_tickets[message.from_user.id] = nums
        
        ticket_text = f"🎫 **የእርስዎ ካርታ**\n\n"
        for i in range(0, 15, 5):
            row = " | ".join(f"`{n:02d}`" for n in nums[i:i+5])
            ticket_text += f"| {row} |\n"
        
        builder = InlineKeyboardBuilder()
        builder.add(InlineKeyboardButton(text=STRINGS[lang]['bingo_btn'], callback_data="check_bingo"))
        await message.answer(ticket_text, reply_markup=builder.as_markup(), parse_mode="MarkdownV2")
    else:
        # በግሩፕ ውስጥ ከሆነ ቋንቋ እንዲመርጡ
        builder = InlineKeyboardBuilder()
        builder.add(InlineKeyboardButton(text="አማርኛ 🇪🇹", callback_data="lang_am"))
        builder.add(InlineKeyboardButton(text="English 🇺🇸", callback_data="lang_en"))
        await message.answer(STRINGS['am']['welcome'], reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    user_lang[callback.from_user.id] = lang
    bot_info = await bot.get_me()
    
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text=STRINGS[lang]['get_ticket'], url=f"https://t.me/{bot_info.username}?start=join"))
    builder.add(InlineKeyboardButton(text=STRINGS[lang]['start_draw'], callback_data="start_draw"))
    await callback.message.edit_text(STRINGS[lang]['welcome'], reply_markup=builder.as_markup())

@dp.callback_query(F.data == "start_draw")
async def start_drawing(callback: types.CallbackQuery):
    lang = user_lang.get(callback.from_user.id, 'am')
    drawn_numbers.clear()
    await callback.message.answer(STRINGS[lang]['game_started'])
    
    all_nums = list(range(1, 91))
    random.shuffle(all_nums)
    
    for num in all_nums:
        drawn_numbers.append(num)
        await callback.message.answer(f"🔢 ቁጥር: **{num}**", parse_mode="Markdown")
        await asyncio.sleep(10)

@dp.callback_query(F.data == "check_bingo")
async def verify_bingo(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    lang = user_lang.get(user_id, 'am')
    my_nums = user_tickets.get(user_id, [])
    
    if not my_nums:
        await callback.answer("መጀመሪያ ካርታ ይውሰዱ!", show_alert=True)
        return

    missing = [n for n in my_nums if n not in drawn_numbers]
    
    if not missing:
        # አሸናፊውን በግሩፕ ውስጥ ማስታወቅ (ከቻት ID ጋር)
        await bot.send_message(callback.message.chat.id, STRINGS[lang]['winner'].format(name=callback.from_user.full_name))
        await callback.answer("እንኳን ደስ አለዎት!", show_alert=True)
    else:
        await callback.answer(STRINGS[lang]['not_yet'].format(num=len(missing)), show_alert=True)

# --- 4. Main ---
async def main():
    Thread(target=run_flask).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
