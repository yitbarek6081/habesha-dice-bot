import os
import asyncio
from aiogram import Bot, Dispatcher, types, executor

# መረጃዎችን ከ Render Environment Variables ያነባል
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID") 

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    await message.answer("🎲 እንኳን ወደ ሐበሻ ዳይስ ጨዋታ በሰላም መጡ!\n\nለመወራረድና ለመጫወት /play የሚለውን ይጫኑ።")

@dp.message_handler(commands=['play'])
async def play(message: types.Message):
    # እዚህ ጋር ያንተን ትክክለኛ የባንክ እና ስልክ ቁጥሮች አስገባ
    payment_msg = (
        "📍 ለመጫወት መጀመሪያ ክፍያ ይፈጽሙ\n\n"
        "💰 የመወራረጃ መጠን፦ 20 ብር\n\n"
        "💳 የክፍያ አማራጮች፦\n"
        "━━━━━━━━━━━━━━━\n"
        "🔸 ቴሌብር (Telebirr): 0945880474\n"
        "🔸 CBE Birr (ሲቢኢ ብር): 0945880474\n"
        "🔸 ንግድ ባንክ (CBE): 100072458954\n"
        "━━━━━━━━━━━━━━━\n\n"
        "⚠️ ክፍያውን እንደፈጸሙ የደረሰኝ ፎቶ (Screenshot) እዚህ ይላኩ።"
    )
    await message.answer(payment_msg)

# ተጫዋቹ የደረሰኝ ፎቶ ሲልክ ለአንተ (ለአድሚኑ) እንዲመጣ
@dp.message_handler(content_types=['photo'])
async def handle_screenshot(message: types.Message):
    user_id = message.from_user.id
    user_name = message.from_user.full_name
    
    # ለአንተ (ለአድሚኑ) የሚላክ የውሳኔ ቁልፍ
    keyboard = types.InlineKeyboardMarkup()
    approve_btn = types.InlineKeyboardButton("ያለፈ (Approve) ✅", callback_data=f"app_{user_id}")
    reject_btn = types.InlineKeyboardButton("ውድቅ (Reject) ❌", callback_data=f"rej_{user_id}")
    keyboard.add(approve_btn, reject_btn)
    
    await bot.send_photo(
        ADMIN_ID, 
        message.photo[-1].file_id, 
        caption=f"📩 አዲስ የክፍያ ደረሰኝ!\n\nከ፦ {user_name}\nመለያ (ID)፦ {user_id}", 
        reply_markup=keyboard
    )
    await message.answer("🙏 ደረሰኙ ደርሶናል። አድሚኑ እስኪያረጋግጥ ድረስ እባክዎ ትንሽ ይጠብቁ...")

# አንተ 'Approve' ስትል ቦቱ ዳይሱን ይጥላል
@dp.callback_query_handler(lambda c: c.data.startswith('app_'))
async def approve(callback_query: types.CallbackQuery):
    target_id = callback_query.data.split("_")[1]
    
    await bot.send_message(target_id, "✅ ክፍያዎ ተረጋግጧል! ጨዋታው ተጀምሯል... መልካም እድል! 🎲")
    
    # ዳይሱን መጣል
    dice = await bot.send_dice(target_id)
    await asyncio.sleep(4) # ዳይሱ ተንከባሎ እስኪያቆም መጠበቅ
    
    # ውጤቱን ማሳወቅ (ከ 4 በላይ ካመጣ ያሸንፋል)
    if dice.dice.value >= 4:
        await bot.send_message(target_id, f"🎉 እንኳን ደስ አለዎት! {dice.dice.value} ወጥቶልዎታል! አሸንፈዋል። አድሚኑን ያነጋግሩ።")
    else:
        await bot.send_message(target_id, f"😔 ውጤቱ {dice.dice.value} ነው። ለጥቂት አልሳካሎትም፤ እንደገና ይሞክሩ።")
        
    await bot.answer_callback_query(callback_query.id, "ተፈቅዷል!")
    await bot.edit_message_caption(callback_query.message.chat.id, callback_query.message.message_id, caption="✅ ይህ ክፍያ ተረጋግጧል")

# አንተ 'Reject' ስትል
@dp.callback_query_handler(lambda c: c.data.startswith('rej_'))
async def reject(callback_query: types.CallbackQuery):
    target_id = callback_query.data.split("_")[1]
    await bot.send_message(target_id, "❌ ይቅርታ፣ የላኩት ደረሰኝ ተቀባይነት አላገኘም። እባክዎ ትክክለኛ መሆኑን ያረጋግጡ።")
    await bot.answer_callback_query(callback_query.id, "ውድቅ ተደርጓል!")
    await bot.edit_message_caption(callback_query.message.chat.id, callback_query.message.message_id, caption="❌ ውድቅ ተደርጓል")

if __name__ == '__main__':
    executor.start_polling(dp)
