# استخدام Python 3.11 slim
FROM python:3.11-slim

# تعيين متغيرات البيئة
ENV PYTHONUNBUFFERED=1

# تعيين مجلد العمل
WORKDIR /app

# تثبيت التبعيات النظامية (ffmpeg + sqlite3)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    sqlite3 \
    libsqlite3-dev \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# نسخ ملف المتطلبات
COPY requirements.txt .

# تثبيت المكتبات Python (بما في ذلك gallery-dl)
RUN pip install --no-cache-dir -r requirements.txt

# نسخ جميع الملفات
COPY . .

# إنشاء مجلد التحميلات
RUN mkdir -p downloads

# تعريف المنفذ
EXPOSE 8080

# تشغيل البوت
CMD ["python", "app.py"]
