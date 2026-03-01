import os
import asyncio
from flask import Flask, render_template
from threading import Thread
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Flask Setup
app = Flask(__name__)
@app.route('/')
def index():
    return render_template('index.html')

# Bot Setup
TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL") # Render URL እዚህ ይገባል
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(
        text="🎮 ቶምቦላ ተጫወት (Play)", 
        web_app=types.WebAppInfo(url=WEB_APP_URL))
    )
    await message.answer(
        f"እንኳን ወደ ቶምቦላ በሰላም መጡ! 🚀\n\nልክ በቪዲዮው ላይ እንዳዩት አይነት ሰሌዳ ላይ ለመጫወት ከታች ያለውን ቁልፍ ይጫኑ።",
        reply_markup=builder.as_markup()
    )

async def main():
    # Conflict እንዳይፈጠር መጀመሪያ Webhook ማጥፋት
    await bot.delete_webhook(drop_pending_updates=True)
    print("✅ ቦቱ ተነስቷል!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Flaskን በሌላ Thread ማስነሳት ለ Render Keep-alive
    # Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()
    asyncio.run(main())
