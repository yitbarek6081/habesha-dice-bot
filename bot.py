import os
import asyncio
import random
from flask import Flask, render_template
from threading import Thread
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo, InlineKeyboardButton

# --- 1. Flask Setup (ለ Mini App ዲዛይኑ) ---
app = Flask(__name__)

@app.route('/')
def index():
    # 'templates/index.html' ውስጥ ያለው ዲዛይን እንዲታይ ያደርጋል
    return render_template('index.html')

def run_flask():
    # Render የዌብ ገጹን እንዲያገኘው ፖርት 10000 ይጠቀማል
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- 2. Bot Setup ---
TOKEN = os.getenv("BOT_TOKEN")
# Render Dashboard ላይ የሞላኸው የዌብሳይትህ ሊንክ
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://habesha-dice-bot.onrender.com")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- 3. Handlers (ትዕዛዞች) ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """ተጫዋቹ /start ሲል የጨዋታውን ቁልፍ ያሳያል"""
    builder = InlineKeyboardBuilder()
    
    # ቪዲዮው ላይ የታየውን ገጽ ለመክፈት የሚያገለግል ቁልፍ
    builder.row(InlineKeyboardButton(
        text="🎮 ቶምቦላ ተጫወት (Play Tombola)", 
        web_app=WebAppInfo(url=WEB_APP_URL))
    )
    
    welcome_text = (
        "እንኳን ወደ ቶምቦላ በሰላም መጡ! 🇪🇹\n\n"
        "ልክ በቪዲዮው ላይ እንዳዩት አይነት ሰሌዳ ላይ ለመጫወት "
        "ከታች ያለውን ቁልፍ ይጫኑ።"
    )
    await message.answer(welcome_text, reply_markup=builder.as_markup())

# --- 4. Main Function (Conflict Errorን ለመከላከል) ---

async def main():
    # 🔴 ወሳኝ፡ የቀድሞ የፖሊንግ ግንኙነቶችን በሙሉ በሃይል ያቋርጣል
    print("🧹 የቆዩ ግንኙነቶች እየተጸዱ ነው...")
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Flaskን በሌላ Thread ማስነሳት (Bot እና Web ገጹ አብረው እንዲሰሩ)
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    print("✅ ቦቱ እና ዌብ አፑ በሰላም ተነስተዋል!")
    
    # ፖሊንግ ይጀምራል
    try:
        await dp.start_polling(bot)
    except Exception as e:
        print(f"❌ ስህተት ተከስቷል: {e}")

if __name__ == "__main__":
    # በአንድ ፋይል ውስጥ ሁሉንም ለማስነሳት
    asyncio.run(main())
