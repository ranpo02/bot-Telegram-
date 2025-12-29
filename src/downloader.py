# src/downloader.py

import asyncio
from yt_dlp import YoutubeDL
from .config import DOWNLOAD_PATH, PROXY_LIST, PROXY_INDEX_LOCK
from .utils import logger  # <-- سيستورد logger من utils.py
import src.config

async def get_next_proxy() -> str | None:
    if not PROXY_LIST:
        return None
    async with PROXY_INDEX_LOCK:
        proxy = PROXY_LIST[src.config.CURRENT_PROXY_INDEX]
        src.config.CURRENT_PROXY_INDEX = (src.config.CURRENT_PROXY_INDEX + 1) % len(PROXY_LIST)
        return proxy

async def ytdlp_downloader(url: str) -> str | None:
    proxy = await get_next_proxy()
    if proxy:
        logger.info(f"محاولة التحميل للرابط: {url} باستخدام البروكسي")
    else:
        logger.info(f"محاولة التحميل للرابط: {url} (بدون بروكسي)")

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': f'{DOWNLOAD_PATH}/%(id)s.%(ext)s',
        'noplaylist': True, 'quiet': True, 'merge_output_format': 'mp4',
    }
    if proxy:
        ydl_opts['proxy'] = proxy

    try:
        loop = asyncio.get_running_loop()
        with YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            filepath = ydl.prepare_filename(info)
            logger.info(f"نجح التحميل. الملف: {filepath}")
            return filepath
    except Exception as e:
        logger.error(f"فشل التحميل (البروكسي: {proxy}): {e}")
        return None

DOWNLOAD_TOOLS = [ytdlp_downloader]

async def download_media(url: str) -> str | None:
    for tool in DOWNLOAD_TOOLS:
        filepath = await tool(url)
        if filepath:
            return filepath
    logger.warning(f"فشلت جميع أدوات التحميل للرابط: {url}")
    return None
