# src/main.py
# FINAL CORRECTED VERSION

import logging
import threading
from flask import Flask
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler

# --- Corrected Relative Imports ---
# This is the key change. We are now using relative imports
# that work correctly when Gunicorn runs from the root directory.
from .core import config
from .bot import handlers

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger("httpx").setLevel(logging.WARNING)

# --- Telegram Bot Setup ---
def run_bot():
    """Initializes and runs the Telegram bot in a separate thread."""
    try:
        application = Application.builder().token(config.BOT_TOKEN).build()

        # Add handlers
        application.add_handler(CommandHandler("start", handlers.start))
        application.add_handler(CommandHandler("help", handlers.help_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_link))
        application.add_handler(CallbackQueryHandler(handlers.button_handler))

        logging.info("Starting Telegram bot polling...")
        application.run_polling()
    except Exception as e:
        logging.error(f"FATAL: Failed to start bot polling: {e}", exc_info=True)


# --- Start the Bot Thread ---
# This code runs as soon as the module is imported by Gunicorn.
logging.info("Setting up bot thread...")
bot_thread = threading.Thread(target=run_bot)
bot_thread.daemon = True
bot_thread.start()
logging.info("Bot thread started.")


# --- Flask App for Render Health Check ---
# Gunicorn will look for this 'app' variable.
app = Flask(__name__)

@app.route('/')
def index():
    """A simple index page to show the service is alive."""
    return "Bot is running. Use the Telegram bot directly.", 200

@app.route('/health')
def health_check():
    """Endpoint for Render's health check to keep the service alive."""
    return "OK", 200

