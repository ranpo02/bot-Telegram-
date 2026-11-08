# src/downloader/tools/ytdlp_tool.py
import asyncio
import shutil
from pathlib import Path
from yt_dlp import YoutubeDL
import logging

from src.core.config import DOWNLOAD_PATH, COOKIES_PATH, PROXY_URL
from src.core.exceptions import VideoIsPrivateOrDeletedError, DownloadError

logger = logging.getLogger(__name__)

async def download_with_ytdlp(url: str, proxy: str | None = None, is_audio: bool = False) -> str:
    """
    Downloads a video or audio using yt-dlp.
    Returns the file path on success.
    """
    ffmpeg_path = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
    node_path = shutil.which('node') or shutil.which('deno')

    ydl_opts = {
        'format': 'bestaudio/best' if is_audio else 'bestvideo[filesize<46M]+bestaudio/best[filesize<48M]/best',
        'outtmpl': f'{DOWNLOAD_PATH}/%(id)s.%(ext)s',
        'noplaylist': True,
        'quiet': True,
        'ffmpeg_location': ffmpeg_path,
        'merge_output_format': 'mp4',
        'remote_components': ['ejs:github'],
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios'],
            }
        },
    }

    if node_path:
        ydl_opts['js_runtimes'] = {'node': {'path': node_path}}

    if is_audio:
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]

    if proxy or PROXY_URL:
        ydl_opts['proxy'] = proxy or PROXY_URL

    if Path(COOKIES_PATH).exists() and Path(COOKIES_PATH).stat().st_size > 0:
        ydl_opts['cookiefile'] = COOKIES_PATH

    try:
        loop = asyncio.get_running_loop()
        def _do_download():
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if 'entries' in info and info['entries']:
                    info = info['entries'][0]
                filename = ydl.prepare_filename(info)
                if is_audio:
                    mp3_path = str(Path(filename).with_suffix('.mp3'))
                    if Path(mp3_path).exists():
                        return mp3_path
                mp4_path = str(Path(filename).with_suffix('.mp4'))
                if Path(mp4_path).exists():
                    return mp4_path
                return filename

        return await loop.run_in_executor(None, _do_download)
    except Exception as e:
        error_str = str(e).lower()
        if "private video" in error_str or "video is unavailable" in error_str:
            raise VideoIsPrivateOrDeletedError()
        
        logger.error(f"yt-dlp failed for URL {url}: {e}")
        raise DownloadError(f"فشل تحميل الرابط: {e}")
