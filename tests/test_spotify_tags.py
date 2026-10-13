import base64
import shutil
import subprocess

import httpx
import pytest
from mutagen.flac import Picture
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus

from easyscrape.extractors import spotify_tags
from easyscrape.extractors.spotify_metadata import SpotifyTrack
from easyscrape.models import DownloadError, RetryableError

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5l8AAAAASUVORK5CYII="
)


def mock_cover(monkeypatch, status=200, mime="image/png", content=PNG):
    original = httpx.Client
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, headers={"content-type": mime}, content=content)
    )
    monkeypatch.setattr(spotify_tags.httpx, "Client",
                        lambda **options: original(transport=transport, **options))


def test_cover_download_validates_mime_and_bytes(monkeypatch):
    mock_cover(monkeypatch)
    assert spotify_tags.download_cover("https://i.scdn.co/image/public") == (PNG, "image/png")


@pytest.mark.parametrize("mime, content, error", [
    ("text/html", b"<html>login</html>", "MIME"),
    ("image/png", b"not really a PNG", "bytes"),
    ("image/jpeg", PNG, "bytes"),
])
def test_cover_rejects_invalid_payloads(monkeypatch, mime, content, error):
    mock_cover(monkeypatch, mime=mime, content=content)
    with pytest.raises(DownloadError, match=error):
        spotify_tags.download_cover("https://i.scdn.co/image/public")


def test_cover_size_limit(monkeypatch):
    mock_cover(monkeypatch)
    monkeypatch.setattr(spotify_tags, "MAX_COVER_BYTES", 16)
    with pytest.raises(DownloadError, match="limit"):
        spotify_tags.download_cover("https://i.scdn.co/image/public")


@pytest.mark.parametrize("url", ["http://i.scdn.co/image/x", "https://localhost/x",
                                "https://i.scdn.co.evil.example/x"])
def test_cover_host_and_https_are_required(url):
    with pytest.raises(DownloadError, match="supported public image host"):
        spotify_tags.download_cover(url)


def test_cover_rate_limit_is_retryable(monkeypatch):
    mock_cover(monkeypatch, status=429)
    with pytest.raises(RetryableError):
        spotify_tags.download_cover("https://i.scdn.co/image/public")


def packet_hash(path):
    return subprocess.check_output(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-c:a", "copy",
         "-f", "hash", "-hash", "sha256", "-"], timeout=15
    )


@pytest.mark.parametrize("ext, codec", [("m4a", "aac"), ("opus", "libopus")])
def test_actual_audio_tag_persistence_without_reencoding(tmp_path, ext, codec):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required to generate the audio fixture")
    path = tmp_path / f"test.{ext}"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=0.2", "-c:a", codec, str(path)],
        check=True, capture_output=True, timeout=15,
    )
    original_packets = packet_hash(path)
    track = SpotifyTrack("4uLU6hMCjMI75M1A2tKUQC", "Été", "Artist", "Album", 180, number=3)
    spotify_tags.write_tags(path, track, (PNG, "image/png"))
    assert packet_hash(path) == original_packets
    if ext == "m4a":
        audio = MP4(path)
        assert audio["\xa9nam"] == ["Été"]
        assert audio["\xa9ART"] == ["Artist"] and audio["\xa9alb"] == ["Album"]
        assert audio["trkn"] == [(3, 0)]
        assert bytes(audio["covr"][0]) == PNG
        assert audio["covr"][0].imageformat == MP4Cover.FORMAT_PNG
    else:
        audio = OggOpus(path)
        assert audio["title"] == ["Été"]
        assert audio["artist"] == ["Artist"] and audio["album"] == ["Album"]
        assert audio["tracknumber"] == ["3"]
        picture = Picture(base64.b64decode(audio["metadata_block_picture"][0]))
        assert picture.mime == "image/png" and picture.type == 3 and picture.data == PNG
