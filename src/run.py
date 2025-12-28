# run.py
import sys
import os

# هذا السطر مهم جدًا ليتمكن بايثون من إيجاد الملفات داخل مجلد src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

# الآن نستورد دالة التشغيل من src/bot.py
from bot import run

if __name__ == "__main__":
    print("بدء تشغيل البوت...")
    run()
