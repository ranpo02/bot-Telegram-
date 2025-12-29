# src/downloader.py

import asyncio
from yt_dlp import YoutubeDL

# استيراد الإعدادات والمتغيرات اللازمة من ملف config
from .config import (
    DOWNLOAD_PATH,
    PROXY_LIST,
    PROXY_INDEX_LOCK,
)
from .utils import logger

# نحتاج إلى الوصول إلى المتغير العام لتحديثه
import src.config

async def get_next_proxy() -> str | None:
    """
    دالة آمنة (thread-safe) للحصول على البروكسي التالي من القائمة بشكل دائري.
    تستخدم قفلًا (Lock) لتجنب حالات التضارب (Race Conditions).
    """
    if not PROXY_LIST:
        return None
    
    async with PROXY_INDEX_LOCK:
        # الوصول إلى المتغير العام وتحديثه
        proxy = PROXY_LIST[src.config.CURRENT_PROXY_INDEX]
        src.config.CURRENT_PROXY_INDEX = (src.config.CURRENT_PROXY_INDEX + 1) % len(PROXY_LIST)
        logger.info(f"تم اختيار البروكسي التالي: {proxy}")
        return proxy

async def ytdlp_downloader(url: str) -> str | None:
    """
    يحاول تحميل الفيديو/الصوت باستخدام yt-dlp مع استخدام بروكسي متغير.
    يُرجع مسار الملف عند النجاح, و None عند الفشل.
    """
    proxy = await get_next_proxy()
    
    if proxy:
        logger.info(f"محاولة التحميل للرابط: {url} باستخدام البروكسي: {proxy.split('@')[-1]}") # لإخفاء بيانات الدخول لو وجدت
    else:
        logger.info(f"محاولة التحميل للرابط: {url} (بدون بروكسي)")

    # إعدادات yt-dlp
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': f'{DOWNLOAD_PATH}/%(id)s.%(ext)s',
        'noplaylist': True,
        'quiet': True,
        'merge_output_format': 'mp4',
        'http_headers': { # محاكاة متصفح حقيقي
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.5',
        }
    }

    # إضافة البروكسي إلى الإعدادات إذا كان متوفرًا
    if proxy:
        ydl_opts['proxy'] = proxy

    try:
        # تشغيل yt-dlp في thread منفصل لتجنب حظر الحلقة الرئيسية (event loop)
        loop = asyncio.get_running_loop()
        with YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(url, download=True)
            )
            filepath = ydl.prepare_filename(info)
            logger.info(f"نجح التحميل. الملف: {filepath}")
            return filepath
    except Exception as e:
        logger.error(f"فشل التحميل (البروكسي: {proxy}): {e}")
        return None

# --- مدير التحميل ---
# قائمة الأدوات التي سيتم تجربتها بالترتيب. حاليًا أداة واحدة.
DOWNLOAD_TOOLS = [
    ytdlp_downloader,
    # يمكنك إضافة دوال تحميل أخرى هنا في المستقبل لتكون كخطة بديلة (fallback)
]

async def download_media(url: str) -> str | None:
    """
    مدير التحميل الذكي.
    يجرب كل أداة في قائمة DOWNLOAD_TOOLS حتى تنجح إحداها.
    """
    for tool in DOWNLOAD_TOOLS:
        filepath = await tool(url)
        if filepath:
            return filepath # نجحت الأداة، أرجع مسار الملف وتوقف
    
    logger.warning(f"فشلت جميع أدوات التحميل للرابط: {url}")
    return None
