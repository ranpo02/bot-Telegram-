# src/run.py
from bot import main
import asyncio

if __name__ == "__main__":
    print("بدء تشغيل البوت...")
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("تم إيقاف البوت.")
