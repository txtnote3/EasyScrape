"""Spotify public metadata → strict YouTube Music match → original tagged audio."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

import yt_dlp
from ytmusicapi import YTMusic

from easyscrape.models import DownloadContext, DownloadError, MediaResult, RetryableError

from .spotify_matching import choose_match_detailed
from .spotify_metadata import URL_PATTERN, SpotifyResolver, SpotifyTrack
from .spotify_tags import download_cover, write_tags

PLATFORM = "spotify"
MATCHERS = [URL_PATTERN]
_T = TypeVar("_T")


def _retry(action: Callable[[], _T]) -> _T:
    for attempt in range(3):
        try:
            return action()
        except DownloadError as exc:
            if not exc.retryable or attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("Retry loop exhausted")


def _external_error(operation: str, exc: Exception) -> DownloadError:
    text = str(exc)
    transient = any(word in text.casefold() for word in
                    ("429", "403", "timed out", "timeout", "temporar", "connection", "502", "503"))
    transient = transient or bool(re.search(r"\b5\d{2}\b", text))
    message = f"{operation} failed: {text}"
    return RetryableError(message) if transient else DownloadError(message)


def _find(track: SpotifyTrack, ctx: DownloadContext) -> tuple[str, float]:
    ctx.wait_for_slot("youtube")
    try:
        candidates = YTMusic().search(f"{track.artist} - {track.title}", filter="songs", limit=20)
    except Exception as exc:
        raise _external_error("YouTube Music search", exc) from exc
    if not isinstance(candidates, list):
        raise DownloadError("YouTube Music search returned unsupported metadata")
    return choose_match_detailed(track, [item for item in candidates if isinstance(item, dict)])


def _download(video_id: str, staging: Path, ctx: DownloadContext) -> Path:
    """Download best original audio, remuxing WebM Opus with stream copy only."""
    ffmpeg = shutil.which("ffmpeg")
    options: dict[str, Any] = {
        "format": ("bestaudio[ext~='^(m4a|opus|webm)$'][acodec~='^(mp4a|aac|opus)']"
                   if ffmpeg else "bestaudio[ext=m4a]"),
        "outtmpl": str(staging / "audio.%(ext)s"),
        "noplaylist": True,
        "quiet": ctx.quiet,
        "no_warnings": ctx.quiet,
        "retries": 0,
        "fragment_retries": 0,
        "extractor_retries": 0,
        "file_access_retries": 0,
        "socket_timeout": 20,
    }
    if ctx.cookies:
        options["cookiefile"] = str(ctx.cookies)
    if ctx.cookies_from_browser:
        options["cookiesfrombrowser"] = (ctx.cookies_from_browser,)
    if ctx.progress_callback:
        def progress(event: dict[str, Any]) -> None:
            total = event.get("total_bytes") or event.get("total_bytes_estimate")
            downloaded = event.get("downloaded_bytes")
            percent = (downloaded / total
                       if isinstance(total, (int, float)) and total > 0
                       and isinstance(downloaded, (int, float)) else None)
            if ctx.progress_callback:
                ctx.progress_callback(percent, "Spotify: downloading YouTube Music audio")
        options["progress_hooks"] = [progress]
    ctx.wait_for_slot("youtube")
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(f"https://music.youtube.com/watch?v={video_id}",
                                          download=True)
            if not isinstance(info, dict):
                raise DownloadError("YouTube Music download returned no audio metadata")
            requested = info.get("requested_downloads", [])
            filename = (requested[0].get("filepath")
                        if requested and isinstance(requested[0], dict) else None)
            path = Path(filename or downloader.prepare_filename(info)).resolve()
    except DownloadError:
        raise
    except Exception as exc:
        raise _external_error("YouTube Music download", exc) from exc
    if not path.is_file() or not path.is_relative_to(staging.resolve()):
        raise DownloadError("YouTube Music did not produce an audio file in the staging directory")
    if path.suffix.lower() == ".webm":
        if info.get("acodec") != "opus" or ffmpeg is None:
            raise DownloadError("WebM audio requires Opus and ffmpeg for lossless .opus remuxing")
        opus = staging / "audio.opus"
        try:
            subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                 "-i", str(path), "-map", "0:a:0", "-vn", "-c:a", "copy", str(opus)],
                check=True, capture_output=True, timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DownloadError(f"Lossless Opus container remux failed: {exc}") from exc
        path = opus
    if path.suffix.lower() not in {".m4a", ".opus"}:
        raise DownloadError(f"YouTube Music produced unsupported audio container {path.suffix}")
    return path


def _save(
    track: SpotifyTrack, video_id: str, ctx: DownloadContext, delta: float = 0.0
) -> MediaResult:
    directory = (ctx.target_dir(PLATFORM) / ctx.safe_filename(track.artist)
                 / ctx.safe_filename(track.album or "Unknown album"))
    root = ctx.out_dir.resolve()
    if not directory.resolve().is_relative_to(root):
        raise DownloadError("Spotify output directory resolves outside the selected output root")
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{track.number:02d}-{ctx.safe_filename(track.title, max_length=100)} [{track.id}]"
    with tempfile.TemporaryDirectory(prefix=".easyscrape-", dir=directory) as temporary:
        staging = Path(temporary)
        audio = _retry(lambda: _download(video_id, staging, ctx))
        cover = None
        if track.cover_url:
            def fetch_cover() -> tuple[bytes, str]:
                ctx.wait_for_slot(PLATFORM)
                return download_cover(track.cover_url)
            cover = _retry(fetch_cover)
        write_tags(audio, track, cover)
        target = directory / f"{stem}{audio.suffix.lower()}"
        if target.exists() and not ctx.force:
            raise DownloadError(
                "Spotify destination exists without a completed cache entry; use --force"
            )
        audio.replace(target)
    return MediaResult(
        url=track.url, platform=PLATFORM, title=track.title, author=track.artist,
        id=track.id, files=[target.resolve()],
        warnings=([f"Approximate match: duration differs from Spotify by {delta:.0f} s "
                   "(likely a remaster or reissue)"] if delta > 2 else []),
        license_notes=("Audio sourced from a matched public YouTube Music recording, "
                       "not Spotify's stream; original lossy M4A/Opus, no transcoding. "
                       "Download only content you have permission to use."),
    )


def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
    resolver = SpotifyResolver(lambda: ctx.wait_for_slot(PLATFORM))
    try:
        # The dispatcher owns source-level retries; child operations retry here.
        tracks = resolver.resolve(url)
        if ctx.max_items:
            tracks = tracks[: ctx.max_items]
        notes = list(getattr(resolver, "warnings", []))
        for note in notes:
            ctx.log(f"Spotify: {note}")
        results: list[MediaResult] = []
        for track in tracks:
            if ctx.is_downloaded(track.url):
                ctx.record_skip(track.url, "Previously downloaded Spotify track", PLATFORM)
                continue
            try:
                complete = _retry(partial(resolver.complete, track))
                video_id, delta = _retry(partial(_find, complete, ctx))
                results.append(_save(complete, video_id, ctx, delta))
            except DownloadError as exc:
                ctx.record_failure(track.url, str(exc), PLATFORM)
            except Exception as exc:
                ctx.record_failure(track.url, f"Spotify track processing failed: {exc}", PLATFORM)
        if results:
            results[0].warnings.extend(notes)
        return results
    finally:
        resolver.close()
