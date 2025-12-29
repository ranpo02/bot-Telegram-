# src/utils.py

import os
import logging

# 1. إعداد مسجل الأحداث (Logger)
# هذا هو الجزء الأهم: نقوم بإعداد الـ logger هنا
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# 2. إنشاء متغير logger يمكن استيراده من الملفات الأخرى
# هذا هو السطر الذي كان مفقودًا أو غير صحيح
logger = logging.getLogger(__name__)

# 3. بقية الدوال المساعدة تبقى كما هي
async def cleanup_file(file_path: str):
    """دالة غير متزامنة لحذف ملف بعد استخدامه."""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"تم حذف الملف المؤقت: {file_path}")
    except Exception as e:
        logger.error(f"خطأ أثناء حذف الملف {file_path}: {e}")

def is_valid_url(url: str) -> bool:
    """تحقق بسيط من أن النص هو رابط صالح."""
    if not isinstance(url, str):
        return False
    return url.startswith("http://") or url.startswith("https://")
