# app.py - الإصدار 18.0: كل الميزات + واجهة مستخدم محسنة
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
# 1. الإعدادات الكاملة
# ==============================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))
ADMIN_ID = "5898628858"
PRIMARY_PROXY = "154.3.236.202:3128"  # البروكسي كما هو

DOWNLOAD_PATH = Path("downloads")
DB_PATH = Path("bot_stats.db")
CACHE_PATH = Path("cache")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_REQUESTS_PER_MINUTE = 5
CLEANUP_INTERVAL = 1800
FILE_MAX_AGE = 3600
CACHE_TTL = 600
MAX_CONCURRENT_DOWNLOADS = 3  # ⬅️ خفضنا لـ 3 بدلاً من 10

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
for logger_name in ["httpx", "werkzeug", "telegram.ext.Application"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

# ==============================================================================
# 2. نظام التزامن المحسّن
# ==============================================================================
active_downloads = defaultdict(int)
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
user_locks = defaultdict(asyncio.Lock)

# ==============================================================================
# 3. قاعدة البيانات المحسنة (تحافظ على البيانات)
# ==============================================================================
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    c = conn.cursor()
    
    # الجدول الأساسي للتحميلات
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
    
    # جدول إحصائيات المستخدمين
    c.execute('''CREATE TABLE IF NOT EXISTS user_stats
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  total_downloads INTEGER DEFAULT 0,
                  successful_downloads INTEGER DEFAULT 0,
                  failed_downloads INTEGER DEFAULT 0,
                  first_use DATETIME,
                  last_use DATETIME)''')
    
    # جدول التخزين المؤقت
    c.execute('''CREATE TABLE IF NOT EXISTS url_cache
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
                     last_use = ?''',
                  (user_id, username, 1 if success else 0, 1 if not success else 0, 
                   datetime.now(), datetime.now(),
                   1 if success else 0, 1 if not success else 0, datetime.now()))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"❌ خطأ في تسجيل التحميل: {e}")

# ==============================================================================
# 4. نظام التخزين المؤقت
# ==============================================================================
class SimpleCache:
    @staticmethod
    def get_key(url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()
    
    @staticmethod
    async def get(url: str) -> dict:
        cache_key = SimpleCache.get_key(url)
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            c = conn.cursor()
            c.execute('SELECT data, timestamp FROM url_cache WHERE url_hash = ?', (cache_key,))
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
    
    @staticmethod
    async def set(url: str, data: dict):
        cache_key = SimpleCache.get_key(url)
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO url_cache (url_hash, data, timestamp) VALUES (?, ?, ?)',
                      (cache_key, json.dumps(data), datetime.now()))
            conn.commit()
            conn.close()
        except:
            pass

# ==============================================================================
# 5. Rate Limiting
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
# 6. التنظيف الذكي (لا يحذف قاعدة البيانات)
# ==============================================================================
def cleanup_old_files():
    try:
        if not DOWNLOAD_PATH.exists():
            return
        
        now = datetime.now().timestamp()
        deleted = 0
        for file_path in DOWNLOAD_PATH.glob("*"):
            if file_path.is_file():
                file_age = now - file_path.stat().st_mtime
                if file_age > FILE_MAX_AGE:
                    file_path.unlink()
                    deleted += 1
        
        if deleted > 0:
            logging.info(f"🧹 تم تنظيف {deleted} ملف قديم")
    except Exception as e:
        logging.error(f"❌ خطأ في التنظيف: {e}")

async def periodic_cleanup():
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL)
        cleanup_old_files()

# ==============================================================================
# 7. الوظائف المساعدة
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
    else:
        return 'other'

def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} ثانية"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} دقيقة"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours} ساعة {minutes} دقيقة"

def escape_markdown(text: str) -> str:
    if not text:
        return ""
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', str(text))

# ==============================================================================
# 8. نظام التحميل مع البروكسي
# ==============================================================================
class DownloadError(Exception):
    pass

def get_ydl_opts(url: str) -> dict:
    """إعدادات yt-dlp مع البروكسي"""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
        'outtmpl': str(DOWNLOAD_PATH / '%(id)s.%(ext)s'),
        'ffmpeg_location': '/usr/bin/ffmpeg',
    }
    
    # استخدام البروكسي لجميع المواقع إلا Facebook
    if 'facebook.com' not in url and 'fb.watch' not in url:
        opts['proxy'] = PRIMARY_PROXY
    
    return opts

async def run_gallery_dl(url: str) -> list:
    """تحميل من Instagram"""
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
        error = stderr.decode('utf-8', errors='ignore')
        raise DownloadError(f"فشل تحميل Instagram: {error.splitlines()[-1] if error else 'خطأ غير معروف'}")
    
    files = []
    for root, _, filenames in os.walk(DOWNLOAD_PATH):
        for name in filenames:
            files.append(os.path.join(root, name))
    
    if not files:
        raise DownloadError("لم يتم العثور على ملفات")
    
    return files

async def run_ydl_analysis(url: str) -> dict:
    """تحليل الرابط"""
    cached = await SimpleCache.get(url)
    if cached:
        logging.info("📦 استخدام الكاش")
        return cached
    
    opts = get_ydl_opts(url)
    opts['skip_download'] = True
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=False)
        
        if not info:
            raise DownloadError("فشل تحليل الرابط")
        
        await SimpleCache.set(url, info)
        return info
    except Exception as e:
        raise DownloadError(f"خطأ في التحليل: {str(e)}")

async def run_ydl_download(url: str, format_id: str, is_audio: bool) -> str:
    """تحميل الفيديو/الصوت"""
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    opts = get_ydl_opts(url)
    
    if is_audio:
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3'
        }]
    else:
        opts['format'] = format_id
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=True)
            filename = ydl.prepare_filename(info)
        
        return str(Path(filename).with_suffix('.mp3')) if is_audio else filename
    except Exception as e:
        raise DownloadError(f"خطأ في التحميل: {str(e)}")

# ==============================================================================
# 9. واجهات المستخدم المحسنة والواضحة
# ==============================================================================
def create_simple_menu(info: dict, msg_id: int) -> tuple:
    """إنشاء واجهة بسيطة وواضحة"""
    platform = detect_platform(info.get('webpage_url', ''))
    title = info.get('title', 'فيديو')[:100]
    duration = info.get('duration', 0)
    
    # تحديد المنصة ورسالتها
    if platform == 'youtube':
        platform_icon = "🎬"
        platform_name = "يوتيوب"
        uploader = f"\n👤 **القناة:** {escape_markdown(info.get('uploader', 'غير معروف'))}"
    elif platform == 'instagram':
        platform_icon = "📸"
        platform_name = "إنستغرام"
        uploader = ""
    else:
        platform_icon = "🌐"
        platform_name = info.get('extractor_key', 'موقع').upper()
        uploader = ""
    
    # بناء النص
    caption = (
        f"{platform_icon} **{platform_name}**\n\n"
        f"📝 **العنوان:** {escape_markdown(title)}\n"
        f"⏱ **المدة:** {format_duration(duration)}{uploader}\n\n"
        f"👇 **اختر طريقة التحميل:**"
    )
    
    # بناء الأزرار
    keyboard = []
    
    # الصف الأول: الخيارات الرئيسية
    keyboard.append([
        InlineKeyboardButton("📥 تحميل فيديو", callback_data=f"dl:v:best:{msg_id}"),
        InlineKeyboardButton("🎵 تحميل صوت", callback_data=f"dl:a:best:{msg_id}")
    ])
    
    # الصف الثاني: خيارات إضافية (لليوتيوب فقط)
    if platform == 'youtube':
        keyboard.append([
            InlineKeyboardButton("⚙️ جودة أخرى", callback_data=f"more:v:na:{msg_id}"),
            InlineKeyboardButton("❓ مساعدة", callback_data=f"help:na:na:{msg_id}")
        ])
    
    # الصف الثالث: إلغاء
    keyboard.append([
        InlineKeyboardButton("❌ إلغاء العملية", callback_data=f"cancel:na:na:{msg_id}")
    ])
    
    return caption, InlineKeyboardMarkup(keyboard)

def create_qualities_menu(info: dict, msg_id: int) -> InlineKeyboardMarkup:
    """إنشاء قائمة الجودات"""
    formats = [
        f for f in info.get('formats', [])
        if f.get('vcodec') != 'none' and f.get('acodec') != 'none'
    ]
    
    # ترتيب من الأعلى جودة إلى الأقل
    formats.sort(key=lambda x: x.get('height', 0), reverse=True)
    
    buttons = []
    seen_heights = set()
    
    for fmt in formats:
        height = fmt.get('height', 0)
        if height and height not in seen_heights:
            seen_heights.add(height)
            
            # حساب الحجم التقريبي
            filesize = fmt.get('filesize') or fmt.get('filesize_approx', 0)
            size_text = f" ({format_size(filesize)})" if filesize else ""
            
            # تحديد إذا كان الملف كبيراً جداً
            if filesize and filesize > MAX_FILE_SIZE:
                button_text = f"❌ {height}p{size_text}"
                buttons.append([InlineKeyboardButton(
                    button_text,
                    callback_data=f"ignore:na:na:{msg_id}"
                )])
            else:
                button_text = f"📹 {height}p{size_text}"
                buttons.append([InlineKeyboardButton(
                    button_text,
                    callback_data=f"dl:v:{fmt['format_id']}:{msg_id}"
                )])
    
    # أزرار التنقل
    buttons.append([
        InlineKeyboardButton("🔙 رجوع", callback_data=f"back:na:na:{msg_id}"),
        InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:na:na:{msg_id}")
    ])
    
    return InlineKeyboardMarkup(buttons)

# ==============================================================================
# 10. معالجات الأوامر مع واجهة محسنة
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة ترحيبية بسيطة وواضحة"""
    user = update.effective_user
    
    welcome_text = (
        f"👋 **مرحباً {escape_markdown(user.first_name)}!**\n\n"
        f"🤖 **أنا بوت لتحميل الفيديوهات**\n\n"
        f"🎯 **ماذا أستطيع فعل؟**\n"
        f"• 📥 تحميل من **يوتيوب**\n"
        f"• 📸 تحميل من **إنستغرام**\n"
        f"• 👥 تحميل من **فيسبوك**\n"
        f"• 🐦 تحميل من **تويتر/X**\n"
        f"• 🎵 تحميل من **تيك توك**\n\n"
        f"🚀 **كيفية الاستخدام:**\n"
        f"1. أرسل رابط الفيديو\n"
        f"2. اختر طريقة التحميل\n"
        f"3. انتظر حتى يتم الإرسال\n\n"
        f"📌 **ملاحظات مهمة:**\n"
        f"• الحد الأقصى: 50 ميغابايت\n"
        f"• للفيديوهات الطويلة اختر جودة أقل\n\n"
        f"🔧 **الأوامر:**\n"
        f"/start - عرض هذه الرسالة\n"
        f"/help - المساعدة والتفاصيل\n"
    )
    
    if str(user.id) == ADMIN_ID:
        welcome_text += f"\n⚙️ **أوامر الإدارة:**\n/stats - عرض الإحصائيات"
    
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN_V2)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة المساعدة المفصلة"""
    help_text = (
        f"📖 **دليل الاستخدام الكامل**\n\n"
        f"🎬 **المنصات المدعومة:**\n"
        f"• YouTube (يوتيوب)\n"
        f"• Instagram (إنستغرام)\n"
        f"• Facebook (فيسبوك)\n"
        f"• Twitter/X (تويتر)\n"
        f"• TikTok (تيك توك)\n"
        f"• معظم المواقع الأخرى\n\n"
        f"⚡ **طريقة العمل:**\n"
        f"1. أرسل رابط الفيديو\n"
        f"2. اختر 'تحميل فيديو' أو 'تحميل صوت'\n"
        f"3. للفيديوهات الطويلة، اختر جودة أقل\n"
        f"4. انتظر حتى يرسل لك البوت الملف\n\n"
        f"⚠️ **المعلومات المهمة:**\n"
        f"• الحد الأقصى لحجم الملف: 50 ميغابايت\n"
        f"• إذا كان الفيديو طويلاً (>10 دقائق)، اختر جودة 480p أو أقل\n"
        f"• للفيديوهات الطويلة جداً، استخدم 'تحميل صوت' لتقليل الحجم\n\n"
        f"❓ **استفسارات شائعة:**\n"
        f"• لماذا لا يعمل الرابط؟ - قد يكون الفيديو محمياً أو غير متاح\n"
        f"• لماذا الملف كبير؟ - اختر جودة أقل في المرة القادمة\n"
        f"• لماذا يأخذ وقتاً؟ - الفيديوهات الطويلة تحتاج وقتاً أطول\n\n"
        f"💡 **نصائح:**\n"
        f"• استخدم جودة 480p للفيديوهات المتوسطة\n"
        f"• استخدم جودة 360p للفيديوهات الطويلة\n"
        f"• استخدم 'تحميل صوت' للمقاطع الطويلة جداً\n"
        f"• قص الفيديو الطويل باستخدام YouTube Studio قبل التحميل"
    )
    
    await update.message.reply_text(
        escape_markdown(help_text),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض إحصائيات البوت"""
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("⛔️ هذا الأمر متاح للمشرف فقط.")
        return
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # إحصائيات أساسية
        c.execute('SELECT COUNT(*) FROM downloads')
        total = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM downloads WHERE success=1')
        success = c.fetchone()[0]
        
        c.execute('SELECT COUNT(DISTINCT user_id) FROM downloads')
        users = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM downloads WHERE DATE(timestamp) = DATE("now")')
        today = c.fetchone()[0]
        
        # المنصات الأكثر استخداماً
        c.execute('SELECT platform, COUNT(*) FROM downloads WHERE success=1 GROUP BY platform ORDER BY COUNT(*) DESC LIMIT 5')
        top_platforms = c.fetchall()
        
        conn.close()
        
        # حساب نسبة النجاح
        success_rate = (success / total * 100) if total > 0 else 0
        
        # تنسيق النتائج
        stats_text = (
            f"📊 **إحصائيات البوت**\n\n"
            f"👥 **المستخدمون:** {users}\n"
            f"📥 **إجمالي التحميلات:** {total}\n"
            f"✅ **النجاح:** {success}\n"
            f"📈 **نسبة النجاح:** {success_rate:.1f}%\n"
            f"📅 **اليوم:** {today}\n\n"
            f"🏆 **أكثر المنصات استخداماً:**\n"
        )
        
        for platform, count in top_platforms:
            stats_text += f"• {platform}: {count}\n"
        
        await update.message.reply_text(
            escape_markdown(stats_text),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        await update.message.reply_text("❌ حدث خطأ في جلب الإحصائيات")

# ==============================================================================
# 11. معالج الروابط الرئيسي
# ==============================================================================
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الروابط المرسلة"""
    user = update.effective_user
    url = update.message.text.strip()
    
    # التحقق من صحة الرابط
    if not is_valid_url(url):
        await update.message.reply_text("⚠️ الرابط غير صالح. يرجى إرسال رابط صحيح.")
        return
    
    # التحقق من حد الطلبات
    if not await check_rate_limit(user.id):
        await update.message.reply_text("⏱ لقد تجاوزت الحد المسموح (5 طلبات/دقيقة). يرجى الانتظار قليلاً.")
        return
    
    # التحقق من التحميلات النشطة
    if active_downloads[user.id] >= 1:  # ⬅️ واحد فقط في الوقت الواحد
        await update.message.reply_text("⏳ لديك تحميل قيد التنفيذ بالفعل. يرجى الانتظار حتى يكتمل.")
        return
    
    active_downloads[user.id] += 1
    
    try:
        msg = await update.message.reply_text("⏳ جاري تحليل الرابط...")
        platform = detect_platform(url)
        
        if platform == 'instagram':
            # Instagram له معالجة خاصة
            files = await run_gallery_dl(url)
            await msg.edit_text("⚡️ جاري إرسال الملف...")
            
            if len(files) == 1:
                file_path = Path(files[0])
                file_size = file_path.stat().st_size if file_path.exists() else 0
                
                # إرسال كصورة أو فيديو
                with open(file_path, 'rb') as file_obj:
                    if file_path.suffix.lower() in ['.jpg', '.jpeg', '.png', '.webp']:
                        await context.bot.send_photo(
                            chat_id=update.effective_chat.id,
                            photo=file_obj,
                            caption="✅ تم التحميل بنجاح"
                        )
                    else:
                        await context.bot.send_video(
                            chat_id=update.effective_chat.id,
                            video=file_obj,
                            supports_streaming=True,
                            caption="✅ تم التحميل بنجاح"
                        )
                
                log_download(user.id, user.username, url, platform, True, file_size=file_size, quality='N/A')
            
            else:
                # إذا كان هناك عدة ملفات، نرسلها كمضغوط
                zip_path = DOWNLOAD_PATH / f"instagram_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
                with zipfile.ZipFile(zip_path, 'w') as zipf:
                    for file_path in files:
                        p = Path(file_path)
                        zipf.write(p, p.name)
                
                zip_size = zip_path.stat().st_size
                with open(zip_path, 'rb') as zip_obj:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=zip_obj,
                        caption=f"✅ تم تحميل {len(files)} ملف بنجاح"
                    )
                files.append(str(zip_path))
                
                log_download(user.id, user.username, url, platform, True, file_size=zip_size, quality='ZIP')
            
            await msg.delete()
        
        else:
            # للمنصات الأخرى (يوتيوب، فيسبوك، إلخ)
            info = await run_ydl_analysis(url)
            
            # تخزين المعلومات للاستخدام لاحقاً
            context.user_data[msg.message_id] = info
            
            # إنشاء واجهة المستخدم
            caption, keyboard = create_simple_menu(info, msg.message_id)
            
            # إرسال مع صورة مصغرة إن وجدت
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
    
    except Exception as e:
        error_msg = str(e)
        await msg.edit_text(f"❌ حدث خطأ: {escape_markdown(error_msg[:200])}")
        log_download(user.id, user.username, url, 'unknown', False, error_msg)
    
    finally:
        active_downloads[user.id] -= 1
        # تنظيف الملفات المؤقتة
        if 'files' in locals():
            for file_path in files:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except:
                    pass

# ==============================================================================
# 12. معالج أزرار الاختيار
# ==============================================================================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الضغط على الأزرار"""
    query = update.callback_query
    await query.answer()
    
    try:
        action_type, media_type, resource_id, msg_id_str = query.data.split(':')
        msg_id = int(msg_id_str)
    except ValueError:
        await query.message.delete()
        return
    
    # معالجة الأزرار الخاصة
    if action_type == "ignore":
        await query.answer("⚠️ هذا الملف كبير جداً (>50MB)", show_alert=True)
        return
    
    if action_type == "cancel":
        await query.message.delete()
        if msg_id in context.user_data:
            del context.user_data[msg_id]
        return
    
    if action_type == "help":
        await query.answer("📖 أرسل /help لمشاهدة دليل الاستخدام الكامل", show_alert=True)
        return
    
    if action_type == "back":
        if msg_id in context.user_data:
            info = context.user_data[msg_id]
            caption, keyboard = create_simple_menu(info, msg_id)
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
    
    if action_type == "more":
        if msg_id in context.user_data:
            info = context.user_data[msg_id]
            keyboard = create_qualities_menu(info, msg_id)
            try:
                await query.message.edit_reply_markup(reply_markup=keyboard)
            except BadRequest:
                pass
        return
    
    # إذا كان زر تحميل
    if action_type == "dl":
        user = query.from_user
        
        # التحقق من وجود المعلومات
        if msg_id not in context.user_data:
            await query.edit_message_text("⚠️ انتهت صلاحية الجلسة. يرجى إرسال الرابط مرة أخرى.")
            return
        
        info = context.user_data[msg_id]
        url = info.get('webpage_url')
        platform = detect_platform(url)
        
        # التحقق من التحميلات النشطة
        if active_downloads[user.id] >= 1:
            await query.answer("⏳ لديك تحميل قيد التنفيذ. يرجى الانتظار.", show_alert=True)
            return
        
        active_downloads[user.id] += 1
        
        async with download_semaphore:
            # تحديث الرسالة
            await query.edit_message_reply_markup(None)
            try:
                current_caption = query.message.caption_markdown_v2 or ""
                await query.message.edit_caption(
                    caption=current_caption + escape_markdown("\n\n⏳ جاري التحميل..."),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except BadRequest:
                pass
            
            file_path = None
            try:
                # تحديد نوع التحميل
                is_audio = (media_type == 'a')
                
                # التحميل
                file_path = await run_ydl_download(url, resource_id, is_audio)
                
                # فحص حجم الملف
                if os.path.exists(file_path):
                    actual_size = os.path.getsize(file_path)
                    if actual_size > MAX_FILE_SIZE:
                        await query.message.delete()
                        await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=f"❌ حجم الملف ({format_size(actual_size)}) يتجاوز الحد المسموح (50MB)."
                        )
                        if msg_id in context.user_data:
                            del context.user_data[msg_id]
                        return
                
                # تحديث الرسالة للرفع
                await query.message.edit_caption(
                    caption=escape_markdown("⚡️ جاري إرسال الملف..."),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                
                # إرسال الملف
                title = info.get('title', 'تحميل')
                
                with open(file_path, 'rb') as file_obj:
                    if is_audio:
                        await context.bot.send_audio(
                            chat_id=query.message.chat_id,
                            audio=file_obj,
                            title=title[:64],
                            caption="✅ تم التحميل بنجاح"
                        )
                        quality = 'MP3'
                    else:
                        await context.bot.send_video(
                            chat_id=query.message.chat_id,
                            video=file_obj,
                            caption=f"✅ {escape_markdown(title[:200])}",
                            parse_mode=ParseMode.MARKDOWN_V2,
                            supports_streaming=True
                        )
                        quality = 'فيديو'
                
                await query.message.delete()
                
                # تسجيل النجاح
                actual_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
                log_download(user.id, user.username, url, platform, True, 
                           file_size=actual_size, quality=quality)
            
            except Exception as e:
                error_msg = str(e)
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"❌ فشل التحميل: {escape_markdown(error_msg[:200])}",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                log_download(user.id, user.username, url, platform, False, error_msg)
            
            finally:
                active_downloads[user.id] -= 1
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except:
                        pass
                
                if msg_id in context.user_data:
                    del context.user_data[msg_id]

# ==============================================================================
# 13. معالج الأخطاء
# ==============================================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الأخطاء العامة"""
    logging.error("خطأ في معالجة التحديث:", exc_info=context.error)
    
    try:
        if update and isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "❌ حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى."
            )
    except:
        pass

# ==============================================================================
# 14. خادم Flask للصحة
# ==============================================================================
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    return "✅ البوت يعمل بشكل طبيعي", 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

# ==============================================================================
# 15. التطبيق الرئيسي
# ==============================================================================
def main():
    """تشغيل البوت"""
    # تهيئة قاعدة البيانات
    init_db()
    
    # إنشاء المجلدات اللازمة
    DOWNLOAD_PATH.mkdir(exist_ok=True)
    
    # تشغيل خادم Flask في خيط منفصل
    threading.Thread(target=run_flask, daemon=True).start()
    logging.info(f"🌐 خادم الصحة يعمل على المنفذ {PORT}")
    
    # إنشاء تطبيق التلغرام
    app = Application.builder().token(BOT_TOKEN).build()
    
    # إضافة المعالجات
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)
    
    # بدء التنظيف التلقائي
    loop = asyncio.get_event_loop()
    loop.create_task(periodic_cleanup())
    
    logging.info("🚀 بدء تشغيل البوت...")
    logging.info("✅ البوت جاهز للاستخدام!")
    
    # تشغيل البوت
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    if not BOT_TOKEN:
        logging.fatal("❌ خطأ: BOT_TOKEN غير معرف!")
        exit(1)
    
    try:
        main()
    except KeyboardInterrupt:
        logging.info("⏹ توقف البوت بواسطة المستخدم")
    except Exception as e:
        logging.fatal(f"💥 خطأ فادح: {e}", exc_info=True)
        exit(1)
