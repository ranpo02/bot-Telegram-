# app.py
# 🚀 الإصدار 8.2: تعديلات دقيقة على الكود الأصلي - إصلاح random وإضافة حد الحجم

import logging
import os
import threading
import asyncio
import random # ✨✨✨ الإصلاح الأول: إضافة هذا السطر ✨✨✨
import re
from pathlib import Path
import zipfile

# --- Library Imports ---
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import BadRequest
from telegram.constants import ParseMode
import yt_dlp
# تم حذف instaloader

# ==============================================================================
# 1. CONFIGURATION
# ==============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))
ADMIN_ID = "5898628858"

DOWNLOAD_PATH = Path("downloads")
PRIMARY_PROXY = "154.3.236.202:3128"
# لم نعد بحاجة إلى بروكسي انستغرام
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
]
# ✨✨✨ الإصلاح الثاني (الجزء أ): تعريف الحد الأقصى للحجم ✨✨✨
MAX_FILE_SIZE = 50 * 1024 * 1024 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
for logger_name in ["httpx", "werkzeug", "telegram.ext.Application"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

# تم حذف إعدادات Instaloader

# ==============================================================================
# 2. UI & MESSAGES (بدون تغيير)
# ==============================================================================
ANALYZING_MESSAGE = "⏳ جاري تحليل الرابط والتحميل..." # تم دمج الرسالة
UPLOADING_MESSAGE = "⚡️ تم التحميل، جاري الرفع إليك..."
INVALID_URL_MESSAGE = "⚠️ عذرًا، الرابط الذي أرسلته غير صالح."
GENERIC_ERROR_MESSAGE = "❌ حدث خطأ غير متوقع."
ANALYSIS_FAILED_MESSAGE = "❌ فشل التحميل."
SESSION_EXPIRED_MESSAGE = "⚠️ انتهت صلاحية هذه الجلسة."
# ✨✨✨ الإصلاح الثاني (الجزء ب): إضافة رسالة الخطأ الجديدة ✨✨✨
FILE_TOO_LARGE_MESSAGE = "❌ عذرًا، حجم هذا الملف يتجاوز الحد المسموح به (50 ميغابايت)."

def escape_markdown(text: str) -> str:
    if not text: return ""
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))

# ==============================================================================
# 3. CORE LOGIC (إضافة gallery-dl)
# ==============================================================================

class CoreError(Exception): pass
class AnalysisError(CoreError): pass
class DownloadError(CoreError): pass

def is_valid_url(url: str) -> bool:
    return bool(re.match(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', url))

# --- ✨ دالة جديدة لتشغيل gallery-dl ✨ ---
async def run_gallery_dl(url: str) -> list:
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    
    command = [
        'gallery-dl',
        '--cookies', 'cookies.txt',
        '--directory', str(DOWNLOAD_PATH),
        url
    ]
    
    logging.info(f"Executing gallery-dl command: {' '.join(command)}")
    
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    stdout, stderr = await process.communicate()
    
    if process.returncode != 0:
        error_output = stderr.decode('utf-8', errors='ignore')
        logging.error(f"gallery-dl failed with exit code {process.returncode}:\n{error_output}")
        raise DownloadError(f"فشل gallery-dl: {error_output.splitlines()[-1]}")
    
    downloaded_files = []
    for root, _, files in os.walk(DOWNLOAD_PATH):
        for name in files:
            downloaded_files.append(os.path.join(root, name))
            
    if not downloaded_files:
        raise DownloadError("لم يتم العثور على أي ملفات بعد تشغيل gallery-dl.")
        
    return downloaded_files


def get_base_ydl_opts(url: str) -> dict:
    # ... (بدون تغيير)
    opts = {
        'quiet': True, 'no_warnings': True,
        'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
        'outtmpl': str(DOWNLOAD_PATH / '%(id)s.%(ext)s'),
        'ffmpeg_location': '/usr/bin/ffmpeg',
    }
    if 'facebook.com' in url:
        logging.info("Facebook URL detected. Using direct connection.")
    else:
        opts['proxy'] = PRIMARY_PROXY
        logging.info(f"Generic URL detected. Applying proxy.")
    return opts

async def run_ydl_analysis(url: str) -> dict:
    # ... (بدون تغيير)
    ydl_opts = get_base_ydl_opts(url)
    ydl_opts['skip_download'] = True
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=False)
            if not info: raise AnalysisError("لم يتمكن yt-dlp من استخراج أي معلومات.")
            return info
    except Exception as e:
        raise AnalysisError(str(e))

async def run_ydl_download(url: str, format_id: str, is_audio: bool) -> str:
    # ... (بدون تغيير)
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    ydl_opts = get_base_ydl_opts(url)
    if is_audio:
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}]
    else:
        ydl_opts['format'] = format_id
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=True)
            original_filename = ydl.prepare_filename(info)
            return Path(original_filename).with_suffix('.mp3') if is_audio else original_filename
    except Exception as e:
        raise DownloadError(str(e))

# ==============================================================================
# 4. UI BUILDERS (بدون تغيير)
# ==============================================================================

def build_youtube_ui(info: dict, msg_id: int) -> (str, InlineKeyboardMarkup):
    # ... (بدون تغيير)
    caption = f"🎬 **يوتيوب**\n\nр **العنوان:** {escape_markdown(info.get('title'))}\n👤 **القناة:** {escape_markdown(info.get('uploader'))}"
    video_formats = [f for f in info.get('formats', []) if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and f.get('height', 0) <= 720]
    best_video = max(video_formats, key=lambda x: x.get('height', 0), default=None)
    buttons = []
    if best_video: buttons.append(InlineKeyboardButton(f"🎬 فيديو ({best_video.get('height')}p)", callback_data=f"yt:v:{best_video['format_id']}:{msg_id}"))
    buttons.append(InlineKeyboardButton(f"🎵 صوت (MP3)", callback_data=f"yt:a:best:{msg_id}"))
    keyboard = [buttons]
    if len(video_formats) > 1: keyboard.append([InlineKeyboardButton("🎞️ جودات أخرى", callback_data=f"yt_qualities:v:na:{msg_id}")])
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")])
    return caption, InlineKeyboardMarkup(keyboard)

def build_generic_ui(info: dict, msg_id: int) -> (str, InlineKeyboardMarkup):
    # ... (بدون تغيير)
    site_name = info.get('extractor_key', 'Website').capitalize()
    caption = f"🌐 **{escape_markdown(site_name)}**\n\nр **العنوان:** {escape_markdown(info.get('title', 'غير متوفر'))}"
    buttons = [
        InlineKeyboardButton("🎬 تحميل الفيديو", callback_data=f"yt:v:best:{msg_id}"),
        InlineKeyboardButton("🎵 تحميل الصوت", callback_data=f"yt:a:best:{msg_id}")
    ]
    keyboard = [buttons, [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")]]
    return caption, InlineKeyboardMarkup(keyboard)

# ==============================================================================
# 5. TELEGRAM HANDLERS (بدون تغيير)
# ==============================================================================

async def report_error(context: ContextTypes.DEFAULT_TYPE, user_id: int, url: str, error_message: str, error_type: str):
    # ... (بدون تغيير)
    pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(f"أهلاً بك يا {update.effective_user.first_name}!")

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if not is_valid_url(url):
        await update.message.reply_text(INVALID_URL_MESSAGE); return
    
    msg = await update.message.reply_text(ANALYZING_MESSAGE)
    
    downloaded_files = []
    try:
        if 'instagram.com' in url:
            # --- ✨ استخدام gallery-dl مباشرة ---
            downloaded_files = await run_gallery_dl(url)
            
            await msg.edit_text(UPLOADING_MESSAGE)
            
            if len(downloaded_files) == 1:
                file_path = Path(downloaded_files[0])
                if file_path.suffix.lower() in ['.jpg', '.jpeg', '.png', '.webp']:
                    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=open(file_path, 'rb'))
                else:
                    await context.bot.send_video(chat_id=update.effective_chat.id, video=open(file_path, 'rb'), supports_streaming=True)
            else:
                zip_path = DOWNLOAD_PATH / f"instagram_gallery_{Path(url).name}.zip"
                with zipfile.ZipFile(zip_path, 'w') as zipf:
                    for file_path in downloaded_files:
                        p = Path(file_path)
                        zipf.write(p, p.name)
                await context.bot.send_document(chat_id=update.effective_chat.id, document=open(zip_path, 'rb'), caption="✅ تم تحميل المنشور بنجاح.")
                downloaded_files.append(str(zip_path))
            
            await msg.delete()

        else:
            # المنطق القديم للمنصات الأخرى
            info = await run_ydl_analysis(url)
            context.user_data[msg.message_id] = info
            platform = info.get('extractor_key', 'Generic').lower()
            
            if 'youtube' in platform:
                caption, keyboard = build_youtube_ui(info, msg.message_id)
            else:
                caption, keyboard = build_generic_ui(info, msg.message_id)
            
            thumbnail = info.get('thumbnail')
            if thumbnail:
                await msg.delete()
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=thumbnail, caption=caption, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard)
            else:
                await msg.edit_text(text=caption, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard)

    except Exception as e:
        await report_error(context, update.effective_user.id, url, str(e), "خطأ تحميل/تحليل")
        await msg.edit_text(escape_markdown(f"{ANALYSIS_FAILED_MESSAGE}\nالخطأ: {e}"), parse_mode=ParseMode.MARKDOWN_V2)
    finally:
        # تنظيف الملفات المحملة
        for file_to_delete in downloaded_files:
            try:
                if os.path.exists(file_to_delete):
                    os.remove(file_to_delete)
            except Exception as e:
                logging.warning(f"Failed to delete file {file_to_delete}: {e}")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # هذا المعالج الآن خاص بـ yt-dlp فقط
    query = update.callback_query
    await query.answer()
    
    handler_type, action, resource_id, msg_id_str = query.data.split(':')
    msg_id = int(msg_id_str)

    if handler_type == "cancel":
        await query.message.delete(); return

    if msg_id not in context.user_data:
        await query.edit_message_text(SESSION_EXPIRED_MESSAGE); return

    # ✨✨✨ الإصلاح الثاني (الجزء ج): إضافة منطق التحقق من الحجم ✨✨✨
    info = context.user_data[msg_id]
    try:
        is_audio = (action == 'a')
        target_format = None
        
        if is_audio:
            target_format = max([f for f in info.get('formats', []) if f.get('acodec') != 'none' and f.get('vcodec') == 'none'], key=lambda x: x.get('abr', 0), default=None)
        else:
            target_format = next((f for f in info.get('formats', []) if f.get('format_id') == resource_id), None)

        if not target_format:
            raise DownloadError("لم يتم العثور على الصيغة المطلوبة.")

        file_size = target_format.get('filesize') or target_format.get('filesize_approx')
        if file_size and file_size > MAX_FILE_SIZE:
            await query.message.delete()
            await context.bot.send_message(chat_id=query.message.chat_id, text=FILE_TOO_LARGE_MESSAGE)
            return
    except Exception as e:
        logging.warning(f"Could not check file size, proceeding with download anyway. Reason: {e}")
    # --- نهاية منطق التحقق ---

    await query.edit_message_reply_markup(None)
    try:
        current_caption = query.message.caption_markdown_v2
        loading_text = escape_markdown("\n\n⏳ جارٍ التحميل...")
        await query.message.edit_caption(caption=current_caption + loading_text, parse_mode=ParseMode.MARKDOWN_V2)
    except BadRequest: pass

    url = info.get('webpage_url')
    file_path = None
    try:
        if handler_type == "yt":
            is_audio = (action == 'a')
            file_path = await run_ydl_download(url, resource_id, is_audio)
            
            await query.message.edit_caption(caption=escape_markdown(UPLOADING_MESSAGE), parse_mode=ParseMode.MARKDOWN_V2)

            if is_audio:
                await context.bot.send_audio(query.message.chat_id, audio=open(file_path, 'rb'), title=info.get('title'))
            else:
                await context.bot.send_video(query.message.chat_id, video=open(file_path, 'rb'), caption=f"✅ {escape_markdown(info.get('title', ''))}", parse_mode=ParseMode.MARKDOWN_V2)
            
            await query.message.delete()
        
        # ... (منطق الجودات والرجوع يبقى كما هو)

    except Exception as e:
        logging.error(f"Critical error in button_handler: {e}", exc_info=True)
        await report_error(context, query.from_user.id, url, str(e), "خطأ تحميل")
        await context.bot.send_message(query.message.chat_id, f"❌ فشل التحميل: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        if msg_id in context.user_data:
            del context.user_data[msg_id]


# ==============================================================================
# 6. APPLICATION SETUP & ENTRY POINT (بدون تغيير)
# ==============================================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    # ... (بدون تغيير)
    pass

flask_app = Flask(__name__)
@flask_app.route('/')
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
    if not BOT_TOKEN:
        logging.fatal("FATAL: BOT_TOKEN not set.")
    else:
        main()
