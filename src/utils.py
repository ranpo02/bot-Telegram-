# src/utils.py
import logging
import re

URL_REGEX = r'https?://[^\s/$.?#].[^\s]*'

def setup_logger():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("bot_activity.log"),
            logging.StreamHandler()
        ]
    )

def find_url_in_text(text: str) -> str | None:
    match = re.search(URL_REGEX, text)
    return match.group(0) if match else None
