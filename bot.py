import os
import random
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

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# Bot Setup
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    # እዚህ ጋር Render የሚሰጥህን ሊንክ ታስገባለህ
    web_app_url = os.getenv("WEB_APP_URL", "https://your-app-name.onrender.com") 
    
    builder.row(types.InlineKeyboardButton(
        text="🎮 ቶምቦላ ተጫወት (Play)", 
        web_app=types.WebAppInfo(url=web_app_url))
    )
    await message.answer("እንኳን መጡ! ለመጫወት ከታች ያለውን ቁልፍ ይጫኑ።", reply_markup=builder.as_markup())

async def main():
    Thread(target=run_flask).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
