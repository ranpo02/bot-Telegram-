# src/config.py
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("لم يتم العثور على BOT_TOKEN! الرجاء إضافته إلى ملف .env")
