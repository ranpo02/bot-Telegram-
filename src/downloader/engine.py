# src/downloader/engine.py
import asyncio
import logging
from typing import List

from src.core.config import YOUTUBE_PROXIES
from src.core.exceptions import AllProxiesFailedError, DownloadError
from src.downloader.tools import ytdlp_tool

logger = logging.getLogger(__name__)

# A simple round-robin index for proxies
proxy_round_robin_index = 0
proxy_lock = asyncio.Lock()

async def get_next_proxy_index() -> int:
    """Safely gets the next proxy index for round-robin distribution."""
    global proxy_round_robin_index
    async with proxy_lock:
        start_index = proxy_round_robin_index
        proxy_round_robin_index = (proxy_round_robin_index + 1) % len(YOUTUBE_PROXIES)
        return start_index

async def download(url: str) -> str:
    """
    The main download engine.
    Detects the platform and applies the correct download strategy.
    """
    if "youtube.com" in url or "youtu.be" in url:
        logger.info(f"YouTube link detected. Applying proxy rotation strategy for {url}")
        return await _download_from_youtube(url)
    else:
        logger.info(f"Standard link detected. Downloading directly for {url}")
        return await _download_standard(url)

async def _download_standard(url: str) -> str:
    """Standard download strategy (without proxy)."""
    try:
        return await ytdlp_tool.download_with_ytdlp(url)
    except Exception as e:
        # Re-raise as a generic DownloadError if it's not already one
        if not isinstance(e, DownloadError):
            raise DownloadError(f"فشل تحميل الرابط: {url}") from e
        raise e

async def _download_from_youtube(url: str) -> str:
    """
    YouTube download strategy with sequential proxy fallback.
    """
    if not YOUTUBE_PROXIES:
        logger.warning("No proxies configured for YouTube. Attempting direct download.")
        return await _download_standard(url)

    start_index = await get_next_proxy_index()
    
    # Create a sorted list of proxies to try, starting from the round-robin index
    sorted_proxies = [YOUTUBE_PROXIES[(start_index + i) % len(YOUTUBE_PROXIES)] for i in range(len(YOUTUBE_PROXIES))]

    last_exception = None
    for proxy in sorted_proxies:
        try:
            logger.info(f"Attempting YouTube download for {url} with proxy: {proxy}")
            filepath = await ytdlp_tool.download_with_ytdlp(url, proxy=proxy)
            logger.info(f"Successfully downloaded from YouTube with proxy: {proxy}")
            return filepath
        except Exception as e:
            logger.warning(f"Proxy {proxy} failed for {url}. Error: {e}. Trying next proxy.")
            last_exception = e
    
    # If all proxies failed
    logger.error(f"All proxies failed for YouTube URL: {url}")
    raise AllProxiesFailedError() from last_exception
