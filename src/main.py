# src/main.py
import logging
import threading
from flask import Flask
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler

from .core import config
from .bot import handlers

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger("httpx").setLevel(logging.WARNING)

app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running. Use the Telegram bot directly.", 200

@app.route('/health')
def health_check():
    return "OK", 200

def run_web_server():
    app.run(host='0.0.0.0', port=config.PORT)

def main():
    if not config.BOT_TOKEN:
        logging.error("BOT_TOKEN is not set.")
        return

    application = Application.builder().token(config.BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_link))
    application.add_handler(CallbackQueryHandler(handlers.button_handler))

    logging.info("Starting Telegram bot polling...")
    application.run_polling()

if __name__ == '__main__':
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logging.info(f"Health check web server started on port {config.PORT}.")
    main()
