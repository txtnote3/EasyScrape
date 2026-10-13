"""Shared contracts for extractor plugins and the batch runner."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_filename(value: str, max_length: int = 120) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")
    if value.split(".")[0].rstrip(" ").upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
        *(f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "¹²³"),
    }:
        value = "_" + value
    return value[:max_length].rstrip(" .") or "untitled"


class DownloadError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class RetryableError(DownloadError):
    def __init__(self, message: str):
        super().__init__(message, retryable=True)


@dataclass
class MediaResult:
    url: str
    platform: str
    title: str = ""
    author: str = ""
    id: str = ""
    files: list[Path] = field(default_factory=list)
    date: str = ""
    license_notes: str = ""


@dataclass
class DownloadContext:
    out_dir: Path
    cookies: Path | None = None
    cookies_from_browser: str | None = None
    audio_only: bool = False
    force: bool = False
    quiet: bool = False
    metadata: bool = True
    platform: str = "generic"
    progress_callback: Callable[[float | None, str], None] | None = None
    failures: list[dict[str, str]] = field(default_factory=list)
    skips: list[dict[str, str]] = field(default_factory=list)
    completed: dict[str, Any] = field(default_factory=dict)
    slot_callback: Callable[[str], None] | None = None

    safe_filename = staticmethod(safe_filename)

    def target_dir(self, platform: str | None = None) -> Path:
        target = self.out_dir / safe_filename(platform or self.platform)
        if not target.resolve().is_relative_to(self.out_dir.resolve()):
            raise DownloadError("Platform output directory resolves outside the output root")
        target.mkdir(parents=True, exist_ok=True)
        return target

    def record_failure(self, url: str, message: str, platform: str | None = None) -> None:
        self.invalidate_completed(url)
        self.failures.append(
            {
                "url": url,
                "platform": platform or self.platform,
                "message": message,
                "timestamp": timestamp(),
            }
        )

    def record_skip(self, url: str, message: str, platform: str | None = None) -> None:
        self.skips.append({"url": url, "platform": platform or self.platform, "message": message})

    def is_downloaded(self, url: str) -> bool:
        return not self.force and self._is_complete(url, set())

    def invalidate_completed(self, url: str) -> None:
        """Invalidate this item and dependent collections, preserving siblings."""
        pending = [url]
        while pending:
            child = pending.pop()
            self.completed.pop(child, None)
            for parent, entry in list(self.completed.items()):
                children = entry.get("children", []) if isinstance(entry, dict) else []
                if isinstance(children, list) and child in children:
                    self.completed.pop(parent, None)
                    pending.append(parent)

    def _is_complete(self, url: str, visited: set[str]) -> bool:
        if url in visited:
            return False
        visited = visited | {url}
        entry = self.completed.get(url, {})
        paths = entry.get("files", []) if isinstance(entry, dict) else []
        children = entry.get("children", []) if isinstance(entry, dict) else []
        try:
            return (
                isinstance(paths, list)
                and bool(paths)
                and all(
                    isinstance(p, str)
                    and Path(p).is_file()
                    and Path(p).resolve().is_relative_to(self.out_dir.resolve())
                    for p in paths
                )
                and isinstance(children, list)
                and all(
                    isinstance(child, str) and self._is_complete(child, visited)
                    for child in children
                )
            )
        except (OSError, ValueError):
            return False

    def wait_for_slot(self, platform: str | None = None) -> None:
        """Pace collection child operations through the same platform scheduler."""
        if self.slot_callback:
            self.slot_callback(platform or self.platform)

    throttle = wait_for_slot

    def log(self, message: str) -> None:
        if not self.quiet:
            print(message, file=sys.stderr)


@dataclass
class RunReport:
    successful: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return int(bool(self.failed))
