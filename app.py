# app.py - بوت تحميل الفيديوهات والصوتيات المطور
import logging
import os
import shutil
import asyncio
import random
import re
import sqlite3
import hashlib
import json
import time
import html
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
import zipfile

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import BadRequest
from telegram.constants import ParseMode
import yt_dlp

# ==============================================================================
# 1. الإعدادات
# ==============================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))
ADMIN_ID = os.getenv("ADMIN_ID", "5898628858")
# تم إلغاء البروكسي المتعطل - يدعم الآن بروكسي اختياري عبر المتغير البيئي PROXY_URL
PROXY_URL = os.getenv("PROXY_URL", "").strip() or None

DOWNLOAD_PATH = Path("downloads")
DB_PATH = Path("bot_stats.db")
COOKIES_PATH = Path("cookies.txt")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
]

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB (حد تيليجرام للبوتات العادية)
MAX_REQUESTS_PER_MINUTE = 8
CLEANUP_INTERVAL = 900  # تنظيف كل 15 دقيقة
FILE_MAX_AGE = 1800     # حذف أي ملفات أقدم من 30 دقيقة
CACHE_TTL = 1800        # كاش الرابط 30 دقيقة
DOWNLOAD_TIMEOUT = 300  # 5 دقائق مهلة أقصى للتحميل
MAX_CONCURRENT_DOWNLOADS = 3

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
for logger_name in ["httpx", "werkzeug", "telegram.ext.Application"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

# ==============================================================================
# 2. نظام التزامن
# ==============================================================================
active_downloads = defaultdict(int)
download_locks = defaultdict(asyncio.Lock)

# ==============================================================================
# 3. قاعدة البيانات
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
    
    conn.commit()
    conn.close()
    logging.info("✅ قاعدة البيانات جاهزة")

def log_download(user_id: int, username: str, url: str, platform: str, success: bool, 
                 error_msg: str = None, file_size: int = None, quality: str = None):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        
        c.execute('''INSERT INTO downloads (user_id, username, url, platform, timestamp, 
                     success, error_message, file_size, quality)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, username, url[:500], platform, datetime.now(), success, 
                   error_msg, file_size, quality))
        
        c.execute('''INSERT INTO user_stats (user_id, username, total_downloads, 
                     successful_downloads, failed_downloads, first_use, last_use)
                     VALUES (?, ?, 1, ?, ?, ?, ?)
                     ON CONFLICT(user_id) DO UPDATE SET
                     total_downloads = total_downloads + 1,
                     successful_downloads = successful_downloads + ?,
                     failed_downloads = failed_downloads + ?,
                     username = ?,
                     last_use = ?''',
                  (user_id, username, 1 if success else 0, 1 if not success else 0, 
                   datetime.now(), datetime.now(),
                   1 if success else 0, 1 if not success else 0, username, datetime.now()))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"❌ خطأ في تسجيل التحميل: {e}")

# ==============================================================================
# 4. التخزين المؤقت (Cache)
# ==============================================================================
async def get_cached_info(url: str):
    url_hash = hashlib.md5(url.encode()).hexdigest()
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        c = conn.cursor()
        c.execute('SELECT data, timestamp FROM cache WHERE url_hash = ?', (url_hash,))
        result = c.fetchone()
        conn.close()
        
        if result:
            data_str, cache_time = result
            age = (datetime.now() - datetime.fromisoformat(cache_time)).total_seconds()
            if age < CACHE_TTL:
                return json.loads(data_str)
    except:
        pass
    return None

async def set_cached_info(url: str, data: dict):
    url_hash = hashlib.md5(url.encode()).hexdigest()
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO cache (url_hash, data, timestamp) VALUES (?, ?, ?)',
                  (url_hash, json.dumps(data), datetime.now()))
        conn.commit()
        conn.close()
    except:
        pass

# ==============================================================================
# 5. التحكم بمعدل الطلبات (Rate Limiting)
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

# ==============================================================================
# 6. التنظيف التلقائي للملفات
# ==============================================================================
def cleanup_old_files():
    """حذف أي ملفات قديمة مؤقتة لتجنب امتلاء قرص التخزين المجاني"""
    try:
        if not DOWNLOAD_PATH.exists():
            return
        
        now = time.time()
        deleted = 0
        for file_path in DOWNLOAD_PATH.glob("*"):
            if file_path.is_file():
                if now - file_path.stat().st_mtime > FILE_MAX_AGE:
                    try:
                        file_path.unlink()
                        deleted += 1
                    except Exception:
                        pass
        
        if deleted > 0:
            logging.info(f"🧹 تم تنظيف {deleted} ملف قديم من مجلد التحميل")
    except Exception as e:
        logging.error(f"❌ خطأ في التنظيف الدوري: {e}")

async def periodic_cleanup():
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL)
        cleanup_old_files()

# ==============================================================================
# 7. الدوال المساعدة
# ==============================================================================
def is_valid_url(url: str) -> bool:
    if not url or len(url) > 2000:
        return False
    return bool(re.match(r'^https?://\S+', url.strip()))

def detect_platform(url: str) -> str:
    lower_url = url.lower()
    if 'youtube.com' in lower_url or 'youtu.be' in lower_url:
        return 'youtube'
    elif 'instagram.com' in lower_url:
        return 'instagram'
    elif 'facebook.com' in lower_url or 'fb.watch' in lower_url or 'fb.com' in lower_url:
        return 'facebook'
    elif 'twitter.com' in lower_url or 'x.com' in lower_url:
        return 'twitter'
    elif 'tiktok.com' in lower_url:
        return 'tiktok'
    elif 'soundcloud.com' in lower_url:
        return 'soundcloud'
    elif 'dailymotion.com' in lower_url:
        return 'dailymotion'
    elif 'pinterest.' in lower_url or 'pin.it' in lower_url:
        return 'pinterest'
    else:
        return 'other'

def format_size(size_bytes: int) -> str:
    if not size_bytes or size_bytes <= 0:
        return "غير معروف"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

def format_duration(seconds: int) -> str:
    if not seconds or seconds <= 0:
        return "غير محدد"
    if seconds < 60:
        return f"{seconds} ثانية"
    elif seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes} دقيقة و {secs} ثانية" if secs else f"{minutes} دقيقة"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours} ساعة و {minutes} دقيقة"

def escape_markdown(text: str) -> str:
    if not text:
        return ""
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', str(text))

# ==============================================================================
# 8. محرك التحميل الحديث والشامل
# ==============================================================================
class DownloadError(Exception):
    pass

def get_base_ydl_opts(url: str) -> dict:
    ffmpeg_path = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
    node_path = shutil.which('node') or shutil.which('deno')

    opts = {
        'quiet': True,
        'no_warnings': True,
        'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
        'outtmpl': str(DOWNLOAD_PATH / '%(id)s.%(ext)s'),
        'ffmpeg_location': ffmpeg_path,
        'socket_timeout': 30,
        'retries': 5,
        'fragment_retries': 5,
        'concurrent_fragment_downloads': 3,
        'nooverwrites': False,
        'continuedl': True,
        'noprogress': True,
        'geo_bypass': True,
        'remote_components': ['ejs:github'],
    }
    
    # دعم تشغيل أكواد الجافاسكريبت لفك تشفير يوتيوب وحل التحديات
    if node_path:
        opts['js_runtimes'] = {'node': {'path': node_path}}

    # دعم cookies.txt تلقائياً إذا كان موجوداً
    if COOKIES_PATH.exists() and COOKIES_PATH.stat().st_size > 0:
        opts['cookiefile'] = str(COOKIES_PATH)

    # دعم بروكسي اختياري إذا وفره المستخدم كمتغير بيئي
    if PROXY_URL:
        opts['proxy'] = PROXY_URL

    # تحسينات خاصة بيوتيوب لتخطي مشاكل الحظر
    if 'youtube.com' in url.lower() or 'youtu.be' in url.lower():
        opts['extractor_args'] = {
            'youtube': {
                'player_client': ['mweb', 'web', 'android', 'ios'],
                'skip': ['dash', 'hls'] if False else []
            }
        }
    
    return opts

async def run_gallery_dl(url: str) -> list:
    """تحميل المنشورات المتعددة أو الصور عبر gallery-dl"""
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    
    command = [
        'gallery-dl',
        '--directory', str(DOWNLOAD_PATH),
        '--retries', '3',
    ]
    
    if COOKIES_PATH.exists() and COOKIES_PATH.stat().st_size > 0:
        command.extend(['--cookies', str(COOKIES_PATH)])

    if PROXY_URL:
        command.extend(['--proxy', PROXY_URL])

    command.append(url)
    
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=120
        )
    except asyncio.TimeoutError:
        try:
            process.kill()
            await process.wait()
        except:
            pass
        raise DownloadError("انتهت مهلة تحميل Instagram")
    
    if process.returncode != 0:
        error = stderr.decode('utf-8', errors='ignore')
        # لا نوقف فوراً بل نترك محاولة yt-dlp كخيار بديل
        raise DownloadError(f"gallery-dl error: {error[:200]}")
    
    files = []
    for root, _, filenames in os.walk(DOWNLOAD_PATH):
        for name in filenames:
            file_path = os.path.join(root, name)
            # تجاهل الملفات القديمة
            if time.time() - os.path.getmtime(file_path) < 300:
                files.append(file_path)
    
    if not files:
        raise DownloadError("لم يتم العثور على ملفات")
    
    return files

async def run_ydl_analysis(url: str) -> dict:
    """تحليل الرابط وجلب بياناته بدون تحميل"""
    cached = await get_cached_info(url)
    if cached:
        logging.info("📦 استخدام البيانات المؤقتة (Cache)")
        return cached
    
    opts = get_base_ydl_opts(url)
    opts['skip_download'] = True
    opts['extract_flat'] = False
    
    try:
        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await loop.run_in_executor(
                None,
                lambda: ydl.extract_info(url, download=False)
            )
        
        if not info:
            raise DownloadError("تعذر قراءة بيانات هذا الرابط.")
        
        # إذا كان الرابط قائمة تشغيل أو مدخلات متعددة
        if 'entries' in info and info['entries']:
            info = info['entries'][0]

        await set_cached_info(url, info)
        return info
    except Exception as e:
        err = str(e)
        if "Private video" in err or "private" in err.lower():
            raise DownloadError("هذا الفيديو خاص أو غير متاح للعامة.")
        elif "Sign in" in err:
            raise DownloadError("المحتوى يتطلب تسجيل دخول أو تأكيد أنك لست روبوت.")
        raise DownloadError(f"خطأ في التحليل: {err[:200]}")

async def run_ydl_download(url: str, format_id: str, is_audio: bool) -> str:
    """تحميل الملف باستخدام yt-dlp بأفضل جودة ملائمة لحدود تيليجرام (50MB)"""
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    opts = get_base_ydl_opts(url)
    
    if is_audio:
        # تحميل الصوت بجودة ملائمة ومناسبة لحجم تيليجرام
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    else:
        if format_id and format_id != 'best':
            opts['format'] = f"{format_id}+bestaudio/best"
        else:
            # الذكاء التلقائي: اختيار أفضل دقة لا تتجاوز حدود 50MB لتيليجرام
            opts['format'] = 'bestvideo[filesize<46M]+bestaudio/best[filesize<48M]/best'

        opts['merge_output_format'] = 'mp4'
    
    try:
        loop = asyncio.get_event_loop()
        
        def download():
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if 'entries' in info and info['entries']:
                    info = info['entries'][0]

                filename = ydl.prepare_filename(info)
                if is_audio:
                    mp3_file = str(Path(filename).with_suffix('.mp3'))
                    if os.path.exists(mp3_file):
                        return mp3_file

                # في حال تم دمج الفيديو بصيغة mp4
                mp4_file = str(Path(filename).with_suffix('.mp4'))
                if os.path.exists(mp4_file):
                    return mp4_file

                return filename
        
        result = await asyncio.wait_for(
            loop.run_in_executor(None, download),
            timeout=DOWNLOAD_TIMEOUT
        )
        
        return result
        
    except asyncio.TimeoutError:
        raise DownloadError(f"انتهت مهلة التحميل ({DOWNLOAD_TIMEOUT} ثانية). يرجى تجربة جودة أقل أو مقطع أقصر.")
    except Exception as e:
        raise DownloadError(f"خطأ في التحميل: {str(e)[:200]}")

# ==============================================================================
# 9. واجهات المستخدم التفاعلية السريعة
# ==============================================================================
def build_simple_ui(info: dict, msg_id: int) -> tuple:
    platform = detect_platform(info.get('webpage_url', ''))
    title = info.get('title', 'فيديو')[:90]
    duration = info.get('duration', 0)
    
    platform_map = {
        'youtube': ("🎬", "يوتيوب"),
        'instagram': ("📸", "إنستغرام"),
        'facebook': ("👥", "فيسبوك"),
        'twitter': ("🐦", "تويتر / X"),
        'tiktok': ("🎵", "تيك توك"),
        'soundcloud': ("🎧", "ساوند كلاود"),
        'dailymotion': ("📹", "ديلي موشن"),
        'pinterest': ("📌", "بينتريست"),
    }

    icon, name = platform_map.get(platform, ("🌐", info.get('extractor_key', 'منصة').upper()))
    uploader = info.get('uploader') or info.get('channel') or info.get('creator') or ''
    uploader_text = f"\n👤 *الناشر:* {escape_markdown(uploader)}" if uploader else ""
    
    caption = (
        f"{icon} *{escape_markdown(name)}*\n\n"
        f"📝 *العنوان:* {escape_markdown(title)}\n"
        f"⏱ *المدة:* {format_duration(duration)}{uploader_text}\n\n"
        f"👇 *اختر نوع التحميل السريع:*"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("📥 تحميل فيديو سريع", callback_data=f"dl:v:best:{msg_id}"),
            InlineKeyboardButton("🎵 تحميل صوت (MP3)", callback_data=f"dl:a:best:{msg_id}")
        ],
        [
            InlineKeyboardButton("⚙️ اختيار جودة محددة", callback_data=f"more:v:na:{msg_id}"),
            InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")
        ]
    ]
    
    return caption, InlineKeyboardMarkup(keyboard)

def build_qualities_ui(info: dict, msg_id: int) -> InlineKeyboardMarkup:
    formats = [
        f for f in info.get('formats', [])
        if f.get('vcodec') != 'none'
    ]
    
    formats.sort(key=lambda x: (x.get('height') or 0), reverse=True)
    
    buttons = []
    seen_heights = set()
    
    for fmt in formats:
        height = fmt.get('height')
        if height and height not in seen_heights:
            seen_heights.add(height)
            filesize = fmt.get('filesize') or fmt.get('filesize_approx') or 0
            size_text = f" ({format_size(filesize)})" if filesize else ""
            
            if filesize and filesize > MAX_FILE_SIZE:
                button_text = f"⚠️ {height}p{size_text} - كبير"
                buttons.append([InlineKeyboardButton(button_text, callback_data=f"ignore:na:na:{msg_id}")])
            else:
                button_text = f"📹 {height}p{size_text}"
                buttons.append([InlineKeyboardButton(button_text, callback_data=f"dl:v:{fmt['format_id']}:{msg_id}")])

            if len(buttons) >= 6:  # أقصى 6 دقات مختلفة لسهولة القراءة
                break
    
    buttons.append([
        InlineKeyboardButton("🔙 رجوع", callback_data=f"back:na:na:{msg_id}"),
        InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")
    ])
    
    return InlineKeyboardMarkup(buttons)

# ==============================================================================
# 10. معالجات الأوامر
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logging.info(f"🚀 مستخدم {user.id} ({user.first_name}) بدأ البوت")
    
    welcome_text = (
        f"👋 <b>مرحباً بك {html.escape(user.first_name or '')}!</b>\n\n"
        f"⚡ <b>أنا بوت تحميل الفيديو والصوت السريع والمجاني</b>\n\n"
        f"🌟 <b>المنصات المدعومة:</b>\n"
        f"• YouTube (يوتيوب) 🎬\n"
        f"• Instagram (إنستغرام) 📸\n"
        f"• TikTok (تيك توك) 🎵\n"
        f"• Facebook (فيسبوك) 👥\n"
        f"• Twitter / X 🐦\n"
        f"• SoundCloud، Pinterest، Dailymotion ومعظم المواقع الأخرى!\n\n"
        f"🚀 <b>طريقة التحميل بكل سهولة:</b>\n"
        f"1️⃣ أرسل رابط الفيديو مباشرة.\n"
        f"2️⃣ اختر <b>تحميل فيديو</b> أو <b>تحميل صوت</b>.\n"
        f"3️⃣ سيصلك الملف مباشرة في ثوانٍ.\n\n"
        f"💡 <b>ملاحظة:</b> يتم اختيار أفضل جودة تناسب حجم تيليجرام تلقائياً."
    )
    
    if str(user.id) == ADMIN_ID:
        welcome_text += "\n\n⚙️ <b>أوامر الإدارة:</b>\n/stats - إحصائيات البوت"
    
    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        f"📖 <b>دليل الاستخدام السريع</b>\n\n"
        f"⚡ <b>كيف تحمل أي مقطع؟</b>\n"
        f"فقط انسخ الرابط وأرسله للبوت هنا، وسيقوم البوت بتحليله وتوفير خيارات التحميل المناسبة لك.\n\n"
        f"📌 <b>نصائح مهمة:</b>\n"
        f"• الحد الأقصى للملف في تيليجرام للبوتات هو 50 ميغابايت.\n"
        f"• إذا كان الفيديو طويلاً جداً (مثل محاضرة أو حلقة كاملة)، استخدم زر <b>تحميل صوت (MP3)</b> لتنزيله دون تجاوز الحد.\n"
        f"• تأكد من أن حساب الناشر أو الفيديو عام وليس حساباً خاصاً يتطلب متابعة."
    )
    await update.message.reply_text(
        help_text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) != ADMIN_ID:
        await update.message.reply_text("⛔️ هذا الأمر متاح للمشرف فقط.")
        return
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('SELECT COUNT(*) FROM downloads')
        total = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM downloads WHERE success=1')
        success = c.fetchone()[0]
        
        c.execute('SELECT COUNT(DISTINCT user_id) FROM downloads')
        users = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM downloads WHERE DATE(timestamp) = DATE("now")')
        today = c.fetchone()[0]
        
        conn.close()
        
        success_rate = (success / total * 100) if total > 0 else 0
        
        stats_text = (
            f"📊 <b>إحصائيات البوت</b>\n\n"
            f"👥 <b>إجمالي المستخدمين:</b> {users}\n"
            f"📥 <b>إجمالي الطلبات:</b> {total}\n"
            f"✅ <b>التحميلات الناجحة:</b> {success}\n"
            f"📈 <b>نسبة النجاح:</b> {success_rate:.1f}%\n"
            f"📅 <b>طلبات اليوم:</b> {today}\n"
            f"🔄 <b>التحميلات الحالية:</b> {sum(active_downloads.values())}"
        )
        
        await update.message.reply_text(
            stats_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في جلب الإحصائيات: {e}")

# ==============================================================================
# 11. معالج الروابط
# ==============================================================================
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
    
    # استخراج الرابط إذا كان النص يحتوي على كلمات أخرى
    urls = re.findall(r'https?://[^\s]+', text)
    if not urls or not is_valid_url(urls[0]):
        await update.message.reply_text("⚠️ يرجى إرسال رابط صالح يبدأ بـ http أو https.")
        return
    
    url = urls[0]
    
    if not await check_rate_limit(user.id):
        await update.message.reply_text("⏱ يرجى الانتظار قليلاً قبل إرسال رابط جديد (الحد: 8 طلبات في الدقيقة).")
        return
    
    async with download_locks[user.id]:
        if active_downloads[user.id] >= 1:
            await update.message.reply_text("⏳ لديك تحميل قيد المعالجة بالفعل، يرجى الانتظار لحظات حتى يكتمل.")
            return
        active_downloads[user.id] += 1
    
    msg = None
    try:
        msg = await update.message.reply_text("⏳ جاري فحص الرابط وجلب التفاصيل...")
        platform = detect_platform(url)
        logging.info(f"🔗 معالجة رابط من منصة: {platform} للمستخدم {user.id}")
        
        # إنستغرام: تجربة التحميل المتعدد للصور والمنشورات عبر gallery-dl أولاً، أو الانتقال لـ yt-dlp
        if platform == 'instagram':
            try:
                files = await run_gallery_dl(url)
                await msg.edit_text("⚡️ جاري إرسال المحتوى...")
                
                for f_path in files:
                    p = Path(f_path)
                    if not p.exists():
                        continue
                    file_size = p.stat().st_size
                    with open(p, 'rb') as f_obj:
                        if p.suffix.lower() in ['.jpg', '.jpeg', '.png', '.webp']:
                            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=f_obj, caption="✅ تم التحميل بنجاح")
                        else:
                            await context.bot.send_video(chat_id=update.effective_chat.id, video=f_obj, caption="✅ تم التحميل بنجاح", supports_streaming=True)
                    try:
                        p.unlink()
                    except:
                        pass
                
                await msg.delete()
                log_download(user.id, user.username, url, platform, True, quality='Instagram')
                return
            except Exception as e:
                logging.info(f"gallery-dl fallback to yt-dlp for Instagram: {e}")
                # المتابعة إلى yt-dlp في حال فشل gallery-dl
        
        # التحليل بواسطة yt-dlp
        info = await run_ydl_analysis(url)
        context.user_data[msg.message_id] = info

        caption, keyboard = build_simple_ui(info, msg.message_id)
        thumbnail = info.get('thumbnail')

        if thumbnail:
            try:
                await msg.delete()
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=thumbnail,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=keyboard
                )
                return
            except Exception as e:
                logging.warning(f"Could not send thumbnail photo: {e}")

        await msg.edit_text(
            text=caption,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=keyboard
        )

    except Exception as e:
        error_msg = str(e)
        logging.error(f"❌ خطأ في معالجة الرابط: {error_msg}")
        reply_err = f"❌ حدث خطأ: {error_msg}"
        try:
            if msg:
                await msg.edit_text(reply_err)
            else:
                await update.message.reply_text(reply_err)
        except:
            pass
        log_download(user.id, user.username, url, 'unknown', False, error_msg)

    finally:
        async with download_locks[user.id]:
            active_downloads[user.id] = max(0, active_downloads[user.id] - 1)

# ==============================================================================
# 12. معالج الأزرار التفاعلية
# ==============================================================================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    logging.info(f"🔘 نقر زر من المستخدم {user.id}: {query.data}")
    
    try:
        action_type, media_type, resource_id, msg_id_str = query.data.split(':')
        msg_id = int(msg_id_str)
    except ValueError:
        await query.message.delete()
        return
    
    if action_type == "ignore":
        await query.answer("⚠️ حجم هذا الخيار أكبر من 50 ميغابايت (حد تيليجرام المجاني). اختر جودة أقل أو حمّل بصيغة صوت MP3.", show_alert=True)
        return
    
    if action_type == "cancel":
        await query.message.delete()
        if msg_id in context.user_data:
            del context.user_data[msg_id]
        return
    
    if action_type == "back":
        if msg_id in context.user_data:
            info = context.user_data[msg_id]
            caption, keyboard = build_simple_ui(info, msg_id)
            try:
                await query.message.edit_caption(caption=caption, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard)
            except BadRequest:
                await query.message.edit_text(text=caption, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard)
        return
    
    if action_type == "more":
        if msg_id in context.user_data:
            info = context.user_data[msg_id]
            keyboard = build_qualities_ui(info, msg_id)
            try:
                await query.message.edit_reply_markup(reply_markup=keyboard)
            except BadRequest:
                pass
        return
    
    if action_type == "dl":
        if msg_id not in context.user_data:
            await query.edit_message_text("⚠️ انتهت صلاحية الجلسة، يرجى إرسال الرابط مرة أخرى.")
            return
        
        info = context.user_data[msg_id]
        url = info.get('webpage_url') or info.get('original_url')
        platform = detect_platform(url)
        
        async with download_locks[user.id]:
            if active_downloads[user.id] >= 1:
                await query.answer("⏳ لديك تحميل قيد التنفيذ، يرجى الانتظار...", show_alert=True)
                return
            active_downloads[user.id] += 1
        
        await query.edit_message_reply_markup(None)
        
        status_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="⏳ جاري تحميل المحتوى من الخادم..."
        )

        file_path = None
        try:
            is_audio = (media_type == 'a')
            file_path = await run_ydl_download(url, resource_id, is_audio)
            
            if not file_path or not os.path.exists(file_path):
                raise DownloadError("تعذر العثور على الملف المحمل.")
            
            actual_size = os.path.getsize(file_path)
            if actual_size > MAX_FILE_SIZE:
                await status_msg.edit_text(
                    f"❌ حجم الملف ({format_size(actual_size)}) يتجاوز حد تيليجرام (50MB).\n"
                    f"💡 جرب اختيار جودة أقل أو تحميله كصوت MP3."
                )
                log_download(user.id, user.username, url, platform, False, "File too large", file_size=actual_size)
                return

            await status_msg.edit_text("⚡️ جاري رفع وإرسال الملف إليك...")
            
            title = info.get('title', 'تم التحميل')
            caption = f"✅ *{escape_markdown(title[:80])}*\n\n🤖 بواسطة بوت التحميل"
            
            with open(file_path, 'rb') as f_obj:
                if is_audio:
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=f_obj,
                        title=title[:60],
                        caption=caption,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    quality = 'MP3'
                else:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=f_obj,
                        caption=caption,
                        supports_streaming=True,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    quality = 'Video'
            
            await status_msg.delete()
            try:
                await query.message.delete()
            except:
                pass

            log_download(user.id, user.username, url, platform, True, file_size=actual_size, quality=quality)
            
        except Exception as e:
            err = str(e)
            logging.error(f"❌ خطأ أثناء التحميل أو الرفع: {err}")
            await status_msg.edit_text(f"❌ تعذر استكمال التحميل: {err[:200]}")
            log_download(user.id, user.username, url, platform, False, err)
            
        finally:
            async with download_locks[user.id]:
                active_downloads[user.id] = max(0, active_downloads[user.id] - 1)
            
            # مسح الملف فوراً لتوفير مساحة التخزين المجانية
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logging.info(f"🗑 تم حذف الملف المؤقت فورياً: {file_path}")
                except Exception as e:
                    logging.warning(f"Failed to delete temp file {file_path}: {e}")
            
            if msg_id in context.user_data:
                del context.user_data[msg_id]

# ==============================================================================
# 13. معالج الأخطاء العامة
# ==============================================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error("💥 خطأ غير معالج في التطبيق:", exc_info=context.error)

# ==============================================================================
# 14. خادم Flask للـ Health Check (الاستضافات المجانية)
# ==============================================================================
flask_app = Flask(__name__)

@flask_app.route('/')
@flask_app.route('/health')
def health_check():
    active = sum(active_downloads.values())
    return jsonify({
        "status": "online",
        "active_downloads": active,
        "timestamp": datetime.now().isoformat()
    }), 200

@flask_app.route('/stats')
def stats_endpoint():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM downloads')
        total = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM downloads WHERE success=1')
        success = c.fetchone()[0]
        conn.close()
        
        return jsonify({
            "total_downloads": total,
            "successful_downloads": success,
            "active_downloads": sum(active_downloads.values())
        }), 200
    except:
        return jsonify({"error": "database error"}), 500

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT, threaded=True)

# ==============================================================================
# 15. بدء التشغيل
# ==============================================================================
async def main():
    init_db()
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    cleanup_old_files()
    
    logging.info("🚀 بدء تشغيل البوت...")
    logging.info(f"⚙️ المنفذ: {PORT} | Proxy: {'مفعل' if PROXY_URL else 'اتصال مباشر سريع (بدون بروكسي)'}")
    
    import threading
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logging.info(f"🌐 خادم الفحص (Health Check) يعمل على المنفذ {PORT}")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)
    
    asyncio.create_task(periodic_cleanup())
    
    logging.info("✅ البوت جاهز تماماً للاستخدام واستقبال الروابط!")
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )
    
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        logging.info("⏹ إيقاف البوت...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == '__main__':
    if not BOT_TOKEN:
        logging.fatal("❌ خطأ: لم يتم ضبط BOT_TOKEN في متغيرات البيئة!")
        exit(1)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("⏹ تم إيقاف البوت بواسطة المستخدم.")
    except Exception as e:
        logging.fatal(f"💥 خطأ فادح: {e}", exc_info=True)
        exit(1)
