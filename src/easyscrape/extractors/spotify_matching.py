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
    text = re.sub(r"\s*[\[(](?:\d{4}\s+)?(?:digital\s+)?(?:re-?master(?:ed)?|"
                  r"(?:album|single|stereo|mono)\s+version)(?:\s+\d{4})?[\])]\s*", " ", text)
    text = re.sub(r"\s+-\s+(?:\d{4}\s+)?re-?master(?:ed)?(?:\s+\d{4})?\s*$", "", text)
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


def relaxed_tolerance(track: SpotifyTrack) -> float:
    """Reissues and remasters differ by a few seconds; allow that for exact title matches."""
    return max(10.0, (track.duration_s or 0) * 0.05)


def match_score(
    track: SpotifyTrack, candidate: dict[str, Any], tolerance: float = 2.0
) -> float | None:
    duration = candidate_duration(candidate)
    if (track.duration_s is None or not math.isfinite(track.duration_s)
            or duration is None or abs(duration - track.duration_s) > tolerance):
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
    if tolerance > 2 and source_title != result_title:
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
    duration_score = max(0.0, 1 - abs(duration - track.duration_s) / tolerance)
    return title_similarity * 0.8 + overlap * 0.15 + duration_score * 0.05


def choose_match_detailed(
    track: SpotifyTrack, candidates: list[dict[str, Any]]
) -> tuple[str, float]:
    """Return (video id, duration difference in seconds); prefer the strict ±2 s tier."""
    if track.duration_s is None or not math.isfinite(track.duration_s):
        raise DownloadError(
            "Spotify track has no reliable duration; strict matching is unavailable"
        )
    for tolerance in (2.0, relaxed_tolerance(track)):
        ranked: list[tuple[float, str, float]] = []
        for candidate in candidates:
            ident = candidate.get("videoId")
            score = match_score(track, candidate, tolerance)
            duration = candidate_duration(candidate)
            if (score is not None and duration is not None and isinstance(ident, str)
                    and re.fullmatch(r"[\w-]{11}", ident)):
                ranked.append((score, ident, abs(duration - track.duration_s)))
        if ranked:
            _, ident, delta = max(ranked)
            return ident, delta
    raise DownloadError(
        "No YouTube Music match passed title/artist, variant, and duration checks "
        f"(strict ±2 s, then ±{relaxed_tolerance(track):.0f} s for exact titles)"
    )


def choose_match(track: SpotifyTrack, candidates: list[dict[str, Any]]) -> str:
    return choose_match_detailed(track, candidates)[0]
