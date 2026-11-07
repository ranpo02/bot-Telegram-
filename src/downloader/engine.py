# src/downloader/engine.py
import asyncio
import logging
from src.core.exceptions import DownloadError
from src.downloader.tools import ytdlp_tool

logger = logging.getLogger(__name__)

async def download(url: str, is_audio: bool = False) -> str:
    """
    Downloads media from URL directly without broken proxies.
    """
    logger.info(f"Downloading from {url} (is_audio={is_audio})")
    return await ytdlp_tool.download_with_ytdlp(url, is_audio=is_audio)
