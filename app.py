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
                 error_msg: str = None, file_size: int = None, quality: str = None):
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
# 4. إدارة الجلسات (Session Management)
# ==============================================================================
# كل مستخدم له جلسة مستقلة تماماً
user_sessions = {}  # {user_id: {'task': asyncio.Task, 'msg_id': int, 'info': dict}}

def get_user_session(user_id: int):
    """الحصول على جلسة المستخدم أو إنشاء واحدة جديدة"""
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            'task': None,
            'msg_id': None,
            'info': None,
            'lock': asyncio.Lock()
        }
    return user_sessions[user_id]

def cancel_user_session(user_id: int):
    """إلغاء جلسة المستخدم الحالية"""
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if session['task'] and not session['task'].done():
            session['task'].cancel()
        user_sessions[user_id] = {
            'task': None,
            'msg_id': None,
            'info': None,
            'lock': asyncio.Lock()
        }

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
        f"• 🚀 جلسة مستقلة لكل مستخدم\n"
        f"• ⚡️ تحميل فوري بدون انتظار\n"
        f"• 💾 تخزين مؤقت ذكي\n"
        f"• 📊 عرض الحجم قبل التحميل\n"
        f"• 🔄 إعادة محاولة تلقائية\n"
        f"• ♾️ بدون حد للطلبات\n\n"
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
        "• 50 ميغابايت كحد أقصى للملف\n\n"
        "✨ **مميزات:**\n"
        "• جلسة مستقلة لكل مستخدم\n"
        "• عرض الحجم قبل التحميل\n"
        "• كاش ذكي\n"
        "• بدون حد للطلبات\n"
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
# 12. معالج الروابط (جلسة مستقلة لكل مستخدم)
# ==============================================================================
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    url = update.message.text.strip()
    
    if not is_valid_url(url):
        await update.message.reply_text("⚠️ رابط غير صالح")
        return
    
    # الحصول على جلسة المستخدم
    session = get_user_session(user.id)
    
    # إنشاء مهمة جديدة لهذا المستخدم
    async def process_download():
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
                        await context.bot.send_photo(update.effective_chat.id, open(fp, 'rb'), 
                                                    caption=f"✅ تم ({format_size(size)})")
                    else:
                        await context.bot.send_video(update.effective_chat.id, open(fp, 'rb'), 
                                                    caption=f"✅ تم ({format_size(size)})")
                    
                    quality = 'N/A'
                else:
                    zip_path = DOWNLOAD_PATH / f"ig_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
                    with zipfile.ZipFile(zip_path, 'w') as z:
                        for f in files:
                            z.write(f, Path(f).name)
                    
                    size = zip_path.stat().st_size
                    await context.bot.send_document(update.effective_chat.id, open(zip_path, 'rb'), 
                                                   caption=f"✅ {len(files)} ملف ({format_size(size)})")
                    files.append(str(zip_path))
                    quality = 'ZIP'
                
                await msg.delete()
                log_download(user.id, user.username, url, platform, True, file_size=size, quality=quality)
            
            else:
                info = await run_ydl_analysis(url)
                
                # حفظ المعلومات في جلسة المستخدم
                session['msg_id'] = msg.message_id
                session['info'] = info
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
        
        except asyncio.CancelledError:
            await msg.edit_text("⚠️ تم إلغاء العملية")
            raise
        
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
    
    # إنشاء المهمة وحفظها في الجلسة
    session['task'] = asyncio.create_task(process_download())
    
    try:
        await session['task']
    except asyncio.CancelledError:
        logging.info(f"تم إلغاء مهمة المستخدم {user.id}")
    except Exception as e:
        logging.error(f"خطأ في مهمة المستخدم {user.id}: {e}")

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
        # إلغاء جلسة المستخدم
        cancel_user_session(user.id)
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
            pass
        return
    
    if handler_type == "back":
        if platform == 'youtube':
            caption, keyboard = build_youtube_ui(info, msg_id)
        else:
            caption, keyboard = build_generic_ui(info, msg_id)
        
        try:
            await query.message.edit_caption(caption=caption, parse_mode=ParseMode.MARKDOWN_V2, 
                                           reply_markup=keyboard)
        except BadRequest:
            await query.message.edit_text(text=caption, parse_mode=ParseMode.MARKDOWN_V2, 
                                         reply_markup=keyboard)
        return
    
    # فحص الحجم قبل التحميل
    try:
        is_audio = (action == 'a')
        target_fmt = None
        
        if is_audio:
            audio_fmts = [f for f in info.get('formats', []) 
                         if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
            target_fmt = max(audio_fmts, key=lambda x: x.get('abr', 0), default=None)
        else:
            if resource_id == 'best':
                video_fmts = [f for f in info.get('formats', []) 
                             if f.get('vcodec') != 'none' and f.get('acodec') != 'none']
                target_fmt = max(video_fmts, key=lambda x: x.get('height', 0), default=None)
            else:
                target_fmt = next((f for f in info.get('formats', []) 
                                  if f.get('format_id') == resource_id), None)
        
        if target_fmt:
            size = target_fmt.get('filesize') or target_fmt.get('filesize_approx')
            if size and size > MAX_FILE_SIZE:
                await query.message.delete()
                await context.bot.send_message(
                    query.message.chat_id,
                    f"❌ الملف كبير جداً ({format_size(size)})\n\n💡 جرب جودة أقل أو MP3"
                )
                if msg_id in context.user_data:
                    del context.user_data[msg_id]
                return
    except Exception as e:
        logging.warning(f"فشل فحص الحجم: {e}")
    
    # إزالة الأزرار
    await query.edit_message_reply_markup(None)
    try:
        caption = query.message.caption_markdown_v2 or ""
        await query.message.edit_caption(
            caption=caption + escape_markdown("\n\n⏳ جارٍ التحميل..."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except BadRequest:
        pass
    
    file_path = None
    
    # الحصول على جلسة المستخدم واستخدامها
    session = get_user_session(user.id)
    
    async def download_task():
        nonlocal file_path
        
        try:
            is_audio = (action == 'a')
            file_path = await run_ydl_download(url, resource_id, is_audio)
            
            # فحص الحجم الفعلي
            if os.path.exists(file_path):
                actual_size = os.path.getsize(file_path)
                if actual_size > MAX_FILE_SIZE:
                    await query.message.delete()
                    await context.bot.send_message(
                        query.message.chat_id,
                        f"❌ الملف المحمّل ({format_size(actual_size)}) أكبر من 50MB"
                    )
                    if msg_id in context.user_data:
                        del context.user_data[msg_id]
                    return
            
            # تحديث الرسالة
            try:
                await query.message.edit_caption(
                    caption=escape_markdown("⚡️ جارٍ الرفع..."),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except BadRequest:
                pass
            
            title = info.get('title', 'تحميل')
            
            if is_audio:
                await context.bot.send_audio(
                    query.message.chat_id,
                    open(file_path, 'rb'),
                    title=title[:64],
                    caption="✅ تم التحميل"
                )
                quality = 'MP3'
            else:
                # تحديد الجودة
                quality = 'Unknown'
                try:
                    if resource_id == 'best':
                        vfmts = [f for f in info.get('formats', []) if f.get('vcodec') != 'none']
                        if vfmts:
                            quality = f"{max(f.get('height', 0) for f in vfmts)}p"
                    else:
                        tfmt = next((f for f in info.get('formats', []) 
                                    if f.get('format_id') == resource_id), None)
                        if tfmt:
                            quality = f"{tfmt.get('height', 0)}p"
                except:
                    pass
                
                await context.bot.send_video(
                    query.message.chat_id,
                    open(file_path, 'rb'),
                    caption=f"✅ {escape_markdown(title[:200])}",
                    parse_mode=ParseMode.MARKDOWN_V2,
                    supports_streaming=True
                )
            
            await query.message.delete()
            
            size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            log_download(user.id, user.username, url, platform, True, file_size=size, quality=quality)
        
        except asyncio.CancelledError:
            await query.message.edit_caption(caption="⚠️ تم إلغاء التحميل")
            raise
        
        except (DownloadError, Exception) as e:
            logging.error(f"خطأ في التحميل: {e}", exc_info=True)
            try:
                await context.bot.send_message(
                    query.message.chat_id,
                    f"❌ فشل التحميل: {escape_markdown(str(e))}",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except:
                await context.bot.send_message(
                    query.message.chat_id,
                    f"❌ فشل التحميل: {str(e)}"
                )
            log_download(user.id, user.username, url, platform, False, str(e))
        
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    logging.warning(f"فشل حذف الملف: {e}")
            is_audio = (action == 'a')
            file_path = await run_ydl_download(url, resource_id, is_audio)
            
            # فحص الحجم الفعلي
            if os.path.exists(file_path):
                actual_size = os.path.getsize(file_path)
                if actual_size > MAX_FILE_SIZE:
                    await query.message.delete()
                    await context.bot.send_message(
                        query.message.chat_id,
                        f"❌ الملف المحمّل ({format_size(actual_size)}) أكبر من 50MB"
                    )
                    if msg_id in context.user_data:
                        del context.user_data[msg_id]
                    return
            
            # تحديث الرسالة
            try:
                await query.message.edit_caption(
                    caption=escape_markdown("⚡️ جارٍ الرفع..."),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except BadRequest:
                pass
            
            title = info.get('title', 'تحميل')
            
            if is_audio:
                await context.bot.send_audio(
                    query.message.chat_id,
                    open(file_path, 'rb'),
                    title=title[:64],
                    caption="✅ تم التحميل"
                )
                quality = 'MP3'
            else:
                # تحديد الجودة
                quality = 'Unknown'
                try:
                    if resource_id == 'best':
                        vfmts = [f for f in info.get('formats', []) if f.get('vcodec') != 'none']
                        if vfmts:
                            quality = f"{max(f.get('height', 0) for f in vfmts)}p"
                    else:
                        tfmt = next((f for f in info.get('formats', []) 
                                    if f.get('format_id') == resource_id), None)
                        if tfmt:
                            quality = f"{tfmt.get('height', 0)}p"
                except:
                    pass
                
                await context.bot.send_video(
                    query.message.chat_id,
                    open(file_path, 'rb'),
                    caption=f"✅ {escape_markdown(title[:200])}",
                    parse_mode=ParseMode.MARKDOWN_V2,
                    supports_streaming=True
                )
            
            await query.message.delete()
            
            size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            log_download(user.id, user.username, url, platform, True, file_size=size, quality=quality)
        
        except (DownloadError, Exception) as e:
            logging.error(f"خطأ في التحميل: {e}", exc_info=True)
            try:
                await context.bot.send_message(
                    query.message.chat_id,
                    f"❌ فشل التحميل: {escape_markdown(str(e))}",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except:
                await context.bot.send_message(
                    query.message.chat_id,
                    f"❌ فشل التحميل: {str(e)}"
                )
            log_download(user.id, user.username, url, platform, False, str(e))
        
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    logging.warning(f"فشل حذف الملف: {e}")
            
            if msg_id in context.user_data:
                del context.user_data[msg_id]
    
    # إنشاء المهمة وتشغيلها
    session['task'] = asyncio.create_task(download_task())
    
    try:
        await session['task']
    except asyncio.CancelledError:
        logging.info(f"تم إلغاء تحميل المستخدم {user.id}")
    except Exception as e:
        logging.error(f"خطأ في تحميل المستخدم {user.id}: {e}")

# ==============================================================================
# 14. معالج الأخطاء العام
# ==============================================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error("خطأ في التحديث:", exc_info=context.error)
    
    if update and isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("❌ حدث خطأ غير متوقع")
        except:
            pass

# ==============================================================================
# 15. Flask للصحة
# ==============================================================================
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    return "OK", 200

@flask_app.route('/stats')
def stats_api():
    stats = get_stats()
    return stats if stats else {"error": "Failed"}, 500 if not stats else 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

# ==============================================================================
# 16. التطبيق الرئيسي
# ==============================================================================
def main():
    init_db()
    
    threading.Thread(target=run_flask, daemon=True).start()
    logging.info(f"Flask يعمل على منفذ {PORT}")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)
    
    # بدء التنظيف الدوري
    loop = asyncio.get_event_loop()
    loop.create_task(periodic_cleanup())
    
    logging.info("🚀 البوت يعمل الآن!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    if not BOT_TOKEN:
        logging.fatal("خطأ: BOT_TOKEN غير معرف!")
        exit(1)
    
    try:
        main()
    except KeyboardInterrupt:
        logging.info("توقف البوت")
    except Exception as e:
        logging.fatal(f"خطأ فادح: {e}", exc_info=True)
        exit(1)
