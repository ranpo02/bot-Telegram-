# src/bot/ui.py
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# --- Message Templates ---
WELCOME_MESSAGE = """
أهلاً بك في بوت التحميل الأسطوري! ✨

أرسل لي أي رابط من المنصات المدعومة وسأقوم بتحميله لك بأقصى سرعة.

**الميزات:**
- 🚀 سرعة فائقة في التحميل.
- 🎬 دعم يوتيوب، تيك توك، انستغرام، والمزيد.
- 💎 واجهة سهلة وبسيطة.

ابدأ الآن بإرسال رابط!
"""

HELP_MESSAGE = """
**كيفية الاستخدام:**
ببساطة، أرسل رابط الفيديو الذي تريد تحميله.

**ماذا لو فشل التحميل؟**
قد يكون الفيديو خاصاً، محذوفاً، أو أن المنصة قامت بتغيير ما. حاول مجدداً بعد فترة.

**للحصول على أفضل أداء، تأكد من أن الرابط عام.**
"""

# --- Inline Keyboards ---
def get_start_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📚 المنصات المدعومة", callback_data="supported_platforms")],
        [InlineKeyboardButton("شارك البوت 📤", switch_inline_query="")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_success_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("تحميل فيديو آخر", callback_data="new_download")],
        [InlineKeyboardButton("شارك البوت 📤", switch_inline_query="")]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Dynamic Messages ---
def get_status_message(status: str) -> str:
    messages = {
        "analyzing": "[⏳] جاري تحليل الرابط...",
        "downloading": "[⚡] تم التعرف على الفيديو، جاري التحميل بأقصى سرعة...",
        "success": "[✅] تم التحميل بنجاح!",
    }
    return messages.get(status, "جاري المعالجة...")
