"""Synthetic public presentation-schema unit tests; no live-access claim."""
import json
from dataclasses import replace

import httpx
import pytest

from easyscrape.extractors.spotify_metadata import (
    SpotifyResolver,
    SpotifyTrack,
    parse_embed,
    supplement_page,
)
from easyscrape.models import DownloadError, RetryableError

TRACK_ID = "4uLU6hMCjMI75M1A2tKUQC"
OTHER_ID = "5ChkMS8OtdzJeqyybCc9R5"


def embed(entity):
    data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script>'


def entry(ident=TRACK_ID, **changes):
    return {"uri": f"spotify:track:{ident}", "title": "Song", "subtitle": "Artist",
            "duration": 180123, **changes}


def resolver_with(transport):
    resolver = SpotifyResolver()
    resolver.client.close()
    resolver.client = httpx.Client(transport=transport)
    return resolver


def test_embed_track_duration_and_public_cover():
    page = embed({"name": "Song", "artists": [{"name": "Artist"}],
                  "album": {"name": "Album"}, "duration": 180123,
                  "coverArt": {"sources": [{"url": "https://i.scdn.co/small", "width": 64},
                                            {"url": "https://i.scdn.co/large", "width": 640}]}})
    track = parse_embed(page, "track", TRACK_ID)[0]
    assert track.title == "Song" and track.artist == "Artist" and track.album == "Album"
    assert track.duration_s == 180.123
    assert track.cover_url == "https://i.scdn.co/large"


@pytest.mark.parametrize("kind", ["album", "playlist"])
def test_complete_collection_preserves_every_item_and_order(kind):
    tracks = parse_embed(embed({"title": "Album", "totalTracks": 2,
                                "trackList": [entry(), entry(OTHER_ID)]}), kind, TRACK_ID)
    assert [track.id for track in tracks] == [TRACK_ID, OTHER_ID]
    assert [track.number for track in tracks] == [1, 2]
    assert tracks[0].album == ("Album" if kind == "album" else "")


@pytest.mark.parametrize("changes, expected, fragment", [
    ({}, None, "did not report"),
    ({"totalTracks": 3}, None, "only 1 of 3"),
    ({"totalTracks": 1, "next": "more"}, None, "only 1 of 1"),
    ({}, 5, "only 1 of 5"),
])
def test_partial_collections_are_reported_not_hidden(changes, expected, fragment):
    notes = []
    tracks = parse_embed(embed({"trackList": [entry()], **changes}), "playlist", TRACK_ID,
                         expected, notes)
    assert len(tracks) == 1 and any(fragment in note for note in notes)


def test_unavailable_entries_are_skipped_with_a_note():
    notes = []
    tracks = parse_embed(embed({"totalTracks": 2, "trackList": [entry(), {"title": "Gone"}]}),
                         "playlist", TRACK_ID, None, notes)
    assert len(tracks) == 1 and any("unavailable" in note for note in notes)


def test_collection_with_no_tracks_fails():
    with pytest.raises(DownloadError, match="no downloadable"):
        parse_embed(embed({"trackList": [{"title": "Gone"}]}), "album", TRACK_ID)


def test_oembed_title_thumbnail_alone_is_not_track_metadata():
    with pytest.raises(DownloadError, match="unavailable"):
        parse_embed(json.dumps({"title": "Song", "thumbnail_url": "https://i.scdn.co/a"}),
                    "track", TRACK_ID)


def test_json_ld_enriches_album_duration_and_skips_unrelated_recordings():
    track = SpotifyTrack(TRACK_ID, "Song", "Artist")
    page = '<script type="application/ld+json">' + json.dumps({"@graph": [
        {"@type": "MusicRecording", "url": f"https://open.spotify.com/track/{OTHER_ID}",
         "inAlbum": {"name": "Wrong album"}, "duration": "PT1H"},
        {"@type": "MusicRecording", "url": track.url,
         "inAlbum": {"name": "Correct album"}, "duration": "PT3M0.123S"},
    ]}) + '</script>'
    complete = supplement_page(track, page)
    assert complete.album == "Correct album" and complete.duration_s == 180.123


@pytest.mark.parametrize("bad_duration", [None, 0, float("inf"), float("nan")])
def test_unavailable_or_unreliable_duration_fails_honestly(bad_duration):
    resolver = resolver_with(httpx.MockTransport(lambda request: httpx.Response(200, text="")))
    try:
        track = parse_embed(embed(entry(duration=bad_duration)), "track", TRACK_ID)[0]
        with pytest.raises(DownloadError, match="reliable duration"):
            resolver.complete(replace(track, album="Album"))
    finally:
        resolver.close()


@pytest.mark.parametrize("status, expected", [(404, DownloadError), (429, RetryableError),
                                             (503, RetryableError)])
def test_public_request_errors_classify_transient_responses(status, expected):
    resolver = resolver_with(httpx.MockTransport(lambda request: httpx.Response(status)))
    try:
        with pytest.raises(expected):
            resolver.resolve(f"https://open.spotify.com/track/{TRACK_ID}")
    finally:
        resolver.close()


def test_metadata_timeouts_are_retryable_and_paced():
    waits = []
    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)
    resolver = resolver_with(httpx.MockTransport(timeout))
    resolver.pace = lambda: waits.append(True)
    try:
        with pytest.raises(RetryableError):
            resolver.resolve(f"https://open.spotify.com/track/{TRACK_ID}")
        assert waits == [True]
    finally:
        resolver.close()


def test_album_falls_back_to_page_description():
    page = ('<meta property="og:description" '
            'content="Billie Eilish · HIT ME HARD AND SOFT · Song · 2024"/>')
    track = supplement_page(SpotifyTrack(TRACK_ID, "Song", "Billie Eilish"), page)
    assert track.album == "HIT ME HARD AND SOFT"
