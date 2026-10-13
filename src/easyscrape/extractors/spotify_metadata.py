"""Resolve public Spotify metadata without credentials or playback tokens.

Embed pages are presentation data, not a guaranteed complete collection API.
We accept collections only when their advertised count matches every track URI.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from html import unescape
from typing import Any
from urllib.parse import urlparse

import httpx

from easyscrape.models import DownloadError, RetryableError

URL_PATTERN = (
    r"^https?://open\.spotify\.com/(?:intl-[a-z-]+/)?"
    r"(track|album|playlist)/([A-Za-z0-9]{22})(?:[/?#]|$)"
)
_NEXT_DATA = re.compile(
    r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.DOTALL
)
_LD_DATA = re.compile(
    r'<script\b[^>]*\btype=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)


@dataclass(frozen=True)
class SpotifyTrack:
    id: str
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_s: float | None = None
    cover_url: str = ""
    number: int = 1

    @property
    def url(self) -> str:
        return f"https://open.spotify.com/track/{self.id}"


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: Any) -> float | None:
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0):
        return float(value)
    return None


def _duration(data: dict[str, Any]) -> float | None:
    for key in ("duration_ms", "durationMs", "duration"):
        value = _number(data.get(key))
        if value is not None:
            return value / 1000
    return _number(data.get("duration_s"))


def _artist(data: dict[str, Any]) -> str:
    artists = data.get("artists")
    if isinstance(artists, list):
        names = [_text(item.get("name")) for item in artists if isinstance(item, dict)]
        if any(names):
            return ", ".join(name for name in names if name)
    return _text(data.get("subtitle")) or _text(data.get("artist"))


def _cover(data: dict[str, Any]) -> str:
    art = data.get("coverArt")
    sources = art.get("sources", []) if isinstance(art, dict) else []
    valid = [item for item in sources if isinstance(item, dict) and _text(item.get("url"))]
    if valid:
        best = max(valid, key=lambda item: _number(item.get("width")) or 0)
        return _text(best["url"])
    images = data.get("images", [])
    if isinstance(images, list) and images and isinstance(images[0], dict):
        return _text(images[0].get("url"))
    return ""


def _album(data: dict[str, Any]) -> str:
    album = data.get("album")
    if isinstance(album, dict):
        return _text(album.get("name")) or _text(album.get("title"))
    return _text(data.get("albumName")) or _text(album)


def _track_id(data: dict[str, Any]) -> str:
    uri = _text(data.get("uri")) or _text(data.get("uid"))
    match = re.fullmatch(r"spotify:track:([A-Za-z0-9]{22})", uri)
    if match:
        return match[1]
    match = re.match(URL_PATTERN, uri)
    if match and match[1] == "track":
        return match[2]
    raise DownloadError("Spotify collection contains an unavailable or unidentified track")


def parse_embed(page: str, kind: str, ident: str) -> list[SpotifyTrack]:
    """Parse supported public embed JSON; refuse unproven collection completeness."""
    match = _NEXT_DATA.search(page)
    if not match:
        raise DownloadError("Spotify public embed metadata is unavailable")
    try:
        root = json.loads(match[1])
        entity = root["props"]["pageProps"]["state"]["data"]["entity"]
    except (ValueError, TypeError, KeyError) as exc:
        raise DownloadError("Spotify public embed metadata schema is unsupported") from exc
    if not isinstance(entity, dict):
        raise DownloadError("Spotify public embed entity is unavailable")
    entries = entity.get("trackList")
    if kind == "track":
        track_data = entity
        if isinstance(entries, list) and len(entries) == 1 and isinstance(entries[0], dict):
            track_data = {**entity, **entries[0]}
        number = _number(entity.get("trackNumber")) or 1
        return [SpotifyTrack(
            ident, _text(track_data.get("title")) or _text(track_data.get("name")),
            _artist(track_data), _album(entity), _duration(track_data), _cover(entity),
            int(number),
        )]
    if not isinstance(entries, list):
        raise DownloadError("Spotify collection track metadata is unavailable")
    advertised = next((entity[key] for key in ("totalTracks", "trackCount", "total")
                       if key in entity), None)
    if not isinstance(advertised, int) or isinstance(advertised, bool) or advertised < 1:
        raise DownloadError(
            "Spotify public collection does not advertise a complete track count; "
            "authless album/playlist expansion is unsupported for this page"
        )
    if advertised != len(entries) or entity.get("next"):
        raise DownloadError(
            f"Spotify public collection is incomplete: {len(entries)} of {advertised} tracks"
        )
    tracks: list[SpotifyTrack] = []
    album = (_text(entity.get("title")) or _text(entity.get("name"))) if kind == "album" else ""
    for number, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise DownloadError("Spotify collection contains an unavailable track")
        tracks.append(SpotifyTrack(
            _track_id(entry), _text(entry.get("title")) or _text(entry.get("name")),
            _artist(entry), _album(entry) or album, _duration(entry),
            _cover(entry) or (_cover(entity) if kind == "album" else ""), number,
        ))
    return tracks


def _ld_nodes(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [node for item in value for node in _ld_nodes(item)]
    if isinstance(value, dict):
        nodes = [value]
        if "@graph" in value:
            nodes.extend(_ld_nodes(value["@graph"]))
        return nodes
    return []


def supplement_page(track: SpotifyTrack, page: str) -> SpotifyTrack:
    """Use public MusicRecording JSON-LD for album and duration when available."""
    for match in _LD_DATA.finditer(page):
        try:
            nodes = _ld_nodes(json.loads(unescape(match[1])))
        except ValueError:
            continue
        for node in nodes:
            types = node.get("@type", [])
            if "MusicRecording" not in ([types] if isinstance(types, str) else types):
                continue
            # Avoid unrelated recommendations embedded in the same public page.
            node_url = _text(node.get("url")) or _text(node.get("@id"))
            if node_url and track.id not in node_url:
                continue
            album = node.get("inAlbum", {})
            artist = node.get("byArtist", {})
            duration = _text(node.get("duration"))
            parsed = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", duration)
            seconds = (sum(float(value or 0) * factor for value, factor in
                           zip(parsed.groups(), (3600, 60, 1), strict=True)) if parsed else None)
            track = replace(
                track,
                title=track.title or _text(node.get("name")),
                artist=track.artist or (_text(artist.get("name"))
                                        if isinstance(artist, dict) else ""),
                album=track.album or (_text(album.get("name"))
                                      if isinstance(album, dict) else ""),
                duration_s=track.duration_s or seconds,
                cover_url=track.cover_url or _text(node.get("image")),
            )
    return track


class SpotifyResolver:
    def __init__(self, pace: Callable[[], None] | None = None) -> None:
        self.pace = pace
        self.client = httpx.Client(
            timeout=15, follow_redirects=True,
            headers={"User-Agent": "EasyScrape/0.1 (public metadata resolver)"},
        )

    def close(self) -> None:
        self.client.close()

    def _page(self, url: str) -> str:
        try:
            if self.pace:
                self.pace()
            response = self.client.get(url)
            if response.status_code == 429 or response.status_code >= 500:
                raise RetryableError(f"Spotify metadata HTTP {response.status_code}")
            response.raise_for_status()
            if urlparse(str(response.url)).hostname != "open.spotify.com":
                raise DownloadError("Spotify metadata redirected outside its public domain")
            if len(response.content) > 10 * 1024 * 1024:
                raise DownloadError("Spotify metadata response exceeds the size limit")
            return response.text
        except httpx.RequestError as exc:
            raise RetryableError(f"Spotify public metadata request failed: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise DownloadError(f"Spotify public metadata request failed: {exc}") from exc

    def resolve(self, url: str) -> list[SpotifyTrack]:
        match = re.match(URL_PATTERN, url)
        if not match:
            raise DownloadError("Unsupported Spotify URL; expected a track, album, or playlist")
        kind, ident = match.groups()
        tracks = parse_embed(self._page(f"https://open.spotify.com/embed/{kind}/{ident}"),
                             kind, ident)
        return tracks

    def complete(self, track: SpotifyTrack) -> SpotifyTrack:
        if not track.title or not track.artist or track.duration_s is None or not track.album:
            page = self._page(track.url)
            track = supplement_page(track, page)
        if not track.title or not track.artist:
            raise DownloadError("Spotify track title/artist metadata is unavailable")
        if track.duration_s is None or track.duration_s <= 0:
            raise DownloadError(
                "Spotify track has no reliable duration; strict matching is unavailable"
            )
        return track
