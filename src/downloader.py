# src/downloader.py
import asyncio
import os
import re
import uuid
import logging
from typing import Optional, Tuple

class Downloader:
    """أداة تحميل تستخدم yt-dlp مع بروكسي لتجاوز الحظر."""

    async def _run_command(self, command: str) -> Tuple[bool, str, str]:
        """تشغيل أمر في الـ shell بشكل غير متزامن."""
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            return True, stdout.decode('utf-8', errors='ignore'), stderr.decode('utf-8', errors='ignore')
        else:
            logging.error(f"فشل الأمر. الخطأ: {stderr.decode('utf-8', errors='ignore')}")
            return False, stdout.decode('utf-8', errors='ignore'), stderr.decode('utf-8', errors='ignore')

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
        تحميل الفيديو أو الصوت باستخدام yt-dlp مع بروكسي.
        """
        download_id = str(uuid.uuid4())
        temp_dir = "temp_downloads"
        os.makedirs(temp_dir, exist_ok=True)
        output_template = os.path.join(temp_dir, f"{download_id}.%(ext)s")
        
        # === السلاح الجديد: استخدام بروكسي ===
        # هذا بروكسي مجاني كمثال، قد يتوقف عن العمل ولكن يمكننا إيجاد غيره
        proxy = "http://14.225.255.61:80" 
        
        # خيارات إضافية للتحايل على حماية بعض المواقع
        extra_opts = f"--proxy {proxy} --no-check-certificate --add-header 'User-Agent: Mozilla/5.0'"

        if to_mp3:
            command = f'yt-dlp {extra_opts} -x --audio-format mp3 -o "{output_template}" "{url}"'
        else:
            command = f'yt-dlp {extra_opts} -f "bestvideo[filesize<=50M]+bestaudio/best[filesize<=50M]/best" --merge-output-format mp4 -o "{output_template}" "{url}"'

        logging.info(f"بدء التحميل بالأمر: {command}")
        success, stdout, stderr = await self._run_command(command)

        if not success:
            logging.error(f"فشل التحميل من الرابط {url}.")
            return None

        try:
            created_files = [f for f in os.listdir(temp_dir) if f.startswith(download_id)]
            if not created_files:
                logging.error("لم يتم العثور على الملف المحمل بعد انتهاء الأمر.")
                return None
            
            filepath = os.path.join(temp_dir, created_files[0])
            title = "media"
            title_search = re.search(r'\[info\]\s+(.*?):\s+Downloading webpage', stdout, re.IGNORECASE)
            if title_search:
                title = title_search.group(1).strip()
            
            return filepath, title
        except Exception as e:
            logging.error(f"خطأ في معالجة مخرجات yt-dlp: {e}\nstdout: {stdout}")
            return None
