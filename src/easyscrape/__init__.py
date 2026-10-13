"""EasyScrape: adapter-based batch media downloads."""

from .models import DownloadContext, DownloadError, MediaResult, RetryableError

__version__ = "0.1.0"
__all__ = ["DownloadContext", "DownloadError", "MediaResult", "RetryableError"]
