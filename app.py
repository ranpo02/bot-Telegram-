# app.py
# UPGRADED VERSION with improvements

import logging
import os
import threading
import asyncio
import re
import random
from pathlib import Path

# --- Library Imports ---
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import BadRequest
import yt_dlp
import redis

# ==============================================================================
# 1. CONFIGURATION
# ==============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 10000))

DOWNLOAD_PATH = Path("downloads")
YOUTUBE_PROXIES = [
    "154.3.236.202:3128",
    "167.206.113.248:3128",
    "115.114.77.133:9090",
    "80.85.247.161:5555",
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING) # Quieter web server logs

try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    logging.info("Successfully connected to Redis.")
except Exception as e:
    logging.error(f"Could not connect to Redis: {e}")
    redis_client = None

# ==============================================================================
# 2. UI & MESSAGES
# ==============================================================================

def get_start_message(user_name: str) -> str:
    return f"أهلاً بك يا {user_name}!\n\nأنا بوت تحميل الفيديوهات. أرسل لي أي رابط وسأقوم بتحميله لك."

HELP_MESSAGE = "<b>مساعدة ℹ️</b>\n\n- أرسل رابط فيديو من (يوتيوب، تيك توك، انستغرام...).\n- سأقوم بتحميل الفيديو وإرساله لك بجودة مناسبة وسريعة.\n- يمكنك إلغاء التحميل في أي وقت."

PROCESSING_MESSAGE = "⏳ جاري معالجة الرابط..."
UPLOADING_MESSAGE = "⚡️ جاري رفع الفيديو..."
INVALID_URL_MESSAGE = "⚠️ الرابط الذي أرسلته غير صالح. يرجى التأكد منه."
GENERIC_ERROR_MESSAGE = "❌ حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى."
DOWNLOAD_CANCELED_MESSAGE = "✅ تم إلغاء عملية التحميل."

def get_video_caption(title: str, url: str) -> str:
    return f"✅ **{title}**\n\n🔗 [الرابط الأصلي]({url})"

def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❓ مساعدة", callback_data='show_help')]])

def get_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data='cancel_download')]])

# ==============================================================================
# 3. CORE LOGIC
# ==============================================================================

class DownloadError(Exception): pass
class VideoIsPrivateOrDeletedError(DownloadError): pass
class AllProxiesFailedError(DownloadError): pass

def is_valid_url(url: str) -> bool:
    return bool(re.match(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', url))

async def cleanup_file(file_path: str):
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logging.info(f"Cleaned up file: {file_path}")
    except Exception as e:
        logging.error(f"Error cleaning up file {file_path}: {e}")

async def download_video(url: str, context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int) -> tuple[str, str] | None:
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    
    # IMPROVEMENT: Prefer faster, medium-quality formats.
    ydl_opts_base = {
        'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': str(DOWNLOAD_PATH / '%(id)s.%(ext)s'),
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [lambda d: check_if_cancelled(d, context, chat_id, message_id)],
    }
    
    shuffled_proxies = random.sample(YOUTUBE_PROXIES, len(YOUTUBE_PROXIES))

    for proxy in shuffled_proxies + [None]:
        ydl_opts = ydl_opts_base.copy()
        ydl_opts['proxy'] = proxy
        
        try:
            logging.info(f"Attempting download for {url} using proxy: {proxy or 'None'}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                video_title = info.get('title', 'video')
                file_path = ydl.prepare_filename(info)
                logging.info(f"Download successful with proxy: {proxy or 'None'}")
                return file_path, video_title
        except DownloadError as e: # Catch our custom cancel error
            raise e
        except yt_dlp.utils.DownloadError as e:
            if 'private' in str(e).lower() or 'unavailable' in str(e).lower():
                raise VideoIsPrivateOrDeletedError("الفيديو خاص أو تم حذفه.")
            logging.warning(f"Proxy {proxy or 'None'} failed for {url}: {e}")
            continue
    
    raise AllProxiesFailedError("فشلت كل محاولات التحميل. قد يكون الرابط غير مدعوم أو أن الخوادم محظورة.")

def check_if_cancelled(d, context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    """Hook for yt-dlp to check if the user cancelled the download."""
    if context.user_data.get(f'cancel_{chat_id}_{message_id}', False):
        raise DownloadError("تم إلغاء التحميل من قبل المستخدم.")

# ==============================================================================
# 4. TELEGRAM HANDLERS
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_name = update.effective_user.first_name
    await update.message.reply_html(text=get_start_message(user_name), reply_markup=get_main_keyboard())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(text=HELP_MESSAGE, reply_markup=get_main_keyboard())

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = update.message.text
    if not is_valid_url(url):
        await update.message.reply_text(INVALID_URL_MESSAGE)
        return

    processing_message = await update.message.reply_text(PROCESSING_MESSAGE, reply_markup=get_cancel_keyboard())
    chat_id = update.effective_chat.id
    message_id = processing_message.message_id
    context.user_data[f'cancel_{chat_id}_{message_id}'] = False
    
    video_path = None
    try:
        video_path, video_title = await download_video(url, context, chat_id, message_id)
        
        if not video_path: # Download was cancelled
            return

        await processing_message.edit_text(UPLOADING_MESSAGE, reply_markup=None)
        
        with open(video_path, 'rb') as video_file:
            await context.bot.send_video(
                chat_id=chat_id,
                video=video_file,
                caption=get_video_caption(video_title, url),
                supports_streaming=True
            )
        await processing_message.delete()
    except DownloadError as e:
        await processing_message.edit_text(f"{e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred for URL {url}: {e}", exc_info=True)
        await processing_message.edit_text(GENERIC_ERROR_MESSAGE)
    finally:
        if video_path:
            await cleanup_file(video_path)
        context.user_data.pop(f'cancel_{chat_id}_{message_id}', None)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    if query.data == 'show_help':
        try:
            await query.edit_message_text(text=HELP_MESSAGE, reply_markup=get_main_keyboard())
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass # Ignore this error silently
            else:
                raise e
    elif query.data == 'cancel_download':
        chat_id = query.message.chat_id
        message_id = query.message.message_id
        context.user_data[f'cancel_{chat_id}_{message_id}'] = True
        await query.edit_message_text(DOWNLOAD_CANCELED_MESSAGE, reply_markup=None)

# ==============================================================================
# 5. APPLICATION SETUP & ENTRY POINT
# ==============================================================================

flask_app = Flask(__name__)
@flask_app.route('/health')
def health_check(): return "OK", 200

def run_web_server():
    flask_app.run(host='0.0.0.0', port=PORT)

def main():
    web_thread = threading.Thread(target=run_web_server)
    web_thread.daemon = True
    web_thread.start()
    logging.info(f"Health check server started on port {PORT}.")

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    application.add_handler(CallbackQueryHandler(button_handler))

    logging.info("Starting Telegram bot polling...")
    application.run_polling()

if __name__ == '__main__':
    if not BOT_TOKEN:
        logging.fatal("FATAL: BOT_TOKEN environment variable not set.")
    else:
        main()
