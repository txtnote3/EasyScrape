from __future__ import annotations

import functools
import http.server
import threading
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yt_dlp

from easyscrape.extractors import generic
from easyscrape.extractors import yt_dlp_adapter as adapter
from easyscrape.models import DownloadContext, DownloadError, RetryableError


def media(identifier: str = "one", **overrides: Any) -> dict[str, Any]:
    return {
        "id": identifier,
        "title": "Test clip",
        "uploader": "Test author",
        "ext": "mp4",
        "webpage_url": f"https://example.test/{identifier}",
        "upload_date": "20260102",
        "license": "CC BY",
        **overrides,
    }


@pytest.fixture
def fake_ydl(monkeypatch: pytest.MonkeyPatch) -> type[Any]:
    class FakeYDL:
        extracted: Any = media()
        extraction_error: Exception | None = None
        download_errors: dict[str, list[str]] = {}
        create_files = True
        merge = False
        outside: Path | None = None
        instances: list[Any] = []
        calls: list[str] = []

        def __init__(self, options: dict[str, Any]):
            self.params = dict(options)
            self.options = dict(options)
            self.progress_hooks: list[Any] = []
            self.post_hooks: list[Any] = []
            self.instances.append(self)

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def extract_info(self, url: str, download: bool) -> Any:
            assert download is False
            if self.extraction_error:
                raise self.extraction_error
            return self.extracted

        def add_progress_hook(self, hook: Any) -> None:
            self.progress_hooks.append(hook)

        def add_postprocessor_hook(self, hook: Any) -> None:
            self.post_hooks.append(hook)

        def process_ie_result(self, info: dict[str, Any], download: bool) -> dict[str, Any]:
            assert download is True
            assert "entries" not in info
            self.calls.append(info["id"])
            errors = self.download_errors.get(info["id"], [])
            if errors:
                raise yt_dlp.utils.DownloadError(errors.pop(0))
            template = self.params["outtmpl"]["default"]
            filename = Path(template % {"ext": info.get("ext", "mp4")})
            original = filename
            if self.merge:
                filename = filename.with_suffix(".mkv")
            if self.outside:
                filename = self.outside
            if self.create_files:
                filename.parent.mkdir(parents=True, exist_ok=True)
                filename.write_bytes(b"fixture media stream")
            for hook in self.progress_hooks:
                hook({"status": "downloading", "downloaded_bytes": 5, "total_bytes": 10})
                hook({"status": "finished", "filename": str(original), "info_dict": info})
            downloaded = {**info, "requested_downloads": [{"filepath": str(original)}]}
            for hook in self.post_hooks:
                hook({"status": "finished", "info_dict": {**downloaded, "filepath": str(filename)}})
            return downloaded

    monkeypatch.setattr(adapter.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(adapter.shutil, "which", lambda executable: "/usr/bin/ffmpeg")
    monkeypatch.setattr(adapter.time, "sleep", lambda seconds: None)
    return FakeYDL


def context(tmp_path: Path, **kwargs: Any) -> DownloadContext:
    return DownloadContext(tmp_path / "output", platform="youtube", **kwargs)


@pytest.fixture
def local_audio(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """Serve genuine generated media locally to exercise the real downloader."""
    web_root = tmp_path / "http"
    web_root.mkdir()
    source = web_root / "fixture.wav"
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x00\x00" * 800)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return None

    handler = functools.partial(Handler, directory=str(web_root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/fixture.wav", source
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_original_media_and_metadata(tmp_path: Path, fake_ydl: Any) -> None:
    result = adapter.handle("https://youtu.be/one", context(tmp_path))[0]
    assert result.platform == "youtube"
    assert result.files[0].is_absolute()
    assert result.files[0].read_bytes() == b"fixture media stream"
    assert result.files[0].parts[-4:-1] == ("youtube", "Test author", "one")
    assert result.title == "Test clip"
    assert result.author == "Test author"
    assert result.id == "one"
    assert result.date == "20260102"
    assert result.license_notes == "CC BY"
    assert fake_ydl.instances[0].options["format"] == "bestvideo*+bestaudio/best"
    assert "postprocessors" not in fake_ydl.instances[0].options


def test_merged_file_comes_from_final_hook(tmp_path: Path, fake_ydl: Any) -> None:
    fake_ydl.merge = True
    files = adapter.handle("https://youtu.be/one", context(tmp_path))[0].files
    assert len(files) == 1
    assert files[0].suffix == ".mkv"
    assert files[0].is_file()
    assert not files[0].with_suffix(".mp4").exists()


@pytest.mark.parametrize("ext", ["m4a", "opus"])
def test_audio_does_not_reencode(
    tmp_path: Path, fake_ydl: Any, monkeypatch: pytest.MonkeyPatch, ext: str
) -> None:
    fake_ydl.extracted = media(ext=ext)
    monkeypatch.setattr(adapter.shutil, "which", lambda executable: None)
    result = adapter.handle("https://youtu.be/one", context(tmp_path, audio_only=True))[0]
    assert result.files[0].suffix == f".{ext}"
    options = fake_ydl.instances[0].options
    assert options["format"] == "bestaudio"
    assert "postprocessors" not in options


def test_video_without_ffmpeg_uses_single_format(
    tmp_path: Path, fake_ydl: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapter.shutil, "which", lambda executable: None)
    adapter.handle("https://youtu.be/one", context(tmp_path))
    assert "+" not in fake_ydl.instances[0].options["format"]


def test_safe_paths_and_percent_template(tmp_path: Path, fake_ydl: Any) -> None:
    fake_ydl.extracted = media("../../evil", uploader="../../author", title="100% / unsafe\\title")
    ctx = context(tmp_path)
    result = adapter.handle("https://youtu.be/one", ctx)[0]
    assert result.files[0].is_relative_to(ctx.out_dir.resolve())
    assert "100%" in result.files[0].name
    assert result.files[0].is_file()


def test_rejects_external_downloaded_path(tmp_path: Path, fake_ydl: Any) -> None:
    fake_ydl.outside = tmp_path / "escaped.mp4"
    ctx = context(tmp_path)
    assert adapter.handle("https://youtu.be/one", ctx) == []
    assert len(ctx.failures) == 1
    assert "outside" in ctx.failures[0]["message"]


@pytest.mark.parametrize("extracted", [None, {"_type": "playlist", "entries": []}])
def test_empty_success_is_no_media_error(tmp_path: Path, fake_ydl: Any, extracted: Any) -> None:
    fake_ydl.extracted = extracted
    with pytest.raises(adapter.NoMediaError):
        adapter.handle("https://youtu.be/one", context(tmp_path))


def test_missing_actual_file_is_failure(tmp_path: Path, fake_ydl: Any) -> None:
    fake_ydl.create_files = False
    ctx = context(tmp_path)
    assert adapter.handle("https://youtu.be/one", ctx) == []
    assert len(ctx.failures) == 1
    assert "without producing" in ctx.failures[0]["message"]


def test_nested_playlist_preserves_partial_success(tmp_path: Path, fake_ydl: Any) -> None:
    fake_ydl.extracted = {
        "_type": "playlist",
        "id": "container",
        "entries": [
            media("one"),
            None,
            {"_type": "multi_video", "entries": [media("bad"), media("two")]},
        ],
    }
    fake_ydl.download_errors = {"bad": ["private video; sign in"]}
    ctx = context(tmp_path)
    results = adapter.handle("https://youtube.com/playlist?list=collection", ctx)
    assert [item.id for item in results] == ["one", "two"]
    assert len(ctx.failures) == 2
    assert "container" not in fake_ydl.calls


def test_playlist_skip_and_force(tmp_path: Path, fake_ydl: Any) -> None:
    fake_ydl.extracted = {"_type": "playlist", "entries": [media("one"), media("two")]}
    ctx = context(tmp_path)
    previous = ctx.out_dir / "previous.mp4"
    previous.parent.mkdir(parents=True)
    previous.write_bytes(b"existing")
    ctx.completed = {"https://example.test/one": {"files": [str(previous)]}}
    results = adapter.handle("https://youtube.com/playlist?list=collection", ctx)
    assert [item.id for item in results] == ["two"]
    assert fake_ydl.calls == ["two"]
    assert len(ctx.skips) == 1
    ctx.force = True
    adapter.handle("https://youtube.com/playlist?list=collection", ctx)
    assert fake_ydl.calls == ["two", "one", "two"]
    assert fake_ydl.instances[-1].options["overwrites"] is True


def test_external_cached_file_does_not_skip_download(tmp_path: Path, fake_ydl: Any) -> None:
    previous = tmp_path / "external.mp4"
    previous.write_bytes(b"external existing file")
    ctx = context(tmp_path)
    ctx.completed = {"https://example.test/one": {"files": [str(previous)]}}
    result = adapter.handle("https://youtu.be/one", ctx)[0]
    assert result.files[0].is_relative_to(ctx.out_dir.resolve())
    assert fake_ydl.calls == ["one"]
    assert not ctx.skips


def test_existing_author_symlink_is_rejected_before_download(tmp_path: Path, fake_ydl: Any) -> None:
    ctx = context(tmp_path)
    platform_dir = ctx.target_dir()
    escaped = tmp_path / "escaped"
    escaped.mkdir()
    (platform_dir / "Test author").symlink_to(escaped, target_is_directory=True)
    with pytest.raises(DownloadError, match="escapes"):
        adapter.handle("https://youtu.be/one", ctx)
    assert not fake_ydl.calls
    assert not list(escaped.iterdir())


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("suffix", [".wav.part", ".wav.ytdl", ".f137.wav.part", ".wav.part-Frag1"])
def test_real_download_never_opens_existing_sidecar_symlink(
    tmp_path: Path, local_audio: tuple[str, Path], force: bool, suffix: str
) -> None:
    url, source = local_audio
    ctx = DownloadContext(tmp_path / "output", platform="generic", force=force)
    directory = ctx.target_dir() / "unknown" / "fixture"
    directory.mkdir(parents=True)
    external = tmp_path / "external.txt"
    external.write_bytes(b"KEEP")
    sidecar = directory / ("fixture [fixture]" + suffix)
    sidecar.symlink_to(external)

    results = adapter.handle(url, ctx)

    assert external.read_bytes() == b"KEEP"
    assert sidecar.is_symlink()
    assert len(results) == 1 and not ctx.failures
    assert results[0].files == [directory / "fixture [fixture].wav"]
    assert results[0].files[0].read_bytes() == source.read_bytes()
    assert not list(directory.glob(".easyscrape-*"))


@pytest.mark.parametrize("force", [False, True])
def test_real_download_rejects_existing_final_file_symlink(
    tmp_path: Path, local_audio: tuple[str, Path], force: bool
) -> None:
    url, _ = local_audio
    ctx = DownloadContext(tmp_path / "output", platform="generic", force=force)
    directory = ctx.target_dir() / "unknown" / "fixture"
    directory.mkdir(parents=True)
    external = tmp_path / "external.txt"
    external.write_bytes(b"KEEP")
    destination = directory / "fixture [fixture].wav"
    destination.symlink_to(external)

    assert adapter.handle(url, ctx) == []

    assert external.read_bytes() == b"KEEP"
    assert destination.is_symlink()
    assert len(ctx.failures) == 1
    assert "symlink" in ctx.failures[0]["message"]
    assert not list(directory.glob(".easyscrape-*"))


def test_existing_regular_destination_preserved_unless_forced(
    tmp_path: Path, fake_ydl: Any
) -> None:
    ctx = context(tmp_path)
    directory = ctx.target_dir() / "Test author" / "one"
    directory.mkdir(parents=True)
    destination = directory / "Test clip [one].mp4"
    destination.write_bytes(b"KEEP")
    result = adapter.handle("https://youtu.be/one", ctx)[0]
    assert result.files == [destination]
    assert destination.read_bytes() == b"KEEP"
    ctx.force = True
    adapter.handle("https://youtu.be/one", ctx)
    assert destination.read_bytes() == b"fixture media stream"
    assert not list(directory.glob(".easyscrape-*"))


def test_retryable_item_has_three_attempt_limit(
    tmp_path: Path, fake_ydl: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_ydl.download_errors = {"one": ["HTTP Error 429"] * 8}
    slots: list[Any] = []
    monkeypatch.setattr(DownloadContext, "wait_for_slot", lambda self: slots.append(True))
    ctx = context(tmp_path)
    assert adapter.handle("https://youtu.be/one", ctx) == []
    assert len(fake_ydl.calls) == 3
    assert len(slots) == 3
    assert len(ctx.failures) == 1
    options = fake_ydl.instances[0].options
    assert options["retries"] == options["fragment_retries"] == options["extractor_retries"] == 0


def test_retryable_item_can_recover(tmp_path: Path, fake_ydl: Any) -> None:
    fake_ydl.download_errors = {"one": ["HTTP Error 503", "connection reset"]}
    assert len(adapter.handle("https://youtu.be/one", context(tmp_path))) == 1
    assert len(fake_ydl.calls) == 3


@pytest.mark.parametrize(
    "message, error_type",
    [
        ("HTTP Error 429", RetryableError),
        ("Unsupported URL: example", adapter.NoMediaError),
        ("No video formats found", adapter.NoMediaError),
        ("Private post: login required; no video formats", DownloadError),
    ],
)
def test_extraction_error_categories(
    tmp_path: Path, fake_ydl: Any, message: str, error_type: type[Exception]
) -> None:
    fake_ydl.extraction_error = yt_dlp.utils.DownloadError(message)
    with pytest.raises(error_type) as caught:
        adapter.handle("https://youtu.be/one", context(tmp_path))
    assert type(caught.value) is error_type


def test_cookies_and_browser_options(tmp_path: Path, fake_ydl: Any) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    ctx = context(tmp_path, cookies=cookies, cookies_from_browser="firefox:profile::container")
    adapter.handle("https://youtu.be/one", ctx)
    options = fake_ydl.instances[0].options
    assert options["cookiefile"] == str(cookies)
    assert options["cookiesfrombrowser"] == ("firefox", "profile", None, "container")
    assert adapter._browser_cookies("firefox::personal") == ("firefox", None, None, "personal")
    assert adapter._browser_cookies("chrome+gnomekeyring:Default") == (
        "chrome",
        "Default",
        "gnomekeyring",
        None,
    )


def test_missing_cookie_file_fails_early(tmp_path: Path, fake_ydl: Any) -> None:
    with pytest.raises(DownloadError, match="cookies file"):
        adapter.handle("https://youtu.be/one", context(tmp_path, cookies=tmp_path / "missing"))
    assert not fake_ydl.instances


@pytest.mark.parametrize("platform", ["instagram", "facebook"])
def test_session_warning_is_early(
    tmp_path: Path, fake_ydl: Any, capsys: pytest.CaptureFixture[str], platform: str
) -> None:
    ctx = DownloadContext(tmp_path / "output", platform=platform)
    adapter.handle(f"https://www.{platform}.com/video/one", ctx)
    assert "logged-in session may be required" in capsys.readouterr().err


def test_progress_and_generic_fallback(tmp_path: Path, fake_ydl: Any) -> None:
    progress: list[Any] = []
    ctx = DownloadContext(
        tmp_path / "output",
        progress_callback=lambda fraction, title: progress.append((fraction, title)),
    )
    result = generic.handle("https://example.test/one", ctx)[0]
    assert result.platform == "generic"
    assert progress[0] == (0.5, "Test clip")
    assert generic.FALLBACK is True
    assert generic.MATCHERS[0]("https://example.test/one")
