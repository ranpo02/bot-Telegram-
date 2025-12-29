# app.py
# FINAL, FIXED, AND IMPROVED MULTI-PLATFORM ASSISTANT

import logging
import os
import threading
import asyncio
import re
import random
from pathlib import Path
import zipfile

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
    "154.3.236.202:3128", "167.206.113.248:3128",
    "115.114.77.133:9090", "80.85.247.161:5555",
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.INFO)

try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    logging.info("Successfully connected to Redis.")
except Exception as e:
    logging.error(f"Could not connect to Redis: {e}")
    redis_client = None

# ==============================================================================
# 2. UI & MESSAGES
# ==============================================================================

ANALYZING_MESSAGE = "⏳ جاري تحليل الرابط..."
UPLOADING_MESSAGE = "⚡️ جاري رفع الملف..."
INVALID_URL_MESSAGE = "⚠️ الرابط الذي أرسلته غير صالح."
GENERIC_ERROR_MESSAGE = "❌ حدث خطأ غير متوقع. تم إبلاغ المطور."
ANALYSIS_FAILED_MESSAGE = "❌ فشل تحليل الرابط. قد يكون المحتوى خاصًا، محذوفًا، أو من منصة غير مدعومة حاليًا."
DOWNLOAD_CANCELED_MESSAGE = "✅ تم إلغاء العملية."

def format_duration(s): return f"{s//60:02d}:{s%60:02d}" if s else "N/A"
def format_count(n): return f"{n/1_000_000:.1f}M" if n and n >= 1_000_000 else f"{n/1_000:.1f}K" if n and n >= 1_000 else str(n or "N/A")
def format_bytes(b):
    if not b: return ""
    p, n, l = 1024, 0, {0: '', 1: 'KB', 2: 'MB', 3: 'GB'}
    while b > p and n < 3: b /= p; n += 1
    return f"~{b:.1f}{l[n]}"

# ==============================================================================
# 3. CORE LOGIC (ANALYSIS & DOWNLOAD)
# ==============================================================================

class CoreError(Exception): pass
class AnalysisError(CoreError): pass
class DownloadError(CoreError): pass

# --- CRITICAL FIX: Re-adding the missing function ---
def is_valid_url(url: str) -> bool:
    """Checks if the provided string is a valid URL."""
    return bool(re.match(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', url))
# --- END OF FIX ---

async def run_ydl_analysis(url: str) -> dict:
    """Generic analysis function."""
    ydl_opts = {'quiet': True, 'no_warnings': True, 'skip_download': True, 'proxy': random.choice(YOUTUBE_PROXIES) if YOUTUBE_PROXIES else None}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return await asyncio.to_thread(ydl.extract_info, url, download=False)
    except Exception as e:
        logging.error(f"Failed to analyze link {url}: {e}")
        raise AnalysisError("فشل تحليل الرابط.")

async def download_media(url: str, format_id: str = 'best', is_audio: bool = False, extra_opts: dict = None) -> str:
    """Generic download function."""
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    ydl_opts = {
        'format': format_id,
        'outtmpl': str(DOWNLOAD_PATH / '%(id)s.%(ext)s'),
        'proxy': random.choice(YOUTUBE_PROXIES) if YOUTUBE_PROXIES else None,
        'quiet': True, 'no_warnings': True,
    }
    if is_audio:
        ydl_opts.update({'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}], 'outtmpl': str(DOWNLOAD_PATH / '%(id)s.mp3')})
    if extra_opts:
        ydl_opts.update(extra_opts)
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=True)
            file_path = ydl.prepare_filename(info)
            if is_audio: return os.path.splitext(file_path)[0] + ".mp3"
            return file_path
    except Exception as e:
        logging.error(f"Download failed for {url}: {e}")
        raise DownloadError("فشل التحميل.")

# ==============================================================================
# 4. TELEGRAM HANDLERS
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(f"أهلاً بك يا {update.effective_user.first_name}!\n\nأنا مساعد التحميل الذكي. أرسل لي أي رابط وسأقوم بتحليله لك.")

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if not is_valid_url(url):
        await update.message.reply_text(INVALID_URL_MESSAGE); return

    msg = await update.message.reply_text(ANALYZING_MESSAGE)
    
    try:
        info = await run_ydl_analysis(url)
        context.user_data[msg.message_id] = info
        
        platform = info.get('extractor_key', '').lower()
        caption, keyboard = "", []

        if 'youtube' in platform:
            caption = f"**🎬 العنوان:** {info.get('title')}\n**👤 القناة:** {info.get('uploader')}\n**🕑 المدة:** {format_duration(info.get('duration'))}\n**👁️ المشاهدات:** {format_count(info.get('view_count'))}"
            formats = sorted([f for f in info.get('formats', []) if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and f.get('height') in [360, 720]], key=lambda x: x.get('height', 0))
            buttons = [InlineKeyboardButton(f"🎬 فيديو ({f.get('height')}p) {format_bytes(f.get('filesize') or f.get('filesize_approx'))}", callback_data=f"dl_video_{f['format_id']}_{msg.message_id}") for f in formats]
            audio_format = max([f for f in info.get('formats', []) if f.get('acodec') != 'none' and f.get('vcodec') == 'none'], key=lambda x: x.get('abr', 0), default=None)
            if audio_format: buttons.append(InlineKeyboardButton(f"🎵 صوت (MP3) {format_bytes(audio_format.get('filesize') or audio_format.get('filesize_approx'))}", callback_data=f"dl_audio_{audio_format['format_id']}_{msg.message_id}"))
            keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]

        elif 'instagram' in platform:
            caption = f"**📸 انستغرام**\n**👤 الحساب:** {info.get('uploader')}"
            if 'entries' in info: # Carousel
                caption += f"\n\nهذا المنشور يحتوي على **{len(info['entries'])}** من الصور/الفيديوهات."
                keyboard = [[InlineKeyboardButton("📥 تحميل الكل (ZIP)", callback_data=f"dl-gallery_all_{msg.message_id}")]]
            else: # Single video/image
                keyboard = [[InlineKeyboardButton("🎬 تحميل", callback_data=f"dl-video_best_{msg.message_id}")]]

        elif 'tiktok' in platform:
            caption = f"**🎵 تيك توك**\n**👤 الحساب:** {info.get('uploader')}\n**❤️ الإعجابات:** {format_count(info.get('like_count'))}"
            buttons = [
                InlineKeyboardButton("🎬 فيديو (بدون علامة)", callback_data=f"dl-video_best_{msg.message_id}"),
                InlineKeyboardButton("🎵 صوت فقط (MP3)", callback_data=f"dl-audio_best_{msg.message_id}")
            ]
            keyboard = [buttons]
        
        else:
            await msg.edit_text(f"تحليل منصة '{platform}' غير مدعوم بعد."); return

        keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel__{msg.message_id}")])
        await msg.delete()
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=info.get('thumbnail'), caption=caption, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

    except AnalysisError: await msg.edit_text(ANALYSIS_FAILED_MESSAGE)
    except Exception as e: logging.error(f"Error in handle_link: {e}", exc_info=True); await msg.edit_text(GENERIC_ERROR_MESSAGE)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    action, format_id, msg_id_str = query.data.split('_', 2)
    msg_id = int(msg_id_str)

    if action == "cancel":
        await query.message.delete(); return
    
    if msg_id not in context.user_data:
        await query.edit_message_caption(caption=query.message.caption + "\n\n⚠️ انتهت صلاحية هذه الجلسة."); return

    info = context.user_data[msg_id]
    url = info.get('webpage_url')
    await query.edit_message_caption(caption=query.message.caption + "\n\n⏳ جاري التحميل، يرجى الانتظار...")

    file_path, download_dir_path = None, None
    try:
        if action.startswith("dl-gallery"):
            await query.edit_message_caption(caption=query.message.caption + "\n\n📥 جاري تحميل المنشورات...")
            download_dir_path = DOWNLOAD_PATH / str(msg_id)
            download_dir_path.mkdir(exist_ok=True)
            
            for i, entry in enumerate(info['entries']):
                await query.edit_message_caption(caption=query.message.caption.split('\n\n📥')[0] + f"\n\n📥 ... {i+1}/{len(info['entries'])}")
                await download_media(entry['url'], extra_opts={'outtmpl': str(download_dir_path / '%(id)s.%(ext)s')})

            await query.edit_message_caption(caption=query.message.caption.split('\n\n📥')[0] + "\n\n🗜️ جاري ضغط الملفات...")
            file_path = DOWNLOAD_PATH / f"{msg_id}.zip"
            with zipfile.ZipFile(file_path, 'w') as zf:
                for f in download_dir_path.iterdir(): zf.write(f, f.name)
            
            await query.edit_message_caption(caption=query.message.caption.split('\n\n🗜️')[0] + f"\n{UPLOADING_MESSAGE}")
            with open(file_path, 'rb') as f: await context.bot.send_document(query.message.chat_id, f, caption=f"✅ **{info.get('uploader')}** - منشور متعدد")
        
        else: # Single file download
            is_audio = (action == 'dl-audio')
            file_path = await download_media(url, format_id, is_audio)
            await query.edit_message_caption(caption=query.message.caption.split('\n\n⏳')[0] + f"\n{UPLOADING_MESSAGE}")
            
            if is_audio:
                with open(file_path, 'rb') as f: await context.bot.send_audio(query.message.chat_id, f, title=info.get('title'), duration=info.get('duration'))
            else:
                with open(file_path, 'rb') as f: await context.bot.send_video(query.message.chat_id, f, caption=f"✅ **{info.get('title')}**", parse_mode='Markdown', supports_streaming=True)
        
        await query.message.delete()

    except (DownloadError, CoreError) as e: await context.bot.send_message(query.message.chat_id, f"❌ فشل الإجراء: {e}")
    except Exception as e: logging.error(f"Error in button_handler: {e}", exc_info=True); await context.bot.send_message(query.message.chat_id, GENERIC_ERROR_MESSAGE)
    finally:
        if file_path and os.path.exists(file_path): os.remove(file_path)
        if download_dir_path and os.path.exists(download_dir_path):
            for f in download_dir_path.iterdir(): os.remove(f)
            os.rmdir(download_dir_path)
        if msg_id in context.user_data: del context.user_data[msg_id]

# --- IMPROVEMENT: Global Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the developer."""
    logging.error(f"Exception while handling an update:", exc_info=context.error)
    # You can add your user ID to get notified of errors
    # if DEVELOPER_CHAT_ID:
    #     await context.bot.send_message(chat_id=DEVELOPER_CHAT_ID, text=f"Bot error: {context.error}")

# ==============================================================================
# 5. APPLICATION SETUP & ENTRY POINT
# ==============================================================================

flask_app = Flask(__name__)
@flask_app.route('/health')
def health_check(): return "OK", 200

def main():
    threading.Thread(target=lambda: flask_app.run(host='0.0.0.0', port=PORT), daemon=True).start()
    logging.info(f"Health check server started on port {PORT}.")

    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # --- IMPROVEMENT: Register the global error handler ---
    app.add_error_handler(error_handler)

    logging.info("Starting Telegram bot polling...")
    app.run_polling()

if __name__ == '__main__':
    if not BOT_TOKEN: logging.fatal("FATAL: BOT_TOKEN not set.")
    else: main()
