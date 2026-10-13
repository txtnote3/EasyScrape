"""Last-resort yt-dlp extraction for otherwise unrecognised URLs."""

from .yt_dlp_adapter import handle

PLATFORM = "generic"
MATCHERS = [lambda url: True]
FALLBACK = True

__all__ = ["FALLBACK", "MATCHERS", "PLATFORM", "handle"]
