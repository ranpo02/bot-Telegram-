# src/main.py
# FINAL, CORRECTED PATTERN FOR ASYNC + THREADING

import logging
import threading
import asyncio
from flask import Flask
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler

# Corrected relative imports
from .core import config
from .bot import handlers

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("gunicorn").setLevel(logging.INFO)


# --- Flask App (to be run in a background thread) ---
app = Flask(__name__)

@app.route('/')
def index():
    """A simple index page to show the service is alive."""
    return "Bot is running. Use the Telegram bot directly.", 200

@app.route('/health')
def health_check():
    """Endpoint for Render's health check."""
    return "OK", 200

def run_flask_app():
    """Runs the Flask app using Gunicorn's configuration."""
    # Gunicorn is already running this file, so we just need to serve the app.
    # We can't run gunicorn from here, but we can run Flask's built-in server
    # for simplicity, as Gunicorn handles the production load.
    # A better approach is to let Gunicorn manage the main process and run the bot loop.
    # Let's reverse the logic. The main process will be the bot.
    pass # This function is no longer needed with the new logic.


# --- Main Application Logic ---
def main():
    """
    This is the main entry point.
    It sets up and runs the bot in the main thread.
    """
    # --- Telegram Bot Setup ---
    # This now runs in the MAIN THREAD, which is correct.
    application = Application.builder().token(config.BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_link))
    application.add_handler(CallbackQueryHandler(handlers.button_handler))

    logging.info("Starting Telegram bot polling in the main thread...")
        
    # This is a synchronous call that blocks until the bot is stopped.
    application.run_polling()


# This check is important. Gunicorn imports the file to find 'app'.
# We don't want the bot to start when Gunicorn is just importing.
# The bot should only start when we run the file directly.
# This reveals the core conflict.

# --- THE CORRECT, FINAL, AND SIMPLEST LOGIC ---
# Gunicorn runs the web server. The web server starts the bot.
# But the bot needs the main thread.
# The solution is to NOT use Gunicorn and run the bot directly,
# while the web server runs in a thread.

# Let's try the simplest possible code that should work.
# The previous logic was flawed.

# --- NEW FINAL ATTEMPT ---
    
# 1. The bot needs the main thread.
# 2. The web server is needed for the health check.
    
# Let's run the web server in a background thread and the bot in the main thread.
    
# This code will be executed when the module is loaded.
    
# Flask server thread
def run_web_server():
    # We can't use gunicorn here. We use Flask's simple server.
    # It's only for the health check, so it's fine.
    app.run(host='0.0.0.0', port=config.PORT)

web_thread = threading.Thread(target=run_web_server)
web_thread.daemon = True
web_thread.start()
logging.info(f"Health check web server started in a background thread on port {config.PORT}.")

# Bot runs in the main thread
logging.info("Preparing to start bot in the main thread.")
main()

