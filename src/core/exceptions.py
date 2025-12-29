# src/core/exceptions.py

class DownloadError(Exception):
    """Custom exception for download failures."""
    def __init__(self, message="فشل التحميل"):
        self.message = message
        super().__init__(self.message)

class AllProxiesFailedError(DownloadError):
    """Raised when all YouTube proxies fail."""
    def __init__(self, message="خوادمنا المخصصة ليوتيوب مضغوطة حالياً. حاول مجدداً بعد قليل."):
        super().__init__(message)

class VideoIsPrivateOrDeletedError(DownloadError):
    """Raised when the video is private or has been deleted."""
    def __init__(self, message="لا يمكن الوصول لهذا المحتوى. قد يكون الفيديو خاصاً أو تم حذفه."):
        super().__init__(message)
