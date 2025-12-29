# src/bot/handlers.py
# FINAL CORRECTED VERSION

import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes

# --- CORRECTED RELATIVE IMPORTS ---
# This is the fix. We are using dots to indicate relative paths.
from . import ui
from ..core import config
from ..core.exceptions import DownloadError
from ..downloader import engine
from ..utils import cleanup_file, is_valid_url
# --- END OF FIX ---


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the /start command is issued."""
    user_name = update.effective_user.first_name
    await update.message.reply_html(
        text=ui.get_start_message(user_name),
        reply_markup=ui.get_main_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a help message when the /help command is issued."""
    await update.message.reply_html(
        text=ui.HELP_MESSAGE,
        reply_markup=ui.get_main_keyboard()
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming messages containing a video link."""
    url = update.message.text
    if not is_valid_url(url):
        await update.message.reply_text(ui.INVALID_URL_MESSAGE)
        return

    # Acknowledge receipt and show "processing" message
    processing_message = await update.message.reply_text(ui.PROCESSING_MESSAGE)

    try:
        # --- Download Process ---
        logging.info(f"Starting download for URL: {url}")
        video_path, video_title = await engine.download_video(url)
        logging.info(f"Download complete. Video saved at: {video_path}")

        # --- Upload Process ---
        await processing_message.edit_text(ui.UPLOADING_MESSAGE)
        logging.info(f"Uploading video to Telegram: {video_path}")

        with open(video_path, 'rb') as video_file:
            await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=video_file,
                caption=ui.get_video_caption(video_title, url),
                supports_streaming=True
            )

        # Delete the temporary message
        await processing_message.delete()
        logging.info("Upload successful. Temporary message deleted.")

    except DownloadError as e:
        logging.error(f"A download error occurred for URL {url}: {e}")
        await processing_message.edit_text(f"❌ حدث خطأ أثناء التحميل:\n\n{e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred for URL {url}: {e}", exc_info=True)
        await processing_message.edit_text(ui.GENERIC_ERROR_MESSAGE)
    finally:
        # --- Cleanup ---
        if 'video_path' in locals() and video_path:
            # Run cleanup in the background to not block the user
            asyncio.create_task(cleanup_file(video_path))
            logging.info(f"Scheduled cleanup for: {video_path}")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles inline keyboard button presses."""
    query = update.callback_query
    await query.answer()  # Acknowledge the button press

    if query.data == 'show_help':
        await query.edit_message_text(
            text=ui.HELP_MESSAGE,
            reply_markup=ui.get_main_keyboard()
        )
    # Add more button handlers here if needed in the future
    # elif query.data == 'another_button':
    #     await query.edit_message_text(text="You pressed another button!")

