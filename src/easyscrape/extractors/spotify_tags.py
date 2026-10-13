"""Embed track tags and bounded, validated public cover art in original audio."""
from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import urlparse

import httpx
from mutagen.flac import Picture
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus

from easyscrape.models import DownloadError, RetryableError

from .spotify_metadata import SpotifyTrack

MAX_COVER_BYTES = 5 * 1024 * 1024
_COVER_HOSTS = {"i.scdn.co", "mosaic.scdn.co", "image-cdn-ak.spotifycdn.com",
                "image-cdn-fa.spotifycdn.com"}


def download_cover(url: str) -> tuple[bytes, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _COVER_HOSTS:
        raise DownloadError("Spotify cover is not on a supported public image host")
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            with client.stream("GET", url) as response:
                if response.status_code == 429 or response.status_code >= 500:
                    raise RetryableError(f"Spotify cover HTTP {response.status_code}")
                response.raise_for_status()
                mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if mime not in {"image/jpeg", "image/png"}:
                    raise DownloadError("Spotify cover must have JPEG or PNG MIME type")
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_COVER_BYTES:
                        raise DownloadError("Spotify cover exceeds the 5 MiB limit")
        raw = bytes(data)
        if (mime == "image/jpeg" and not raw.startswith(b"\xff\xd8\xff")) or (
            mime == "image/png" and not raw.startswith(b"\x89PNG\r\n\x1a\n")
        ):
            raise DownloadError("Spotify cover bytes do not match their MIME type")
        return raw, mime
    except httpx.RequestError as exc:
        raise RetryableError(f"Spotify cover request failed: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise DownloadError(f"Spotify cover request failed: {exc}") from exc


def write_tags(path: Path, track: SpotifyTrack, cover: tuple[bytes, str] | None) -> None:
    # Mutagen's public constructors/save methods have no third-party annotations.
    try:
        if path.suffix.lower() == ".m4a":
            audio = MP4(path)  # type: ignore[no-untyped-call]
            if audio.tags is None:
                audio.add_tags()  # type: ignore[no-untyped-call]
            audio["\xa9nam"] = [track.title]
            audio["\xa9ART"] = [track.artist]
            audio["\xa9alb"] = [track.album]
            audio["trkn"] = [(track.number, 0)]
            if cover:
                raw, mime = cover
                fmt = MP4Cover.FORMAT_JPEG if mime == "image/jpeg" else MP4Cover.FORMAT_PNG
                audio["covr"] = [MP4Cover(raw, imageformat=fmt)]  # type: ignore[no-untyped-call]
            audio.save()  # type: ignore[no-untyped-call]
        elif path.suffix.lower() == ".opus":
            opus = OggOpus(path)  # type: ignore[no-untyped-call]
            opus["title"], opus["artist"], opus["album"] = (
                [track.title], [track.artist], [track.album]
            )
            opus["tracknumber"] = [str(track.number)]
            if cover:
                picture = Picture()  # type: ignore[no-untyped-call]
                picture.type, picture.mime, picture.desc = 3, cover[1], "Front cover"
                picture.data = cover[0]
                block = picture.write()  # type: ignore[no-untyped-call]
                opus["metadata_block_picture"] = [base64.b64encode(block).decode("ascii")]
            opus.save()
        else:
            raise DownloadError(f"Unsupported Spotify audio container: {path.suffix}")
    except DownloadError:
        raise
    except Exception as exc:
        raise DownloadError(f"Cannot embed Spotify audio tags: {exc}") from exc
