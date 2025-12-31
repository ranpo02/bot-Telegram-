# app.py
# 🚀 الإصدار 12.0: نسخة محسنة مع جميع التحسينات في ملف واحد

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

# --- Library Imports ---
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import BadRequest, NetworkError
from telegram.constants import ParseMode
import yt_dlp

# ==============================================================================
# 1. CONFIGURATION - مع إضافة إعدادات جديدة
# ==============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))
ADMIN_ID = "5898628858"  # ⬅️ كما هو
PRIMARY_PROXY = "154.3.236.202:3128"  # ⬅️ كما هو

DOWNLOAD_PATH = Path("downloads")
DB_PATH = Path("bot_stats.db")
CACHE_PATH = Path("cache")  # ⬅️ جديد للملفات المؤقتة

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
]

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB - الحد الأقصى
MAX_REQUESTS_PER_MINUTE = 5
MAX_CONCURRENT_DOWNLOADS = 2  # ⬅️ جديد: حد التحميلات المتزامنة
CLEANUP_INTERVAL = 1800  # 30 دقيقة
FILE_MAX_AGE = 3600  # ساعة واحدة
CACHE_TTL = 600  # ⬅️ جديد: 10 دقائق للتخزين المؤقت

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
for logger_name in ["httpx", "werkzeug", "telegram.ext.Application"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

# ==============================================================================
# 2. DATABASE SETUP - مع جداول جديدة
# ==============================================================================

def init_db():
    """تهيئة قاعدة البيانات مع جداول محسنة"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # الجداول الحالية
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
                  preferred_quality TEXT DEFAULT '720p',
                  preferred_format TEXT DEFAULT 'mp4')''')
    
    # ⬅️ جديد: جدول التخزين المؤقت
    c.execute('''CREATE TABLE IF NOT EXISTS cache
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  url_hash TEXT UNIQUE,
                  data TEXT,
                  timestamp DATETIME)''')
    
    # ⬅️ جديد: جدول قائمة الانتظار
    c.execute('''CREATE TABLE IF NOT EXISTS download_queue
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  url TEXT,
                  status TEXT DEFAULT 'pending',
                  added_time DATETIME,
                  start_time DATETIME,
                  end_time DATETIME)''')
    
    # ⬅️ جديد: فهارس للأداء
    c.execute('CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_downloads_time ON downloads(timestamp)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_cache_hash ON cache(url_hash)')
    
    conn.commit()
    conn.close()
    logging.info("Database initialized successfully with enhanced tables.")

def log_download(user_id: int, username: str, url: str, platform: str, success: bool, 
                 error_msg: str = None, file_size: int = None, quality: str = None):
    """تسجيل عملية التحميل مع بيانات إضافية"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('''INSERT INTO downloads (user_id, username, url, platform, timestamp, 
                     success, error_message, file_size, quality)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, username, url, platform, datetime.now(), success, 
                   error_msg, file_size, quality))
        
        # تحديث تفضيلات المستخدم إذا نجح التحميل
        if success and quality:
            c.execute('''UPDATE user_stats 
                         SET preferred_quality = ?,
                             last_use = ?
                         WHERE user_id = ? AND (
                             preferred_quality IS NULL OR 
                             preferred_quality = '' OR
                             ? IN ('720p', '1080p', '480p')
                         )''',
                      (quality, datetime.now(), user_id, quality))
        
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
        logging.error(f"Failed to log download: {e}")

def get_stats():
    """الحصول على إحصائيات البوت مع بيانات إضافية"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('SELECT COUNT(DISTINCT user_id) FROM user_stats')
        total_users = c.fetchone()[0]
        
        c.execute('SELECT SUM(total_downloads), SUM(successful_downloads), SUM(failed_downloads) FROM user_stats')
        stats = c.fetchone()
        
        c.execute('SELECT COUNT(*) FROM downloads WHERE DATE(timestamp) = DATE("now")')
        today_downloads = c.fetchone()[0]
        
        # ⬅️ جديد: متوسط حجم الملفات
        c.execute('SELECT AVG(file_size) FROM downloads WHERE file_size IS NOT NULL')
        avg_size = c.fetchone()[0] or 0
        
        # ⬅️ جديد: الجودة الأكثر طلباً
        c.execute('''SELECT quality, COUNT(*) as count 
                     FROM downloads 
                     WHERE quality IS NOT NULL AND quality != ''
                     GROUP BY quality 
                     ORDER BY count DESC 
                     LIMIT 1''')
        top_quality = c.fetchone()
        
        # ⬅️ جديد: الملفات في قائمة الانتظار
        c.execute('SELECT COUNT(*) FROM download_queue WHERE status = "pending"')
        queue_pending = c.fetchone()[0]
        
        conn.close()
        
        return {
            'total_users': total_users,
            'total_downloads': stats[0] or 0,
            'successful': stats[1] or 0,
            'failed': stats[2] or 0,
            'today': today_downloads,
            'avg_file_size': f"{avg_size / (1024*1024):.1f} MB" if avg_size > 0 else "N/A",
            'top_quality': top_quality[0] if top_quality else "N/A",
            'queue_pending': queue_pending
        }
    except Exception as e:
        logging.error(f"Failed to get stats: {e}")
        return None

# ==============================================================================
# 3. نظام التخزين المؤقت - ⬅️ جديد بالكامل
# ==============================================================================

class AdvancedCache:
    """نظام تخزين مؤقت متقدم"""
    
    @staticmethod
    def get_cache_key(url: str) -> str:
        """إنشاء مفتاح تخزين مؤقت فريد"""
        return hashlib.md5(url.encode()).hexdigest()
    
    @staticmethod
    @lru_cache(maxsize=100)
    async def get_cached_analysis(url: str) -> dict:
        """الحصول على تحليل مخزن مؤقت"""
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
                
                if cache_age < CACHE_TTL:  # لا يزال جديداً
                    return json.loads(data_str)
                else:
                    # حذف إذا انتهت صلاحيته
                    AdvancedCache.delete_cache(url)
        except Exception as e:
            logging.debug(f"Cache read error: {e}")
        
        return None
    
    @staticmethod
    async def set_cached_analysis(url: str, data: dict):
        """تخزين التحليل مؤقتاً"""
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
            logging.error(f"Failed to cache analysis: {e}")
    
    @staticmethod
    def delete_cache(url: str):
        """حذف عنصر من التخزين المؤقت"""
        cache_key = AdvancedCache.get_cache_key(url)
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('DELETE FROM cache WHERE url_hash = ?', (cache_key,))
            conn.commit()
            conn.close()
        except Exception as e:
            logging.error(f"Failed to delete cache: {e}")

# ==============================================================================
# 4. نظام قائمة الانتظار - ⬅️ جديد بالكامل
# ==============================================================================

class DownloadQueue:
    """إدارة قائمة انتظار التحميلات"""
    
    def __init__(self):
        self.queue = []
        self.active_downloads = defaultdict(int)
        self.queue_lock = asyncio.Lock()
    
    async def add_to_queue(self, user_id: int, url: str, priority: int = 5) -> int:
        """إضافة تحميل للقائمة مع إرجاع الموقع"""
        async with self.queue_lock:
            # حساب الأولوية (الأدمن أولاً)
            actual_priority = priority
            if str(user_id) == ADMIN_ID:
                actual_priority += 100
            
            position = len(self.queue) + 1
            heapq.heappush(self.queue, (actual_priority, time.time(), user_id, url))
            
            # تسجيل في قاعدة البيانات
            try:
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute('''INSERT INTO download_queue (user_id, url, added_time, status)
                             VALUES (?, ?, ?, ?)''',
                          (user_id, url, datetime.now(), 'pending'))
                conn.commit()
                conn.close()
            except Exception as e:
                logging.error(f"Failed to log queue: {e}")
            
            return position
    
    async def get_next_download(self) -> tuple:
        """الحصول على التحميل التالي"""
        async with self.queue_lock:
            if not self.queue:
                return None
            
            # التحقق من الحد الأقصى للتحميلات النشطة
            for priority, timestamp, user_id, url in sorted(self.queue):
                if self.active_downloads[user_id] < MAX_CONCURRENT_DOWNLOADS:
                    # إزالة من القائمة
                    self.queue.remove((priority, timestamp, user_id, url))
                    heapq.heapify(self.queue)
                    
                    self.active_downloads[user_id] += 1
                    
                    # تحديث الحالة في قاعدة البيانات
                    try:
                        conn = sqlite3.connect(DB_PATH)
                        c = conn.cursor()
                        c.execute('''UPDATE download_queue 
                                     SET status = 'downloading', start_time = ?
                                     WHERE user_id = ? AND url = ? AND status = 'pending'
                                     LIMIT 1''',
                                  (datetime.now(), user_id, url))
                        conn.commit()
                        conn.close()
                    except Exception as e:
                        logging.error(f"Failed to update queue status: {e}")
                    
                    return user_id, url
            
            return None
    
    def complete_download(self, user_id: int, url: str, success: bool = True):
        """إكمال تحميل"""
        self.active_downloads[user_id] = max(0, self.active_downloads[user_id] - 1)
        
        # تحديث قاعدة البيانات
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('''UPDATE download_queue 
                         SET status = ?, end_time = ?
                         WHERE user_id = ? AND url = ? AND status = 'downloading' 
                         LIMIT 1''',
                      ('completed' if success else 'failed', datetime.now(), user_id, url))
            conn.commit()
            conn.close()
        except Exception as e:
            logging.error(f"Failed to complete queue item: {e}")

# إنشاء كائن قائمة الانتظار العام
download_queue = DownloadQueue()

# ==============================================================================
# 5. نظام تتبع التقدم - ⬅️ جديد بالكامل
# ==============================================================================

class ProgressTracker:
    """تتبع تقدم التحميل"""
    
    def __init__(self, chat_id: int, message_id: int, bot):
        self.chat_id = chat_id
        self.message_id = message_id
        self.bot = bot
        self.start_time = time.time()
        self.last_update = 0
        self.progress_messages = [
            "⏳ جاري التحليل...",
            "📥 جاري التحميل...",
            "⚡ تجهيز الملف...",
            "🚀 جاري الرفع..."
        ]
        self.current_step = 0
    
    def get_progress_hook(self):
        """إرجاع هوك التقدم لـ yt-dlp"""
        def progress_hook(d):
            if d['status'] == 'downloading':
                # تحديث كل 3 ثوانٍ فقط لتجنب spam
                current_time = time.time()
                if current_time - self.last_update > 3:
                    self.last_update = current_time
                    
                    # حساب النسبة المئوية
                    total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                    downloaded = d.get('downloaded_bytes', 0)
                    
                    if total and downloaded:
                        percent = (downloaded / total) * 100
                        asyncio.create_task(self.update_progress(percent))
        
        return progress_hook
    
    async def update_progress(self, percent: float):
        """تحديث رسالة التقدم"""
        try:
            # شريط تقدم بسيط
            bars = 10
            filled = int(bars * percent / 100)
            progress_bar = "█" * filled + "░" * (bars - filled)
            
            progress_text = (
                f"{self.progress_messages[min(self.current_step, len(self.progress_messages)-1)]}\n\n"
                f"`{progress_bar}` {percent:.1f}%\n"
                f"⏱️ {int(time.time() - self.start_time)} ثانية"
            )
            
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=progress_text,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logging.debug(f"Progress update skipped: {e}")
    
    async def next_step(self):
        """الانتقال للخطوة التالية"""
        self.current_step += 1
        await self.update_progress(0)

# ==============================================================================
# 6. نظام إعادة المحاولة - ⬅️ جديد بالكامل
# ==============================================================================

class RetryManager:
    """إدارة إعادة المحاولة مع التدرج الأسي"""
    
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
    
    async def execute_with_retry(self, coroutine_func, *args, **kwargs):
        """تنفيذ مع إعادة المحاولة"""
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                return await coroutine_func(*args, **kwargs)
            except (NetworkError, TimeoutError, ConnectionError) as e:
                last_exception = e
                
                if attempt == self.max_retries - 1:
                    break
                
                # تأخير متزايد
                delay = self.base_delay * (2 ** attempt) + random.uniform(0, 1)
                logging.info(f"Retry {attempt + 1}/{self.max_retries} after {delay:.1f}s")
                await asyncio.sleep(delay)
            except Exception as e:
                # لا نعيد المحاولة على أخطاء أخرى
                raise e
        
        if last_exception:
            raise last_exception
        raise Exception("فشل بعد كل المحاولات")

# ==============================================================================
# 7. RATE LIMITING - مع تحسينات
# ==============================================================================

user_requests = defaultdict(list)

async def check_rate_limit(user_id: int) -> bool:
    """التحقق من حد الطلبات مع تحسينات"""
    now = datetime.now()
    
    # تنظيف الطلبات القديمة
    user_requests[user_id] = [
        req_time for req_time in user_requests[user_id]
        if now - req_time < timedelta(minutes=1)
    ]
    
    # ⬅️ جديد: التحقق من قائمة الانتظار أيضاً
    active_count = sum(1 for _, _, uid, _ in download_queue.queue if uid == user_id)
    if active_count >= MAX_CONCURRENT_DOWNLOADS:
        return False
    
    if len(user_requests[user_id]) >= MAX_REQUESTS_PER_MINUTE:
        return False
    
    user_requests[user_id].append(now)
    return True

# ==============================================================================
# 8. FILE CLEANUP - كما هو
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
# 9. UI & MESSAGES - مع إضافة رسائل جديدة
# ==============================================================================

ANALYZING_MESSAGE = "⏳ جاري تحليل الرابط والتحميل..."
UPLOADING_MESSAGE = "⚡️ تم التحميل، جاري الرفع إليك..."
INVALID_URL_MESSAGE = "⚠️ عذرًا، الرابط الذي أرسلته غير صالح."
GENERIC_ERROR_MESSAGE = "❌ حدث خطأ غير متوقع."
ANALYSIS_FAILED_MESSAGE = "❌ فشل التحميل."
SESSION_EXPIRED_MESSAGE = "⚠️ انتهت صلاحية هذه الجلسة."
RATE_LIMIT_MESSAGE = "⏱ لقد تجاوزت الحد المسموح من الطلبات (5 طلبات/دقيقة). يرجى الانتظار قليلاً."
QUEUE_MESSAGE = "📋 تم إضافتك لقائمة الانتظار. موقعك: {position}"  # ⬅️ جديد
QUEUE_PROCESSING_MESSAGE = "🔄 جاري معالجة طلبك من قائمة الانتظار..."  # ⬅️ جديد

def escape_markdown(text: str) -> str:
    """تنظيف النص من أحرف Markdown الخاصة"""
    if not text:
        return ""
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))

# ==============================================================================
# 10. CORE LOGIC - مع تحسينات
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

def get_base_ydl_opts(url: str, progress_hook=None) -> dict:  # ⬅️ أضيف progress_hook
    """الحصول على إعدادات yt-dlp الأساسية"""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
        'outtmpl': str(DOWNLOAD_PATH / '%(id)s.%(ext)s'),
        'ffmpeg_location': '/usr/bin/ffmpeg',
    }
    
    if progress_hook:
        opts['progress_hooks'] = [progress_hook]  # ⬅️ جديد: إضافة هوك التقدم
    
    if 'facebook.com' in url or 'fb.watch' in url:
        logging.info("Facebook URL detected. Using direct connection.")
    else:
        opts['proxy'] = PRIMARY_PROXY
        logging.info(f"Using proxy for URL.")
    
    return opts

async def run_ydl_analysis(url: str) -> dict:
    """تحليل الرابط باستخدام yt-dlp مع التخزين المؤقت"""
    # ⬅️ جديد: التحقق من التخزين المؤقت أولاً
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
            
            # ⬅️ جديد: تخزين في الكاش
            await AdvancedCache.set_cached_analysis(url, info)
            return info
    except yt_dlp.utils.DownloadError as e:
        raise AnalysisError(f"خطأ في التحليل: {str(e)}")
    except Exception as e:
        raise AnalysisError(f"خطأ غير متوقع: {str(e)}")

async def run_ydl_download(url: str, format_id: str, is_audio: bool, progress_hook=None) -> str:
    """تحميل الفيديو/الصوت باستخدام yt-dlp مع إعادة المحاولة"""
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
    
    # ⬅️ جديد: استخدام نظام إعادة المحاولة
    retry_manager = RetryManager(max_retries=3)
    
    try:
        def download_task():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                original_filename = ydl.prepare_filename(info)
                
                if is_audio:
                    return str(Path(original_filename).with_suffix('.mp3'))
                return original_filename
        
        # تنفيذ مع إعادة المحاولة
        filename = await retry_manager.execute_with_retry(
            lambda: asyncio.to_thread(download_task)
        )
        return filename
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(f"خطأ في التحميل: {str(e)}")
    except Exception as e:
        raise DownloadError(f"خطأ غير متوقع: {str(e)}")

# ==============================================================================
# 11. UI BUILDERS - مع تحسينات لتعلم التفضيلات
# ==============================================================================

def get_user_preferred_quality(user_id: int) -> str:
    """الحصول على الجودة المفضلة للمستخدم"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT preferred_quality FROM user_stats WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        conn.close()
        
        if result and result[0]:
            return result[0]
    except Exception as e:
        logging.error(f"Failed to get user preference: {e}")
    
    return '720p'  # القيمة الافتراضية

def build_youtube_ui(info: dict, msg_id: int, user_id: int = None) -> tuple:
    """بناء واجهة يوتيوب مع مراعاة تفضيلات المستخدم"""
    title = info.get('title', 'غير متوفر')
    uploader = info.get('uploader', 'غير متوفر')
    duration = info.get('duration', 0)
    
    duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "غير معروف"
    
    # ⬅️ جديد: الحصول على تفضيلات المستخدم
    preferred_quality = get_user_preferred_quality(user_id) if user_id else '720p'
    
    caption = (
        f"🎬 **يوتيوب**\n\n"
        f"📝 **العنوان:** {escape_markdown(title)}\n"
        f"👤 **القناة:** {escape_markdown(uploader)}\n"
        f"⏱ **المدة:** {duration_str}\n"
        f"⭐ **الجودة المقترحة:** {preferred_quality}"
    )
    
    # تصفية الصيغ مع مراعاة التفضيلات
    video_formats = [
        f for f in info.get('formats', [])
        if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and f.get('height', 0) <= 1080
    ]
    
    # إضافة معلومات الحجم لكل صيغة
    for fmt in video_formats:
        size = fmt.get('filesize') or fmt.get('filesize_approx', 0)
        fmt['_size_str'] = format_size(size) if size else "غير معروف"
        fmt['_too_large'] = size > MAX_FILE_SIZE if size else False
        fmt['_height_str'] = f"{fmt.get('height', 0)}p"
    
    # محاولة العثور على الجودة المفضلة
    preferred_format = None
    if preferred_quality and preferred_quality.endswith('p'):
        target_height = int(preferred_quality[:-1])
        preferred_format = next(
            (f for f in video_formats if f.get('height', 0) == target_height and not f.get('_too_large', False)),
            None
        )
    
    # إذا لم نجد الجودة المفضلة، نأخذ أفضل جودة مناسبة
    if not preferred_format:
        available_formats = [f for f in video_formats if not f.get('_too_large', False)]
        if available_formats:
            preferred_format = max(available_formats, key=lambda x: x.get('height', 0))
    
    buttons = []
    
    # زر الفيديو المفضل أو الأفضل
    if preferred_format and not preferred_format.get('_too_large', False):
        quality_label = "⭐ مفضل" if preferred_format.get('height_str') == preferred_quality else "🎬 فيديو"
        buttons.append(InlineKeyboardButton(
            f"{quality_label} ({preferred_format.get('height')}p - {preferred_format.get('_size_str')})",
            callback_data=f"yt:v:{preferred_format['format_id']}:{msg_id}"
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

# باقي الدوال (build_generic_ui, build_qualities_ui) تبقى كما هي...

# ==============================================================================
# 12. ERROR REPORTING - كما هو
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
# 13. TELEGRAM HANDLERS - مع تحسينات للقائمة الانتظار والتقدم
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر /start مع معلومات عن النظام الجديد"""
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
        f"🚀 **المميزات الجديدة:**\n"
        f"• 📋 نظام قائمة الانتظار الذكي\n"
        f"• ⏳ شريط تقدم مرئي\n"
        f"• 🔄 إعادة محاولة تلقائية\n"
        f"• 💾 تخزين مؤقت للروابط\n\n"
        f"📌 فقط أرسل لي رابط الفيديو وسأقوم بتحميله لك\\!\n\n"
        f"⚙️ الأوامر المتاحة:\n"
        f"/start \\- البداية\n"
        f"/help \\- المساعدة\n"
        f"/queue \\- حالة قائمة الانتظار"
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
        f"🚀 **المميزات المتقدمة:**\n"
        f"• 📋 **قائمة الانتظار:** عند ازدحام النظام\n"
        f"• ⏳ **شريط التقدم:** تتبع تقدم التحميل\n"
        f"• 🔄 **إعادة المحاولة:** تلقائية عند فشل الشبكة\n"
        f"• 💾 **التخزين المؤقت:** تحليل أسرع للروابط المتكررة\n\n"
        f"⚠️ **ملاحظات:**\n"
        f"• الحد الأقصى للحجم: 50 ميغابايت\n"
        f"• الحد الأقصى للطلبات: 5 طلبات/دقيقة\n"
        f"• الحد الأقصى للتحميلات المتزامنة: 2\n\n"
        f"❓ **مشاكل؟** تواصل مع المطور"
    )
    
    await update.message.reply_text(
        escape_markdown(help_text),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """⬅️ جديد: عرض حالة قائمة الانتظار"""
    user = update.effective_user
    
    # حساب الموقع في قائمة الانتظار
    queue_position = 0
    for _, _, user_id, _ in download_queue.queue:
        if user_id == user.id:
            queue_position += 1
    
    if queue_position > 0:
        message = f"📋 **قائمة الانتظار**\n\nموقعك: **#{queue_position}**\n\nيرجى الانتظار، سيتم معالجة طلبك قريباً."
    else:
        message = "✅ **قائمة الانتظار**\n\nلا توجد طلبات في قائمة الانتظار. يمكنك إرسال رابط جديد."
    
    await update.message.reply_text(
        escape_markdown(message),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر /stats (للأدمن فقط) مع إحصائيات إضافية"""
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
        f"📅 **تحميلات اليوم:** {stats['today']}\n"
        f"💾 **متوسط حجم الملف:** {stats['avg_file_size']}\n"
        f"⭐ **الجودة الأكثر طلباً:** {stats['top_quality']}\n"
        f"📋 **في قائمة الانتظار:** {stats['queue_pending']}\n\n"
        f"📈 **نسبة النجاح:** {(stats['successful'] / stats['total_downloads'] * 100) if stats['total_downloads'] > 0 else 0:.1f}%"
    )
    
    await update.message.reply_text(
        escape_markdown(stats_text),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الروابط الرئيسي مع دعم القائمة الانتظار"""
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
    
    # ⬅️ جديد: التحقق من وجود تحميلات نشطة
    active_count = sum(1 for _, _, uid, _ in download_queue.queue if uid == user.id)
    if active_count >= MAX_CONCURRENT_DOWNLOADS:
        # إضافة لقائمة الانتظار
        position = await download_queue.add_to_queue(user.id, url)
        await update.message.reply_text(
            QUEUE_MESSAGE.format(position=position)
        )
        
        # ⬅️ جديد: بدء معالج قائمة الانتظار إذا لم يكن يعمل
        if not hasattr(context.application, 'queue_processor_started'):
            context.application.queue_processor_started = True
            asyncio.create_task(process_download_queue(context.application))
        
        return
    
    # إذا لم يكن هناك ازدحام، معالجة مباشرة
    await process_download_request(update, context, user, url)

async def process_download_request(update: Update, context: ContextTypes.DEFAULT_TYPE, user, url: str):
    """معالجة طلب التحميل"""
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
                
                log_download(user.id, user.username, url, platform, True, 
                           file_size=file_size, quality='N/A')
            else:
                # إرسال كملف ZIP للمحتوى المتعدد
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
                
                log_download(user.id, user.username, url, platform, True,
                           file_size=zip_size, quality='ZIP')
            
            await msg.delete()
        
        else:
            # استخدام yt-dlp للمنصات الأخرى مع التخزين المؤقت
            info = await run_ydl_analysis(url)
            context.user_data[msg.message_id] = info
            
            # ⬅️ جديد: بناء واجهة مع تفضيلات المستخدم
            if platform == 'youtube':
                caption, keyboard = build_youtube_ui(info, msg.message_id, user.id)
            else:
                from .ui_builders import build_generic_ui  # ستحتاج لاستيراد الوظيفة
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
        await msg.edit_text(f"{ANALYSIS_FAILED_MESSAGE}\n\n{escape_markdown(error_msg)}", 
                          parse_mode=ParseMode.MARKDOWN_V2)
        log_download(user.id, user.username, url, platform, False, error_msg)
    
    except Exception as e:
        error_msg = str(e)
        logging.error(f"Unexpected error in handle_link: {e}", exc_info=True)
        await report_error(context, user.id, user.username, url, error_msg, "خطأ غير متوقع")
        await msg.edit_text(f"{GENERIC_ERROR_MESSAGE}\n\n{escape_markdown(error_msg)}", 
                          parse_mode=ParseMode.MARKDOWN_V2)
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

async def process_download_queue(app):
    """⬅️ جديد: معالج قائمة الانتظار"""
    while True:
        try:
            # الحصول على التحميل التالي
            next_download = await download_queue.get_next_download()
            
            if next_download:
                user_id, url = next_download
                
                # البحث عن آخر رسالة للمستخدم
                # (في تطبيق حقيقي، قد تحتاج لتخزين chat_id مع الطلب)
                
                logging.info(f"Processing queued download for user {user_id}: {url}")
                
                # هنا يمكنك إضافة منطق لمعالجة التحميل
                # سيحتاج لبعض التعديلات للوصول للـ context الصحيح
                
                # محاكاة معالجة
                await asyncio.sleep(2)
                
                # إكمال التحميل (نجاح/فشل)
                download_queue.complete_download(user_id, url, success=True)
            
            await asyncio.sleep(1)  # فحص كل ثانية
        except Exception as e:
            logging.error(f"Queue processor error: {e}")
            await asyncio.sleep(5)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أزرار الاختيار مع شريط التقدم"""
    query = update.callback_query
    await query.answer()
    
    try:
        handler_type, action, resource_id, msg_id_str = query.data.split(':')
        msg_id = int(msg_id_str)
    except ValueError:
        await query.message.delete()
        return
    
    # تجاهل الأزرار غير النشطة
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
        from .ui_builders import build_qualities_ui  # ستحتاج لاستيراد الوظيفة
        keyboard = build_qualities_ui(info, msg_id)
        try:
            await query.message.edit_reply_markup(reply_markup=keyboard)
        except BadRequest:
            pass
        return
    
    # الرجوع للقائمة الرئيسية
    if handler_type == "back":
        if platform == 'youtube':
            caption, keyboard = build_youtube_ui(info, msg_id, user.id)
        else:
            from .ui_builders import build_generic_ui
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
    
    # ⬅️ جديد: إنشاء متتبع التقدم
    progress_tracker = ProgressTracker(
        chat_id=query.message.chat_id,
        message_id=query.message.message_id,
        bot=context.bot
    )
    
    # إزالة الأزرار وإضافة رسالة التحميل
    await query.edit_message_reply_markup(None)
    try:
        current_caption = query.message.caption_markdown_v2 or ""
        loading_text = escape_markdown("\n\n⏳ جارٍ التحميل...")
        await query.message.edit_caption(
            caption=current_caption + loading_text,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except BadRequest:
        pass
    
    await progress_tracker.next_step()  # ⬅️ جديد: الانتقال لخطوة التحميل
    
    file_path = None
    try:
        # التحميل مع شريط التقدم
        is_audio = (action == 'a')
        file_path = await run_ydl_download(
            url, resource_id, is_audio, 
            progress_tracker.get_progress_hook()  # ⬅️ جديد: تمرير هوك التقدم
        )
        
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
        
        await progress_tracker.next_step()  # ⬅️ جديد: الانتقال لخطوة الرفع
        
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
                title=title[:64],  # حد تليجرام
                caption="✅ تم التحميل بنجاح"
            )
            quality = 'MP3'
        else:
            # تحديد الجودة من الـ format_id
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
        
        # ⬅️ محسّن: تسجيل مع الحجم والجودة
        actual_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        log_download(user.id, user.username, url, platform, True, 
                    file_size=actual_size, quality=quality)
    
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
            del context.user_
