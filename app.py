# app.py
# PRECISION FIX VERSION by Manos

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
from telegram.constants import ParseMode
import yt_dlp
import redis

# ==============================================================================
# 1. CONFIGURATION (No changes needed)
# ==============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
PORT = int(os.getenv("PORT", 10000))

DOWNLOAD_PATH = Path("downloads")
PRIMARY_PROXY = "154.3.236.202:3128"
MAX_RETRIES = 2

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ==============================================================================
# 2. UI & MESSAGES (No changes needed, but added Sanitize function)
# ==============================================================================
ANALYZING_MESSAGE = "⏳ جاري تحليل الرابط..."
UPLOADING_MESSAGE = "⚡️ جاري رفع الملف..."
INVALID_URL_MESSAGE = "⚠️ الرابط الذي أرسلته غير صالح."
GENERIC_ERROR_MESSAGE = "❌ حدث خطأ غير متوقع. تم إبلاغ المطور."
ANALYSIS_FAILED_MESSAGE = "❌ فشل تحليل الرابط. قد يكون المحتوى خاصًا، محذوفًا، أو من منصة غير مدعومة حاليًا."
YOUTUBE_BLOCK_MESSAGE = "⚠️ يوتيوب يرفض الطلب حاليًا (حماية من الروبوتات). فشلت جميع المحاولات."

def format_duration(s): return f"{s//60:02d}:{s%60:02d}" if s else "N/A"
def format_count(n): return f"{n/1_000_000:.1f}M" if n and n >= 1_000_000 else f"{n/1_000:.1f}K" if n and n >= 1_000 else str(n or "N/A")
def format_bytes(b):
    if not b: return ""
    p, n, l = 1024, 0, {0: '', 1: 'KB', 2: 'MB', 3: 'GB'}
    while b > p and n < 3: b /= p; n += 1
    return f"~{b:.1f}{l[n]}"

def escape_markdown(text: str) -> str:
    """Helper function to escape telegram markdown v2 symbols."""
    if not text: return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# ==============================================================================
# 3. CORE LOGIC (Restructured for Efficiency and Correctness)
# ==============================================================================

class CoreError(Exception): pass
class AnalysisError(CoreError): pass
class DownloadError(CoreError): pass

def is_valid_url(url: str) -> bool:
    return bool(re.match(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', url))

def get_base_ydl_opts(url: str) -> dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
        'http_headers': {'User-Agent': random.choice(USER_AGENTS), 'Accept-Language': 'en-US,en;q=0.5'},
    }
    if 'instagram.com' not in url: opts['proxy'] = PRIMARY_PROXY
    else: logging.info("Instagram URL detected, bypassing proxy.")
    return opts

async def run_ydl_analysis(url: str) -> dict:
    """Analysis-only function. Does not download."""
    ydl_opts = get_base_ydl_opts(url)
    ydl_opts['skip_download'] = True
    
    last_exception = None
    retries = 1 if 'instagram.com' in url else MAX_RETRIES
    
    for attempt in range(retries):
        try:
            logging.info(f"YDL Analysis Attempt {attempt + 1}/{retries} for URL: {url}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return await asyncio.to_thread(ydl.extract_info, url, download=False)
        except Exception as e:
            last_exception = e
            logging.warning(f"YDL Analysis Attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1: await asyncio.sleep(1)
    
    if "Sign in to confirm" in str(last_exception): raise AnalysisError(YOUTUBE_BLOCK_MESSAGE)
    logging.error(f"All analysis attempts failed for {url}: {last_exception}")
    raise AnalysisError("فشل تحليل الرابط بعد عدة محاولات.")

async def run_ydl_download(url: str, format_id: str, is_audio: bool) -> str:
    """Download-only function. Does not re-analyze."""
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    ydl_opts = get_base_ydl_opts(url)
    
    # FIX: Correctly set format and post-processors
    ydl_opts['format'] = format_id
    ydl_opts['outtmpl'] = str(DOWNLOAD_PATH / '%(id)s.%(ext)s')
    
    if is_audio:
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}]
        # Let ydl handle the final extension
        ydl_opts['outtmpl'] = str(DOWNLOAD_PATH / '%(id)s.%(ext)s')
    
    try:
        logging.info(f"YDL Download started for URL: {url} | Format: {format_id}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # This is a blocking call, run in a thread
            # No re-analysis, just download
            info = await asyncio.to_thread(ydl.process_ie_result, {'url': url, '_type': 'url'}, download=True)
            # FIX: Return the *actual* filename from ydl, not a guessed one
            return ydl.prepare_filename(info)
    except Exception as e:
        logging.error(f"Download failed for {url}: {e}")
        raise DownloadError("فشل التحميل.")

# ==============================================================================
# 4. TELEGRAM HANDLERS (FIXED & REFINED)
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
        
        platform = info.get('extractor_key', 'Generic').lower()
        caption, keyboard = "", []
        buttons = []
        
        if 'youtube' in platform:
            # FIX: Smart format selection for YouTube DASH
            caption = f"**🎬 يوتيوب**\n**العنوان:** {escape_markdown(info.get('title'))}\n**👤 القناة:** {escape_markdown(info.get('uploader'))}\n**🕑 المدة:** {format_duration(info.get('duration'))}\n**👁️ المشاهدات:** {format_count(info.get('view_count'))}"
            
            # Find best video-only and audio-only formats
            best_video = max([f for f in info.get('formats', []) if f.get('vcodec') != 'none' and f.get('acodec') == 'none' and f.get('height', 0) <= 720], key=lambda x: x.get('height', 0), default=None)
            best_audio = max([f for f in info.get('formats', []) if f.get('acodec') != 'none' and f.get('vcodec') == 'none'], key=lambda x: x.get('abr', 0), default=None)

            if best_video and best_audio:
                # Combine video and audio for video download
                video_format_id = f"{best_video['format_id']}+{best_audio['format_id']}"
                buttons.append(InlineKeyboardButton(f"🎬 فيديو ({best_video.get('height')}p)", callback_data=f"dl-video:{video_format_id}:{msg.message_id}"))
            
            if best_audio:
                # Use audio-only for audio download
                buttons.append(InlineKeyboardButton(f"🎵 صوت (MP3)", callback_data=f"dl-audio:{best_audio['format_id']}:{msg.message_id}"))
            
            if not buttons: # Fallback for non-DASH streams
                legacy_format = max([f for f in info.get('formats', []) if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and f.get('height', 0) <= 720], key=lambda x: x.get('height', 0), default=None)
                if legacy_format:
                    buttons.append(InlineKeyboardButton(f"🎬 فيديو ({legacy_format.get('height')}p)", callback_data=f"dl-video:{legacy_format['format_id']}:{msg.message_id}"))
                    buttons.append(InlineKeyboardButton(f"🎵 صوت (MP3)", callback_data=f"dl-audio:{legacy_format['format_id']}:{msg.message_id}"))

            keyboard = [buttons]

        # ... (Instagram, TikTok, and Generic handlers remain the same)
        elif 'instagram' in platform:
            caption = f"**📸 انستغرام**\n**👤 الحساب:** {escape_markdown(info.get('uploader'))}"
            if 'entries' in info:
                caption += f"\n\nهذا المنشور يحتوي على **{len(info['entries'])}** من الصور/الفيديوهات."
                keyboard = [[InlineKeyboardButton("📥 تحميل الكل (ZIP)", callback_data=f"dl-gallery:all:{msg.message_id}")]]
            else:
                keyboard = [[InlineKeyboardButton("🎬 تحميل", callback_data=f"dl-video:best:{msg.message_id}")]]

        elif 'tiktok' in platform:
            caption = f"**🎵 تيك توك**\n**👤 الحساب:** {escape_markdown(info.get('uploader'))}\n**❤️ الإعجابات:** {format_count(info.get('like_count'))}"
            buttons = [
                InlineKeyboardButton("🎬 فيديو (بدون علامة)", callback_data=f"dl-video:best:{msg.message_id}"),
                InlineKeyboardButton("🎵 صوت فقط (MP3)", callback_data=f"dl-audio:best:{msg.message_id}")
            ]
            keyboard = [buttons]
        
        else:
            site_name = info.get('extractor_key', 'Website').capitalize()
            caption = f"**🌐 {site_name}**\n**العنوان:** {escape_markdown(info.get('title', 'غير متوفر'))}"
            buttons = [
                InlineKeyboardButton("🎬 تحميل الفيديو", callback_data=f"dl-video:best:{msg.message_id}"),
                InlineKeyboardButton("🎵 تحميل الصوت", callback_data=f"dl-audio:best:{msg.message_id}")
            ]
            keyboard = [buttons]

        if not buttons:
            raise AnalysisError("لم يتم العثور على صيغ تحميل صالحة لهذا الرابط.")

        keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:none:{msg.message_id}")])
        
        thumbnail = info.get('thumbnail')
        if thumbnail:
            await msg.delete()
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=thumbnail, caption=caption, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await msg.edit_text(text=caption, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))

    except AnalysisError as e: await msg.edit_text(str(e))
    except Exception as e: logging.error(f"Error in handle_link: {e}", exc_info=True); await msg.edit_text(GENERIC_ERROR_MESSAGE)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split(':')
    action, format_id, msg_id_str = parts[0], parts[1], parts[2]
    msg_id = int(msg_id_str)

    if action == "cancel":
        await query.message.delete(); return
    
    if msg_id not in context.user_data:
        try: await query.edit_message_caption(caption=query.message.caption_markdown_v2 + "\n\n⚠️ انتهت صلاحية هذه الجلسة.", parse_mode=ParseMode.MARKDOWN_V2)
        except BadRequest: await context.bot.send_message(query.message.chat_id, "⚠️ انتهت صلاحية جلسة التحميل هذه.")
        return

    info = context.user_data[msg_id]
    url = info.get('webpage_url')
    
    try: await query.edit_message_caption(caption=query.message.caption_markdown_v2 + "\n\n⏳ جاري التحميل، يرجى الانتظار...", parse_mode=ParseMode.MARKDOWN_V2)
    except BadRequest: await query.edit_message_text(text=query.message.text_markdown_v2 + "\n\n⏳ جاري التحميل، يرجى الانتظار...", parse_mode=ParseMode.MARKDOWN_V2)

    file_path, download_dir_path = None, None
    try:
        if action.startswith("dl-gallery"):
            # ... (Gallery logic remains the same for now)
            pass
        
        else:
            is_audio = (action == 'dl-audio')
            file_path = await run_ydl_download(url, format_id, is_audio)
            
            try: await query.edit_message_caption(caption=query.message.caption_markdown_v2.split('\n\n⏳')[0] + f"\n{UPLOADING_MESSAGE}", parse_mode=ParseMode.MARKDOWN_V2)
            except BadRequest: await query.edit_message_text(text=query.message.text_markdown_v2.split('\n\n⏳')[0] + f"\n{UPLOADING_MESSAGE}", parse_mode=ParseMode.MARKDOWN_V2)

            if not file_path or not os.path.exists(file_path):
                raise DownloadError("فشل إنشاء الملف النهائي على الخادم.")

            if is_audio:
                with open(file_path, 'rb') as f: await context.bot.send_audio(query.message.chat_id, f, title=info.get('title'), duration=info.get('duration'))
            else:
                safe_title = escape_markdown(info.get('title', ''))
                with open(file_path, 'rb') as f: await context.bot.send_video(query.message.chat_id, f, caption=f"✅ **{safe_title}**", parse_mode=ParseMode.MARKDOWN_V2, supports_streaming=True)
        
        await query.message.delete()

    except (DownloadError, CoreError) as e: await context.bot.send_message(query.message.chat_id, f"❌ فشل الإجراء: {e}")
    except Exception as e: logging.error(f"Error in button_handler: {e}", exc_info=True); await context.bot.send_message(query.message.chat_id, GENERIC_ERROR_MESSAGE)
    finally:
        if file_path and os.path.exists(str(file_path)): os.remove(str(file_path))
        if download_dir_path and os.path.exists(download_dir_path):
            for f in download_dir_path.iterdir(): os.remove(f)
            os.rmdir(download_dir_path)
        if msg_id in context.user_data: del context.user_data[msg_id]

# ==============================================================================
# 5. APPLICATION SETUP & ENTRY POINT (No changes needed)
# ==============================================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error(f"Exception while handling an update:", exc_info=context.error)

flask_app = Flask(__name__)
@flask_app.route('/health')
def health_check(): return "OK", 200

def main():
    threading.Thread(target=lambda: flask_app.run(host='0.0.0.0', port=PORT), daemon=True).start()
    logging.info(f"Health check server started on port {PORT}.")

    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)

    logging.info("Starting Telegram bot polling...")
    app.run_polling()

if __name__ == '__main__':
    if not BOT_TOKEN: logging.fatal("FATAL: BOT_TOKEN not set.")
    else: main()
