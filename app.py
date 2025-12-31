# app.py - الإصدار النهائي 15.0
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
import zipfile

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import BadRequest, NetworkError
from telegram.constants import ParseMode
import yt_dlp

# ==============================================================================
# 1. الإعدادات
# ==============================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))
ADMIN_ID = "5898628858"
PRIMARY_PROXY = "154.3.236.202:3128"

DOWNLOAD_PATH = Path("downloads")
DB_PATH = Path("bot_stats.db")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
MAX_REQUESTS_PER_MINUTE = 5
CLEANUP_INTERVAL = 1800
FILE_MAX_AGE = 3600
CACHE_TTL = 600
DOWNLOAD_TIMEOUT = 600  # 10 دقائق

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
for logger_name in ["httpx", "werkzeug", "telegram.ext.Application"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

# ==============================================================================
# 2. قاعدة البيانات
# ==============================================================================
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
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
                  last_use DATETIME)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS cache
                 (url_hash TEXT PRIMARY KEY,
                  data TEXT,
                  timestamp DATETIME)''')
    
    c.execute('CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_cache_timestamp ON cache(timestamp)')
    
    conn.commit()
    conn.close()

def log_download(user_id: int, username: str, url: str, platform: str, success: bool, 
                 error_msg: str = # app.py - الإصدار النهائي 16.0 - محسّن بالكامل
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
import zipfile

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import BadRequest, NetworkError
from telegram.constants import ParseMode
import yt_dlp

# ==============================================================================
# 1. الإعدادات
# ==============================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))
ADMIN_ID = "5898628858"
PRIMARY_PROXY = "154.3.236.202:3128"

DOWNLOAD_PATH = Path("downloads")
DB_PATH = Path("bot_stats.db")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
MAX_REQUESTS_PER_MINUTE = 5
CLEANUP_INTERVAL = 1800
FILE_MAX_AGE = 3600
CACHE_TTL = 600
DOWNLOAD_TIMEOUT = 600
MAX_CONCURRENT_DOWNLOADS = 10  # حد أقصى للتحميلات المتزامنة

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
for logger_name in ["httpx", "werkzeug", "telegram.ext.Application"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

# ==============================================================================
# 2. نظام التزامن المحسّن
# ==============================================================================
active_downloads = defaultdict(int)  # عدد التحميلات النشطة لكل مستخدم
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)  # حد التحميلات الكلي

# ==============================================================================
# 3. قاعدة البيانات (مع حفظ البيانات)
# ==============================================================================
def init_db():
    """تهيئة قاعدة البيانات - لا تحذف البيانات"""
    conn = sqlite3.connect(DB_PATH, timeout=10)
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
                  quality TEXT,
                  download_duration REAL)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS user_stats
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  total_downloads INTEGER DEFAULT 0,
                  successful_downloads INTEGER DEFAULT 0,
                  failed_downloads INTEGER DEFAULT 0,
                  total_data_downloaded INTEGER DEFAULT 0,
                  first_use DATETIME,
                  last_use DATETIME)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS cache
                 (url_hash TEXT PRIMARY KEY,
                  data TEXT,
                  timestamp DATETIME)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bot_metrics
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  date DATE UNIQUE,
                  total_requests INTEGER DEFAULT 0,
                  successful_requests INTEGER DEFAULT 0,
                  failed_requests INTEGER DEFAULT 0,
                  total_data_transferred INTEGER DEFAULT 0,
                  unique_users INTEGER DEFAULT 0)''')
    
    # إنشاء الفهارس
    c.execute('CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_downloads_timestamp ON downloads(timestamp)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_cache_timestamp ON cache(timestamp)')
    
    conn.commit()
    conn.close()
    logging.info("✅ تم تهيئة قاعدة البيانات")

def log_download(user_id: int, username: str, url: str, platform: str, success: bool, 
                 error_msg: str = None, file_size: int = None, quality: str = None, duration: float = None):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        
        # تسجيل التحميل
        c.execute('''INSERT INTO downloads (user_id, username, url, platform, timestamp, 
                     success, error_message, file_size, quality, download_duration)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, username, url, platform, datetime.now(), success, 
                   error_msg, file_size, quality, duration))
        
        # تحديث إحصائيات المستخدم
        c.execute('''INSERT INTO user_stats (user_id, username, total_downloads, 
                     successful_downloads, failed_downloads, total_data_downloaded, first_use, last_use)
                     VALUES (?, ?, 1, ?, ?, ?, ?, ?)
                     ON CONFLICT(user_id) DO UPDATE SET
                     total_downloads = total_downloads + 1,
                     successful_downloads = successful_downloads + ?,
                     failed_downloads = failed_downloads + ?,
                     total_data_downloaded = total_data_downloaded + ?,
                     last_use = ?''',
                  (user_id, username, 1 if success else 0, 1 if not success else 0, 
                   file_size or 0, datetime.now(), datetime.now(),
                   1 if success else 0, 1 if not success else 0, file_size or 0, datetime.now()))
        
        # تحديث مقاييس اليوم
        today = datetime.now().date()
        c.execute('''INSERT INTO bot_metrics (date, total_requests, successful_requests, 
                     failed_requests, total_data_transferred, unique_users)
                     VALUES (?, 1, ?, ?, ?, 1)
                     ON CONFLICT(date) DO UPDATE SET
                     total_requests = total_requests + 1,
                     successful_requests = successful_requests + ?,
                     failed_requests = failed_requests + ?,
                     total_data_transferred = total_data_transferred + ?''',
                  (today, 1 if success else 0, 1 if not success else 0, file_size or 0,
                   1 if success else 0, 1 if not success else 0, file_size or 0))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"فشل تسجيل التحميل: {e}")

def get_stats():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        
        c.execute('SELECT COUNT(DISTINCT user_id) FROM user_stats')
        total_users = c.fetchone()[0]
        
        c.execute('SELECT SUM(total_downloads), SUM(successful_downloads), SUM(failed_downloads), SUM(total_data_downloaded) FROM user_stats')
        stats = c.fetchone()
        
        c.execute('SELECT COUNT(*) FROM downloads WHERE DATE(timestamp) = DATE("now")')
        today = c.fetchone()[0]
        
        c.execute('SELECT platform, COUNT(*) FROM downloads WHERE success=1 GROUP BY platform ORDER BY COUNT(*) DESC LIMIT 5')
        top_platforms = c.fetchall()
        
        c.execute('SELECT AVG(download_duration) FROM downloads WHERE success=1 AND download_duration IS NOT NULL')
        avg_duration = c.fetchone()[0]
        
        # أسرع 3 مستخدمين
        c.execute('''SELECT username, total_downloads FROM user_stats 
                     ORDER BY total_downloads DESC LIMIT 3''')
        top_users = c.fetchall()
        
        conn.close()
        
        return {
            'users': total_users,
            'total': stats[0] or 0,
            'success': stats[1] or 0,
            'failed': stats[2] or 0,
            'total_data': stats[3] or 0,
            'today': today,
            'top_platforms': top_platforms,
            'avg_duration': avg_duration,
            'top_users': top_users
        }
    except Exception as e:
        logging.error(f"فشل جلب الإحصائيات: {e}")
        return None

# ==============================================================================
# 4. التخزين المؤقت
# ==============================================================================
async def get_cached_info(url: str):
    url_hash = hashlib.md5(url.encode()).hexdigest()
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute('SELECT data, timestamp FROM cache WHERE url_hash = ?', (url_hash,))
        result = c.fetchone()
        conn.close()
        
        if result:
            data_str, cache_time = result
            age = (datetime.now() - datetime.fromisoformat(cache_time)).total_seconds()
            if age < CACHE_TTL:
                logging.info("📦 استخدام الكاش")
                return json.loads(data_str)
    except Exception as e:
        logging.debug(f"خطأ قراءة الكاش: {e}")
    return None

async def set_cached_info(url: str, data: dict):
    url_hash = hashlib.md5(url.encode()).hexdigest()
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO cache (url_hash, data, timestamp) VALUES (?, ?, ?)',
                  (url_hash, json.dumps(data), datetime.now()))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"فشل حفظ الكاش: {e}")

async def cleanup_old_cache():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        cutoff = datetime.now() - timedelta(seconds=CACHE_TTL * 2)
        c.execute('DELETE FROM cache WHERE timestamp < ?', (cutoff,))
        deleted = c.rowcount
        conn.commit()
        conn.close()
        if deleted > 0:
            logging.info(f"🧹 تم حذف {deleted} عنصر من الكاش")
    except Exception as e:
        logging.error(f"فشل تنظيف الكاش: {e}")

# ==============================================================================
# 5. Rate Limiting
# ==============================================================================
user_requests = defaultdict(list)

async def check_rate_limit(user_id: int) -> bool:
    now = datetime.now()
    user_requests[user_id] = [t for t in user_requests[user_id] if now - t < timedelta(minutes=1)]
    
    if len(user_requests[user_id]) >= MAX_REQUESTS_PER_MINUTE:
        return False
    
    user_requests[user_id].append(now)
    return True

# ==============================================================================
# 6. التنظيف (بدون حذف قاعدة البيانات)
# ==============================================================================
def cleanup_old_files():
    try:
        if not DOWNLOAD_PATH.exists():
            return
        
        now = datetime.now().timestamp()
        deleted = 0
        for file_path in DOWNLOAD_PATH.glob("*"):
            if file_path.is_file() and now - file_path.stat().st_mtime > FILE_MAX_AGE:
                file_path.unlink()
                deleted += 1
        
        if deleted > 0:
            logging.info(f"🧹 تم حذف {deleted} ملف قديم")
    except Exception as e:
        logging.error(f"خطأ في التنظيف: {e}")

async def periodic_cleanup():
    while True:
        cleanup_old_files()
        await cleanup_old_cache()
        await asyncio.sleep(CLEANUP_INTERVAL)

def cleanup_on_exit():
    """تنظيف الملفات المؤقتة فقط - لا تحذف قاعدة البيانات"""
    try:
        if DOWNLOAD_PATH.exists():
            for file_path in DOWNLOAD_PATH.glob("*"):
                if file_path.is_file():
                    file_path.unlink()
            logging.info("🧹 تم تنظيف الملفات المؤقتة")
    except Exception as e:
        logging.error(f"فشل التنظيف عند الخروج: {e}")

atexit.register(cleanup_on_exit)

# ==============================================================================
# 7. الدوال المساعدة
# ==============================================================================
def is_valid_url(url: str) -> bool:
    return bool(re.match(r'https?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', url))

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
    return 'generic'

def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"

def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}ث"
    minutes = int(seconds / 60)
    secs = int(seconds % 60)
    return f"{minutes}د {secs}ث"

def escape_markdown(text: str) -> str:
    if not text:
        return ""
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))

# ==============================================================================
# 8. Exceptions
# ==============================================================================
class DownloadError(Exception):
    pass

class AnalysisError(Exception):
    pass

# ==============================================================================
# 9. التحميل من Instagram
# ==============================================================================
async def run_gallery_dl(url: str) -> list:
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    
    command = ['gallery-dl', '--cookies', 'cookies.txt', '--directory', str(DOWNLOAD_PATH), url]
    
    try:
        process = await asyncio.wait_for(
            asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE),
            timeout=DOWNLOAD_TIMEOUT
        )
        
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=DOWNLOAD_TIMEOUT)
    except asyncio.TimeoutError:
        raise DownloadError("انتهت مهلة التحميل من Instagram (أكثر من 10 دقائق)")
    
    if process.returncode != 0:
        error = stderr.decode('utf-8', errors='ignore')
        logging.error(f"فشل gallery-dl: {error}")
        raise DownloadError("فشل التحميل من Instagram. تأكد من صحة الرابط")
    
    files = []
    for root, _, names in os.walk(DOWNLOAD_PATH):
        for name in names:
            fp = os.path.join(root, name)
            size = os.path.getsize(fp)
            if size <= MAX_FILE_SIZE:
                files.append(fp)
            else:
                logging.warning(f"ملف كبير جداً: {name} ({format_size(size)})")
                os.remove(fp)
    
    if not files:
        raise DownloadError("لم يتم العثور على ملفات مناسبة (قد تكون كبيرة جداً)")
    
    return files

# ==============================================================================
# 10. التحميل من YouTube وغيرها
# ==============================================================================
def get_ydl_opts(url: str) -> dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
        'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
        'outtmpl': str(DOWNLOAD_PATH / '%(id)s.%(ext)s'),
        'ffmpeg_location': '/usr/bin/ffmpeg',
        'socket_timeout': 300
    }
    
    if 'facebook.com' not in url and 'fb.watch' not in url:
        opts['proxy'] = PRIMARY_PROXY
    
    return opts

async def run_ydl_analysis(url: str) -> dict:
    cached = await get_cached_info(url)
    if cached:
        return cached
    
    opts = get_ydl_opts(url)
    opts['skip_download'] = True
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=False)
            if not info:
                raise AnalysisError("فشل استخراج المعلومات")
            
            await set_cached_info(url, info)
            return info
    except yt_dlp.utils.DownloadError as e:
        raise AnalysisError(f"خطأ: {str(e)}")
    except Exception as e:
        raise AnalysisError(f"خطأ غير متوقع: {str(e)}")

async def run_ydl_download(url: str, format_id: str, is_audio: bool) -> str:
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    opts = get_ydl_opts(url)
    
    if is_audio:
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}]
    else:
        opts['format'] = format_id
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await asyncio.wait_for(
                asyncio.to_thread(ydl.extract_info, url, download=True),
                timeout=DOWNLOAD_TIMEOUT
            )
            filename = ydl.prepare_filename(info)
            
            if is_audio:
                return str(Path(filename).with_suffix('.mp3'))
            return filename
    except asyncio.TimeoutError:
        raise DownloadError("انتهت مهلة التحميل (أكثر من 10 دقائق)")
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(f"خطأ: {str(e)}")
    except Exception as e:
        raise DownloadError(f"خطأ غير متوقع: {str(e)}")

# ==============================================================================
# 11. واجهات المستخدم المحسّنة (مع عرض الأحجام)
# ==============================================================================
def get_total_size_estimate(info: dict) -> str:
    """حساب الحجم الإجمالي التقريبي"""
    formats = info.get('formats', [])
    if not formats:
        return "غير معروف"
    
    # أخذ أفضل صيغة فيديو
    video_formats = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') != 'none']
    if video_formats:
        best = max(video_formats, key=lambda x: x.get('height', 0))
        size = best.get('filesize') or best.get('filesize_approx', 0)
        if size:
            return format_size(size)
    
    return "غير معروف"

def build_youtube_ui(info: dict, msg_id: int) -> tuple:
    title = info.get('title', 'غير متوفر')
    uploader = info.get('uploader', 'غير متوفر')
    duration = info.get('duration', 0)
    views = info.get('view_count', 0)
    
    dur_str = f"{duration // 60}:{duration % 60:02d}" if duration else "غير معروف"
    views_str = f"{views:,}" if views else "غير معروف"
    size_est = get_total_size_estimate(info)
    
    caption = (
        f"🎬 **يوتيوب**\n\n"
        f"📝 {escape_markdown(title[:100])}\n"
        f"👤 {escape_markdown(uploader)}\n"
        f"⏱ المدة: {dur_str}\n"
        f"👁 المشاهدات: {escape_markdown(views_str)}\n"
        f"📦 الحجم التقريبي: {escape_markdown(size_est)}"
    )
    
    formats = [f for f in info.get('formats', []) 
               if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and f.get('height', 0) <= 720]
    
    for f in formats:
        size = f.get('filesize') or f.get('filesize_approx', 0)
        f['_size_str'] = format_size(size) if size else ""
        f['_too_large'] = size > MAX_FILE_SIZE if size else False
    
    best = max(formats, key=lambda x: x.get('height', 0), default=None)
    
    buttons = []
    if best and not best.get('_too_large'):
        btn_text = f"🎬 {best.get('height')}p"
        if best.get('_size_str'):
            btn_text += f" ({best.get('_size_str')})"
        buttons.append(InlineKeyboardButton(btn_text, callback_data=f"yt:v:{best['format_id']}:{msg_id}"))
    
    buttons.append(InlineKeyboardButton("🎵 MP3", callback_data=f"yt:a:best:{msg_id}"))
    
    keyboard = [buttons]
    
    available = [f for f in formats if not f.get('_too_large')]
    if len(available) > 1:
        keyboard.append([InlineKeyboardButton("🎞️ جودات أخرى", callback_data=f"qualities:v:na:{msg_id}")])
    
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")])
    
    return caption, InlineKeyboardMarkup(keyboard)

def build_generic_ui(info: dict, msg_id: int) -> tuple:
    site = info.get('extractor_key', 'Website').capitalize()
    title = info.get('title', 'غير متوفر')
    size_est = get_total_size_estimate(info)
    
    caption = (
        f"🌐 **{escape_markdown(site)}**\n\n"
        f"📝 {escape_markdown(title[:100])}\n"
        f"📦 الحجم التقريبي: {escape_markdown(size_est)}"
    )
    
    buttons = [
        InlineKeyboardButton("🎬 فيديو", callback_data=f"yt:v:best:{msg_id}"),
        InlineKeyboardButton("🎵 صوت", callback_data=f"yt:a:best:{msg_id}")
    ]
    
    keyboard = [buttons, [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")]]
    
    return caption, InlineKeyboardMarkup(keyboard)

def build_qualities_ui(info: dict, msg_id: int) -> InlineKeyboardMarkup:
    formats = [f for f in info.get('formats', []) 
               if f.get('vcodec') != 'none' and f.get('acodec') != 'none']
    
    formats.sort(key=lambda x: x.get('height', 0), reverse=True)
    
    buttons = []
    seen = set()
    
    for f in formats:
        h = f.get('height', 0)
        if h and h not in seen and h <= 1080:
            seen.add(h)
            size = f.get('filesize') or f.get('filesize_approx', 0)
            size_str = f" ({format_size(size)})" if size else ""
            
            if size and size > MAX_FILE_SIZE:
                buttons.append([InlineKeyboardButton(
                    f"❌ {h}p{size_str} (كبير)",
                    callback_data=f"ignore:na:na:{msg_id}"
                )])
            else:
                buttons.append([InlineKeyboardButton(
                    f"📹 {h}p{size_str}",
                    callback_data=f"yt:v:{f['format_id']}:{msg_id}"
                )])
    
    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"back:na:na:{msg_id}")])
    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")])
    
    return InlineKeyboardMarkup(buttons)

# ==============================================================================
# 12. معالجات الأوامر
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = (
        f"👋 مرحباً **{escape_markdown(user.first_name)}**\\!\n\n"
        f"🤖 بوت تحميل محسّن من:\n"
        f"• YouTube 🎬 • Instagram 📸\n"
        f"• Facebook 👥 • Twitter 🐦\n"
        f"• TikTok 🎵 وأكثر\\.\\.\\.\n\n"
        f"✨ **المميزات الجديدة:**\n"
        f"• 🚀 تحميل متزامن \\(10 مستخدمين\\)\n"
        f"• 💾 كاش ذكي للروابط\n"
        f"• 📊 عرض الأحجام قبل التحميل\n"
        f"• 🔄 إعادة محاولة تلقائية\n"
        f"• 📈 إحصائيات محفوظة\n"
        f"• ⚡️ سرعة محسّنة\n\n"
        f"📌 أرسل رابط الفيديو\\!\n\n"
        f"/help \\- المساعدة"
    )
    
    if str(user.id) == ADMIN_ID:
        msg += f"\n/stats \\- إحصائيات مفصلة"
    
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 **دليل الاستخدام**\n\n"
        "**📥 كيفية التحميل:**\n"
        "1\\. أرسل رابط الفيديو\n"
        "2\\. اختر الجودة المناسبة\n"
        "3\\. انتظر التحميل والرفع\n\n"
        "**⚠️ الحدود:**\n"
        "• 50 ميغابايت كحد أقصى\n"
        "• 5 طلبات/دقيقة لكل مستخدم\n"
        "• 10 تحميلات متزامنة كحد أقصى\n\n"
        "**✨ المميزات:**\n"
        "• عرض الأحجام قبل التحميل\n"
        "• تخزين مؤقت للروابط\n"
        "• إعادة محاولة تلقائية\n"
        "• إحصائيات محفوظة\n\n"
        "**💡 نصائح:**\n"
        "• اختر جودة أقل للملفات الكبيرة\n"
        "• استخدم MP3 للملفات الطويلة\n"
        "• البوت يعمل لجميع المستخدمين بالتزامن"
    )
    
    await update.message.reply_text(escape_markdown(text), parse_mode=ParseMode.MARKDOWN_V2)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN, file_size: int = None, quality: str = None):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        
        c.execute('''INSERT INTO downloads (user_id, username, url, platform, timestamp, 
                     success, error_message, file_size, quality)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, username, url, platform, datetime.now(), success, 
                   error_msg, file_size, quality))
        
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
        logging.error(f"فشل تسجيل التحميل: {e}")

def get_stats():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        
        c.execute('SELECT COUNT(DISTINCT user_id) FROM user_stats')
        total_users = c.fetchone()[0]
        
        c.execute('SELECT SUM(total_downloads), SUM(successful_downloads), SUM(failed_downloads) FROM user_stats')
        stats = c.fetchone()
        
        c.execute('SELECT COUNT(*) FROM downloads WHERE DATE(timestamp) = DATE("now")')
        today = c.fetchone()[0]
        
        c.execute('SELECT platform, COUNT(*) FROM downloads WHERE success=1 GROUP BY platform ORDER BY COUNT(*) DESC LIMIT 3')
        top_platforms = c.fetchall()
        
        conn.close()
        
        return {
            'users': total_users,
            'total': stats[0] or 0,
            'success': stats[1] or 0,
            'failed': stats[2] or 0,
            'today': today,
            'top_platforms': top_platforms
        }
    except Exception as e:
        logging.error(f"فشل جلب الإحصائيات: {e}")
        return None

# ==============================================================================
# 3. التخزين المؤقت
# ==============================================================================
async def get_cached_info(url: str):
    url_hash = hashlib.md5(url.encode()).hexdigest()
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute('SELECT data, timestamp FROM cache WHERE url_hash = ?', (url_hash,))
        result = c.fetchone()
        conn.close()
        
        if result:
            data_str, cache_time = result
            age = (datetime.now() - datetime.fromisoformat(cache_time)).total_seconds()
            if age < CACHE_TTL:
                return json.loads(data_str)
    except Exception as e:
        logging.debug(f"خطأ قراءة الكاش: {e}")
    return None

async def set_cached_info(url: str, data: dict):
    url_hash = hashlib.md5(url.encode()).hexdigest()
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO cache (url_hash, data, timestamp) VALUES (?, ?, ?)',
                  (url_hash, json.dumps(data), datetime.now()))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"فشل حفظ الكاش: {e}")

async def cleanup_old_cache():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        cutoff = datetime.now() - timedelta(seconds=CACHE_TTL)
        c.execute('DELETE FROM cache WHERE timestamp < ?', (cutoff,))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"فشل تنظيف الكاش: {e}")

# ==============================================================================
# 4. Rate Limiting
# ==============================================================================
user_requests = defaultdict(list)
user_locks = defaultdict(asyncio.Lock)

async def check_rate_limit(user_id: int) -> bool:
    now = datetime.now()
    user_requests[user_id] = [t for t in user_requests[user_id] if now - t < timedelta(minutes=1)]
    
    if len(user_requests[user_id]) >= MAX_REQUESTS_PER_MINUTE:
        return False
    
    user_requests[user_id].append(now)
    return True

# ==============================================================================
# 5. التنظيف
# ==============================================================================
def cleanup_old_files():
    try:
        if not DOWNLOAD_PATH.exists():
            return
        
        now = datetime.now().timestamp()
        deleted = 0
        for file_path in DOWNLOAD_PATH.glob("*"):
            if file_path.is_file() and now - file_path.stat().st_mtime > FILE_MAX_AGE:
                file_path.unlink()
                deleted += 1
        
        if deleted > 0:
            logging.info(f"تم حذف {deleted} ملف قديم")
    except Exception as e:
        logging.error(f"خطأ في التنظيف: {e}")

async def periodic_cleanup():
    while True:
        cleanup_old_files()
        await cleanup_old_cache()
        await asyncio.sleep(CLEANUP_INTERVAL)

def cleanup_on_exit():
    try:
        if DOWNLOAD_PATH.exists():
            shutil.rmtree(DOWNLOAD_PATH)
    except Exception as e:
        logging.error(f"فشل التنظيف عند الخروج: {e}")

atexit.register(cleanup_on_exit)

# ==============================================================================
# 6. الدوال المساعدة
# ==============================================================================
def is_valid_url(url: str) -> bool:
    return bool(re.match(r'https?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', url))

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
    return 'generic'

def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"

def escape_markdown(text: str) -> str:
    if not text:
        return ""
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))

# ==============================================================================
# 7. Exceptions
# ==============================================================================
class DownloadError(Exception):
    pass

class AnalysisError(Exception):
    pass

# ==============================================================================
# 8. التحميل من Instagram
# ==============================================================================
async def run_gallery_dl(url: str) -> list:
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    
    command = ['gallery-dl', '--cookies', 'cookies.txt', '--directory', str(DOWNLOAD_PATH), url]
    
    try:
        process = await asyncio.wait_for(
            asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE),
            timeout=DOWNLOAD_TIMEOUT
        )
        
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=DOWNLOAD_TIMEOUT)
    except asyncio.TimeoutError:
        raise DownloadError("انتهت مهلة التحميل من Instagram")
    
    if process.returncode != 0:
        error = stderr.decode('utf-8', errors='ignore')
        logging.error(f"فشل gallery-dl: {error}")
        raise DownloadError("فشل التحميل من Instagram. تأكد من صحة الرابط")
    
    files = []
    for root, _, names in os.walk(DOWNLOAD_PATH):
        for name in names:
            fp = os.path.join(root, name)
            if os.path.getsize(fp) <= MAX_FILE_SIZE:
                files.append(fp)
            else:
                os.remove(fp)
    
    if not files:
        raise DownloadError("لم يتم العثور على ملفات مناسبة (قد تكون كبيرة جداً)")
    
    return files

# ==============================================================================
# 9. التحميل من YouTube وغيرها
# ==============================================================================
def get_ydl_opts(url: str) -> dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
        'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
        'outtmpl': str(DOWNLOAD_PATH / '%(id)s.%(ext)s'),
        'ffmpeg_location': '/usr/bin/ffmpeg',
        'socket_timeout': 300
    }
    
    if 'facebook.com' not in url and 'fb.watch' not in url:
        opts['proxy'] = PRIMARY_PROXY
    
    return opts

async def run_ydl_analysis(url: str) -> dict:
    cached = await get_cached_info(url)
    if cached:
        logging.info("استخدام الكاش")
        return cached
    
    opts = get_ydl_opts(url)
    opts['skip_download'] = True
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=False)
            if not info:
                raise AnalysisError("فشل استخراج المعلومات")
            
            await set_cached_info(url, info)
            return info
    except yt_dlp.utils.DownloadError as e:
        raise AnalysisError(f"خطأ: {str(e)}")
    except Exception as e:
        raise AnalysisError(f"خطأ غير متوقع: {str(e)}")

async def run_ydl_download(url: str, format_id: str, is_audio: bool) -> str:
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    opts = get_ydl_opts(url)
    
    if is_audio:
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}]
    else:
        opts['format'] = format_id
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await asyncio.wait_for(
                asyncio.to_thread(ydl.extract_info, url, download=True),
                timeout=DOWNLOAD_TIMEOUT
            )
            filename = ydl.prepare_filename(info)
            
            if is_audio:
                return str(Path(filename).with_suffix('.mp3'))
            return filename
    except asyncio.TimeoutError:
        raise DownloadError("انتهت مهلة التحميل")
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(f"خطأ: {str(e)}")
    except Exception as e:
        raise DownloadError(f"خطأ غير متوقع: {str(e)}")

# ==============================================================================
# 10. واجهات المستخدم
# ==============================================================================
def build_youtube_ui(info: dict, msg_id: int) -> tuple:
    title = info.get('title', 'غير متوفر')
    uploader = info.get('uploader', 'غير متوفر')
    duration = info.get('duration', 0)
    
    dur_str = f"{duration // 60}:{duration % 60:02d}" if duration else "غير معروف"
    
    caption = (
        f"🎬 **يوتيوب**\n\n"
        f"📝 {escape_markdown(title)}\n"
        f"👤 {escape_markdown(uploader)}\n"
        f"⏱ {dur_str}"
    )
    
    formats = [f for f in info.get('formats', []) 
               if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and f.get('height', 0) <= 720]
    
    for f in formats:
        size = f.get('filesize') or f.get('filesize_approx', 0)
        f['_size_str'] = format_size(size) if size else ""
        f['_too_large'] = size > MAX_FILE_SIZE if size else False
    
    best = max(formats, key=lambda x: x.get('height', 0), default=None)
    
    buttons = []
    if best and not best.get('_too_large'):
        buttons.append(InlineKeyboardButton(
            f"🎬 {best.get('height')}p {best.get('_size_str')}",
            callback_data=f"yt:v:{best['format_id']}:{msg_id}"
        ))
    
    buttons.append(InlineKeyboardButton("🎵 MP3", callback_data=f"yt:a:best:{msg_id}"))
    
    keyboard = [buttons]
    
    available = [f for f in formats if not f.get('_too_large')]
    if len(available) > 1:
        keyboard.append([InlineKeyboardButton("🎞️ جودات أخرى", callback_data=f"qualities:v:na:{msg_id}")])
    
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")])
    
    return caption, InlineKeyboardMarkup(keyboard)

def build_generic_ui(info: dict, msg_id: int) -> tuple:
    site = info.get('extractor_key', 'Website').capitalize()
    title = info.get('title', 'غير متوفر')
    
    caption = f"🌐 **{escape_markdown(site)}**\n\n📝 {escape_markdown(title)}"
    
    buttons = [
        InlineKeyboardButton("🎬 فيديو", callback_data=f"yt:v:best:{msg_id}"),
        InlineKeyboardButton("🎵 صوت", callback_data=f"yt:a:best:{msg_id}")
    ]
    
    keyboard = [buttons, [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")]]
    
    return caption, InlineKeyboardMarkup(keyboard)

def build_qualities_ui(info: dict, msg_id: int) -> InlineKeyboardMarkup:
    formats = [f for f in info.get('formats', []) 
               if f.get('vcodec') != 'none' and f.get('acodec') != 'none']
    
    formats.sort(key=lambda x: x.get('height', 0), reverse=True)
    
    buttons = []
    seen = set()
    
    for f in formats:
        h = f.get('height', 0)
        if h and h not in seen and h <= 1080:
            seen.add(h)
            size = f.get('filesize') or f.get('filesize_approx', 0)
            size_str = f" - {format_size(size)}" if size else ""
            
            if size and size > MAX_FILE_SIZE:
                buttons.append([InlineKeyboardButton(
                    f"❌ {h}p{size_str}",
                    callback_data=f"ignore:na:na:{msg_id}"
                )])
            else:
                buttons.append([InlineKeyboardButton(
                    f"📹 {h}p{size_str}",
                    callback_data=f"yt:v:{f['format_id']}:{msg_id}"
                )])
    
    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"back:na:na:{msg_id}")])
    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")])
    
    return InlineKeyboardMarkup(buttons)

# ==============================================================================
# 11. معالجات الأوامر
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = (
        f"👋 مرحباً **{escape_markdown(user.first_name)}**\\!\n\n"
        f"🤖 بوت تحميل من:\n"
        f"• YouTube 🎬 • Instagram 📸\n"
        f"• Facebook 👥 • Twitter 🐦\n"
        f"• TikTok 🎵 وأكثر\\.\\.\\.\n\n"
        f"✨ **المميزات:**\n"
        f"• 🚀 تحميل متزامن لعدة مستخدمين\n"
        f"• 💾 تخزين مؤقت ذكي\n"
        f"• 🔄 إعادة محاولة تلقائية\n"
        f"• 📊 إحصائيات مفصلة\n\n"
        f"📌 أرسل رابط الفيديو\\!\n\n"
        f"/help \\- المساعدة"
    )
    
    if str(user.id) == ADMIN_ID:
        msg += f"\n/stats \\- إحصائيات"
    
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 **المساعدة**\n\n"
        "🔹 أرسل رابط فيديو\n"
        "🔹 اختر الجودة\n"
        "🔹 انتظر التحميل\n\n"
        "⚠️ **حدود:**\n"
        "• 50 ميغابايت كحد أقصى\n"
        "• 5 طلبات/دقيقة\n\n"
        "✨ **مميزات:**\n"
        "• تحميل متزامن\n"
        "• كاش ذكي\n"
        "• إعادة محاولة تلقائية"
    )
    
    await update.message.reply_text(escape_markdown(text), parse_mode=ParseMode.MARKDOWN_V2)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("⛔️ للأدمن فقط")
        return
    
    stats = get_stats()
    if not stats:
        await update.message.reply_text("❌ فشل جلب الإحصائيات")
        return
    
    platforms_text = "\n".join([f"• {p[0]}: {p[1]}" for p in stats['top_platforms']])
    
    text = (
        f"📊 **إحصائيات البوت**\n\n"
        f"👥 المستخدمين: {stats['users']}\n"
        f"📥 التحميلات: {stats['total']}\n"
        f"✅ ناجحة: {stats['success']}\n"
        f"❌ فاشلة: {stats['failed']}\n"
        f"📅 اليوم: {stats['today']}\n\n"
        f"🏆 **أكثر المنصات:**\n{platforms_text}\n\n"
        f"📈 النجاح: {(stats['success'] / stats['total'] * 100) if stats['total'] > 0 else 0:.1f}%"
    )
    
    await update.message.reply_text(escape_markdown(text), parse_mode=ParseMode.MARKDOWN_V2)

# ==============================================================================
# 12. معالج الروابط (متزامن)
# ==============================================================================
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    url = update.message.text.strip()
    
    if not is_valid_url(url):
        await update.message.reply_text("⚠️ رابط غير صالح")
        return
    
    if not await check_rate_limit(user.id):
        await update.message.reply_text("⏱ تجاوزت الحد (5 طلبات/دقيقة)")
        return
    
    # استخدام Lock لكل مستخدم للتزامن
    async with user_locks[user.id]:
        msg = await update.message.reply_text("⏳ جارٍ التحليل...")
        platform = detect_platform(url)
        files = []
        
        try:
            if platform == 'instagram':
                files = await run_gallery_dl(url)
                await msg.edit_text("⚡️ جارٍ الرفع...")
                
                if len(files) == 1:
                    fp = Path(files[0])
                    size = fp.stat().st_size if fp.exists() else 0
                    
                    if fp.suffix.lower() in ['.jpg', '.jpeg', '.png', '.webp']:
                        await context.bot.send_photo(update.effective_chat.id, open(fp, 'rb'), caption="✅ تم")
                    else:
                        await context.bot.send_video(update.effective_chat.id, open(fp, 'rb'), caption="✅ تم")
                    
                    quality = 'N/A'
                else:
                    zip_path = DOWNLOAD_PATH / f"ig_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
                    with zipfile.ZipFile(zip_path, 'w') as z:
                        for f in files:
                            z.write(f, Path(f).name)
                    
                    size = zip_path.stat().st_size
                    await context.bot.send_document(update.effective_chat.id, open(zip_path, 'rb'), 
                                                   caption=f"✅ {len(files)} ملف")
                    files.append(str(zip_path))
                    quality = 'ZIP'
                
                await msg.delete()
                log_download(user.id, user.username, url, platform, True, file_size=size, quality=quality)
            
            else:
                info = await run_ydl_analysis(url)
                context.user_data[msg.message_id] = info
                
                if platform == 'youtube':
                    caption, keyboard = build_youtube_ui(info, msg.message_id)
                else:
                    caption, keyboard = build_generic_ui(info, msg.message_id)
                
                thumb = info.get('thumbnail')
                
                if thumb:
                    await msg.delete()
                    await context.bot.send_photo(update.effective_chat.id, thumb, caption=caption,
                                                parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard)
                else:
                    await msg.edit_text(caption, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard)
        
        except (AnalysisError, DownloadError) as e:
            try:
                await msg.edit_text(f"❌ فشل التحميل\n\n{escape_markdown(str(e))}", 
                                   parse_mode=ParseMode.MARKDOWN_V2)
            except:
                await msg.edit_text(f"❌ فشل التحميل\n\n{str(e)}")
            log_download(user.id, user.username, url, platform, False, str(e))
        
        except Exception as e:
            logging.error(f"خطأ غير متوقع: {e}", exc_info=True)
            try:
                await msg.edit_text(f"❌ خطأ غير متوقع\n\n{escape_markdown(str(e))}", 
                                   parse_mode=ParseMode.MARKDOWN_V2)
            except:
                await msg.edit_text(f"❌ خطأ غير متوقع\n\n{str(e)}")
            log_download(user.id, user.username, url, platform, False, str(e))
        
        finally:
            for f in files:
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except Exception as e:
                    logging.warning(f"فشل حذف {f}: {e}")

# ==============================================================================
# 13. معالج الأزرار
# ==============================================================================
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
        await query.answer("⚠️ الملف أكبر من 50MB", show_alert=True)
        return
    
    if handler_type == "cancel":
        await query.message.delete()
        if msg_id in context.user_data:
            del context.user_data[msg_id]
        return
    
    if msg_id not in context.user_data:
        await query.edit_message_text("⚠️ انتهت الجلسة")
        return
    
    info = context.user_data[msg_id]
    url = info.get('webpage_url')
    user = query.from_user
    platform = detect_platform(url)
    
    if handler_type == "qualities":
        keyboard = build_qualities_ui(info, msg_id)
        try:
            await query.message.edit_reply_markup(reply_markup=keyboard)
        except BadRequest:
  
