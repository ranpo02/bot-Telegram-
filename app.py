# app.py
# 🚀 الإصدار 10.0: نسخة نهائية محسّنة مع فحص الحجم الذكي

import logging
import os
import threading
import asyncio
import random
import re
import sqlite3
import atexit
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
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
PRIMARY_PROXY = "154.3.236.202:3128"

DOWNLOAD_PATH = Path("downloads")
DB_PATH = Path("bot_stats.db")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
]

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB - الحد الأقصى
MAX_REQUESTS_PER_MINUTE = 5
CLEANUP_INTERVAL = 1800  # 30 دقيقة
FILE_MAX_AGE = 3600  # ساعة واحدة

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
for logger_name in ["httpx", "werkzeug", "telegram.ext.Application"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

# ==============================================================================
# 2. DATABASE SETUP
# ==============================================================================

def init_db():
    """تهيئة قاعدة البيانات"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS downloads
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  username TEXT,
                  url TEXT,
                  platform TEXT,
                  timestamp DATETIME,
                  success BOOLEAN,
                  error_message TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS user_stats
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  total_downloads INTEGER DEFAULT 0,
                  successful_downloads INTEGER DEFAULT 0,
                  failed_downloads INTEGER DEFAULT 0,
                  first_use DATETIME,
                  last_use DATETIME)''')
    conn.commit()
    conn.close()
    logging.info("Database initialized successfully.")

def log_download(user_id: int, username: str, url: str, platform: str, success: bool, error_msg: str = None):
    """تسجيل عملية التحميل"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('''INSERT INTO downloads (user_id, username, url, platform, timestamp, success, error_message)
                     VALUES (?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, username, url, platform, datetime.now(), success, error_msg))
        
        c.execute('''INSERT INTO user_stats (user_id, username, total_downloads, successful_downloads, failed_downloads, first_use, last_use)
                     VALUES (?, ?, 1, ?, ?, ?, ?)
                     ON CONFLICT(user_id) DO UPDATE SET
                     total_downloads = total_downloads + 1,
                     successful_downloads = successful_downloads + ?,
                     failed_downloads = failed_downloads + ?,
                     last_use = ?''',
                  (user_id, username, 1 if success else 0, 1 if not success else 0, datetime.now(), datetime.now(),
                   1 if success else 0, 1 if not success else 0, datetime.now()))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Failed to log download: {e}")

def get_stats():
    """الحصول على إحصائيات البوت"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('SELECT COUNT(DISTINCT user_id) FROM user_stats')
        total_users = c.fetchone()[0]
        
        c.execute('SELECT SUM(total_downloads), SUM(successful_downloads), SUM(failed_downloads) FROM user_stats')
        stats = c.fetchone()
        
        c.execute('SELECT COUNT(*) FROM downloads WHERE DATE(timestamp) = DATE("now")')
        today_downloads = c.fetchone()[0]
        
        conn.close()
        
        return {
            'total_users': total_users,
            'total_downloads': stats[0] or 0,
            'successful': stats[1] or 0,
            'failed': stats[2] or 0,
            'today': today_downloads
        }
    except Exception as e:
        logging.error(f"Failed to get stats: {e}")
        return None

# ==============================================================================
# 3. RATE LIMITING
# ==============================================================================

user_requests = defaultdict(list)

async def check_rate_limit(user_id: int) -> bool:
    """التحقق من حد الطلبات"""
    now = datetime.now()
    user_requests[user_id] = [
        req_time for req_time in user_requests[user_id]
        if now - req_time < timedelta(minutes=1)
    ]
    
    if len(user_requests[user_id]) >= MAX_REQUESTS_PER_MINUTE:
        return False
    
    user_requests[user_id].append(now)
    return True

# ==============================================================================
# 4. FILE CLEANUP
# ==============================================================================

def cleanup_old_files():
    """تنظيف الملفات القديمة"""
    try:
        if not DOWNLOAD_PATH.exists():
            return
        
        now = datetime.now().timestamp()
        deleted_count = 0
        
        for file_path in DOWNLOAD_PATH.glob("*"):
            if file_path.is_file():
                file_age = now - file_path.stat().st_mtime
                if file_age > FILE_MAX_AGE:
                    file_path.unlink()
                    deleted_count += 1
        
        if deleted_count > 0:
            logging.info(f"Cleaned up {deleted_count} old files.")
    except Exception as e:
        logging.error(f"Cleanup error: {e}")

async def periodic_cleanup():
    """تنظيف دوري للملفات"""
    while True:
        cleanup_old_files()
        await asyncio.sleep(CLEANUP_INTERVAL)

def cleanup_on_exit():
    """تنظيف عند إغلاق البوت"""
    try:
        if DOWNLOAD_PATH.exists():
            shutil.rmtree(DOWNLOAD_PATH)
            logging.info("Cleaned up downloads directory on exit.")
    except Exception as e:
        logging.error(f"Failed to cleanup on exit: {e}")

atexit.register(cleanup_on_exit)

# ==============================================================================
# 5. UI & MESSAGES
# ==============================================================================

ANALYZING_MESSAGE = "⏳ جاري تحليل الرابط والتحميل..."
UPLOADING_MESSAGE = "⚡️ تم التحميل، جاري الرفع إليك..."
INVALID_URL_MESSAGE = "⚠️ عذرًا، الرابط الذي أرسلته غير صالح."
GENERIC_ERROR_MESSAGE = "❌ حدث خطأ غير متوقع."
ANALYSIS_FAILED_MESSAGE = "❌ فشل التحميل."
SESSION_EXPIRED_MESSAGE = "⚠️ انتهت صلاحية هذه الجلسة."
RATE_LIMIT_MESSAGE = "⏱ لقد تجاوزت الحد المسموح من الطلبات (5 طلبات/دقيقة). يرجى الانتظار قليلاً."

def escape_markdown(text: str) -> str:
    """تنظيف النص من أحرف Markdown الخاصة"""
    if not text:
        return ""
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))

# ==============================================================================
# 6. CORE LOGIC
# ==============================================================================

class CoreError(Exception):
    pass

class AnalysisError(CoreError):
    pass

class DownloadError(CoreError):
    pass

def is_valid_url(url: str) -> bool:
    """التحقق من صحة الرابط"""
    return bool(re.match(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', url))

def detect_platform(url: str) -> str:
    """اكتشاف المنصة من الرابط"""
    if 'youtube.com' in url or 'youtu.be' in url:
        return 'youtube'
    elif 'instagram.com' in url:
        return 'instagram'
    elif 'facebook.com' in url or 'fb.watch' in url:
        return 'facebook'
    elif 'twitter.com' in url or 'x.com' in url:
        return 'twitter'
    elif 'tiktok.com' in url:
        return 'tiktok'
    else:
        return 'generic'

def format_size(size_bytes: int) -> str:
    """تحويل الحجم من bytes إلى تنسيق قابل للقراءة"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

async def run_gallery_dl(url: str) -> list:
    """تشغيل gallery-dl لتحميل من إنستغرام"""
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
        raise DownloadError(f"فشل gallery-dl: {error_output.splitlines()[-1] if error_output else 'خطأ غير معروف'}")
    
    downloaded_files = []
    for root, _, files in os.walk(DOWNLOAD_PATH):
        for name in files:
            downloaded_files.append(os.path.join(root, name))
    
    if not downloaded_files:
        raise DownloadError("لم يتم العثور على أي ملفات بعد تشغيل gallery-dl.")
    
    return downloaded_files

def get_base_ydl_opts(url: str) -> dict:
    """الحصول على إعدادات yt-dlp الأساسية"""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
        'outtmpl': str(DOWNLOAD_PATH / '%(id)s.%(ext)s'),
        'ffmpeg_location': '/usr/bin/ffmpeg',
    }
    
    if 'facebook.com' in url or 'fb.watch' in url:
        logging.info("Facebook URL detected. Using direct connection.")
    else:
        opts['proxy'] = PRIMARY_PROXY
        logging.info(f"Using proxy for URL.")
    
    return opts

async def run_ydl_analysis(url: str) -> dict:
    """تحليل الرابط باستخدام yt-dlp"""
    ydl_opts = get_base_ydl_opts(url)
    ydl_opts['skip_download'] = True
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=False)
            if not info:
                raise AnalysisError("لم يتمكن yt-dlp من استخراج أي معلومات.")
            return info
    except yt_dlp.utils.DownloadError as e:
        raise AnalysisError(f"خطأ في التحليل: {str(e)}")
    except Exception as e:
        raise AnalysisError(f"خطأ غير متوقع: {str(e)}")

async def run_ydl_download(url: str, format_id: str, is_audio: bool) -> str:
    """تحميل الفيديو/الصوت باستخدام yt-dlp"""
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    ydl_opts = get_base_ydl_opts(url)
    
    if is_audio:
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3'
        }]
    else:
        ydl_opts['format'] = format_id
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=True)
            original_filename = ydl.prepare_filename(info)
            
            if is_audio:
                return str(Path(original_filename).with_suffix('.mp3'))
            return original_filename
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(f"خطأ في التحميل: {str(e)}")
    except Exception as e:
        raise DownloadError(f"خطأ غير متوقع: {str(e)}")

# ==============================================================================
# 7. UI BUILDERS
# ==============================================================================

def build_youtube_ui(info: dict, msg_id: int) -> tuple:
    """بناء واجهة يوتيوب"""
    title = info.get('title', 'غير متوفر')
    uploader = info.get('uploader', 'غير متوفر')
    duration = info.get('duration', 0)
    
    duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "غير معروف"
    
    caption = (
        f"🎬 **يوتيوب**\n\n"
        f"📝 **العنوان:** {escape_markdown(title)}\n"
        f"👤 **القناة:** {escape_markdown(uploader)}\n"
        f"⏱ **المدة:** {duration_str}"
    )
    
    # تصفية الصيغ: فقط جودة 720p أو أقل
    video_formats = [
        f for f in info.get('formats', [])
        if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and f.get('height', 0) <= 720
    ]
    
    # إضافة معلومات الحجم لكل صيغة
    for fmt in video_formats:
        size = fmt.get('filesize') or fmt.get('filesize_approx', 0)
        fmt['_size_str'] = format_size(size) if size else "غير معروف"
        fmt['_too_large'] = size > MAX_FILE_SIZE if size else False
    
    best_video = max(video_formats, key=lambda x: x.get('height', 0), default=None)
    
    buttons = []
    
    # زر الفيديو الأفضل
    if best_video and not best_video.get('_too_large', False):
        buttons.append(InlineKeyboardButton(
            f"🎬 فيديو ({best_video.get('height')}p - {best_video.get('_size_str')})",
            callback_data=f"yt:v:{best_video['format_id']}:{msg_id}"
        ))
    
    # زر الصوت
    buttons.append(InlineKeyboardButton(
        f"🎵 صوت (MP3)",
        callback_data=f"yt:a:best:{msg_id}"
    ))
    
    keyboard = [buttons]
    
    # زر الجودات الأخرى (إذا كان هناك أكثر من جودة متاحة)
    available_formats = [f for f in video_formats if not f.get('_too_large', False)]
    if len(available_formats) > 1:
        keyboard.append([InlineKeyboardButton(
            "🎞️ جودات أخرى",
            callback_data=f"yt_qualities:v:na:{msg_id}"
        )])
    
    keyboard.append([InlineKeyboardButton(
        "❌ إلغاء",
        callback_data=f"cancel:na:na:{msg_id}"
    )])
    
    return caption, InlineKeyboardMarkup(keyboard)

def build_generic_ui(info: dict, msg_id: int) -> tuple:
    """بناء واجهة عامة للمنصات الأخرى"""
    site_name = info.get('extractor_key', 'Website').capitalize()
    title = info.get('title', 'غير متوفر')
    
    caption = (
        f"🌐 **{escape_markdown(site_name)}**\n\n"
        f"📝 **العنوان:** {escape_markdown(title)}"
    )
    
    buttons = [
        InlineKeyboardButton("🎬 تحميل الفيديو", callback_data=f"yt:v:best:{msg_id}"),
        InlineKeyboardButton("🎵 تحميل الصوت", callback_data=f"yt:a:best:{msg_id}")
    ]
    
    keyboard = [buttons, [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")]]
    
    return caption, InlineKeyboardMarkup(keyboard)

def build_qualities_ui(info: dict, msg_id: int) -> InlineKeyboardMarkup:
    """بناء واجهة الجودات المتعددة"""
    video_formats = [
        f for f in info.get('formats', [])
        if f.get('vcodec') != 'none' and f.get('acodec') != 'none'
    ]
    
    # ترتيب حسب الجودة تنازلياً
    video_formats.sort(key=lambda x: x.get('height', 0), reverse=True)
    
    buttons = []
    seen_heights = set()
    
    for fmt in video_formats:
        height = fmt.get('height', 0)
        if height and height not in seen_heights and height <= 1080:
            seen_heights.add(height)
            filesize = fmt.get('filesize') or fmt.get('filesize_approx', 0)
            size_str = f" - {format_size(filesize)}" if filesize else ""
            
            # تحديد إذا كان الحجم كبير جداً
            if filesize and filesize > MAX_FILE_SIZE:
                button_text = f"❌ {height}p{size_str} (كبير جداً)"
                # لا نضيف callback لأنه غير متاح
                buttons.append([InlineKeyboardButton(button_text, callback_data=f"ignore:na:na:{msg_id}")])
            else:
                button_text = f"📹 {height}p{size_str}"
                buttons.append([InlineKeyboardButton(button_text, callback_data=f"yt:v:{fmt['format_id']}:{msg_id}")])
    
    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"back:na:na:{msg_id}")])
    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")])
    
    return InlineKeyboardMarkup(buttons)

# ==============================================================================
# 8. ERROR REPORTING
# ==============================================================================

async def report_error(context: ContextTypes.DEFAULT_TYPE, user_id: int, username: str, url: str, error_message: str, error_type: str):
    """إرسال تقرير الخطأ للأدمن"""
    if not ADMIN_ID:
        return
    
    platform = detect_platform(url)
    
    report = (
        f"🚨 **تقرير خطأ**\n\n"
        f"👤 **المستخدم:** `{user_id}` (@{username or 'بدون'})\n"
        f"🌐 **المنصة:** {platform}\n"
        f"🔗 **الرابط:** `{url[:50]}...`\n"
        f"⚠️ **النوع:** {error_type}\n"
        f"📝 **الخطأ:** `{error_message[:200]}`"
    )
    
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=report,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logging.error(f"Failed to send error report to admin: {e}")

# ==============================================================================
# 9. TELEGRAM HANDLERS
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر /start"""
    user = update.effective_user
    welcome_message = (
        f"👋 مرحباً بك **{escape_markdown(user.first_name)}**\\!\n\n"
        f"🤖 أنا بوت تحميل الفيديوهات من:\n"
        f"• YouTube 🎬\n"
        f"• Instagram 📸\n"
        f"• Facebook 👥\n"
        f"• Twitter/X 🐦\n"
        f"• TikTok 🎵\n"
        f"• والمزيد\\.\\.\\.\n\n"
        f"📌 فقط أرسل لي رابط الفيديو وسأقوم بتحميله لك\\!\n\n"
        f"⚙️ الأوامر المتاحة:\n"
        f"/start \\- البداية\n"
        f"/help \\- المساعدة"
    )
    
    if str(user.id) == ADMIN_ID:
        welcome_message += f"\n/stats \\- الإحصائيات \\(للأدمن\\)"
    
    await update.message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN_V2)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر /help"""
    help_text = (
        f"📖 **دليل الاستخدام**\n\n"
        f"🔹 أرسل رابط فيديو من أي منصة مدعومة\n"
        f"🔹 اختر الجودة أو الصيغة المطلوبة\n"
        f"🔹 انتظر قليلاً وسيتم إرسال الملف\n\n"
        f"⚠️ **ملاحظات:**\n"
        f"• الحد الأقصى للحجم: 50 ميغابايت\n"
        f"• الحد الأقصى للطلبات: 5 طلبات/دقيقة\n\n"
        f"❓ **مشاكل؟** تواصل مع المطور"
    )
    
    await update.message.reply_text(
        escape_markdown(help_text),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر /stats (للأدمن فقط)"""
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("⛔️ هذا الأمر متاح للأدمن فقط.")
        return
    
    stats = get_stats()
    if not stats:
        await update.message.reply_text("❌ فشل في جلب الإحصائيات.")
        return
    
    stats_text = (
        f"📊 **إحصائيات البوت**\n\n"
        f"👥 **إجمالي المستخدمين:** {stats['total_users']}\n"
        f"📥 **إجمالي التحميلات:** {stats['total_downloads']}\n"
        f"✅ **ناجحة:** {stats['successful']}\n"
        f"❌ **فاشلة:** {stats['failed']}\n"
        f"📅 **تحميلات اليوم:** {stats['today']}\n\n"
        f"📈 **نسبة النجاح:** {(stats['successful'] / stats['total_downloads'] * 100) if stats['total_downloads'] > 0 else 0:.1f}%"
    )
    
    await update.message.reply_text(
        escape_markdown(stats_text),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الروابط الرئيسي"""
    user = update.effective_user
    url = update.message.text.strip()
    
    # التحقق من صحة الرابط
    if not is_valid_url(url):
        await update.message.reply_text(INVALID_URL_MESSAGE)
        return
    
    # التحقق من rate limit
    if not await check_rate_limit(user.id):
        await update.message.reply_text(RATE_LIMIT_MESSAGE)
        return
    
    msg = await update.message.reply_text(ANALYZING_MESSAGE)
    platform = detect_platform(url)
    downloaded_files = []
    
    try:
        if platform == 'instagram':
            # استخدام gallery-dl لإنستغرام
            downloaded_files = await run_gallery_dl(url)
            await msg.edit_text(UPLOADING_MESSAGE)
            
            if len(downloaded_files) == 1:
                file_path = Path(downloaded_files[0])
                
                if file_path.suffix.lower() in ['.jpg', '.jpeg', '.png', '.webp']:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=open(file_path, 'rb'),
                        caption="✅ تم التحميل بنجاح"
                    )
                else:
                    await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=open(file_path, 'rb'),
                        supports_streaming=True,
                        caption="✅ تم التحميل بنجاح"
                    )
            else:
                # إرسال كملف ZIP للمحتوى المتعدد
                zip_path = DOWNLOAD_PATH / f"instagram_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
                with zipfile.ZipFile(zip_path, 'w') as zipf:
                    for file_path in downloaded_files:
                        p = Path(file_path)
                        zipf.write(p, p.name)
                
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=open(zip_path, 'rb'),
                    caption=f"✅ تم تحميل {len(downloaded_files)} ملف بنجاح"
                )
                downloaded_files.append(str(zip_path))
            
            await msg.delete()
            log_download(user.id, user.username, url, platform, True)
        
        else:
            # استخدام yt-dlp للمنصات الأخرى
            info = await run_ydl_analysis(url)
            context.user_data[msg.message_id] = info
            
            if platform == 'youtube':
                caption, keyboard = build_youtube_ui(info, msg.message_id)
            else:
                caption, keyboard = build_generic_ui(info, msg.message_id)
            
            thumbnail = info.get('thumbnail')
            
            if thumbnail:
                await msg.delete()
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=thumbnail,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=keyboard
                )
            else:
                await msg.edit_text(
                    text=caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=keyboard
                )
    
    except (AnalysisError, DownloadError) as e:
        error_msg = str(e)
        await report_error(context, user.id, user.username, url, error_msg, "تحليل/تحميل")
        await msg.edit_text(f"{ANALYSIS_FAILED_MESSAGE}\n\n{escape_markdown(error_msg)}", parse_mode=ParseMode.MARKDOWN_V2)
        log_download(user.id, user.username, url, platform, False, error_msg)
    
    except Exception as e:
        error_msg = str(e)
        logging.error(f"Unexpected error in handle_link: {e}", exc_info=True)
        await report_error(context, user.id, user.username, url, error_msg, "خطأ غير متوقع")
        await msg.edit_text(f"{GENERIC_ERROR_MESSAGE}\n\n{escape_markdown(error_msg)}", parse_mode=ParseMode.MARKDOWN_V2)
        log_download(user.id, user.username, url, platform, False, error_msg)
    
    finally:
        # تنظيف الملفات
        for file_to_delete in downloaded_files:
            try:
                if os.path.exists(file_to_delete):
                    os.remove(file_to_delete)
                    logging.info(f"Deleted file: {file_to_delete}")
            except Exception as e:
                logging.warning(f"Failed to delete file {file_to_delete}: {e}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أزرار الاختيار"""
    query = update.callback_query
    await query.answer()
    
    try:
        handler_type, action, resource_id, msg_id_str = query.data.split(':')
        msg_id = int(msg_id_str)
    except ValueError:
        await query.message.delete()
        return
    
    # تجاهل الأزرار غير النشطة (الملفات الكبيرة)
    if handler_type == "ignore":
        await query.answer("⚠️ هذا الملف أكبر من 50 ميغابايت", show_alert=True)
        return
    
    # إلغاء العملية
    if handler_type == "cancel":
        await query.message.delete()
        if msg_id in context.user_data:
            del context.user_data[msg_id]
        return
    
    # التحقق من وجود البيانات
    if msg_id not in context.user_data:
        await query.edit_message_text(SESSION_EXPIRED_MESSAGE)
        return
    
    info = context.user_data[msg_id]
    url = info.get('webpage_url')
    user = query.from_user
    platform = detect_platform(url)
    
    # عرض جودات إضافية
    if handler_type == "yt_qualities":
        keyboard = build_qualities_ui(info, msg_id)
        try:
            await query.message.edit_reply_markup(reply_markup=keyboard)
        except BadRequest:
            pass
        return
    
    # الرجوع للقائمة الرئيسية
    if handler_type == "back":
        if platform == 'youtube':
            caption, keyboard = build_youtube_ui(info, msg_id)
        else:
            caption, keyboard = build_generic_ui(info, msg_id)
        
        try:
            await query.message.edit_caption(
                caption=caption,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=keyboard
            )
        except BadRequest:
            await query.message.edit_text(
                text=caption,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=keyboard
            )
        return
    
    # التحقق من حجم الملف قبل التحميل
    try:
        is_audio = (action == 'a')
        target_format = None
        
        if is_audio:
            audio_formats = [
                f for f in info.get('formats', [])
                if f.get('acodec') != 'none' and f.get('vcodec') == 'none'
            ]
            target_format = max(audio_formats, key=lambda x: x.get('abr', 0), default=None)
        else:
            if resource_id == 'best':
                video_formats = [
                    f for f in info.get('formats', [])
                    if f.get('vcodec') != 'none' and f.get('acodec') != 'none'
                ]
                target_format = max(video_formats, key=lambda x: x.get('height', 0), default=None)
            else:
                target_format = next(
                    (f for f in info.get('formats', []) if f.get('format_id') == resource_id),
                    None
                )
        
        if target_format:
            file_size = target_format.get('filesize') or target_format.get('filesize_approx')
            if file_size and file_size > MAX_FILE_SIZE:
                size_mb = file_size / (1024 * 1024)
                await query.message.delete()
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"❌ عذراً، حجم هذا الملف ({size_mb:.1f} ميغابايت) يتجاوز الحد المسموح به (50 ميغابايت).\n\n💡 جرّب اختيار جودة أقل أو تحميل الصوت فقط."
                )
                if msg_id in context.user_data:
                    del context.user_data[msg_id]
                return
    except Exception as e:
        logging.warning(f"Could not check file size: {e}")
    
    # إزالة الأزرار وإضافة رسالة التحميل
    await query.edit_message_reply_markup(None)
    try:
        current_caption = query.message.caption_markdown_v2
        loading_text = escape_markdown("\n\n⏳ جارٍ التحميل...")
        await query.message.edit_caption(
            caption=current_caption + loading_text,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except BadRequest:
        pass
    
    file_path = None
    try:
        # التحميل
        is_audio = (action == 'a')
        file_path = await run_ydl_download(url, resource_id, is_audio)
        
        # فحص الحجم الفعلي بعد التحميل
        if os.path.exists(file_path):
            actual_size = os.path.getsize(file_path)
            if actual_size > MAX_FILE_SIZE:
                await query.message.delete()
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"❌ عذراً، حجم الملف المُحمّل ({format_size(actual_size)}) يتجاوز الحد المسموح به (50 ميغابايت)."
                )
                if msg_id in context.user_data:
                    del context.user_data[msg_id]
                return
        
        # تحديث الرسالة للرفع
        try:
            await query.message.edit_caption(
                caption=escape_markdown(UPLOADING_MESSAGE),
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except BadRequest:
            pass
        
        # رفع الملف
        title = info.get('title', 'تحميل')
        
        if is_audio:
            await context.bot.send_audio(
                chat_id=query.message.chat_id,
                audio=open(file_path, 'rb'),
                title=title,
                caption="✅ تم التحميل بنجاح"
            )
        else:
            await context.bot.send_video(
                chat_id=query.message.chat_id,
                video=open(file_path, 'rb'),
                caption=f"✅ {escape_markdown(title)}",
                parse_mode=ParseMode.MARKDOWN_V2,
                supports_streaming=True
            )
        
        await query.message.delete()
        log_download(user.id, user.username, url, platform, True)
    
    except (DownloadError, Exception) as e:
        error_msg = str(e)
        logging.error(f"Error in button_handler: {e}", exc_info=True)
        await report_error(context, user.id, user.username, url, error_msg, "تحميل/رفع")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"❌ فشل التحميل: {escape_markdown(error_msg)}",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        log_download(user.id, user.username, url, platform, False, error_msg)
    
    finally:
        # تنظيف الملف والبيانات
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logging.info(f"Deleted downloaded file: {file_path}")
            except Exception as e:
                logging.warning(f"Failed to delete file {file_path}: {e}")
        
        if msg_id in context.user_data:
            del context.user_data[msg_id]

# ==============================================================================
# 10. ERROR HANDLER
# ==============================================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """معالج الأخطاء العام"""
    logging.error("Exception while handling an update:", exc_info=context.error)
    
    if update and isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ حدث خطأ غير متوقع. تم إبلاغ المطور."
            )
        except Exception:
            pass

# ==============================================================================
# 11. FLASK HEALTH CHECK
# ==============================================================================

flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    """نقطة فحص صحة التطبيق"""
    return "OK", 200

@flask_app.route('/stats')
def stats_endpoint():
    """نقطة الإحصائيات (للمراقبة)"""
    stats = get_stats()
    if stats:
        return stats, 200
    return {"error": "Failed to fetch stats"}, 500

# ==============================================================================
# 12. MAIN APPLICATION
# ==============================================================================

def run_flask():
    """تشغيل خادم Flask"""
    flask_app.run(host='0.0.0.0', port=PORT)

def main():
    """النقطة الرئيسية لتشغيل البوت"""
    # تهيئة قاعدة البيانات
    init_db()
    
    # تشغيل خادم Flask في thread منفصل
    threading.Thread(target=run_flask, daemon=True).start()
    logging.info(f"Health check server started on port {PORT}.")
    
    # إنشاء تطبيق التليجرام
    app = Application.builder().token(BOT_TOKEN).build()
    
    # إضافة المعالجات
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)
    
    # بدء مهمة التنظيف الدورية
    loop = asyncio.get_event_loop()
    loop.create_task(periodic_cleanup())
    
    logging.info("Starting Telegram bot polling...")
    logging.info(f"Bot is ready! Admin ID: {ADMIN_ID}")
    
    # تشغيل البوت
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    if not BOT_TOKEN:
        logging.fatal("FATAL: BOT_TOKEN environment variable is not set!")
        exit(1)
    
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Bot stopped by user.")
    except Exception as e:
        logging.fatal(f"Fatal error: {e}", exc_info=True)
        exit(1)
