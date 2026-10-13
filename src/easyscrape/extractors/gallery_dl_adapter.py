"""Download galleries through gallery-dl's Python API without global option leakage."""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from gallery_dl import config, exception, job
from gallery_dl import cookies as gallery_cookies
from gallery_dl.extractor import common

from easyscrape.models import DownloadContext, DownloadError, MediaResult, RetryableError

_LOCK = threading.RLock()
_AUTH_PLATFORMS = {"instagram", "facebook", "threads"}
_LICENSE = "Source metadata does not grant reuse rights; respect the creator's license."


def require_cookies(ctx: DownloadContext, platform: str) -> None:
    if platform in _AUTH_PLATFORMS and not (ctx.cookies or ctx.cookies_from_browser):
        raise DownloadError(
            f"{platform} requires login cookies. Use --cookies with a Netscape cookie file "
            "or --cookies-from-browser with a logged-in browser."
        )


def browser_spec(value: str) -> tuple[str, str, str, str, str]:
    """Match gallery-dl's BROWSER[/DOMAIN][+KEYRING][:PROFILE][::CONTAINER] parser."""
    browser, _, profile = value.partition(":")
    browser, _, keyring = browser.partition("+")
    browser, _, domain = browser.partition("/")
    if profile.startswith(":"):
        container, profile = profile[1:], ""
    else:
        profile, _, container = profile.partition("::")
    return browser, profile, keyring, container, domain


@contextmanager
def _isolated_config(options: dict[str, Any]) -> Iterator[None]:
    # gallery-dl binds this dictionary in default arguments, so never replace it.
    # A fresh tree prevents site-specific options from a previous invocation winning.
    with _LOCK:
        previous = config._config.copy()
        cookie_cache = common.CACHE_COOKIES.copy()
        memory_cache = common.CACHE_MEMORY.copy()
        config._config.clear()
        common.CACHE_COOKIES.clear()
        common.CACHE_MEMORY.clear()
        try:
            config._config.update(options)
            yield
        finally:
            config._config.clear()
            config._config.update(previous)
            common.CACHE_COOKIES.clear()
            common.CACHE_COOKIES.update(cookie_cache)
            common.CACHE_MEMORY.clear()
            common.CACHE_MEMORY.update(memory_cache)


def _first(data: dict[str, Any], keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            if isinstance(value, dict):
                return _first(value, ("username", "screen_name", "name", "id"), default)
            return str(value)
    return default


def _identity(data: dict[str, Any], url: str) -> tuple[str, str]:
    author = _first(data, ("user", "author", "username", "uploader", "owner"), "unknown")
    identifier = _first(
        data, ("tweet_id", "post_id", "shortcode", "pin_id", "set_id", "id"),
        hashlib.sha256(url.encode()).hexdigest()[:16],
    )
    return author, identifier


class _Errors(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
    """Return only files reported by the job and actually present on disk."""
    platform = ctx.platform
    require_cookies(ctx, platform)
    if ctx.audio_only:
        raise DownloadError("Audio-only downloads require a video URL supported by yt-dlp.")
    root = ctx.out_dir.resolve()
    base = ctx.target_dir(platform).resolve()

    def contained(path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise DownloadError("gallery-dl output path resolves outside the output root")
        return resolved

    contained(base)
    options: dict[str, Any] = {
        "extractor": {
            "base-directory": str(base),
            "directory": ["{_es_author}", "{_es_id}"],
            "parent-directory": False,
            "skip": not ctx.force,
            "cookies-update": False,
            "verify": True,
            "retries": 0,
            "sleep-request": 1.5,
        },
        "downloader": {
            "retries": 0,
            "ytdl": {
                "module": "yt_dlp",
                "forward-cookies": True,
                # Without this upstream mutates YoutubeDL's class methods globally.
                "deprecations": True,
                "raw-options": {
                    "retries": 0,
                    "fragment_retries": 0,
                    "extractor_retries": 0,
                    "file_access_retries": 0,
                    "ignoreerrors": False,
                    "quiet": ctx.quiet,
                    "noprogress": ctx.quiet,
                },
            },
        },
        "output": {"mode": "null" if ctx.quiet else "terminal"},
    }
    if ctx.cookies:
        try:
            MozillaCookieJar(str(ctx.cookies)).load(ignore_discard=True, ignore_expires=True)
        except (OSError, ValueError) as exc:
            raise DownloadError(
                "Cannot load the Netscape cookie file; check its path and format."
            ) from exc
        options["extractor"]["cookies"] = str(ctx.cookies.resolve())
    elif ctx.cookies_from_browser:
        options["extractor"]["cookies"] = browser_spec(ctx.cookies_from_browser)

    results: dict[tuple[str, str], MediaResult] = {}
    errors = _Errors()

    class RecordingJob(job.DownloadJob):  # type: ignore[misc]
        def handle_directory(self, kwdict: dict[str, Any]) -> None:
            author, identifier = _identity(kwdict, url)
            kwdict["_es_author"] = ctx.safe_filename(author)
            kwdict["_es_id"] = ctx.safe_filename(identifier)
            contained(base / kwdict["_es_author"] / kwdict["_es_id"])
            super().handle_directory(kwdict)

        def download(self, media_url: str) -> bool:
            # Check exact computed filenames before the upstream downloader opens them.
            for value in (self.pathfmt.realpath, self.pathfmt.temppath):
                if value:
                    contained(Path(value))
            ctx.wait_for_slot(platform)
            succeeded = bool(super().download(media_url))
            self._es_failed = not succeeded
            return succeeded

        def handle_url(self, media_url: str, kwdict: dict[str, Any]) -> None:
            self._es_failed = False
            # All upstream writes (including late .part and format sidecars) use
            # private staging. Existing final files are checked before downloading.
            existing = None
            if self.pathfmt is not None and kwdict.get("extension"):
                relative_dir = Path(self.pathfmt.realdirectory).relative_to(staging)
                candidate = contained(
                    base / relative_dir / self.pathfmt.build_filename(kwdict)
                )
                if candidate.is_file() and not ctx.force:
                    existing = candidate
                    self.pathfmt.set_filename(kwdict)
            if existing is None:
                super().handle_url(media_url, kwdict)
            pathfmt = self.pathfmt
            if pathfmt is None or self._es_failed:
                return
            filepath = existing or contained(Path(pathfmt.realpath or pathfmt.path))
            if not filepath.is_file() or filepath.name.endswith(".part"):
                return
            if existing is None:
                destination = contained(base / filepath.relative_to(staging))
                contained(destination.parent)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.is_file() and not ctx.force:
                    filepath.unlink()
                else:
                    os.replace(filepath, destination)
                filepath = destination
            data = dict(pathfmt.kwdict)
            author, identifier = _identity(data, url)
            key = (author, identifier)
            if key not in results:
                results[key] = MediaResult(
                    url=url, platform=platform, author=author, id=identifier,
                    title=_first(data, ("title", "description", "content", "caption")),
                    date=_first(data, ("date", "created_at", "datetime")),
                    license_notes=_first(data, ("license", "license_url"), _LICENSE),
                )
            if filepath not in results[key].files:
                results[key].files.append(filepath)

    # gallery-dl logger names (instagram, downloader.http, cookies, etc.) are roots.
    logger = logging.getLogger()
    with TemporaryDirectory(prefix=".easyscrape-gallery-", dir=root) as staging_name, \
            _isolated_config(options):
        staging = Path(staging_name)
        # The isolated config and options share this fresh nested dictionary.
        options["extractor"]["base-directory"] = str(staging)
        logger.addHandler(errors)
        try:
            if ctx.cookies_from_browser and not ctx.cookies:
                spec = browser_spec(ctx.cookies_from_browser)
                try:
                    common.CACHE_COOKIES[spec] = gallery_cookies.load_cookies(spec)
                except Exception as exc:
                    raise DownloadError(
                        "Cannot read browser cookies; check browser/profile access."
                    ) from exc
            try:
                download_job = RecordingJob(url)
                status = download_job.run()
            except exception.NoExtractorError as exc:
                raise DownloadError(
                    f"Installed gallery-dl does not support this {platform} URL. "
                    "Upstream extractor support may be unavailable."
                ) from exc
            except DownloadError:
                raise
            except Exception as exc:
                raise DownloadError(f"gallery-dl failed: {exc}") from exc
        finally:
            logger.removeHandler(errors)

    detail = "; ".join(errors.messages[-3:])
    if status:
        message = f"gallery-dl failed for {platform} (status {status}). {detail}"
        if results:
            ctx.record_failure(url, message, platform)
            return list(results.values())
        if status & 16:
            raise DownloadError(
                f"{platform} authentication failed. "
                f"Refresh login cookies and check access. {detail}"
            )
        lower = detail.lower()
        if any(token in lower for token in (
            "429", "too many requests", "timed out", "timeout", "connection reset",
            "connectionerror", "502", "503", "504",
        )):
            raise RetryableError(message)
        raise DownloadError(message)
    if not results:
        raise DownloadError(
            f"gallery-dl returned no downloadable files for {platform}. "
            "Check URL support, login cookies, and media availability."
        )
    return list(results.values())
