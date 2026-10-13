"""Delegate Threads to gallery-dl; report unavailable upstream support honestly."""
from __future__ import annotations

from easyscrape.models import DownloadContext, MediaResult

from . import gallery_dl_adapter

PLATFORM = "threads"
MATCHERS = [r"(?i)^https?://(?:[a-z0-9-]+\.)?threads\.(?:net|com)(?::\d+)?(?:[/?#]|$)"]


def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
    ctx.platform = PLATFORM
    return gallery_dl_adapter.handle(url, ctx)
