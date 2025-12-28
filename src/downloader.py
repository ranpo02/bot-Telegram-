None
# src/downloader.py
import asyncio
import os
import re
import uuid
import logging
from typing import Optional, Tuple
from pytube import YouTube

class Downloader:
    """أداة تحميل ذكية تستخدم pytube ليوتيوب و yt-dlp للباقي."""

    async def _run_yt_dlp(self, url: str, to_mp3: bool, output_template: str) -> bool:
        """تشغيل yt-dlp لتحميل المحتوى."""
        extra_opts = "--no-check-certificate --add-header 'User-Agent: Mozilla/5.0'"
        if to_mp3:
            command = f'yt-dlp {extra_opts} -x --audio-format mp3 -o "{output_template}" "{url}"'
        else:
            command = f'yt-dlp {extra_opts} -f "bestvideo[filesize<=50M]+bestaudio/best[filesize<=50M]/best" --merge-output-format mp4 -o "{output_template}" "{url}"'
        
        logging.info(f"بدء التحميل بـ yt-dlp: {command}")
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            return True
        else:
            logging.error(f"فشل yt-dlp. الخطأ: {stderr.decode('utf-8', errors='ignore')}")
            return False

    def _download_pytube(self, url: str, to_mp3: bool, output_path: str, filename: str) -> Optional[str]:
        """تشغيل pytube لتحميل محتوى يوتيوب (يعمل بشكل متزامن)."""
        try:
            logging.info(f"بدء التحميل بـ pytube للرابط: {url}")
            yt = YouTube(url)
            
            if to_mp3:
                stream = yt.streams.get_audio_only()
            else:
                stream = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc().first()

            if not stream:
                logging.error("لم يتم العثور على ستريم مناسب باستخدام pytube.")
                return None
            
            return stream.download(output_path=output_path, filename=filename)
        except Exception as e:
            logging.error(f"فشل pytube. الخطأ: {e}")
            return None

    def _cleanup(self, file_path: str):
        """حذف الملف بعد إرساله."""
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logging.info(f"تم حذف الملف: {file_path}")
            except OSError as e:
                logging.error(f"خطأ أثناء حذف الملف {file_path}: {e}")

    async def download_media(self, url: str, to_mp3: bool = False) -> Optional[Tuple[str, str]]:
        """
        تحميل الفيديو أو الصوت باستخدام الأداة المناسبة.
        """
        download_id = str(uuid.uuid4())
        temp_dir = "temp_downloads"
        os.makedirs(temp_dir, exist_ok=True)
        
        is_youtube = 'youtube.com' in url.lower() or 'youtu.be' in url.lower()
        
        filepath = None
        title = "media"

        if is_youtube:
            # استخدام pytube ليوتيوب
            filename_base = f"{download_id}"
            # Pytube يعمل بشكل متزامن، لذا نستخدم run_in_executor لتجنب حظر الكود
            loop = asyncio.get_running_loop()
            filepath = await loop.run_in_executor(
                None, self._download_pytube, url, to_mp3, temp_dir, f"{filename_base}.mp4"
            )
            if filepath:
                yt = YouTube(url)
                title = yt.title
        
        # إذا فشل pytube أو إذا لم يكن الرابط من يوتيوب، استخدم yt-dlp
        if not filepath:
            output_template = os.path.join(temp_dir, f"{download_id}.%(ext)s")
            success = await self._run_yt_dlp(url, to_mp3, output_template)
            if success:
                try:
                    created_files = [f for f in os.listdir(temp_dir) if f.startswith(download_id)]
                    if created_files:
                        filepath = os.path.join(temp_dir, created_files[0])
                except Exception as e:
                    logging.error(f"خطأ في العثور على ملف yt-dlp: {e}")
                    return None

        if filepath and os.path.exists(filepath):
            return filepath, title
        else:
            logging.error(f"فشل التحميل النهائي للرابط {url}.")
            return None

