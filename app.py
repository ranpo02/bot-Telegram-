# app.py - الإصدار النهائي 18.0 معدل
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
import tempfile
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

# استخدام مجلد مؤقت للتنزيلات
DOWNLOAD_PATH = Path(tempfile.mkdtemp(prefix="downloads_"))

# قاعدة البيانات في مجلد دائم إن أمكن
DATA_DIR = Path("/data") if Path("/data").exists() else Path("data")
DATA_DIR.mkdir(exist_ok=True, parents=True)
DB_PATH = DATA_DIR / "bot_stats.db"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB (مخفض لـ fly.io)
MAX_REQUESTS_PER_MINUTE = 5
CLEANUP_INTERVAL = 1800
FILE_MAX_AGE = 3600
CACHE_TTL = 600
DOWNLOAD_TIMEOUT = 600
MAX_CONCURRENT_DOWNLOADS = 3  # مخفض لـ fly.io

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
for logger_name in ["httpx", "werkzeug", "telegram.ext.Application"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

# ==============================================================================
# 2. نظام التزامن
# ==============================================================================
active_downloads = defaultdict(int)
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# ==============================================================================
# 3. قاعدة البيانات
# ==============================================================================
def init_db():
    try:
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
    except Exception as e:
        logging.error(f"❌ خطأ في تهيئة قاعدة البيانات: {e}")

def log_download(user_id: int, username: str, url: str, platform: str, success: bool, 
                 error_msg: str = None, file_size: int = None, quality: str = None):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        
        c.execute('''INSERT INTO downloads (user_id, username, url, platform, timestamp, 
                     success, error_message, file_size, quality)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, username or "", url[:500], platform, datetime.now(), success, 
                   error_msg, file_size, quality))
        
        c.execute('''INSERT INTO user_stats (user_id, username, total_downloads, 
                     successful_downloads, failed_downloads, first_use, last_use)
                     VALUES (?, ?, 1, ?, ?, ?, ?)
                     ON CONFLICT(user_id) DO UPDATE SET
                     total_downloads = total_downloads + 1,
                     successful_downloads = successful_downloads + ?,
                     failed_downloads = failed_downloads + ?,
                     last_use = ?''',
                  (user_id, username or "", 1 if success else 0, 1 if not success else 0, 
                   datetime.now(), datetime.now(),
                   1 if success else 0, 1 if not success else 0, datetime.now()))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"❌ خطأ في تسجيل التحميل: {e}")

# ==============================================================================
# 4. التخزين المؤقت
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
# 5. Rate Limiting
# ==============================================================================
user_requests = defaultdict(list)

async def check_rate_limit(user_id: int) -> bool:
    now = datetime.now()
    if user_id in user_requests:
        user_requests[user_id] = [
            req_time for req_time in user_requests[user_id]
            if now - req_time < timedelta(minutes=1)
        ]
    
    if len(user_requests.get(user_id, [])) >= MAX_REQUESTS_PER_MINUTE:
        return False
    
    if user_id not in user_requests:
        user_requests[user_id] = []
    user_requests[user_id].append(now)
    return True

# ==============================================================================
# 6. التنظيف
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
                    try:
                        file_path.unlink()
                        deleted += 1
                    except:
                        pass
        
        if deleted > 0:
            logging.info(f"🧹 تم تنظيف {deleted} ملف قديم")
    except Exception as e:
        logging.error(f"❌ خطأ في التنظيف: {e}")

async def periodic_cleanup():
    while True:
        cleanup_old_files()
        await asyncio.sleep(CLEANUP_INTERVAL)

# ==============================================================================
# 7. الدوال المساعدة
# ==============================================================================
def is_valid_url(url: str) -> bool:
    return bool(re.match(r'https?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', url))

def detect_platform(url: str) -> str:
    url_lower = url.lower()
    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'youtube'
    elif 'instagram.com' in url_lower:
        return 'instagram'
    elif 'facebook.com' in url_lower or 'fb.watch' in url_lower:
        return 'facebook'
    elif 'twitter.com' in url_lower or 'x.com' in url_lower:
        return 'twitter'
    elif 'tiktok.com' in url_lower:
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
    # جميع الرموز الخاصة في Markdown V2
    escape_chars = r'_*[]()~`>#+-=|{}.!?'
    # تهريب كل الرموز
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text

# ==============================================================================
# 8. نظام التحميل
# ==============================================================================
class DownloadError(Exception):
    pass

def get_ydl_opts(url: str) -> dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
        'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
        'outtmpl': str(DOWNLOAD_PATH / '%(id)s.%(ext)s'),
        'ffmpeg_location': '/usr/bin/ffmpeg',
    }
    
    if 'facebook.com' not in url.lower() and 'fb.watch' not in url.lower():
        opts['proxy'] = PRIMARY_PROXY
    
    return opts

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
        error = stderr.decode('utf-8', errors='ignore')
        raise DownloadError(f"فشل تحميل Instagram: {error[:200]}")
    
    files = []
    for root, _, filenames in os.walk(DOWNLOAD_PATH):
        for name in filenames:
            files.append(os.path.join(root, name))
    
    if not files:
        raise DownloadError("لم يتم العثور على ملفات")
    
    return files

async def run_ydl_analysis(url: str) -> dict:
    cached = await get_cached_info(url)
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
        
        await set_cached_info(url, info)
        return info
    except Exception as e:
        raise DownloadError(f"خطأ في التحليل: {str(e)[:200]}")

async def run_ydl_download(url: str, format_id: str, is_audio: bool) -> str:
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
        raise DownloadError(f"خطأ في التحميل: {str(e)[:200]}")

# ==============================================================================
# 9. واجهات المستخدم
# ==============================================================================
def build_simple_ui(info: dict, msg_id: int) -> tuple:
    url = info.get('webpage_url', '')
    platform = detect_platform(url)
    title = info.get('title', 'فيديو')[:100]
    duration = info.get('duration', 0)
    
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
    
    caption = (
        f"{platform_icon} **{platform_name}**\n\n"
        f"📝 **العنوان:** {escape_markdown(title)}\n"
        f"⏱ **المدة:** {format_duration(duration)}{uploader}\n\n"
        f"👇 **اختر طريقة التحميل:**"
    )
    
    keyboard = []
    keyboard.append([
        InlineKeyboardButton("📥 تحميل فيديو", callback_data=f"dl:v:best:{msg_id}"),
        InlineKeyboardButton("🎵 تحميل صوت", callback_data=f"dl:a:best:{msg_id}")
    ])
    
    if platform == 'youtube':
        keyboard.append([
            InlineKeyboardButton("⚙️ جودة أخرى", callback_data=f"more:v:na:{msg_id}")
        ])
    
    keyboard.append([
        InlineKeyboardButton("❌ إلغاء العملية", callback_data=f"cancel:na:na:{msg_id}")
    ])
    
    return caption, InlineKeyboardMarkup(keyboard)

def build_qualities_ui(info: dict, msg_id: int) -> InlineKeyboardMarkup:
    formats = [
        f for f in info.get('formats', [])
        if f.get('vcodec') != 'none' and f.get('acodec') != 'none'
    ]
    
    formats.sort(key=lambda x: x.get('height', 0), reverse=True)
    
    buttons = []
    seen_heights = set()
    
    for fmt in formats:
        height = fmt.get('height', 0)
        if height and height not in seen_heights:
            seen_heights.add(height)
            
            filesize = fmt.get('filesize') or fmt.get('filesize_approx', 0)
            size_text = f" ({format_size(filesize)})" if filesize else ""
            
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
    
    welcome_text_lines = [
        f"👋 **مرحباً {escape_markdown(user.first_name or 'مستخدم')}\\!**",
        "",
        f"🤖 **أنا بوت لتحميل الفيديوهات**",
        "",
        f"🎯 **ماذا أستطيع فعل\\؟**",
        f"• 📥 تحميل من **يوتيوب**",
        f"• 📸 تحميل من **إنستغرام**",
        f"• 👥 تحميل من **فيسبوك**",
        f"• 🐦 تحميل من **تويتر/X**",
        f"• 🎵 تحميل من **تيك توك**",
        "",
        f"🚀 **كيفية الاستخدام\\:**",
        f"1\\. أرسل رابط الفيديو",
        f"2\\. اختر طريقة التحميل",
        f"3\\. انتظر حتى يتم الإرسال",
        "",
        f"📌 **ملاحظات مهمة\\:**",
        f"• الحد الأقصى\\: 25 ميغابايت",
        f"• للفيديوهات الطويلة اختر جودة أقل",
        "",
        f"🔧 **الأوامر\\:**",
        f"/start \\- عرض هذه الرسالة",
        f"/help \\- المساعدة والتفاصيل",
    ]
    
    if str(user.id) == ADMIN_ID:
        welcome_text_lines.extend([
            "",
            f"⚙️ **أوامر الإدارة\\:**",
            f"/stats \\- عرض الإحصائيات"
        ])
    
    welcome_text = "\n".join(welcome_text_lines)
    
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN_V2)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text_lines = [
        f"📖 **دليل الاستخدام الكامل**",
        "",
        f"🎬 **المنصات المدعومة\\:**",
        f"• YouTube \\(يوتيوب\\)",
        f"• Instagram \\(إنستغرام\\)",
        f"• Facebook \\(فيسبوك\\)",
        f"• Twitter/X \\(تويتر\\)",
        f"• TikTok \\(تيك توك\\)",
        f"• معظم المواقع الأخرى",
        "",
        f"⚡ **طريقة العمل\\:**",
        f"1\\. أرسل رابط الفيديو",
        f"2\\. اختر 'تحميل فيديو' أو 'تحميل صوت'",
        f"3\\. للفيديوهات الطويلة، اختر جودة أقل",
        f"4\\. انتظر حتى يرسل لك البوت الملف",
        "",
        f"⚠️ **المعلومات المهمة\\:**",
        f"• الحد الأقصى لحجم الملف\\: 25 ميغابايت",
        f"• إذا كان الفيديو طويلاً \\(\\>10 دقائق\\)، اختر جودة 480p أو أقل",
        f"• للفيديوهات الطويلة جداً، استخدم 'تحميل صوت' لتقليل الحجم",
        "",
        f"❓ **استفسارات شائعة\\:**",
        f"• لماذا لا يعمل الرابط\\؟ \\- قد يكون الفيديو محمياً أو غير متاح",
        f"• لماذا الملف كبير\\؟ \\- اختر جودة أقل في المرة القادمة",
        f"• لماذا يأخذ وقتاً\\؟ \\- الفيديوهات الطويلة تحتاج وقتاً أطول"
    ]
    
    help_text = "\n".join(help_text_lines)
    
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("⛔️ هذا الأمر متاح للمشرف فقط\\.")
        return
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('SELECT COUNT\\(\\*\\) FROM downloads')
        total = c.fetchone()[0]
        
        c.execute('SELECT COUNT\\(\\*\\) FROM downloads WHERE success=1')
        success = c.fetchone()[0]
        
        c.execute('SELECT COUNT\\(DISTINCT user_id\\) FROM downloads')
        users = c.fetchone()[0]
        
        c.execute('SELECT COUNT\\(\\*\\) FROM downloads WHERE DATE\\(timestamp\\) = DATE\\(\\"now\\"\\)')
        today = c.fetchone()[0]
        
        conn.close()
        
        success_rate = (success / total * 100) if total > 0 else 0
        
        stats_text_lines = [
            f"📊 **إحصائيات البوت**",
            "",
            f"👥 **المستخدمون\\:** {users}",
            f"📥 **إجمالي التحميلات\\:** {total}",
            f"✅ **النجاح\\:** {success}",
            f"📈 **نسبة النجاح\\:** {success_rate:.1f}%",
            f"📅 **اليوم\\:** {today}"
        ]
        
        stats_text = "\n".join(stats_text_lines)
        
        await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await update.message.reply_text("❌ حدث خطأ في جلب الإحصائيات")

# ==============================================================================
# 11. معالج الروابط
# ==============================================================================
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    url = update.message.text.strip()
    
    if not is_valid_url(url):
        await update.message.reply_text("⚠️ الرابط غير صالح\\. يرجى إرسال رابط صحيح\\.")
        return
    
    if not await check_rate_limit(user.id):
        await update.message.reply_text("⏱ لقد تجاوزت الحد المسموح \\(5 طلبات/دقيقة\\)\\. يرجى الانتظار قليلاً\\.")
        return
    
    if active_downloads[user.id] >= 1:
        await update.message.reply_text("⏳ لديك تحميل قيد التنفيذ بالفعل\\. يرجى الانتظار حتى يكتمل\\.")
        return
    
    active_downloads[user.id] += 1
    
    try:
        msg = await update.message.reply_text("⏳ جاري تحليل الرابط\\.\\.\\.")
        platform = detect_platform(url)
        
        if platform == 'instagram':
            files = await run_gallery_dl(url)
            await msg.edit_text("⚡️ جاري إرسال الملف\\.\\.\\.")
            
            if len(files) == 1:
                file_path = Path(files[0])
                file_size = file_path.stat().st_size if file_path.exists() else 0
                
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
            info = await run_ydl_analysis(url)
            context.user_data[msg.message_id] = info
            
            caption, keyboard = build_simple_ui(info, msg.message_id)
            
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
        safe_error_msg = escape_markdown(error_msg[:200])
        await msg.edit_text(f"❌ حدث خطأ\\: {safe_error_msg}")
        log_download(user.id, user.username, url, 'unknown', False, error_msg)
    
    finally:
        active_downloads[user.id] = max(0, active_downloads[user.id] - 1)
        if 'files' in locals():
            for file_path in files:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except:
                    pass

# ==============================================================================
# 12. معالج الأزرار
# ==============================================================================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        action_type, media_type, resource_id, msg_id_str = query.data.split(':')
        msg_id = int(msg_id_str)
    except ValueError:
        await query.message.delete()
        return
    
    if action_type == "ignore":
        await query.answer("⚠️ هذا الملف كبير جداً \\(\\>25MB\\)", show_alert=True)
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
            keyboard = build_qualities_ui(info, msg_id)
            try:
                await query.message.edit_reply_markup(reply_markup=keyboard)
            except BadRequest:
                pass
        return
    
    if action_type == "dl":
        user = query.from_user
        
        if msg_id not in context.user_data:
            await query.edit_message_text("⚠️ انتهت صلاحية الجلسة\\. يرجى إرسال الرابط مرة أخرى\\.")
            return
        
        info = context.user_data[msg_id]
        url = info.get('webpage_url')
        platform = detect_platform(url)
        
        if active_downloads[user.id] >= 1:
            await query.answer("⏳ لديك تحميل قيد التنفيذ\\. يرجى الانتظار\\.", show_alert=True)
            return
        
        active_downloads[user.id] += 1
        
        async with download_semaphore:
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
                is_audio = (media_type == 'a')
                file_path = await run_ydl_download(url, resource_id, is_audio)
                
                if os.path.exists(file_path):
                    actual_size = os.path.getsize(file_path)
                    if actual_size > MAX_FILE_SIZE:
                        await query.message.delete()
                        await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=f"❌ حجم الملف \\({format_size(actual_size)}\\) يتجاوز الحد المسموح \\(25MB\\)\\."
                        )
                        if msg_id in context.user_data:
                            del context.user_data[msg_id]
                        return
                
                await query.message.edit_caption(
                    caption=escape_markdown("⚡️ جاري إرسال الملف..."),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                
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
                
                actual_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
                log_download(user.id, user.username, url, platform, True, 
                           file_size=actual_size, quality=quality)
            
            except Exception as e:
                error_msg = str(e)
                safe_error_msg = escape_markdown(error_msg[:200])
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"❌ فشل التحميل\\: {safe_error_msg}",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                log_download(user.id, user.username, url, platform, False, error_msg)
            
            finally:
                active_downloads[user.id] = max(0, active_downloads[user.id] - 1)
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
    logging.error("خطأ في معالجة التحديث:", exc_info=context.error)
    
    try:
        if update and isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "❌ حدث خطأ غير متوقع\\. يرجى المحاولة مرة أخرى\\."
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

@flask_app.route('/health')
def health():
    return json.dumps({"status": "healthy", "timestamp": datetime.now().isoformat()}), 200

def run_flask():
    from werkzeug.serving import run_simple
    run_simple('0.0.0.0', PORT, flask_app, threaded=True, use_reloader=False)

# ==============================================================================
# 15. التطبيق الرئيسي
# ==============================================================================
def main():
    init_db()
    DOWNLOAD_PATH.mkdir(exist_ok=True, parents=True)
    
    # تنظيف المجلد المؤقت عند الإغلاق
    atexit.register(lambda: shutil.rmtree(DOWNLOAD_PATH, ignore_errors=True))
    
    threading.Thread(target=run_flask, daemon=True).start()
    logging.info(f"🌐 خادم الصحة يعمل على المنفذ {PORT}")
    logging.info(f"📁 مسار التنزيلات المؤقت: {DOWNLOAD_PATH}")
    logging.info(f"🗄️ مسار قاعدة البيانات: {DB_PATH}")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)
    
    loop = asyncio.get_event_loop()
    loop.create_task(periodic_cleanup())
    
    logging.info("🚀 بدء تشغيل البوت...")
    logging.info("✅ البوت جاهز للاستخدام!")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)

if __name__ == '__main__':
    if not BOT_TOKEN:
        logging.fatal("❌ خطأ: BOT_TOKEN غير معرف!")
        exit(1)
    
    try:
        main()
    except KeyboardInterrupt:
        logging.info("⏹ توقف البوت بواسطة المستخدم")
        # تنظيف المجلد المؤقت
        if DOWNLOAD_PATH.exists():
            shutil.rmtree(DOWNLOAD_PATH, ignore_errors=True)
    except Exception as e:
        logging.fatal(f"💥 خطأ فادح: {e}", exc_info=True)
        # تنظيف المجلد المؤقت
        if DOWNLOAD_PATH.exists():
            shutil.rmtree(DOWNLOAD_PATH, ignore_errors=True)
        exit(1)
