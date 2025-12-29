# src/bot.py

import redis.asyncio as redis
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.constants import ParseMode

from .config import (
    BOT_TOKEN, ADMIN_ID, REDIS_URL, REDIS_ENABLED,
    USER_THROTTLE_LIMIT, USER_THROTTLE_PERIOD
)
from .downloader import download_media
# السطر التالي هو الإصلاح الرئيسي: نستورد logger و is_valid_url
from .utils import logger, cleanup_file, is_valid_url

if REDIS_ENABLED:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
else:
    redis_client = None
    logger.warning("لم يتم توفير REDIS_URL. سيتم تعطيل ميزات التخزين المؤقت والتحكم في الاستخدام.")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً بك! أرسل أي رابط فيديو أو صورة لتحميله.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("فقط أرسل الرابط. لا توجد أوامر خاصة.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    message_text = update.message.text

    if not is_valid_url(message_text):
        await update.message.reply_text("عذرًا، لم أتعرف على هذا الرابط. 🧐")
        return

    if REDIS_ENABLED:
        key = f"user:{user_id}:requests"
        current_requests = await redis_client.incr(key)
        if current_requests == 1:
            await redis_client.expire(key, USER_THROTTLE_PERIOD)
        if current_requests > USER_THROTTLE_LIMIT:
            await update.message.reply_text("لحظة من فضلك! ✋ لقد قمت بالعديد من الطلبات في وقت قصير.")
            return

    if REDIS_ENABLED:
        cached_file_id = await redis_client.get(f"url:{message_text}")
        if cached_file_id:
            logger.info(f"إرسال من الكاش: {cached_file_id}")
            try:
                await update.message.reply_video(cached_file_id)
                return
            except Exception as e:
                logger.warning(f"فشل الإرسال من الكاش: {e}")

    processing_message = await update.message.reply_text("⏳ جارٍ تجهيز طلبك...")

    filepath = await download_media(message_text)

    if filepath:
        try:
            logger.info(f"بدء إرسال الملف: {filepath}")
            sent_message = await update.message.reply_video(video=open(filepath, 'rb'), supports_streaming=True)
            if REDIS_ENABLED and sent_message.video:
                await redis_client.set(f"url:{message_text}", sent_message.video.file_id, ex=60*60*24)
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=processing_message.message_id)
        except Exception as e:
            logger.error(f"فشل إرسال الملف: {e}")
            await context.bot.edit_message_text(text=f"عذرًا، حدث خطأ أثناء إرسال الملف.", chat_id=update.effective_chat.id, message_id=processing_message.message_id)
        finally:
            await cleanup_file(filepath)
    else:
        await context.bot.edit_message_text(text="عذرًا، لم أتمكن من تحميل المحتوى من هذا الرابط. 😔", chat_id=update.effective_chat.id, message_id=processing_message.message_id)

def main():
    logger.info("بدء تشغيل البوت...")
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == "__main__":
    main()
