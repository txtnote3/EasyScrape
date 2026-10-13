import shutil
import subprocess

import pytest

from easyscrape.extractors import spotify_adapter as adapter
from easyscrape.extractors.spotify_metadata import SpotifyTrack
from easyscrape.models import DownloadContext, DownloadError, MediaResult, RetryableError

TRACK_ID = "4uLU6hMCjMI75M1A2tKUQC"
OTHER_ID = "5ChkMS8OtdzJeqyybCc9R5"


def track(ident=TRACK_ID, **changes):
    return SpotifyTrack(ident, "Song", "Artist", "Album", 180, **changes)


def fake_resolver(monkeypatch, tracks, complete=None):
    closed = []
    class Resolver:
        def __init__(self, pace):
            self.pace = pace
        def resolve(self, url):
            self.pace()
            return tracks
        def complete(self, item):
            return complete(item) if complete else item
        def close(self):
            closed.append(True)
    monkeypatch.setattr(adapter, "SpotifyResolver", Resolver)
    return closed


def test_unmatched_track_failure_does_not_lose_later_collection_tracks(tmp_path, monkeypatch):
    closed = fake_resolver(monkeypatch, [track(), track(OTHER_ID)])
    def find(item, ctx):
        if item.id == TRACK_ID:
            raise DownloadError("No YouTube Music match")
        return "abcdefghijk"
    monkeypatch.setattr(adapter, "_find", find)
    monkeypatch.setattr(adapter, "_save", lambda item, ident, ctx:
                        MediaResult(item.url, "spotify", id=item.id))
    ctx = DownloadContext(tmp_path, platform="spotify")
    results = adapter.handle("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy", ctx)
    assert [result.id for result in results] == [OTHER_ID]
    assert len(ctx.failures) == 1 and ctx.failures[0]["url"] == track().url
    assert closed == [True]


def test_cached_collection_track_is_skipped_without_search(tmp_path, monkeypatch):
    fake_resolver(monkeypatch, [track()])
    existing = tmp_path / "existing.m4a"
    existing.write_bytes(b"existing audio")
    ctx = DownloadContext(tmp_path, completed={track().url: {"files": [str(existing)]}})
    monkeypatch.setattr(adapter, "_find", lambda *args: pytest.fail("cached track was searched"))
    assert adapter.handle(track().url, ctx) == []
    assert ctx.skips[0]["url"] == track().url


def test_child_metadata_retries_only_three_times_and_continues(tmp_path, monkeypatch):
    calls, waits = [], []
    def complete(item):
        calls.append(item.id)
        raise RetryableError("metadata timeout")
    fake_resolver(monkeypatch, [track()], complete)
    monkeypatch.setattr(adapter.time, "sleep", waits.append)
    ctx = DownloadContext(tmp_path)
    assert adapter.handle(track().url, ctx) == []
    assert calls == [TRACK_ID] * 3 and waits == [1, 2]
    assert len(ctx.failures) == 1


def test_source_retry_budget_belongs_to_dispatcher(tmp_path, monkeypatch):
    calls = []
    class Resolver:
        def __init__(self, pace):
            pass
        def resolve(self, url):
            calls.append(url)
            raise RetryableError("metadata timeout")
        def close(self):
            pass
    monkeypatch.setattr(adapter, "SpotifyResolver", Resolver)
    with pytest.raises(RetryableError):
        adapter.handle(track().url, DownloadContext(tmp_path))
    assert calls == [track().url]


def mock_audio_download(monkeypatch):
    def download(ident, staging, ctx):
        audio = staging / "audio.m4a"
        audio.write_bytes(b"test audio payload")
        return audio
    monkeypatch.setattr(adapter, "_download", download)
    monkeypatch.setattr(adapter, "write_tags", lambda *args: None)


def test_force_preserves_distinct_recordings_with_same_title(tmp_path, monkeypatch):
    mock_audio_download(monkeypatch)
    ctx = DownloadContext(tmp_path, force=True)
    first = adapter._save(track(), "abcdefghijk", ctx)
    second = adapter._save(track(OTHER_ID), "abcdefghijk", ctx)
    assert first.files != second.files
    assert first.files[0].is_file() and second.files[0].is_file()
    assert first.files[0].parent == tmp_path / "spotify" / "Artist" / "Album"


def test_nested_output_symlink_cannot_escape_root(tmp_path, monkeypatch):
    mock_audio_download(monkeypatch)
    root, outside = tmp_path / "out", tmp_path / "outside"
    (root / "spotify").mkdir(parents=True)
    outside.mkdir()
    try:
        (root / "spotify" / "Artist").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(DownloadError, match="outside"):
        adapter._save(track(), "abcdefghijk", DownloadContext(root))
    assert list(outside.iterdir()) == []


def fake_yt_download(monkeypatch, source, codec="mp4a.40.2", options_out=None):
    class Downloader:
        def __init__(self, options):
            self.options = options
            if options_out is not None:
                options_out.append(options)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, url, download):
            target = self.options["outtmpl"].replace("%(ext)s", source.suffix[1:])
            shutil.copyfile(source, target)
            for hook in self.options.get("progress_hooks", []):
                hook({"downloaded_bytes": 1, "total_bytes": 2})
            return {"acodec": codec, "requested_downloads": [{"filepath": target}]}
    monkeypatch.setattr(adapter.yt_dlp, "YoutubeDL", Downloader)


def test_download_uses_original_m4a_cookies_and_fractional_progress(tmp_path, monkeypatch):
    source = tmp_path / "source.m4a"
    source.write_bytes(b"original audio")
    stage = tmp_path / "stage"
    stage.mkdir()
    options, progress, slots = [], [], []
    fake_yt_download(monkeypatch, source, options_out=options)
    monkeypatch.setattr(adapter.shutil, "which", lambda cmd: None)
    ctx = DownloadContext(tmp_path, cookies=tmp_path / "cookies.txt",
                          cookies_from_browser="firefox", slot_callback=slots.append,
                          progress_callback=lambda fraction, message: progress.append(fraction))
    output = adapter._download("abcdefghijk", stage, ctx)
    assert output.read_bytes() == source.read_bytes() and output.suffix == ".m4a"
    assert progress == [0.5] and slots == ["youtube"]
    assert options[0]["format"] == "bestaudio[ext=m4a]"
    assert options[0]["cookiesfrombrowser"] == ("firefox",)
    assert options[0]["cookiefile"] == str(ctx.cookies)
    assert all(options[0][key] == 0 for key in
               ("retries", "fragment_retries", "extractor_retries", "file_access_retries"))


def test_real_webm_opus_remux_preserves_encoded_audio_packets(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required for Opus remux integration")
    source = tmp_path / "source.webm"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=0.2", "-c:a", "libopus", str(source)],
                   check=True, capture_output=True, timeout=15)
    stage = tmp_path / "stage"
    stage.mkdir()
    fake_yt_download(monkeypatch, source, codec="opus")
    output = adapter._download("abcdefghijk", stage, DownloadContext(tmp_path))
    def encoded_hash(path):
        return subprocess.check_output(
            ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-c:a", "copy",
             "-f", "hash", "-hash", "sha256", "-"], timeout=15
        )
    assert output.suffix == ".opus"
    assert encoded_hash(source) == encoded_hash(output)
