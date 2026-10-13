"""Conservative matching of public Spotify metadata against YouTube Music."""
from __future__ import annotations

import math
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from easyscrape.models import DownloadError

from .spotify_metadata import SpotifyTrack

_VARIANTS = (
    r"\blive\b", r"\bremix\b", r"\bsped[\s-]*up\b", r"\bspeed[\s-]*up\b",
    r"\bnightcore\b", r"\b8d(?:\s+audio)?\b",
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    decoration = r"(?:official\s+)?(?:audio|video|lyrics?|visualizer)(?:\s+hd)?"
    text = re.sub(rf"\s*[\[(]{decoration}[\])]\s*$", "", text)
    text = re.sub(rf"\s+-\s+{decoration}\s*$", "", text)
    return " ".join(re.findall(r"\w+", text))


def candidate_duration(candidate: dict[str, Any]) -> float | None:
    seconds = candidate.get("duration_seconds")
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
        return float(seconds) if math.isfinite(seconds) and seconds > 0 else None
    duration = candidate.get("duration")
    if not isinstance(duration, str) or not re.fullmatch(r"\d+(?::\d{2}){1,2}", duration):
        return None
    parts = duration.split(":")
    if any(int(part) >= 60 for part in parts[1:]):
        return None
    seconds = sum(int(part) * 60 ** index for index, part in enumerate(reversed(parts)))
    return float(seconds) if seconds > 0 else None


def match_score(track: SpotifyTrack, candidate: dict[str, Any]) -> float | None:
    duration = candidate_duration(candidate)
    if (track.duration_s is None or not math.isfinite(track.duration_s)
            or duration is None or abs(duration - track.duration_s) > 2):
        return None
    title = candidate.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    for pattern in _VARIANTS:
        if re.search(pattern, title, re.IGNORECASE) and not re.search(
            pattern, track.title, re.IGNORECASE
        ):
            return None
    source_title, result_title = normalize(track.title), normalize(title)
    title_similarity = SequenceMatcher(None, source_title, result_title).ratio()
    source_words, result_words = set(source_title.split()), set(result_title.split())
    overlap = len(source_words & result_words) / max(len(source_words), len(result_words), 1)
    if title_similarity < 0.82 or overlap < 0.75:
        return None
    artists = candidate.get("artists", [])
    if not isinstance(artists, list):
        return None
    result_artists = [normalize(item["name"]) for item in artists
                      if isinstance(item, dict) and isinstance(item.get("name"), str)]
    source_artists = [normalize(track.artist),
                      *(normalize(name) for name in re.split(r",\s*", track.artist))]
    if not result_artists or not any(
        source and result and SequenceMatcher(None, source, result).ratio() >= 0.9
        for source in source_artists for result in result_artists
    ):
        return None
    duration_score = 1 - abs(duration - track.duration_s) / 2
    return title_similarity * 0.8 + overlap * 0.15 + duration_score * 0.05


def choose_match(track: SpotifyTrack, candidates: list[dict[str, Any]]) -> str:
    if track.duration_s is None or not math.isfinite(track.duration_s):
        raise DownloadError(
            "Spotify track has no reliable duration; strict matching is unavailable"
        )
    ranked: list[tuple[float, str]] = []
    for candidate in candidates:
        ident = candidate.get("videoId")
        score = match_score(track, candidate)
        if score is not None and isinstance(ident, str) and re.fullmatch(r"[\w-]{11}", ident):
            ranked.append((score, ident))
    if not ranked:
        raise DownloadError(
            "No YouTube Music match passed title/artist, variant, and ±2 second duration checks"
        )
    return max(ranked)[1]
