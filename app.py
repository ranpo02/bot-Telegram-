# app.py
# THE FINAL, SIMPLIFIED, AND CORRECT SOLUTION

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
import yt_dlp
import redis

# ==============================================================================
# 1. CONFIGURATION (All in one place)
# ==============================================================================

# --- Environment Variables ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 10000))

# --- Bot Settings ---
DOWNLOAD_PATH = Path("downloads")
YOUTUBE_PROXIES = [
    "154.3.236.202:3128",
    "167.206.113.248:3128",
    "115.114.77.133:9090",
    "80.85.247.161:5555",
]

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger("httpx").setLevel(logging.WARNING)

# --- Redis Connection ---
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    logging.info("Successfully connected to Redis.")
except Exception as e:
    logging.error(f"Could not connect to Redis: {e}")
    redis_client = None

# ==============================================================================
# 2. UI & MESSAGES (All text and keyboards)
# ==============================================================================

def get_start_message(user_name: str) -> str:
    return f"أهلاً بك يا {user_name}!\n\nأنا بوت تحميل الفيديوهات. أرسل لي أي رابط وسأقوم بتحميله لك."

HELP_MESSAGE = """
<b>مساعدة ℹ️</b>

- أرسل رابط فيديو من (يوتيوب، تيك توك، انستغرام...).
- سأقوم بتحميل الفيديو وإرساله لك.
- البوت يدعم استئناف التحميل في حال انقطاع الإنترنت.
"""

PROCESSING_MESSAGE = "⏳ جاري معالجة الرابط..."
UPLOADING_MESSAGE = "⚡️ جاري رفع الفيديو..."
INVALID_URL_MESSAGE = "⚠️ الرابط الذي أرسلته غير صالح. يرجى التأكد منه."
GENERIC_ERROR_MESSAGE = "❌ حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى."

def get_video_caption(title: str, url: str) -> str:
    return f"✅ **{title}**\n\n🔗 [الرابط الأصلي]({url})"

def get_main_keyboard() -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton("❓ مساعدة", callback_data='show_help')]]
    return InlineKeyboardMarkup(keyboard)

# ==============================================================================
# 3. CORE LOGIC (Downloader and Utilities)
# ==============================================================================

class DownloadError(Exception):
    pass

class VideoIsPrivateOrDeletedError(DownloadError):
    pass

class AllProxiesFailedError(DownloadError):
    pass

def is_valid_url(url: str) -> bool:
    return bool(re.match(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', url))

async def cleanup_file(file_path: str):
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logging.info(f"Cleaned up file: {file_path}")
    except Exception as e:
        logging.error(f"Error cleaning up file {file_path}: {e}")

async def download_video(url: str) -> tuple[str, str]:
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    
    # Shuffle proxies to try a different one each time
    shuffled_proxies = random.sample(YOUTUBE_PROXIES, len(YOUTUBE_PROXIES))

    for proxy in shuffled_proxies + [None]: # Try all proxies, then try without proxy
        ydl_opts = {
            'format': 'best[ext=mp4]/best',
            'outtmpl': str(DOWNLOAD_PATH / '%(title)s.%(ext)s'),
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'proxy': proxy,
        }
        
        try:
            logging.info(f"Attempting download for {url} using proxy: {proxy or 'None'}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                video_title = info.get('title', 'video')
                file_path = ydl.prepare_filename(info)
                logging.info(f"Download successful with proxy: {proxy or 'None'}")
                return file_path, video_title
        except yt_dlp.utils.DownloadError as e:
            if 'private' in str(e).lower() or 'unavailable' in str(e).lower():
                raise VideoIsPrivateOrDeletedError("الفيديو خاص أو تم حذفه.")
            logging.warning(f"Proxy {proxy or 'None'} failed for {url}: {e}")
            continue # Try next proxy
    
    raise AllProxiesFailedError("فشلت كل محاولات التحميل. قد يكون الرابط غير مدعوم أو أن الخوادم محظورة.")

# ==============================================================================
# 4. TELEGRAM HANDLERS (Bot command and message logic)
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

    processing_message = await update.message.reply_text(PROCESSING_MESSAGE)
    video_path = None
    try:
        video_path, video_title = await download_video(url)
        await processing_message.edit_text(UPLOADING_MESSAGE)
        
        with open(video_path, 'rb') as video_file:
            await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=video_file,
                caption=get_video_caption(video_title, url),
                supports_streaming=True
            )
        await processing_message.delete()
    except DownloadError as e:
        await processing_message.edit_text(f"❌ حدث خطأ أثناء التحميل:\n\n{e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred for URL {url}: {e}", exc_info=True)
        await processing_message.edit_text(GENERIC_ERROR_MESSAGE)
    finally:
        if video_path:
            await cleanup_file(video_path)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data == 'show_help':
        await query.edit_message_text(text=HELP_MESSAGE, reply_markup=get_main_keyboard())

# ==============================================================================
# 5. APPLICATION SETUP & ENTRY POINT (Flask + Bot)
# ==============================================================================

# --- Flask App for Health Check ---
flask_app = Flask(__name__)

@flask_app.route('/health')
def health_check():
    return "OK", 200

# --- Bot Application ---
# We need to run the bot in the main thread for asyncio to work correctly.
# The web server will run in a background thread.

def run_web_server():
    flask_app.run(host='0.0.0.0', port=PORT)

def main():
    # Start the web server in a background thread
    web_thread = threading.Thread(target=run_web_server)
    web_thread.daemon = True
    web_thread.start()
    logging.info(f"Health check server started in a background thread on port {PORT}.")

    # Set up and run the bot in the main thread
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    application.add_handler(CallbackQueryHandler(button_handler))

    logging.info("Starting Telegram bot polling in the main thread...")
    application.run_polling()

if __name__ == '__main__':
    if not BOT_TOKEN:
        logging.fatal("FATAL: BOT_TOKEN environment variable not set.")
    else:
        main()
