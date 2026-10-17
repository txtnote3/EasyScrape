from __future__ import annotations

import functools
import http.server
import json
import re
import shutil
import subprocess
import threading
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yt_dlp
from yt_dlp.extractor.common import InfoExtractor

from easyscrape.extractors import generic
from easyscrape.extractors import yt_dlp_adapter as adapter
from easyscrape.models import DownloadContext, DownloadError, RetryableError

REAL_PROBE_STREAM_TYPES = adapter._probe_stream_types
REAL_WHICH = shutil.which


def media(identifier: str = "one", **overrides: Any) -> dict[str, Any]:
    return {
        "id": identifier,
        "title": "Test clip",
        "uploader": "Test author",
        "ext": "mp4",
        "vcodec": "h264",
        "acodec": "aac",
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
        stream_types = {"video", "audio"}
        warnings: list[str] = []
        source: Path | None = None
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
            for warning in self.warnings:
                self.options["logger"].warning(warning)
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
                filename.write_bytes(
                    self.source.read_bytes() if self.source else b"fixture media stream"
                )
            for hook in self.progress_hooks:
                hook({"status": "downloading", "downloaded_bytes": 5, "total_bytes": 10})
                hook({"status": "finished", "filename": str(original), "info_dict": info})
            downloaded = {**info, "requested_downloads": [{"filepath": str(original)}]}
            for hook in self.post_hooks:
                hook({"status": "finished", "info_dict": {**downloaded, "filepath": str(filename)}})
            return downloaded

    monkeypatch.setattr(adapter.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(
        adapter.shutil,
        "which",
        lambda executable: "/usr/bin/ffmpeg" if executable == "ffmpeg" else None,
    )
    # Unit downloads have explicitly mocked streams; real-file tests restore the actual probe.
    monkeypatch.setattr(adapter, "_probe_stream_types", lambda path: set(FakeYDL.stream_types))
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
    fake_ydl.extracted = media(ext=ext, vcodec="none", acodec="opus" if ext == "opus" else "aac")
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
    ctx = DownloadContext(tmp_path / "output", platform="generic", force=force, audio_only=True)
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
    ctx = DownloadContext(tmp_path / "output", platform="generic", force=force, audio_only=True)
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


@pytest.fixture
def real_stream_files(tmp_path: Path) -> dict[str, Path]:
    """Generate the ambiguous WebM container with genuinely different streams."""
    executable = REAL_WHICH("ffmpeg")
    assert executable, "Real stream validation tests require ffmpeg"
    files = {
        "audio": tmp_path / "audio.f251.webm",
        "video": tmp_path / "video.webm",
        "merged": tmp_path / "merged.mkv",
    }
    commands = [
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.1",
            "-c:a",
            "libopus",
            str(files["audio"]),
        ],
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=16x16:d=0.1",
            "-c:v",
            "libvpx-vp9",
            str(files["video"]),
        ],
        [
            "-i",
            str(files["video"]),
            "-i",
            str(files["audio"]),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            str(files["merged"]),
        ],
    ]
    for arguments in commands:
        subprocess.run(
            [executable, "-hide_banner", "-loglevel", "error", "-y", *arguments],
            check=True,
            capture_output=True,
            timeout=30,
        )
    return files


@pytest.fixture
def local_stream_files(real_stream_files: dict[str, Path]) -> Iterator[tuple[str, dict[str, Path]]]:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return None

    web_root = real_stream_files["audio"].parent
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(web_root))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", real_stream_files
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "kind, audio_only, succeeds",
    [
        ("audio", False, True),
        ("video", False, True),
        ("merged", False, True),
    ],
)
def test_real_http_download_validates_finished_streams(
    tmp_path: Path,
    local_stream_files: tuple[str, dict[str, Path]],
    kind: str,
    audio_only: bool,
    succeeds: bool,
) -> None:
    base_url, sources = local_stream_files
    source = sources[kind]
    ctx = DownloadContext(
        tmp_path / "output", platform="generic", audio_only=audio_only, quiet=True
    )

    results = adapter.handle(f"{base_url}/{source.name}", ctx)

    if succeeds:
        assert len(results) == 1 and not ctx.failures
        assert results[0].files[0].read_bytes() == source.read_bytes()
        actual = adapter._probe_stream_types(results[0].files[0])
        assert actual == (
            {"audio"} if kind == "audio" else {"video"} if kind == "video" else {"video", "audio"}
        )
        if kind == "audio":
            assert any("only provides audio" in note for note in results[0].warnings)
    else:
        assert not results and len(ctx.failures) == 1
        assert "no video stream" in ctx.failures[0]["message"]
        assert not [path for path in ctx.out_dir.rglob("*") if path.is_file()]


@pytest.mark.parametrize("kind", ["video", "merged"])
def test_real_video_and_merged_streams_pass_validation(
    real_stream_files: dict[str, Path], kind: str
) -> None:
    path = real_stream_files[kind]
    adapter._validate_finished_media([path], {"vcodec": "none"}, audio_only=False)
    assert "video" in (adapter._probe_stream_types(path) or set())
    if kind == "merged":
        assert "audio" in (adapter._probe_stream_types(path) or set())


def test_real_audio_only_download_stays_audio(
    tmp_path: Path, local_audio: tuple[str, Path]
) -> None:
    url, source = local_audio
    ctx = DownloadContext(tmp_path / "output", platform="generic", audio_only=True)
    result = adapter.handle(url, ctx)[0]
    assert result.files[0].read_bytes() == source.read_bytes()
    assert adapter._probe_stream_types(result.files[0]) == {"audio"}


def test_no_ffprobe_cannot_validate_preserved_file_from_new_metadata(
    tmp_path: Path, fake_ydl: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path)
    directory = ctx.target_dir() / "Test author" / "one"
    directory.mkdir(parents=True)
    previous = directory / "Test clip [one].mp4"
    previous.write_bytes(b"old media not represented by current extraction metadata")
    monkeypatch.setattr(adapter, "_probe_stream_types", lambda path: None)

    assert adapter.handle("https://youtu.be/one", ctx) == []

    assert len(ctx.failures) == 1 and "Use --force" in ctx.failures[0]["message"]
    assert previous.read_bytes() == b"old media not represented by current extraction metadata"
    ctx.force = True
    results = adapter.handle("https://youtu.be/one", ctx)
    assert len(results) == 1
    assert previous.read_bytes() == b"fixture media stream"


def test_real_audio_webm_rejected_despite_video_metadata(
    real_stream_files: dict[str, Path],
) -> None:
    path = real_stream_files["audio"]
    assert adapter._probe_stream_types(path) == {"audio"}
    with pytest.raises(DownloadError, match="no video stream"):
        adapter._validate_finished_media([path], {"vcodec": "vp9"}, audio_only=False)
    adapter._validate_finished_media([path], {"vcodec": "none"}, audio_only=True)


def test_adapter_rejects_actual_audio_webm_and_retains_runtime_advice(
    tmp_path: Path,
    fake_ydl: Any,
    real_stream_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ydl.source = real_stream_files["audio"]
    fake_ydl.extracted = media(ext="webm", vcodec="vp9")
    fake_ydl.warnings = [
        "No supported JavaScript runtime could be found. Only deno is enabled by default"
    ]
    # fake_ydl deliberately hides executable discovery; restore the actual probe explicitly.
    ffprobe = REAL_WHICH("ffprobe")
    assert ffprobe, "Real stream validation tests require ffprobe"
    monkeypatch.setattr(
        adapter.shutil, "which", lambda name: ffprobe if name == "ffprobe" else None
    )
    monkeypatch.setattr(adapter, "_probe_stream_types", REAL_PROBE_STREAM_TYPES)
    ctx = context(tmp_path, quiet=True)

    assert adapter.handle("https://youtu.be/one", ctx) == []

    assert len(ctx.failures) == 1
    assert "no video stream" in ctx.failures[0]["message"]
    assert "Install Deno" in ctx.failures[0]["message"]
    assert not list(ctx.out_dir.rglob("*.webm"))


@pytest.mark.parametrize(
    "selected, expected",
    [
        ({"vcodec": "h264"}, True),
        ({"vcodec": "none"}, False),
        ({"vcodec": "unknown", "height": 720}, True),
        ({"vcodec": "unknown"}, None),
        ({"vcodec": "none", "formats": [{"vcodec": "h264"}]}, False),
        ({"requested_formats": [{"vcodec": "vp9"}, {"vcodec": "none"}]}, True),
        ({"requested_formats": [{"vcodec": "none"}]}, False),
    ],
)
def test_no_ffprobe_selected_format_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: dict[str, Any], expected: bool | None
) -> None:
    path = tmp_path / "ambiguous.webm"
    # These are metadata-only unit tests; no file is claimed to have real probed streams.
    monkeypatch.setattr(adapter, "_probe_stream_types", lambda path: None)
    if expected:
        adapter._validate_finished_media([path], selected, audio_only=False)
    else:
        with pytest.raises(
            DownloadError, match="no video stream" if expected is False else "Cannot verify"
        ):
            adapter._validate_finished_media([path], selected, audio_only=False)


def test_no_ffprobe_audio_download_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapter, "_probe_stream_types", lambda path: None)
    adapter._validate_finished_media([tmp_path / "audio.webm"], {"vcodec": "none"}, audio_only=True)


def test_probe_excludes_attached_cover_art(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter.shutil, "which", lambda name: "ffprobe")
    payload = {
        "streams": [
            {"codec_type": "audio"},
            {"codec_type": "video", "disposition": {"attached_pic": 1}},
        ]
    }
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(payload), ""),
    )
    with pytest.raises(DownloadError, match="no video stream"):
        adapter._validate_finished_media([tmp_path / "artwork.m4a"], {"vcodec": "mjpeg"}, False)


@pytest.mark.parametrize("stdout, returncode", [("invalid json", 0), ("{}", 0), ("[]", 0), ("", 1)])
def test_failed_probe_cannot_fall_back_to_claimed_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: str, returncode: int
) -> None:
    monkeypatch.setattr(adapter.shutil, "which", lambda name: "ffprobe")
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, returncode, stdout, "probe failed"
        ),
    )
    with pytest.raises(DownloadError, match="ffprobe"):
        adapter._validate_finished_media([tmp_path / "file.mp4"], {"vcodec": "h264"}, False)


def test_runtime_warning_does_not_fail_valid_video(tmp_path: Path, fake_ydl: Any) -> None:
    warning = "No supported JavaScript runtime could be found. Only deno is enabled by default"
    fake_ydl.warnings = [warning]
    ctx = context(tmp_path, quiet=True)
    result = adapter.handle("https://youtu.be/one", ctx)[0]
    assert not ctx.failures
    assert result.title == "Test clip"
    assert len(result.warnings) == 1
    assert warning in result.warnings[0] and "Install Deno" in result.warnings[0]


def test_extraction_failure_keeps_runtime_warning_and_error_type(
    tmp_path: Path, fake_ydl: Any
) -> None:
    fake_ydl.warnings = ["No supported JavaScript runtime could be found"]
    fake_ydl.extraction_error = yt_dlp.utils.DownloadError("No video formats found")
    with pytest.raises(adapter.NoMediaError, match="Install Deno"):
        adapter.handle("https://youtu.be/one", context(tmp_path, quiet=True))


def test_js_runtime_discovery_passes_supported_library_parameters(
    tmp_path: Path, fake_ydl: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = {"deno": "/tools/deno", "node": "/tools/node", "bun": "/tools/bun", "qjs": "/tools/qjs"}
    monkeypatch.setattr(adapter.shutil, "which", lambda name: paths.get(name))
    adapter.handle("https://youtu.be/one", context(tmp_path))
    runtimes = fake_ydl.instances[0].options["js_runtimes"]
    assert runtimes == {
        "deno": {"path": "/tools/deno"},
        "node": {"path": "/tools/node"},
        "bun": {"path": "/tools/bun"},
        "quickjs": {"path": "/tools/qjs"},
    }


def test_playlist_continues_after_audio_only_output(
    tmp_path: Path, fake_ydl: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_ydl.extracted = {"_type": "playlist", "entries": [media("audio"), media("video")]}
    monkeypatch.setattr(
        adapter,
        "_probe_stream_types",
        lambda path: {"audio"} if "audio" in path.parts else {"video", "audio"},
    )
    ctx = context(tmp_path)
    results = adapter.handle("https://youtube.com/playlist?list=collection", ctx)
    assert [result.id for result in results] == ["video"]
    assert len(ctx.failures) == 1 and "no video stream" in ctx.failures[0]["message"]


def test_real_yt_dlp_merges_separate_video_and_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = REAL_WHICH("ffmpeg")
    assert executable, "The native merge integration test requires ffmpeg"
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.m4a"
    commands = [
        ["-f", "lavfi", "-i", "color=c=blue:s=16x16:d=0.1", "-c:v", "libx264", str(video)],
        ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.1", "-c:a", "aac", str(audio)],
    ]
    for arguments in commands:
        subprocess.run(
            [executable, "-hide_banner", "-loglevel", "error", "-y", *arguments],
            capture_output=True,
            check=True,
            timeout=30,
        )
    assert adapter._probe_stream_types(video) == {"video"}
    assert adapter._probe_stream_types(audio) == {"audio"}

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return None

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(tmp_path))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    class LocalFormatsIE(InfoExtractor):
        _VALID_URL = re.escape(base) + r"/merge$"

        def _real_extract(self, url: str) -> dict[str, Any]:
            return {
                "id": "merged",
                "title": "Native merge",
                "uploader": "Local fixture",
                "formats": [
                    {
                        "format_id": "video",
                        "url": f"{base}/video.mp4",
                        "ext": "mp4",
                        "vcodec": "h264",
                        "acodec": "none",
                        "width": 16,
                        "height": 16,
                    },
                    {
                        "format_id": "audio",
                        "url": f"{base}/audio.m4a",
                        "ext": "m4a",
                        "vcodec": "none",
                        "acodec": "aac",
                    },
                ],
            }

    real_downloader = yt_dlp.YoutubeDL

    class LocalDownloader(real_downloader):
        def __init__(self, options: dict[str, Any]) -> None:
            # Only extraction metadata is controlled. Downloads and FFmpegMergerPP remain real.
            super().__init__(options, auto_init=False)
            self.add_info_extractor(LocalFormatsIE())

    monkeypatch.setattr(adapter.yt_dlp, "YoutubeDL", LocalDownloader)
    ctx = DownloadContext(tmp_path / "output", platform="youtube", quiet=True)
    try:
        results = adapter.handle(f"{base}/merge", ctx)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert len(results) == 1 and not ctx.failures
    assert len(results[0].files) == 1
    merged = results[0].files[0]
    assert merged.parent == ctx.out_dir / "youtube" / "Local fixture" / "merged"
    assert adapter._probe_stream_types(merged) == {"video", "audio"}
    assert not list(ctx.out_dir.rglob("*.part"))
    assert not [path for path in ctx.out_dir.rglob(".easyscrape-*") if path.is_dir()]
