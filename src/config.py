# src/config.py

import os
from dotenv import load_dotenv

# تحميل متغيرات البيئة من ملف .env (مفيد للتطوير المحلي)
load_dotenv()

# --- توكن البوت والمعرفات ---
# استخدمت القيم التي أرسلتها كقيم افتراضية تجريبية
# في بيئة الإنتاج (Render), سيتم استخدام المتغيرات التي تضعها هناك
BOT_TOKEN = os.getenv("BOT_TOKEN", "6689824298:AAFB9_iLrYK3DTecls9GQCFAa5idgsEFROo")
ADMIN_ID = os.getenv("ADMIN_ID", "5898628858")

# --- إعدادات Redis ---
REDIS_URL = os.getenv("REDIS_URL")
# إذا لم يتم توفير رابط Redis, سنقوم بتعطيل الميزات التي تعتمد عليه
REDIS_ENABLED = REDIS_URL is not None

# --- إعدادات البوت ---
USER_THROTTLE_LIMIT = 5  # أقصى عدد طلبات للمستخدم الواحد
USER_THROTTLE_PERIOD = 60  # خلال 60 ثانية

# --- إعدادات التحميل ---
DOWNLOAD_PATH = "downloads" # مجلد لتخزين الملفات المحملة مؤقتًا

# --- قائمة البروكس
