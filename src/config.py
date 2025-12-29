# src/config.py

import os
from dotenv import load_dotenv
import asyncio # استيراد asyncio

# تحميل متغيرات البيئة من ملف .env (مفيد للتطوير المحلي)
load_dotenv()

# --- توكن البوت والمعرفات ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

# --- إعدادات Redis ---
REDIS_URL = os.getenv("REDIS_URL")
REDIS_ENABLED = REDIS_URL is not None

# --- إعدادات البوت ---
USER_THROTTLE_LIMIT = 5
USER_THROTTLE_PERIOD = 60

# --- إعدادات التحميل ---
DOWNLOAD_PATH = "downloads"

# --- قائمة البروكسيات ---
# قائمة البروكسيات التي سيتم تدويرها.
PROXY_LIST = [
    "http://154.3.236.202:3128",
    "http://167.206.113.248:3128",
    "http://115.114.77.133:9090",
    "http://80.85.247.161:5555",
]

# --- متغيرات التحكم بالبروكسي ---
# نستخدم قفل لضمان عدم حدوث تضارب عند الوصول للمتغير من عدة مهام متزامنة
PROXY_INDEX_LOCK = asyncio.Lock()
CURRENT_PROXY_INDEX = 0

# --- التحقق من الإعدادات الأساسية ---
if not BOT_TOKEN:
    raise ValueError("خطأ: لم يتم العثور على BOT_TOKEN. يرجى إضافته إلى متغيرات البيئة.")

os.makedirs(DOWNLOAD_PATH, exist_ok=True)
