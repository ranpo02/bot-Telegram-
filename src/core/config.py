# src/core/config.py
import os
from dotenv import load_dotenv

# Load environment variables from .env file for local development
load_dotenv()

# --- Telegram Bot ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in the environment variables.")

# --- Web Server ---
# Render provides the PORT environment variable
PORT = int(os.getenv("PORT", 10000))

# --- Redis (Caching & Throttling) ---
REDIS_URL = os.getenv("REDIS_URL")
REDIS_ENABLED = bool(REDIS_URL)

# --- User Experience ---
USER_THROTTLE_SECONDS = 30  # Cooldown period for each user

# --- Downloader ---
DOWNLOAD_PATH = "downloads"
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# --- YouTube Proxies ---
YOUTUBE_PROXIES = [
    "http://154.3.236.202:3128",
    "http://167.206.113.248:3128",
    "http://115.114.77.133:9090",
    "http://80.85.247.161:5555",
]
