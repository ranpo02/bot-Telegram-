# app.py
# ✨ الإصدار النهائي الحقيقي - مع إصلاح Markdown وكل الميزات ✨

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

# ==============================================================================
# 1. CONFIGURATION
# ==============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))
ADMIN_ID = "5898628858"

DOWNLOAD_PATH = Path("downloads")
PRIMARY_PROXY = "154.3.236.202:3128"
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
for logger_name in ["httpx", "werkzeug", "telegram.ext.Application"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

# ==============================================================================
# 2. UI & MESSAGES
# ==============================================================================
ANALYZING_MESSAGE = "⏳ جاري تحليل الرابط، يرجى الانتظار..."
UPLOADING_MESSAGE = "⚡️ تم التحميل، جاري الرفع إليك..."
INVALID_URL_MESSAGE = "⚠️ عذرًا، الرابط الذي أرسلته غير صالح. يرجى التحقق منه والمحاولة مرة أخرى."
GENERIC_ERROR_MESSAGE = "❌ حدث خطأ غير متوقع. تم إبلاغ المطور للمراجعة."
ANALYSIS_FAILED_MESSAGE = "❌ فشل تحليل الرابط. قد يكون المحتوى خاصًا، محذوفًا، أو من منصة غير مدعومة حاليًا."
SESSION_EXPIRED_MESSAGE = "⚠️ انتهت صلاحية هذه الجلسة. يرجى إرسال الرابط مرة أخرى."

def format_duration(s): return f"{s//3600:02d}:{s//60%60:02d}:{s%60:02d}" if s and s > 3600 else f"{s//60:02d}:{s%60:02d}" if s else "غير محدد"
def format_count(n): return f"{n/1_000_000:.1f}M" if n and n >= 1_000_000 else f"{n/1_000:.1f}K" if n and n >= 1_000 else str(n or "غير محدد")

def escape_markdown(text: str) -> str:
    if not text: return ""
    text = str(text)
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# ==============================================================================
# 3. CORE LOGIC
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
        'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
        'outtmpl': str(DOWNLOAD_PATH / '%(id)s.%(ext)s'),
        'ffmpeg_location': '/usr/bin/ffmpeg',
    }
    is_meta_platform = 'instagram.com' in url or 'facebook.com' in url
    if not is_meta_platform:
        opts['proxy'] = PRIMARY_PROXY
        logging.info(f"Generic URL detected. Applying proxy.")
    else:
        logging.info(f"Meta platform URL detected. Using direct connection.")
    return opts

async def run_ydl_analysis(url: str) -> dict:
    ydl_opts = get_base_ydl_opts(url)
    ydl_opts['skip_download'] = True
    try:
        logging.info(f"YDL Analysis started for URL: {url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return await asyncio.to_thread(ydl.extract_info, url, download=False)
    except Exception as e:
        logging.error(f"YDL Analysis failed for {url}: {e}")
        raise AnalysisError(f"فشل تحليل الرابط. الخطأ: {str(e)}")

async def run_ydl_download(url: str, format_id: str, is_audio: bool) -> str:
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    ydl_opts = get_base_ydl_opts(url)
    if is_audio:
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}]
    else:
        ydl_opts['format'] = format_id
    try:
        logging.info(f"YDL Download started for URL: {url} | Format: {ydl_opts.get('format')}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=True)
            original_filename = ydl.prepare_filename(info)
            if is_audio:
                return Path(original_filename).with_suffix('.mp3')
            return original_filename
    except Exception as e:
        logging.error(f"Download failed for {url}: {e}")
        raise DownloadError(f"فشل التحميل. الخطأ: {str(e)}")

# ==============================================================================
# 4. UI BUILDERS
# ==============================================================================

def build_youtube_ui(info: dict, msg_id: int) -> (str, InlineKeyboardMarkup):
    caption = (
        f"🎬 **يوتيوب**\n\n"
        f"ር **العنوان:** {escape_markdown(info.get('title'))}\n"
        f"👤 **القناة:** {escape_markdown(info.get('uploader'))}\n"
        f"🕑 **المدة:** {escape_markdown(format_duration(info.get('duration')))}\n"
        f"👁️ **المشاهدات:** {escape_markdown(format_count(info.get('view_count')))}"
    )
    video_formats = [f for f in info.get('formats', []) if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and f.get('height', 0) <= 720]
    audio_formats = [f for f in info.get('formats', []) if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
    best_video = max(video_formats, key=lambda x: x.get('height', 0), default=None)
    best_audio = max(audio_formats, key=lambda x: x.get('abr', 0), default=None)
    buttons = []
    if best_video:
        buttons.append(InlineKeyboardButton(f"🎬 فيديو ({best_video.get('height')}p)", callback_data=f"dl:v:{best_video['format_id']}:{msg_id}"))
    if best_audio:
        buttons.append(InlineKeyboardButton(f"🎵 صوت (MP3)", callback_data=f"dl:a:best:{msg_id}"))
    keyboard = [buttons]
    if len(video_formats) > 1:
        keyboard.append([InlineKeyboardButton("🎞️ جودات أخرى", callback_data=f"qualities:v:na:{msg_id}")])
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")])
    return caption, InlineKeyboardMarkup(keyboard)

def build_instagram_ui(info: dict, msg_id: int) -> (str, InlineKeyboardMarkup):
    uploader = info.get('uploader', 'غير معروف')
    caption = f"📸 **انستغرام**\n\n👤 **الحساب:** {escape_markdown(uploader)}"
    if 'entries' in info:
        caption += f"\n\nهذا المنشور يحتوي على **{len(info['entries'])}** من الصور/الفيديوهات."
        keyboard = [[InlineKeyboardButton("📥 تحميل الكل (ZIP)", callback_data=f"dl:ig_gallery:all:{msg_id}")]]
    else:
        media_type = "فيديو" if info.get('duration') else "صورة"
        keyboard = [[InlineKeyboardButton(f"🎬 تحميل ال{media_type}", callback_data=f"dl:v:best:{msg_id}")]]
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")])
    return caption, InlineKeyboardMarkup(keyboard)

def build_generic_ui(info: dict, msg_id: int) -> (str, InlineKeyboardMarkup):
    site_name = info.get('extractor_key', 'Website').capitalize()
    caption = (
        f"🌐 **{escape_markdown(site_name)}**\n\n"
        f"ር **العنوان:** {escape_markdown(info.get('title', 'غير متوفر'))}"
    )
    buttons = [
        InlineKeyboardButton("🎬 تحميل الفيديو", callback_data=f"dl:v:best:{msg_id}"),
        InlineKeyboardButton("🎵 تحميل الصوت", callback_data=f"dl:a:best:{msg_id}")
    ]
    keyboard = [buttons, [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")]]
    return caption, InlineKeyboardMarkup(keyboard)

# ==============================================================================
# 5. TELEGRAM HANDLERS
# ==============================================================================

async def report_error(context: ContextTypes.DEFAULT_TYPE, user_id: int, url: str, error_message: str, error_type: str):
    if not ADMIN_ID: return
    report = (
        f"🚨 {escape_markdown(error_type)} 🚨\n\n"
        f"**المستخدم:** `{user_id}`\n"
        f"**الرابط:** `{escape_markdown(url)}`\n"
        f"**الخطأ:** `{escape_markdown(error_message)}`"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=report, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logging.error(f"Failed to send error report: {e}")

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
        if 'youtube' in platform:
            caption, keyboard = build_youtube_ui(info, msg.message_id)
        elif 'instagram' in platform:
            caption, keyboard = build_instagram_ui(info, msg.message_id)
        else:
            caption, keyboard = build_generic_ui(info, msg.message_id)
        thumbnail = info.get('thumbnail')
        if thumbnail:
            await msg.delete()
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=thumbnail, caption=caption, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard)
        else:
            await msg.edit_text(text=caption, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard)
    except (AnalysisError, Exception) as e:
        await report_error(context, update.effective_user.id, url, str(e), "خطأ تحليل")
        try:
            await msg.edit_text(escape_markdown(ANALYSIS_FAILED_MESSAGE), parse_mode=ParseMode.MARKDOWN_V2)
        except BadRequest:
            await msg.edit_text(escape_markdown(GENERIC_ERROR_MESSAGE), parse_mode=ParseMode.MARKDOWN_V2)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action, media_type, format_id, msg_id_str = query.data.split(':')
    msg_id = int(msg_id_str)
    if action == "cancel":
        await query.message.delete(); return
    if msg_id not in context.user_data:
        await query.edit_message_text(SESSION_EXPIRED_MESSAGE); return
    info = context.user_data[msg_id]
    url = info.get('webpage_url')
    if action == "qualities":
        video_formats = sorted([f for f in info.get('formats', []) if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and f.get('height')], key=lambda x: x.get('height', 0), reverse=True)
        quality_buttons = [InlineKeyboardButton(f"{f.get('height')}p", callback_data=f"dl:v:{f['format_id']}:{msg_id}") for f in video_formats if f.get('height')]
        keyboard = [quality_buttons[i:i + 3] for i in range(0, len(quality_buttons), 3)]
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"back:na:na:{msg_id}")])
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return
    if action == "back":
        _, keyboard = build_youtube_ui(info, msg_id)
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
        # --- الإصلاح الحاسم هنا ---
        current_caption = query.message.caption_markdown_v2
        loading_text = escape_markdown("\n\n⏳ جارٍ التحميل، قد يستغرق الأمر بعض الوقت...")
        await context.bot.edit_message_caption(chat_id=query.message.chat_id, message_id=query.message.message_id, caption=current_caption + loading_text, parse_mode=ParseMode.MARKDOWN_V2)
    except BadRequest: pass
    file_path, download_dir_path = None, None
    try:
        if media_type == "ig_gallery":
            download_dir_path = DOWNLOAD_PATH / f"gallery_{msg_id}"
            download_dir_path.mkdir(exist_ok=True)
            ydl_opts = get_base_ydl_opts(url)
            ydl_opts['outtmpl'] = str(download_dir_path / '%(id)s_%(playlist_index)s.%(ext)s')
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                await asyncio.to_thread(ydl.download, [url])
            zip_path = DOWNLOAD_PATH / f"instagram_{info.get('id', 'gallery')}.zip"
            with zipfile.ZipFile(zip_path, 'w') as zipf:
                for entry in download_dir_path.iterdir(): zipf.write(entry, entry.name)
            await context.bot.send_document(query.message.chat_id, document=open(zip_path, 'rb'), caption="✅ تم تحميل المنشور بنجاح كملف ZIP.")
            file_path = zip_path
        else:
            is_audio = (media_type == 'a')
            file_path = await run_ydl_download(url, format_id, is_audio)
            # --- والإصلاح الحاسم هنا أيضًا ---
            base_caption = query.message.caption_markdown_v2.split('\n\n⏳')[0]
            uploading_text = escape_markdown(f"\n\n{UPLOADING_MESSAGE}")
            await context.bot.edit_message_caption(chat_id=query.message.chat_id, message_id=query.message.message_id, caption=base_caption + uploading_text, parse_mode=ParseMode.MARKDOWN_V2)
            if not file_path or not os.path.exists(file_path):
                raise DownloadError("فشل إنشاء الملف النهائي على الخادم.")
            if is_audio:
                await context.bot.send_audio(query.message.chat_id, audio=open(file_path, 'rb'), title=info.get('title'), duration=info.get('duration'))
            else:
                await context.bot.send_video(query.message.chat_id, video=open(file_path, 'rb'), caption=f"✅ {escape_markdown(info.get('title', ''))}", parse_mode=ParseMode.MARKDOWN_V2, supports_streaming=True)
        await query.message.delete()
    except (DownloadError, CoreError) as e:
        await report_error(context, query.from_user.id, url, str(e), "خطأ تحميل/معالجة")
        await context.bot.send_message(query.message.chat_id, f"❌ فشل الإجراء: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logging.error(f"Critical error in button_handler: {e}", exc_info=True)
        await report_error(context, query.from_user.id, url, str(e), "خطأ حرج في button_handler")
        await context.bot.send_message(query.message.chat_id, GENERIC_ERROR_MESSAGE)
    finally:
        if file_path and os.path.exists(str(file_path)): os.remove(str(file_path))
        if download_dir_path and os.path.exists(download_dir_path):
            for f in download_dir_path.iterdir(): os.remove(f)
            os.rmdir(download_dir_path)
        if msg_id in context.user_data: del context.user_data[msg_id]

# ==============================================================================
# 6. APPLICATION SETUP & ENTRY POINT
# ==============================================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(context.error, BadRequest) and "Message is not modified" in str(context.error):
        return
    logging.error(f"Exception while handling an update:", exc_info=context.error)
    user_id = update.effective_user.id if update and hasattr(update, 'effective_user') else "N/A"
    await report_error(context, user_id, "N/A", str(context.error), "خطأ غير معالج (Handler)")

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

