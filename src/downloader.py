# src/downloader.py
import asyncio
import os
import re
import uuid
import logging
from typing import Optional, Tuple

class Downloader:
    """أداة تحميل تستخدم قائمة من البروكسيات لتجاوز الحظر."""

    # === قائمة البروكسيات الناجحة التي وجدتها ===
    WORKING_PROXIES = [
        "http://157.66.3.34:1111",
        "http://103.155.167.62:8080",
        "http://41.254.48.192:1978",
        "http://47.81.14.7:3129",
        "http://80.85.247.161:5555" # هذا البروكسي نجح في الاتصال ولكنه غير مستقر، نجعله آخر خيار
    ]

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
            # لا نطبع الخطأ هنا، لأننا سنتعامل معه في الدالة الرئيسية
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
        تجربة التحميل باستخدام كل بروكسي في القائمة حتى ينجح.
        """
        download_id = str(uuid.uuid4())
        temp_dir = "temp_downloads"
        os.makedirs(temp_dir, exist_ok=True)
        output_template = os.path.join(temp_dir, f"{download_id}.%(ext)s")
        
        # المرور على كل بروكسي في القائمة وتجربته
        for i, proxy in enumerate(self.WORKING_PROXIES):
            logging.info(f"محاولة التحميل (المحاولة {i+1}/{len(self.WORKING_PROXIES)}) باستخدام البروكسي: {proxy}")
            
            extra_opts = f"--proxy {proxy} --no-check-certificate --add-header 'User-Agent: Mozilla/5.0'"

            if to_mp3:
                command = f'yt-dlp {extra_opts} -x --audio-format mp3 -o "{output_template}" "{url}"'
            else:
                command = f'yt-dlp {extra_opts} -f "bestvideo[filesize<=50M]+bestaudio/best[filesize<=50M]/best" --merge-output-format mp4 -o "{output_template}" "{url}"'

            success, stdout, stderr = await self._run_command(command)

            if success:
                logging.info(f"نجح التحميل باستخدام البروكسي: {proxy}")
                try:
                    created_files = [f for f in os.listdir(temp_dir) if f.startswith(download_id)]
                    if not created_files:
                        continue # إذا نجح الأمر ولكن لم يتم إنشاء ملف، جرب البروكسي التالي
                    
                    filepath = os.path.join(temp_dir, created_files[0])
                    title = "media"
                    title_search = re.search(r'\[info\]\s+(.*?):\s+Downloading webpage', stdout, re.IGNORECASE)
                    if title_search:
                        title = title_search.group(1).strip()
                    
                    return filepath, title # إرجاع النتيجة فورًا عند النجاح
                except Exception as e:
                    logging.error(f"خطأ بعد نجاح التحميل: {e}")
                    continue # جرب البروكسي التالي
            else:
                # طباعة الخطأ هنا لمعرفة سبب فشل البروكسي
                logging.warning(f"فشل البروكسي {proxy}. الخطأ: {stderr.strip().splitlines()[-1]}")

        # إذا فشلت كل البروكسيات
        logging.error("فشلت كل البروكسيات في القائمة.")
        return None
