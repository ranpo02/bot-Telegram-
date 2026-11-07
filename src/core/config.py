# src/core/config.py
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

PORT = int(os.getenv("PORT", 8080))
ADMIN_ID = os.getenv("ADMIN_ID", "5898628858")
PROXY_URL = os.getenv("PROXY_URL", "").strip() or None

USER_THROTTLE_SECONDS = 10

DOWNLOAD_PATH = "downloads"
os.makedirs(DOWNLOAD_PATH, exist_ok=True)
COOKIES_PATH = "cookies.txt"
