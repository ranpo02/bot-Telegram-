 its own command.
    logging.info(f"Starting Flask server for local testing...")
    app(host='0.0.0.0', port=config.PORT)
# src/main.py
import logging
import threading
from flask import Flask
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler

from src.core import config
from src.bot import handlers

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger("httpx").setLevel(logging.WARNING)

# --- Telegram Bot Setup ---
def run_bot():
    """Initializes and runs the Telegram bot in a separate thread."""
    application = Application.builder().token(config.BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_link))
    application.add_handler(CallbackQueryHandler(handlers.button_handler))

    logging.info("Starting Telegram bot polling...")
    application.run_polling()

# --- Start the Bot Thread ---
# This code now runs as soon as the module is imported by Gunicorn
logging.info("Setting up bot thread...")
bot_thread = threading.Thread(target=run_bot)
bot_thread.daemon = True
bot_thread.start()
logging.info("Bot thread started.")

# --- Flask App for Render Health Check ---
# Gunicorn will look for this 'app' variable
app = Flask(__name__)

@app.route('/health')
def health_check():
    """Endpoint for Render's health check to keep the service alive."""
    return "OK", 200

# The 'if __name__ == '__main__':' block is no longer needed
# as Gunicorn is the entry point in production.
