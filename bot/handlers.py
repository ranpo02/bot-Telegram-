# src/bot/handlers.py
import asyncio
import logging
import redis.asyncio as redis
from telegram import Update
from telegram.ext import ContextTypes

from src.bot import ui
from src.core import config
from src.core.exceptions import DownloadError
from src.downloader import engine
from src.utils import cleanup_file, is_valid_url # We will create this utils file

logger = logging.getLogger(__name__)

# Initialize Redis client if enabled
redis_client = redis.from_url(config.REDIS_URL, decode_responses=True) if config.REDIS_ENABLED else None

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        ui.WELCOME_MESSAGE,
        reply_markup=ui.get_start_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(ui.HELP_MESSAGE)

# --- Message Handler ---
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not is_valid_url(url):
        await update.message.reply_text("عذراً، هذا ليس رابطاً صالحاً. يرجى إرسال رابط يبدأ بـ http.")
        return

    user_id = update.message.from_user.id

    # 1. User Throttling
    if redis_client:
        user_lock_key = f"user_lock:{user_id}"
        if await redis_client.get(user_lock_key):
            await update.message.reply_text(f"[✋] لحظة من فضلك! يمكنك إرسال طلب كل {config.USER_THROTTLE_SECONDS} ثانية.")
            return
        await redis_client.set(user_lock_key, "1", ex=config.USER_THROTTLE_SECONDS)

    # 2. Check Cache
    if redis_client:
        cached_file_id = await redis_client.get(f"url_cache:{url}")
        if cached_file_id:
            logger.info(f"Cache hit for URL: {url}. Sending file_id.")
            await update.message.reply_video(cached_file_id, reply_markup=ui.get_success_keyboard())
            return

    # 3. Start Processing
    status_message = await update.message.reply_text(ui.get_status_message("analyzing"))
    filepath = None
    try:
        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text=ui.get_status_message("downloading")
        )
        
        filepath = await engine.download(url)
        
        sent_message = await update.message.reply_video(
            video=open(filepath, 'rb'),
            supports_streaming=True,
            reply_markup=ui.get_success_keyboard()
        )
        
        # 4. Cache the new file_id
        if redis_client and sent_message.video:
            await redis_client.set(f"url_cache:{url}", sent_message.video.file_id, ex=3600 * 24) # Cache for 24 hours

        await status_message.delete()

    except DownloadError as e:
        logger.error(f"A known download error occurred for {url}: {e.message}")
        await status_message.edit_text(f"[⚠️] {e.message}")
    except Exception as e:
        logger.critical(f"An unexpected error occurred for {url}: {e}", exc_info=True)
        await status_message.edit_text("[❌] حدث خطأ غير متوقع. فريقنا يعمل على إصلاحه.")
    finally:
        if filepath:
            await cleanup_file(filepath)

# --- Callback Query Handlers ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'new_download':
        await query.message.reply_text("بالتأكيد! أرسل لي رابطاً جديداً.")
    elif query.data == 'supported_platforms':
        await query.message.reply_text("أنا أدعم تحميل الفيديوهات من معظم المنصات الشهيرة مثل يوتيوب، تيك توك، انستغرام، فيسبوك، وتويتر (X).")
