# src/utils.py
import os
import logging

logger = logging.getLogger(__name__)

async def cleanup_file(file_path: str):
    """Asynchronously deletes a file."""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up temporary file: {file_path}")
    except Exception as e:
        logger.error(f"Error cleaning up file {file_path}: {e}")

def is_valid_url(url: str) -> bool:
    """Simple check to see if a string is a valid URL."""
    if not isinstance(url, str):
        return False
    return url.startswith("http://") or url.startswith("https://")
