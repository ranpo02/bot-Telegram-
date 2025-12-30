# 1. ابدأ من صورة بايثون رسمية
FROM python:3.11-slim

# 2. قم بتثبيت الأدوات الأساسية و ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git

# 3. جهز مجلد العمل
WORKDIR /app

# 4. انسخ ملف المتطلبات فقط
COPY requirements.txt .

# 5. قم بتثبيت مكتبات بايثون
RUN pip install --no-cache-dir -r requirements.txt

# 6. قم بتثبيت gallery-dl
# نستخدم pip لتثبيت أحدث إصدار لضمان أفضل توافق
RUN pip install --no-cache-dir gallery-dl

# 7. انسخ باقي ملفات المشروع
COPY . .

# 8. حدد الأمر لتشغيل البوت
CMD ["python", "app.py"]
