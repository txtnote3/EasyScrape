"""Fallback for otherwise unrecognised URLs: yt-dlp first, then any gallery-dl site."""
from __future__ import annotations

from typing import Any

from easyscrape.models import DownloadContext, DownloadError, MediaResult

from . import gallery_dl_adapter, yt_dlp_adapter

PLATFORM = "generic"
MATCHERS = [lambda url: True]
FALLBACK = True
_NOT_A_SITE = {"directlink", "generic", "recursive", "noop", "ytdl"}


def _gallery_category(url: str) -> str | None:
    """Name of the gallery-dl site extractor that claims this URL, if any."""
    try:
        from gallery_dl import extractor

        found: Any = extractor.find(url)
    except Exception:
        return None
    category = getattr(found, "category", None)
    return category if isinstance(category, str) and category not in _NOT_A_SITE else None


def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
    try:
        return yt_dlp_adapter.handle(url, ctx)
    except DownloadError as exc:
        category = None if ctx.audio_only else _gallery_category(url)
        if category is None:
            raise
        previous = ctx.platform
        ctx.platform = category
        try:
            return gallery_dl_adapter.handle(url, ctx)
        except DownloadError as gallery_exc:
            ctx.platform = previous
            raise DownloadError(f"{exc} | gallery-dl ({category}): {gallery_exc}") from gallery_exc


__all__ = ["FALLBACK", "MATCHERS", "PLATFORM", "handle"]
