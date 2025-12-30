# 1. ابدأ من صورة بايثون رسمية
FROM python:3.11-slim

# 2. قم بتثبيت ffmpeg (هذا هو سبب استخدامنا لـ Docker)
RUN apt-get update && apt-get install -y ffmpeg --no-install-recommends

# 3. جهز مجلد العمل
WORKDIR /app

# 4. انسخ ملف المتطلبات فقط
COPY requirements.txt .

# 5. قم بتثبيت مكتبات بايثون (مع إضافة instaloader)
RUN pip install --no-cache-dir -r requirements.txt instaloader

# 6. انسخ باقي ملفات المشروع
COPY . .

# 7. حدد الأمر لتشغيل البوت
CMD ["python", "app.py"]
