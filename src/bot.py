# src/bot.py
import asyncio
import logging
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage

# === التعديل النهائي هنا: استخدام الاستيراد النسبي (مع نقطة) ===
from .config import BOT_TOKEN
from .utils import setup_logger, find_url_in_text
from .downloader import Downloader

# إعداد الراوتر الرئيسي
router = Router()

@router.message(Command("start", "help", "مساعدة"))
async def cmd_start_help(message: Message):
    """متحكم لأوامر البداية والمساعدة."""
    await message.answer(
        "أهلاً بك في بوت تحميل المحتوى!\n"
        "أرسل لي أي رابط من (YouTube, Instagram, TikTok, X, Facebook, Pinterest) وسأقوم بتحميله لك.\n\n"
        "**لتحويل فيديو يوتيوب إلى ملف صوتي (MP3):**\n"
        "أرسل الرابط متبوعاً بكلمة `mp3`."
    )

@router.message(F.text)
async def handle_link(message: Message, downloader: Downloader):
    """متحكم لمعالجة الرسائل التي تحتوي على روابط."""
    url = find_url_in_text(message.text)
    if not url:
        await message.reply("لم أتمكن من العثور على رابط صالح في رسالتك. الرجاء إرسال رابط مباشر.")
        return

    to_mp3 = 'mp3' in message.text.lower() and ('youtube.com' in url.lower() or 'youtu.be' in url.lower())
    
    status_message = await message.reply("✅ تم استلام الرابط، جاري التحليل والتحميل...")

    try:
        result = await downloader.download_media(url, to_mp3)

        if result:
            file_path, original_name = result
            await status_message.edit_text("⏳ تم التحميل بنجاح، جاري إرسال الملف...")
            
            input_file = FSInputFile(file_path, filename=original_name)
            caption = f"تم التحميل بنجاح!\n\n🔗 المصدر: {url}"

            try:
                if to_mp3:
                    await message.reply_audio(input_file, caption=caption)
                else:
                    await message.reply_video(input_file, caption=caption)
            except Exception as send_error:
                logging.error(f"فشل إرسال الملف: {send_error}")
                await status_message.edit_text("❌ عذراً، حجم الملف أكبر من المسموح به في تيليجرام (50MB).")

            downloader._cleanup(file_path)
            await status_message.delete()
        else:
            await status_message.edit_text("❌ عذراً، فشلت عملية التحميل. قد يكون الرابط غير مدعوم أو حدث خطأ ما.")
    
    except Exception as e:
        logging.error(f"حدث خطأ غير متوقع أثناء معالجة الرابط {url}: {e}")
        await status_message.edit_text("❌ حدث خطأ فادح أثناء المعالجة. الرجاء المحاولة مرة أخرى لاحقاً.")

async def main():
    """الدالة الرئيسية لتشغيل البوت."""
    setup_logger()
    
    bot = Bot(token=BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    downloader_instance = Downloader()
    dp.workflow_data["downloader"] = downloader_instance
    
    dp.include_router(router)

    logging.info("بدء تشغيل البوت...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)
