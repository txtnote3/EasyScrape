"""Download original media streams through yt-dlp's Python API."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yt_dlp

from easyscrape.models import DownloadContext, DownloadError, MediaResult, RetryableError

PLATFORM = "youtube"
MATCHERS = [r"^https?://(?:[\w-]+\.)?(?:youtube\.com|youtu\.be)(?:[/:?#]|$)"]
_RUNTIME_ADVICE = (
    "YouTube video formats may be missing. Install Deno and put it on PATH, "
    "or install a supported Node.js runtime; then retry with --force."
)


class NoMediaError(DownloadError):
    """The extractor found no downloadable media, permitting a gallery fallback."""


class _Logger:
    def __init__(self, ctx: DownloadContext) -> None:
        self.ctx = ctx
        self.errors: list[str] = []
        self.runtime_warnings: list[str] = []

    def debug(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        if re.search(r"javascript runtime|js.runtime|js-runtimes", message, re.I):
            self.runtime_warnings.append(message)
        self.ctx.log(f"yt-dlp: {message}")

    def error(self, message: str) -> None:
        self.errors.append(message)

    def failure_message(self, message: str) -> str:
        if self.runtime_warnings and "Install Deno" not in message:
            return f"{message}. {self.media_warnings()[-1]}"
        return message

    def media_warnings(self) -> list[str]:
        return [
            f"yt-dlp JavaScript runtime warning: {warning} {_RUNTIME_ADVICE}"
            for warning in dict.fromkeys(self.runtime_warnings)
        ]


def _browser_cookies(specification: str) -> tuple[str, str | None, str | None, str | None]:
    """Parse yt-dlp's BROWSER[+KEYRING][:PROFILE][::CONTAINER] syntax."""
    head, separator, container = specification.partition("::")
    browser_spec, profile_separator, profile = head.partition(":")
    match = re.fullmatch(r"(?P<browser>\w+)(?:\+(?P<keyring>\w+))?", browser_spec)
    if match is None:
        raise DownloadError("Invalid cookies-from-browser specification")
    return (
        match["browser"].lower(),
        profile if profile_separator and profile else None,
        match["keyring"] or None,
        container if separator else None,
    )


def _error(message: str) -> DownloadError:
    if re.search(
        r"(?:429|5\d\d|timed? out|timeout|temporar|connection reset"
        r"|unable to extract yt initial data|incomplete data received"
        r"|requested format is not available)",
        message,
        re.I,
    ):
        return RetryableError(message)
    if re.search(r"(?:log.?in|cookies|sign.?in|private|permission|403|401)", message, re.I):
        return DownloadError(message)
    if re.search(
        r"(?:unsupported url|no (?:video|media|formats)|not a video|empty collection)",
        message,
        re.I,
    ):
        return NoMediaError(message)
    return DownloadError(message)


def _media_entries(info: dict[str, Any]) -> list[dict[str, Any] | None]:
    """Flatten collection containers without mistaking them for downloaded media."""
    if info.get("_type") in {"playlist", "multi_video"} or "entries" in info:
        entries: list[dict[str, Any] | None] = []
        for entry in info.get("entries") or []:
            if isinstance(entry, dict):
                entries.extend(_media_entries(entry))
            else:
                entries.append(None)
        return entries
    return [info]


def _component(ctx: DownloadContext, value: Any, default: str) -> str:
    # Treat metadata as untrusted even when a third-party extractor supplied it.
    cleaned = ctx.safe_filename(str(value or default)).replace("/", "_").replace("\\", "_")
    return default if cleaned in {"", ".", ".."} else cleaned


def _actual_files(
    info: dict[str, Any], events: list[dict[str, Any]], directory: Path
) -> list[Path]:
    candidates: list[Any] = []
    for item in [info, *events]:
        candidates.extend(item.get(key) for key in ("filepath", "filename", "_filename"))
        for download in item.get("requested_downloads") or []:
            if isinstance(download, dict):
                candidates.extend(
                    download.get(key) for key in ("filepath", "filename", "_filename")
                )
        moves = item.get("__files_to_move") or {}
        if isinstance(moves, dict):
            candidates.extend(moves.values())
    found: list[Path] = []
    root = directory.resolve()
    for candidate in candidates:
        if not isinstance(candidate, (str, Path)):
            continue
        path = Path(candidate).resolve()
        if not path.is_relative_to(root):
            raise DownloadError("yt-dlp returned a media path outside its output directory")
        if path.is_file() and path.suffix not in {".part", ".ytdl", ".temp"} and path not in found:
            found.append(path)
    return found


def _download_media(
    downloader: Any,
    entry: dict[str, Any],
    events: list[dict[str, Any]],
    directory: Path,
    ctx: DownloadContext,
    logger: _Logger,
) -> tuple[dict[str, Any], list[Path]]:
    for attempt in range(3):
        ctx.wait_for_slot()
        try:
            downloaded = downloader.process_ie_result(dict(entry), download=True)
            if not isinstance(downloaded, dict):
                raise _error(
                    logger.errors[-1] if logger.errors else "yt-dlp returned no downloaded media"
                )
            files = _actual_files(downloaded, events, directory)
            if not files:
                # A silent empty result (no exception, no file) is yt-dlp's way of
                # surfacing corrupted upstream responses (e.g. YouTube's flaky
                # "unable to extract yt initial data"). Treat it as transient and
                # let the retry loop run instead of failing the item outright.
                raise RetryableError("yt-dlp finished without producing a media file")
            return downloaded, files
        except yt_dlp.utils.DownloadError as exc:
            error = _error(str(exc))
        except DownloadError as exc:
            error = exc
        if not error.retryable:
            raise error
        if attempt == 2:
            # The item exhausted its budget; do not retry the whole playlist again.
            raise DownloadError(str(error)) from error
        time.sleep(2**attempt)
    raise DownloadError("Download attempts were exhausted")


def _probe_stream_types(path: Path) -> set[str] | None:
    """Inspect the finished file when ffprobe is installed; never infer from its suffix."""
    executable = shutil.which("ffprobe")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "-v", "error", "-show_streams", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            raise DownloadError(
                f"ffprobe could not inspect the finished media: {result.stderr.strip()}"
            )
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise DownloadError(
                "ffprobe returned invalid stream information for the finished media"
            )
        streams = payload.get("streams")
        if not isinstance(streams, list):
            raise DownloadError("ffprobe returned no stream information for the finished media")
        return {
            str(stream["codec_type"])
            for stream in streams
            if isinstance(stream, dict)
            and stream.get("codec_type")
            and not (stream.get("disposition") or {}).get("attached_pic")
        }
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise DownloadError(f"Could not verify finished media streams with ffprobe: {exc}") from exc


def _selected_video(info: dict[str, Any], path: Path) -> bool | None:
    """Fallback evidence comes from selected streams, never other available formats."""
    for download in info.get("requested_downloads") or []:
        if not isinstance(download, dict):
            continue
        filename = download.get("filepath") or download.get("filename")
        if filename and Path(filename).resolve() == path.resolve() and "vcodec" in download:
            return _format_video(download)
    selected = info.get("requested_formats")
    if isinstance(selected, list) and selected:
        states = [_format_video(item) for item in selected if isinstance(item, dict)]
        if any(state is True for state in states):
            return True
        if states and all(state is False for state in states):
            return False
        return None
    return _format_video(info)


def _format_video(info: dict[str, Any]) -> bool | None:
    codec = str(info.get("vcodec") or "").lower()
    if codec == "none" or str(info.get("resolution") or "").lower() == "audio only":
        return False
    if codec not in {"", "unknown"}:
        return True
    if isinstance(info.get("height"), (int, float)) and info["height"] > 0:
        return True
    return None


def _source_is_audio_only(info: dict[str, Any]) -> bool:
    """True only when extraction listed formats and none of them carries video."""
    formats = info.get("formats")
    if not isinstance(formats, list) or not formats:
        return False
    return not any(isinstance(f, dict) and _format_video(f) is True for f in formats)


def _validate_finished_media(
    files: list[Path], info: dict[str, Any], audio_only: bool
) -> list[str]:
    """Reject video downloads that lost their video; return notes for audio-only sources."""
    notes: list[str] = []
    if audio_only:
        return notes
    for path in files:
        streams = _probe_stream_types(path)
        has_video = "video" in streams if streams is not None else _selected_video(info, path)
        if has_video is None:
            raise DownloadError(
                "Cannot verify a video stream: ffprobe is unavailable and the selected format "
                "does not identify a video codec. Install ffmpeg (including ffprobe) to verify it."
            )
        if not has_video:
            if _source_is_audio_only(info):
                notes.append("The source only provides audio; saved as an audio file.")
                continue
            raise DownloadError(
                "The finished media has no video stream (audio-only output). "
                "Use --audio-only for audio, or check yt-dlp's available video formats and retry."
            )
    return notes


def _js_runtimes() -> dict[str, dict[str, str]]:
    runtimes: dict[str, dict[str, str]] = {}
    for name, binaries in (
        ("deno", ("deno",)),
        ("node", ("node",)),
        ("bun", ("bun",)),
        ("quickjs", ("qjs", "quickjs")),
    ):
        for binary in binaries:
            executable = shutil.which(binary)
            if executable:
                runtimes[name] = {"path": executable}
                break
    return runtimes


def _publish_media(files: list[Path], directory: Path, force: bool) -> list[Path]:
    """Publish finished files without opening existing targets or downloader sidecars."""
    destinations = [directory / path.name for path in files]
    for destination in destinations:
        if destination.is_symlink():
            raise DownloadError("Refusing to replace an existing media-file symlink")
        if destination.exists() and not destination.is_file():
            raise DownloadError("The media destination exists and is not a regular file")
    for source, destination in zip(files, destinations, strict=True):
        if force or not destination.exists():
            # replace() replaces a filename itself; it never writes through a target symlink.
            os.replace(source, destination)
    return [path.resolve() for path in destinations]


def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
    """Extract collections, download their media, and retain partial successes."""
    platform = getattr(ctx, "platform", None) or PLATFORM
    host = (urlsplit(url).hostname or "").lower()
    requires_session = platform in {"instagram", "facebook"} or any(
        host == domain or host.endswith("." + domain)
        for domain in ("instagram.com", "facebook.com", "fb.watch")
    )
    if requires_session and not (ctx.cookies or ctx.cookies_from_browser):
        ctx.log(
            f"{platform}: a logged-in session may be required; provide cookies if extraction fails"
        )
    logger = _Logger(ctx)
    ffmpeg_available = shutil.which("ffmpeg") is not None
    height = f"[height<={ctx.max_height}]" if ctx.max_height else ""
    options: dict[str, Any] = {
        "format": "bestaudio"
        if ctx.audio_only
        else (
            (f"bestvideo*{height}+bestaudio/best{height}/" if height else "")
            + "bestvideo*+bestaudio/best"
            if ffmpeg_available
            else (f"best{height}[acodec!=none][vcodec!=none]/" if height else "")
            + "best[acodec!=none][vcodec!=none]/best"
        ),
        "quiet": True,
        "no_warnings": False,
        "logger": logger,
        "ignoreerrors": True,
        "retries": 0,
        "extractor_retries": 0,
        "fragment_retries": 0,
        "file_access_retries": 0,
        "overwrites": bool(ctx.force),
        "continuedl": not ctx.force,
        "noprogress": True,
        "restrictfilenames": True,
        "windowsfilenames": True,
    }
    if ctx.max_items:
        options["playlistend"] = ctx.max_items
    if ctx.audio_only and ctx.audio_format != "best":
        if not ffmpeg_available:
            raise DownloadError(f"--audio-format {ctx.audio_format} requires ffmpeg on PATH")
        options["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": ctx.audio_format}
        ]
    if ctx.cookies:
        if not Path(ctx.cookies).is_file():
            raise DownloadError("The cookies file does not exist or is not a regular file")
        options["cookiefile"] = str(ctx.cookies)
    if ctx.cookies_from_browser:
        options["cookiesfrombrowser"] = _browser_cookies(ctx.cookies_from_browser)
    if runtimes := _js_runtimes():
        options["js_runtimes"] = runtimes
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            extracted = downloader.extract_info(url, download=False)
            if not isinstance(extracted, dict):
                if logger.errors:
                    raise _error(logger.errors[-1])
                raise NoMediaError("yt-dlp returned no media")
            entries = _media_entries(extracted)
            if not entries:
                raise NoMediaError("yt-dlp returned an empty collection with no media")
            results: list[MediaResult] = []
            initial_errors = list(logger.errors)
            events: list[dict[str, Any]] = []
            title = ""

            def progress(event: dict[str, Any]) -> None:
                if event.get("status") == "finished":
                    info = event.get("info_dict")
                    if isinstance(info, dict):
                        events.append(dict(info))
                    if event.get("filename"):
                        events.append({"filename": event["filename"]})
                callback = getattr(ctx, "progress_callback", None)
                if callback:
                    total = event.get("total_bytes") or event.get("total_bytes_estimate")
                    fraction = event.get("downloaded_bytes", 0) / total if total else None
                    callback(fraction, title)

            def postprocess(event: dict[str, Any]) -> None:
                info = event.get("info_dict")
                if event.get("status") == "finished" and isinstance(info, dict):
                    events.append(dict(info))

            downloader.add_progress_hook(progress)
            downloader.add_postprocessor_hook(postprocess)
            for entry in entries:
                if entry is None:
                    message = (
                        initial_errors.pop(0)
                        if initial_errors
                        else "A collection entry could not be extracted"
                    )
                    ctx.record_failure(url, logger.failure_message(message), platform)
                    continue
                media_url = str(entry.get("webpage_url") or entry.get("original_url") or url)
                if ctx.is_downloaded(media_url):
                    ctx.record_skip(media_url, "Already downloaded", platform)
                    continue
                author = str(
                    entry.get("uploader")
                    or entry.get("channel")
                    or entry.get("creator")
                    or "unknown"
                )
                media_id = str(
                    entry.get("id") or hashlib.sha256(media_url.encode()).hexdigest()[:16]
                )
                title = str(entry.get("title") or media_id)
                directory = (
                    ctx.target_dir(platform)
                    / _component(ctx, author, "unknown")
                    / _component(ctx, media_id, "unknown")
                )
                root = Path(ctx.out_dir).resolve()
                if not directory.resolve().is_relative_to(root):
                    raise DownloadError("Media directory escapes the configured output directory")
                directory.mkdir(parents=True, exist_ok=True)
                directory = directory.resolve()
                # Windows rejects paths past ~260 characters; leave room for the staging
                # directory and yt-dlp suffixes such as ".f616.mp4.ytdl".
                suffix = f" [{media_id}]"
                budget = 240 - (len(str(directory)) + 1 + len(".easyscrape-xxxxxxxx") + 1) - 24
                title_part = title[: max(16, budget - len(suffix))] if len(title) > 16 else title
                stem = _component(ctx, f"{title_part.rstrip()}{suffix}", "media")
                events.clear()
                try:
                    # A newly created private directory keeps every yt-dlp intermediate file
                    # away from pre-existing final, .part, fragment, and .ytdl symlinks.
                    with tempfile.TemporaryDirectory(prefix=".easyscrape-", dir=directory) as temp:
                        staging = Path(temp)
                        template = str(staging / stem).replace("%", "%%") + ".%(ext)s"
                        downloader.params["outtmpl"] = {"default": template}
                        downloaded, staged_files = _download_media(
                            downloader, entry, events, staging, ctx, logger
                        )
                        notes = _validate_finished_media(
                            staged_files, downloaded, ctx.audio_only
                        )
                        for staged_file in staged_files:
                            existing = directory / staged_file.name
                            if not ctx.force and existing.is_file() and not existing.is_symlink():
                                # Fresh extractor metadata describes staged bytes, not an old file.
                                # Existing media needs its own probe or an explicit forced retry.
                                try:
                                    _validate_finished_media([existing], {}, ctx.audio_only)
                                except DownloadError as exc:
                                    raise DownloadError(
                                        f"Could not verify existing media: {exc} "
                                        "Use --force to download and verify fresh media."
                                    ) from exc
                        files = _publish_media(staged_files, directory, ctx.force)
                        _validate_finished_media(files, downloaded, ctx.audio_only)
                    results.append(
                        MediaResult(
                            url=media_url,
                            platform=platform,
                            title=str(downloaded.get("title") or title),
                            author=author,
                            id=media_id,
                            files=files,
                            date=str(
                                downloaded.get("upload_date")
                                or downloaded.get("release_date")
                                or ""
                            ),
                            license_notes=str(downloaded.get("license") or ""),
                            warnings=[*logger.media_warnings(), *notes],
                        )
                    )
                except DownloadError as exc:
                    if isinstance(exc, NoMediaError) and len(entries) == 1:
                        raise
                    ctx.record_failure(media_url, logger.failure_message(str(exc)), platform)
                except (OSError, ValueError) as exc:
                    ctx.record_failure(media_url, logger.failure_message(str(exc)), platform)
            # Extraction can omit failed playlist entries entirely; preserve those errors too.
            for message in initial_errors:
                ctx.record_failure(url, logger.failure_message(message), platform)
            return results
    except yt_dlp.utils.DownloadError as exc:
        raise _error(logger.failure_message(str(exc))) from exc
    except DownloadError as exc:
        message = logger.failure_message(str(exc))
        if message != str(exc):
            raise type(exc)(message) from exc
        raise
    except (OSError, ValueError) as exc:
        raise DownloadError(logger.failure_message(str(exc))) from exc
