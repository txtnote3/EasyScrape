from dataclasses import replace

import pytest

from easyscrape.extractors.spotify_matching import candidate_duration, choose_match, match_score
from easyscrape.extractors.spotify_metadata import SpotifyTrack
from easyscrape.models import DownloadError

TRACK = SpotifyTrack("4uLU6hMCjMI75M1A2tKUQC", "Song", "Artist", "Album", 180)


def candidate(title="Song", artist="Artist", duration=180, ident="abcdefghijk"):
    return {"title": title, "artists": [{"name": artist}], "duration_seconds": duration,
            "videoId": ident}


@pytest.mark.parametrize("duration, accepted", [(178, True), (182, True), (177.999, False),
                                               (182.001, False), (None, False)])
def test_duration_tolerance_is_exactly_two_seconds(duration, accepted):
    assert (match_score(TRACK, candidate(duration=duration)) is not None) == accepted


@pytest.mark.parametrize(
    "variant", ["Live", "Remix", "Sped Up", "sped-up", "Nightcore", "8D Audio"]
)
def test_unsolicited_variants_rejected_and_original_variants_allowed(variant):
    title = f"Song ({variant})"
    assert match_score(TRACK, candidate(title)) is None
    assert match_score(replace(TRACK, title=title), candidate(title)) is not None


@pytest.mark.parametrize("title, artist", [("Other Song", "Artist"), ("Song", "Unrelated Band"),
                                          ("Some Different Song", "Artist")])
def test_unrelated_recordings_are_not_matched(title, artist):
    assert match_score(TRACK, candidate(title, artist)) is None


def test_intrinsic_audio_video_words_preserved():
    assert match_score(replace(TRACK, title="Video Games"), candidate("Games")) is None
    assert match_score(replace(TRACK, title="Audio"), candidate("Video")) is None
    assert match_score(TRACK, candidate("Song (Official Audio)")) is not None


def test_artist_name_with_intrinsic_comma_is_not_split_into_unrelated_artists():
    artist = "Earth, Wind & Fire"
    assert match_score(replace(TRACK, artist=artist), candidate(artist=artist)) is not None


def test_normalized_artist_and_title_matching_and_best_candidate():
    track = replace(TRACK, title="Été!", artist="Beyoncé")
    assert choose_match(track, [candidate("Ete", "Beyonce", 182),
                                candidate("Été (Official Audio)", "Beyoncé", 180, "12345678901")]
                        ) == "12345678901"


@pytest.mark.parametrize("duration", [None, float("nan"), float("inf")])
def test_unknown_source_duration_never_silently_relaxes_filter(duration):
    with pytest.raises(DownloadError, match="reliable duration"):
        choose_match(replace(TRACK, duration_s=duration), [candidate()])


def test_unknown_candidate_duration_no_match_is_actionable():
    with pytest.raises(DownloadError, match="duration checks"):
        choose_match(TRACK, [candidate(duration=None)])


@pytest.mark.parametrize("value, expected", [("3:00", 180), ("1:03:00", 3780),
                                            ("3:99", None), ("live", None), (None, None)])
def test_youtube_music_duration_parser(value, expected):
    assert candidate_duration({"duration": value}) == expected
