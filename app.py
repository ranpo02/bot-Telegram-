# app.py - الإصدار 13.0: إصلاح شامل مع التحسينات
import logging
import os
import threading
import asyncio
import random
import re
import sqlite3
import atexit
import shutil
import hashlib
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from functools import lru_cache
import zipfile
import heapq

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import BadRequest, NetworkError
from telegram.constants import ParseMode
import yt_dlp

# ==============================================================================
# 1. الإعدادات الأساسية
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

MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_REQUESTS_PER_MINUTE = 5
CLEANUP_INTERVAL = 1800
FILE_MAX_AGE = 3600
CACHE_TTL = 600

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
for logger_name in ["httpx", "werkzeug", "telegram.ext.Application"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

# ==============================================================================
# 2. قاعدة البيانات المحسنة
# ==============================================================================
def init_db():
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
                  error_message TEXT,
                  file_size INTEGER,
                  quality TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS user_stats
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  total_downloads INTEGER DEFAULT 0,
                  successful_downloads INTEGER DEFAULT 0,
                  failed_downloads INTEGER DEFAULT 0,
                  first_use DATETIME,
                  last_use DATETIME,
                  preferred_quality TEXT DEFAULT '720p')''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS cache
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  url_hash TEXT UNIQUE,
                  data TEXT,
                  timestamp DATETIME)''')
    
    conn.commit()
    conn.close()
    logging.info("تم تهيئة قاعدة البيانات")

def log_download(user_id: int, username: str, url: str, platform: str, success: bool, 
                 error_msg: str = None, file_size: int = None, quality: str = None):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('''INSERT INTO downloads (user_id, username, url, platform, timestamp, 
                     success, error_message, file_size, quality)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, username, url, platform, datetime.now(), success, 
                   error_msg, file_size, quality))
        
        if success and quality:
            c.execute('''UPDATE user_stats 
                         SET preferred_quality = ?, last_use = ?
                         WHERE user_id = ?''',
                      (quality, datetime.now(), user_id))
        
        c.execute('''INSERT INTO user_stats (user_id, username, total_downloads, 
                     successful_downloads, failed_downloads, first_use, last_use)
                     VALUES (?, ?, 1, ?, ?, ?, ?)
                     ON CONFLICT(user_id) DO UPDATE SET
                     total_downloads = total_downloads + 1,
                     successful_downloads = successful_downloads + ?,
                     failed_downloads = failed_downloads + ?,
                     last_use = ?''',
                  (user_id, username, 1 if success else 0, 1 if not success else 0, 
                   datetime.now(), datetime.now(),
                   1 if success else 0, 1 if not success else 0, datetime.now()))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"خطأ في تسجيل التحميل: {e}")

# ==============================================================================
# 3. نظام التخزين المؤقت
# ==============================================================================
class AdvancedCache:
    @staticmethod
    def get_cache_key(url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()
    
    @staticmethod
    @lru_cache(maxsize=100)
    async def get_cached_analysis(url: str) -> dict:
        cache_key = AdvancedCache.get_cache_key(url)
        
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT data, timestamp FROM cache WHERE url_hash = ?', (cache_key,))
            result = c.fetchone()
            conn.close()
            
            if result:
                data_str, cache_time = result
                cache_age = (datetime.now() - datetime.fromisoformat(cache_time)).total_seconds()
                
                if cache_age < CACHE_TTL:
                    return json.loads(data_str)
                else:
                    AdvancedCache.delete_cache(url)
        except Exception as e:
            logging.debug(f"خطأ قراءة الكاش: {e}")
        
        return None
    
    @staticmethod
    async def set_cached_analysis(url: str, data: dict):
        cache_key = AdvancedCache.get_cache_key(url)
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('''INSERT OR REPLACE INTO cache (url_hash, data, timestamp)
                         VALUES (?, ?, ?)''',
                      (cache_key, json.dumps(data), datetime.now()))
            conn.commit()
            conn.close()
        except Exception as e:
            logging.error(f"خطأ في تخزين الكاش: {e}")
    
    @staticmethod
    def delete_cache(url: str):
        cache_key = AdvancedCache.get_cache_key(url)
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('DELETE FROM cache WHERE url_hash = ?', (cache_key,))
            conn.commit()
            conn.close()
        except Exception as e:
            logging.error(f"خطأ في حذف الكاش: {e}")

# ==============================================================================
# 4. نظام تتبع التقدم
# ==============================================================================
class ProgressTracker:
    def __init__(self, chat_id: int, message_id: int, bot):
        self.chat_id = chat_id
        self.message_id = message_id
        self.bot = bot
        self.start_time = time.time()
        self.last_update = 0
    
    def get_progress_hook(self):
        def progress_hook(d):
            if d['status'] == 'downloading':
                current_time = time.time()
                if current_time - self.last_update > 3:
                    self.last_update = current_time
                    total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                    downloaded = d.get('downloaded_bytes', 0)
                    
                    if total and downloaded:
                        percent = (downloaded / total) * 100
                        asyncio.create_task(self.update_progress(percent))
        
        return progress_hook
    
    async def update_progress(self, percent: float):
        try:
            bars = 10
            filled = int(bars * percent / 100)
            bar = "█" * filled + "░" * (bars - filled)
            
            progress_text = f"⏳ جاري التحميل...\n\n`{bar}` {percent:.1f}%"
            
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=progress_text,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logging.debug(f"تخطيت تحديث التقدم: {e}")

# ==============================================================================
# 5. إعادة المحاولة التلقائية
# ==============================================================================
class RetryManager:
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
    
    async def execute_with_retry(self, coroutine_func, *args, **kwargs):
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                return await coroutine_func(*args, **kwargs)
            except (NetworkError, TimeoutError, ConnectionError) as e:
                last_exception = e
                
                if attempt == self.max_retries - 1:
                    break
                
                delay = self.base_delay * (2 ** attempt) + random.uniform(0, 1)
                logging.info(f"المحاولة {attempt + 1}/{self.max_retries} بعد {delay:.1f} ثانية")
                await asyncio.sleep(delay)
            except Exception as e:
                raise e
        
        if last_exception:
            raise last_exception
        raise Exception("فشل بعد كل المحاولات")

# ==============================================================================
# 6. التحكم في الطلبات والتنظيف
# ==============================================================================
user_requests = defaultdict(list)

async def check_rate_limit(user_id: int) -> bool:
    now = datetime.now()
    user_requests[user_id] = [
        req_time for req_time in user_requests[user_id]
        if now - req_time < timedelta(minutes=1)
    ]
    
    if len(user_requests[user_id]) >= MAX_REQUESTS_PER_MINUTE:
        return False
    
    user_requests[user_id].append(now)
    return True

def cleanup_old_files():
    try:
        if not DOWNLOAD_PATH.exists():
            return
        
        now = datetime.now().timestamp()
        for file_path in DOWNLOAD_PATH.glob("*"):
            if file_path.is_file():
                file_age = now - file_path.stat().st_mtime
                if file_age > FILE_MAX_AGE:
                    file_path.unlink()
    except Exception as e:
        logging.error(f"خطأ في التنظيف: {e}")

async def periodic_cleanup():
    while True:
        cleanup_old_files()
        await asyncio.sleep(CLEANUP_INTERVAL)

# ==============================================================================
# 7. الدوال الأساسية
# ==============================================================================
def is_valid_url(url: str) -> bool:
    return bool(re.match(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', url))

def detect_platform(url: str) -> str:
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
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

def escape_markdown(text: str) -> str:
    if not text:
        return ""
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))

# ==============================================================================
# 8. واجهات المستخدم
# ==============================================================================
def build_youtube_ui(info: dict, msg_id: int, user_id: int = None) -> tuple:
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
    
    video_formats = [
        f for f in info.get('formats', [])
        if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and f.get('height', 0) <= 720
    ]
    
    for fmt in video_formats:
        size = fmt.get('filesize') or fmt.get('filesize_approx', 0)
        fmt['_size_str'] = format_size(size) if size else "غير معروف"
        fmt['_too_large'] = size > MAX_FILE_SIZE if size else False
    
    best_video = max(video_formats, key=lambda x: x.get('height', 0), default=None)
    
    buttons = []
    
    if best_video and not best_video.get('_too_large', False):
        buttons.append(InlineKeyboardButton(
            f"🎬 فيديو ({best_video.get('height')}p - {best_video.get('_size_str')})",
            callback_data=f"yt:v:{best_video['format_id']}:{msg_id}"
        ))
    
    buttons.append(InlineKeyboardButton(
        f"🎵 صوت (MP3)",
        callback_data=f"yt:a:best:{msg_id}"
    ))
    
    keyboard = [buttons]
    
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
    video_formats = [
        f for f in info.get('formats', [])
        if f.get('vcodec') != 'none' and f.get('acodec') != 'none'
    ]
    
    video_formats.sort(key=lambda x: x.get('height', 0), reverse=True)
    
    buttons = []
    seen_heights = set()
    
    for fmt in video_formats:
        height = fmt.get('height', 0)
        if height and height not in seen_heights and height <= 1080:
            seen_heights.add(height)
            filesize = fmt.get('filesize') or fmt.get('filesize_approx', 0)
            size_str = f" - {format_size(filesize)}" if filesize else ""
            
            if filesize and filesize > MAX_FILE_SIZE:
                button_text = f"❌ {height}p{size_str} (كبير جداً)"
                buttons.append([InlineKeyboardButton(button_text, callback_data=f"ignore:na:na:{msg_id}")])
            else:
                button_text = f"📹 {height}p{size_str}"
                buttons.append([InlineKeyboardButton(button_text, callback_data=f"yt:v:{fmt['format_id']}:{msg_id}")])
    
    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"back:na:na:{msg_id}")])
    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")])
    
    return InlineKeyboardMarkup(buttons)

# ==============================================================================
# 9. العمليات الأساسية
# ==============================================================================
class CoreError(Exception):
    pass

class AnalysisError(CoreError):
    pass

class DownloadError(CoreError):
    pass

async def run_gallery_dl(url: str) -> list:
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    
    command = [
        'gallery-dl',
        '--cookies', 'cookies.txt',
        '--directory', str(DOWNLOAD_PATH),
        url
    ]
    
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    stdout, stderr = await process.communicate()
    
    if process.returncode != 0:
        error_output = stderr.decode('utf-8', errors='ignore')
        raise DownloadError(f"فشل gallery-dl: {error_output.splitlines()[-1] if error_output else 'خطأ غير معروف'}")
    
    downloaded_files = []
    for root, _, files in os.walk(DOWNLOAD_PATH):
        for name in files:
            downloaded_files.append(os.path.join(root, name))
    
    if not downloaded_files:
        raise DownloadError("لم يتم العثور على أي ملفات")
    
    return downloaded_files

def get_base_ydl_opts(url: str, progress_hook=None) -> dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
        'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
        'outtmpl': str(DOWNLOAD_PATH / '%(id)s.%(ext)s'),
        'ffmpeg_location': '/usr/bin/ffmpeg',
    }
    
    if progress_hook:
        opts['progress_hooks'] = [progress_hook]
    
    if 'facebook.com' in url or 'fb.watch' in url:
        logging.info("Facebook URL detected. Using direct connection.")
    else:
        opts['proxy'] = PRIMARY_PROXY
    
    return opts

async def run_ydl_analysis(url: str) -> dict:
    cached_info = await AdvancedCache.get_cached_analysis(url)
    if cached_info:
        logging.info(f"Using cached analysis for {url}")
        return cached_info
    
    ydl_opts = get_base_ydl_opts(url)
    ydl_opts['skip_download'] = True
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=False)
            if not info:
                raise AnalysisError("لم يتمكن yt-dlp من استخراج أي معلومات.")
            
            await AdvancedCache.set_cached_analysis(url, info)
            return info
    except yt_dlp.utils.DownloadError as e:
        raise AnalysisError(f"خطأ في التحليل: {str(e)}")
    except Exception as e:
        raise AnalysisError(f"خطأ غير متوقع: {str(e)}")

async def run_ydl_download(url: str, format_id: str, is_audio: bool, progress_hook=None) -> str:
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    ydl_opts = get_base_ydl_opts(url, progress_hook)
    
    if is_audio:
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3'
        }]
    else:
        ydl_opts['format'] = format_id
    
    retry_manager = RetryManager(max_retries=3)
    
    try:
        def download_task():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                original_filename = ydl.prepare_filename(info)
                
                if is_audio:
                    return str(Path(original_filename).with_suffix('.mp3'))
                return original_filename
        
        filename = await retry_manager.execute_with_retry(
            lambda: asyncio.to_thread(download_task)
        )
        return filename
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(f"خطأ في التحميل: {str(e)}")
    except Exception as e:
        raise DownloadError(f"خطأ غير متوقع: {str(e)}")

# ==============================================================================
# 10. معالجات التلغرام
# ==============================================================================
ANALYZING_MESSAGE = "⏳ جاري تحليل الرابط..."
UPLOADING_MESSAGE = "⚡️ تم التحميل، جاري الرفع..."
INVALID_URL_MESSAGE = "⚠️ عذرًا، الرابط غير صالح."
GENERIC_ERROR_MESSAGE = "❌ حدث خطأ غير متوقع."
ANALYSIS_FAILED_MESSAGE = "❌ فشل التحميل."
SESSION_EXPIRED_MESSAGE = "⚠️ انتهت صلاحية الجلسة."
RATE_LIMIT_MESSAGE = "⏱ لقد تجاوزت حد الطلبات (5/دقيقة)."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_message = (
        f"👋 مرحباً بك **{escape_markdown(user.first_name)}**\\!\n\n"
        f"🤖 أنا بوت تحميل الفيديوهات من:\n"
        f"• YouTube 🎬 • Instagram 📸\n"
        f"• Facebook 👥 • Twitter/X 🐦\n"
        f"• TikTok 🎵 والمزيد...\n\n"
        f"🚀 **المميزات الجديدة:**\n"
        f"• ⏳ شريط تقدم مرئي\n"
        f"• 🔄 إعادة محاولة تلقائية\n"
        f"• 💾 تخزين مؤقت للروابط\n\n"
        f"📌 فقط أرسل لي رابط الفيديو!\n\n"
        f"⚙️ الأوامر:\n"
        f"/start - البداية\n/help - المساعدة"
    )
    
    if str(user.id) == ADMIN_ID:
        welcome_message += f"\n/stats - الإحصائيات (للأدمن)"
    
    await update.message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN_V2)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        f"📖 **دليل الاستخدام**\n\n"
        f"🔹 أرسل رابط فيديو من أي منصة\n"
        f"🔹 اختر الجودة أو الصيغة المطلوبة\n"
        f"🔹 انتظر قليلاً وسيتم إرسال الملف\n\n"
        f"🚀 **المميزات:**\n"
        f"• ⏳ شريط تقدم مرئي\n"
        f"• 🔄 إعادة محاولة تلقائية\n"
        f"• 💾 تخزين مؤقت\n\n"
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
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("⛔️ هذا الأمر للأدمن فقط.")
        return
    
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
        
        stats_text = (
            f"📊 **إحصائيات البوت**\n\n"
            f"👥 **المستخدمين:** {total_users}\n"
            f"📥 **التحميلات:** {stats[0] or 0}\n"
            f"✅ **ناجحة:** {stats[1] or 0}\n"
            f"❌ **فاشلة:** {stats[2] or 0}\n"
            f"📅 **اليوم:** {today_downloads}\n\n"
            f"📈 **نسبة النجاح:** {(stats[1] / stats[0] * 100) if stats[0] and stats[0] > 0 else 0:.1f}%"
        )
        
        await update.message.reply_text(
            escape_markdown(stats_text),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        await update.message.reply_text("❌ فشل في جلب الإحصائيات.")

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    url = update.message.text.strip()
    
    if not is_valid_url(url):
        await update.message.reply_text(INVALID_URL_MESSAGE)
        return
    
    if not await check_rate_limit(user.id):
        await update.message.reply_text(RATE_LIMIT_MESSAGE)
        return
    
    msg = await update.message.reply_text(ANALYZING_MESSAGE)
    platform = detect_platform(url)
    downloaded_files = []
    
    try:
        if platform == 'instagram':
            downloaded_files = await run_gallery_dl(url)
            await msg.edit_text(UPLOADING_MESSAGE)
            
            if len(downloaded_files) == 1:
                file_path = Path(downloaded_files[0])
                file_size = file_path.stat().st_size if file_path.exists() else 0
                
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
                quality = 'N/A'
            else:
                zip_path = DOWNLOAD_PATH / f"instagram_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
                with zipfile.ZipFile(zip_path, 'w') as zipf:
                    for file_path in downloaded_files:
                        p = Path(file_path)
                        zipf.write(p, p.name)
                
                zip_size = zip_path.stat().st_size if zip_path.exists() else 0
                
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=open(zip_path, 'rb'),
                    caption=f"✅ تم تحميل {len(downloaded_files)} ملف بنجاح"
                )
                downloaded_files.append(str(zip_path))
                quality = 'ZIP'
            
            await msg.delete()
            log_download(user.id, user.username, url, platform, True, 
                       file_size=file_size if 'file_size' in locals() else zip_size, 
                       quality=quality)
        
        else:
            info = await run_ydl_analysis(url)
            context.user_data[msg.message_id] = info
            
            if platform == 'youtube':
                caption, keyboard = build_youtube_ui(info, msg.message_id, user.id)
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
        await msg.edit_text(f"{ANALYSIS_FAILED_MESSAGE}\n\n{escape_markdown(error_msg)}", 
                          parse_mode=ParseMode.MARKDOWN_V2)
        log_download(user.id, user.username, url, platform, False, error_msg)
    
    except Exception as e:
        error_msg = str(e)
        logging.error(f"خطأ غير متوقع: {e}", exc_info=True)
        await msg.edit_text(f"{GENERIC_ERROR_MESSAGE}\n\n{escape_markdown(error_msg)}", 
                          parse_mode=ParseMode.MARKDOWN_V2)
        log_download(user.id, user.username, url, platform, False, error_msg)
    
    finally:
        for file_to_delete in downloaded_files:
            try:
                if os.path.exists(file_to_delete):
                    os.remove(file_to_delete)
            except Exception as e:
                logging.warning(f"فشل حذف الملف: {e}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        handler_type, action, resource_id, msg_id_str = query.data.split(':')
        msg_id = int(msg_id_str)
    except ValueError:
        await query.message.delete()
        return
    
    if handler_type == "ignore":
        await query.answer("⚠️ هذا الملف أكبر من 50 ميغابايت", show_alert=True)
        return
    
    if handler_type == "cancel":
        await query.message.delete()
        if msg_id in context.user_data:
            del context.user_data[msg_id]
        return
    
    if msg_id not in context.user_data:
        await query.edit_message_text(SESSION_EXPIRED_MESSAGE)
        return
    
    info = context.user_data[msg_id]
    url = info.get('webpage_url')
    user = query.from_user
    platform = detect_platform(url)
    
    if handler_type == "yt_qualities":
        keyboard = build_qualities_ui(info, msg_id)
        try:
            await query.message.edit_reply_markup(reply_markup=keyboard)
        except BadRequest:
            pass
        return
    
    if handler_type == "back":
        if platform == 'youtube':
            caption, keyboard = build_youtube_ui(info, msg_id, user.id)
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
    
    progress_tracker = ProgressTracker(
        chat_id=query.message.chat_id,
        message_id=query.message.message_id,
        bot=context.bot
    )
    
    await query.edit_message_reply_markup(None)
    try:
        current_caption = query.message.caption_markdown_v2 or ""
        await query.message.edit_caption(
            caption=current_caption + escape_markdown("\n\n⏳ جارٍ التحميل..."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except BadRequest:
        pass
    
    file_path = None
    try:
        is_audio = (action == 'a')
        file_path = await run_ydl_download(
            url, resource_id, is_audio, 
            progress_tracker.get_progress_hook()
        )
        
        if os.path.exists(file_path):
            actual_size = os.path.getsize(file_path)
            if actual_size > MAX_FILE_SIZE:
                await query.message.delete()
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"❌ عذراً، حجم الملف ({format_size(actual_size)}) يتجاوز الحد المسموح به."
                )
                if msg_id in context.user_data:
                    del context.user_data[msg_id]
                return
        
        try:
            await query.message.edit_caption(
                caption=escape_markdown(UPLOADING_MESSAGE),
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except BadRequest:
            pass
        
        title = info.get('title', 'تحميل')
        
        if is_audio:
            await context.bot.send_audio(
                chat_id=query.message.chat_id,
                audio=open(file_path, 'rb'),
                title=title[:64],
                caption="✅ تم التحميل بنجاح"
            )
            quality = 'MP3'
        else:
            quality = 'Unknown'
            try:
                if resource_id == 'best':
                    video_formats = [f for f in info.get('formats', []) 
                                   if f.get('vcodec') != 'none']
                    if video_formats:
                        quality = f"{max(f.get('height', 0) for f in video_formats)}p"
                else:
                    target_fmt = next((f for f in info.get('formats', []) 
                                     if f.get('format_id') == resource_id), None)
                    if target_fmt:
                        quality = f"{target_fmt.get('height', 0)}p"
            except:
                quality = 'Unknown'
            
            await context.bot.send_video(
                chat_id=query.message.chat_id,
                video=open(file_path, 'rb'),
                caption=f"✅ {escape_markdown(title[:200])}",
                parse_mode=ParseMode.MARKDOWN_V2,
                supports_streaming=True
            )
        
        await query.message.delete()
        
        actual_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        log_download(user.id, user.username, url, platform, True, 
                    file_size=actual_size, quality=quality)
    
    except (DownloadError, Exception) as e:
        error_msg = str(e)
        logging.error(f"خطأ في زر الاختيار: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"❌ فشل التحميل: {escape_markdown(error_msg)}",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        log_download(user.id, user.username, url, platform, False, error_msg)
    
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                logging.warning(f"فشل حذف الملف: {e}")
        
        if msg_id in context.user_data:
            del context.user_data[msg_id]

# ==============================================================================
# 11. تطبيق Flask للصحة
# ==============================================================================
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    return "OK", 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

# ==============================================================================
# 12. التطبيق الرئيسي
# ==============================================================================
def main():
    init_db()
    
    threading.Thread(target=run_flask, daemon=True).start()
    logging.info(f"خادم الصحة يعمل على منفذ {PORT}")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    loop = asyncio.get_event_loop()
    loop.create_task(periodic_cleanup())
    
    logging.info("بدء تشغيل بوت التلغرام...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    if not BOT_TOKEN:
        logging.fatal("خطأ: لم يتم تعيين BOT_TOKEN!")
        exit(1)
    
    try:
        main()
    except KeyboardInterrupt:
        logging.info("توقف البوت بواسطة المستخدم.")
    except Exception as e:
        logging.fatal(f"خطأ فادح: {e}", exc_info=True)
        exit(1)
