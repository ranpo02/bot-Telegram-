# src/downloader/tools/ytdlp_tool.py
import asyncio
from yt_dlp import YoutubeDL
import logging

from src.core.config import DOWNLOAD_PATH
from src.core.exceptions import VideoIsPrivateOrDeletedError

logger = logging.getLogger(__name__)

async def download_with_ytdlp(url: str, proxy: str | None = None) -> str:
    """
    Downloads a video using yt-dlp with optional proxy.
    Returns the file path on success.
    Raises DownloadError on failure.
    """
    ydl_opts = {
        'format': 'b[ext=mp4]/best[ext=mp4]/best',
        'outtmpl': f'{DOWNLOAD_PATH}/%(id)s.%(ext)s',
        'noplaylist': True,
        'quiet': True,
        'merge_output_format': 'mp4',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
        }
    }
    if proxy:
        ydl_opts['proxy'] = proxy

    try:
        loop = asyncio.get_running_loop()
        with YoutubeDL(ydl_opts) as ydl:
            # Run the blocking I/O operation in a separate thread
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(url, download=True)
            )
            return ydl.prepare_filename(info)
    except Exception as e:
        error_str = str(e).lower()
        if "private video" in error_str or "video is unavailable" in error_str:
            raise VideoIsPrivateOrDeletedError()
        
        logger.error(f"yt-dlp failed for URL {url} with proxy {proxy}: {e}")
        raise
