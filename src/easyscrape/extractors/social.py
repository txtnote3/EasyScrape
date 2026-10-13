"""Social media routing; extraction remains with upstream downloader libraries."""
from __future__ import annotations

from urllib.parse import urlsplit

from easyscrape.models import DownloadContext, DownloadError, MediaResult

from . import gallery_dl_adapter, yt_dlp_adapter

PLATFORM = "social"
MATCHERS = [
    r"(?i)^https?://(?:[a-z0-9-]+\.)?(?:instagram\.com|facebook\.com|fb\.watch|"
    r"twitter\.com|x\.com|reddit\.com|redd\.it|pinterest\.com|pin\.it)(?::\d+)?(?:[/?#]|$)"
]
_PLATFORMS = {
    "instagram.com": "instagram", "facebook.com": "facebook", "fb.watch": "facebook",
    "twitter.com": "twitter", "x.com": "twitter", "reddit.com": "reddit",
    "redd.it": "reddit", "pinterest.com": "pinterest", "pin.it": "pinterest",
}


def platform_for(url: str) -> str:
    hostname = (urlsplit(url).hostname or "").lower()
    for domain, platform in _PLATFORMS.items():
        if hostname == domain or hostname.endswith("." + domain):
            return platform
    raise DownloadError("Unsupported social media host.")


def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
    platform = platform_for(url)
    ctx.platform = platform
    gallery_dl_adapter.require_cookies(ctx, platform)
    path = urlsplit(url).path.lower()
    hostname = (urlsplit(url).hostname or "").lower()
    explicit_video = (
        ctx.audio_only
        or (platform == "instagram" and path.startswith(("/reel/", "/reels/", "/tv/")))
        or (platform == "facebook" and (
            hostname == "fb.watch" or "/videos/" in path
            or path.startswith(("/watch", "/reel/", "/video.php"))
        ))
        or (platform == "twitter" and "/video/" in path)
        or (platform == "reddit" and hostname == "v.redd.it")
    )
    if explicit_video:
        try:
            return yt_dlp_adapter.handle(url, ctx)
        except yt_dlp_adapter.NoMediaError:
            if ctx.audio_only:
                raise
            return gallery_dl_adapter.handle(url, ctx)
    # Gallery-first handles mixed image/video posts without losing carousel images.
    return gallery_dl_adapter.handle(url, ctx)
