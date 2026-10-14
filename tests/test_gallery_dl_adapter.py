import base64
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest
from gallery_dl import config, exception, extractor, job
from gallery_dl.extractor.common import Extractor, Message

from easyscrape.extractors import gallery_dl_adapter as adapter
from easyscrape.models import DownloadContext, DownloadError, RetryableError


@pytest.mark.parametrize("root", [
    PureWindowsPath(r"C:\Users\alice\output\.easyscrape-gallery-stage"),
    PureWindowsPath(r"\\server\share\output\.easyscrape-gallery-stage"),
])
@pytest.mark.parametrize("extended_root", [False, True])
@pytest.mark.parametrize("extended_child", [False, True])
def test_windows_extended_paths_share_staging_anchor(root, extended_root, extended_child):
    def extended(path):
        text = str(path)
        prefix = "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text
        return PureWindowsPath(prefix)

    child = root / "alice" / "123" / "photo.jpg"
    if extended_root:
        root = extended(root)
    if extended_child:
        child = extended(child)
    original = str(child)
    if extended_root != extended_child:
        with pytest.raises(ValueError):
            child.relative_to(root)  # Reproduce gallery-dl's original anchor mismatch.
    assert adapter._relative_path(child, root) == PureWindowsPath(r"alice\123\photo.jpg")
    assert adapter._relative_path(child.parent, root) == PureWindowsPath(r"alice\123")
    assert str(child) == original  # File-operation paths retain their original prefix.


@pytest.mark.parametrize("child, root", [
    (r"\\?\C:\output-other\file.jpg", r"C:\output"),
    (r"\\?\D:\output\file.jpg", r"C:\output"),
    (r"\\?\UNC\server\other-share\output\file.jpg", r"\\server\share\output"),
    (r"\\?\UNC\other-server\share\output\file.jpg", r"\\server\share\output"),
    (r"\\?\UNC\server\share\output-other\file.jpg", r"\\server\share\output"),
    (r"\\.\C:\output\file.jpg", r"C:\output"),
])
def test_windows_extended_path_normalization_preserves_containment(child, root):
    with pytest.raises(ValueError):
        adapter._relative_path(PureWindowsPath(child), PureWindowsPath(root))


@pytest.mark.parametrize("root", [
    PureWindowsPath(r"C:\output"), PureWindowsPath(r"\\server\share\output"),
])
def test_windows_long_paths_keep_all_segments(root):
    relative = PureWindowsPath(*(["a" * 80] * 4), "photo.jpg")
    text = str(root / relative)
    child = PureWindowsPath(
        "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text
    )
    assert len(str(child)) > 260
    assert adapter._relative_path(child, root) == relative
    assert str(child).startswith("\\\\?\\")


def test_windows_comparisons_preserve_case_insensitive_paths():
    assert adapter._relative_path(
        PureWindowsPath(r"\\?\c:\OUTPUT\alice\photo.jpg"), PureWindowsPath(r"C:\output"),
    ) == PureWindowsPath(r"alice\photo.jpg")


def test_posix_paths_with_windows_prefix_text_are_not_normalized():
    root = PurePosixPath("/output")
    child = root / r"\\?\C:\photo.jpg"
    assert adapter._relative_path(child, root) == PurePosixPath(r"\\?\C:\photo.jpg")


class SampleExtractor(Extractor):
    category = "sample"
    subcategory = "post"
    pattern = r"https://sample.invalid/(\w+)"

    def items(self):
        metadata = {"user": {"username": "alice"}, "post_id": "123", "title": "A post"}
        yield Message.Directory, "", metadata.copy()
        for index in range(2):
            yield Message.Url, f"https://cdn.invalid/{index}.jpg", {
                **metadata, "filename": f"photo-{index}", "extension": "jpg",
            }


@pytest.fixture
def simulated_download(monkeypatch):
    observed = []

    def find(url):
        observed.append(config._config.copy())
        return SampleExtractor.from_url("https://sample.invalid/post")

    def download(self, url):
        filepath = Path(self.pathfmt.temppath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(b"downloaded image")
        return True

    monkeypatch.setattr(extractor, "find", find)
    monkeypatch.setattr(job.DownloadJob, "download", download)
    return observed


def test_real_download_job_tracks_saved_carousel(tmp_path, simulated_download):
    results = adapter.handle("https://sample.invalid/post", DownloadContext(
        tmp_path, platform="pinterest", quiet=True,
    ))
    assert len(results) == 1
    result = results[0]
    assert (result.title, result.author, result.id, result.platform) == (
        "A post", "alice", "123", "pinterest",
    )
    assert len(result.files) == 2
    assert all(p.read_bytes() == b"downloaded image" for p in result.files)
    assert all(p.parent == tmp_path / "pinterest" / "alice" / "123" for p in result.files)
    assert "reuse rights" in result.license_notes


def test_options_and_cookie_caches_restored(tmp_path, simulated_download):
    previous = config._config.copy()
    cookies = adapter.common.CACHE_COOKIES.copy()
    memory = adapter.common.CACHE_MEMORY.copy()
    config._config.clear()
    sentinel = {"extractor": {"pinterest": {"download": False}}, "cookies": "old-cookie"}
    config._config.update(sentinel)
    adapter.common.CACHE_COOKIES["sentinel"] = "secret"
    adapter.common.CACHE_MEMORY["sentinel"] = "secret"
    try:
        adapter.handle("https://sample.invalid/post", DownloadContext(tmp_path, quiet=True))
        assert config._config == sentinel
        assert adapter.common.CACHE_COOKIES["sentinel"] == "secret"
        assert adapter.common.CACHE_MEMORY["sentinel"] == "secret"
        opts = simulated_download[0]
        assert "cookies" not in opts and "cookies" not in opts["extractor"]
        assert "pinterest" not in opts["extractor"]
    finally:
        config._config.clear()
        config._config.update(previous)
        adapter.common.CACHE_COOKIES.clear()
        adapter.common.CACHE_COOKIES.update(cookies)
        adapter.common.CACHE_MEMORY.clear()
        adapter.common.CACHE_MEMORY.update(memory)


@pytest.mark.parametrize("status, error", [(16, DownloadError), (4, DownloadError)])
def test_job_status_cannot_silently_succeed(tmp_path, monkeypatch, status, error):
    monkeypatch.setattr(extractor, "find", lambda u: SampleExtractor.from_url(u))
    monkeypatch.setattr(job.DownloadJob, "run", lambda self: status)
    with pytest.raises(error):
        adapter.handle("https://sample.invalid/post", DownloadContext(tmp_path))


def test_empty_results_fail_and_restore_config(tmp_path, monkeypatch):
    before = config._config.copy()
    monkeypatch.setattr(extractor, "find", lambda u: SampleExtractor.from_url(u))
    monkeypatch.setattr(job.DownloadJob, "run", lambda self: 0)
    with pytest.raises(DownloadError, match="no downloadable files"):
        adapter.handle("https://sample.invalid/post", DownloadContext(tmp_path))
    assert config._config == before


def test_unsupported_extractor_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(extractor, "find", lambda u: None)
    with pytest.raises(DownloadError, match="does not support"):
        adapter.handle("https://sample.invalid/post", DownloadContext(tmp_path))


def test_actual_job_session_error_is_visible(tmp_path, monkeypatch):
    class BrokenExtractor(SampleExtractor):
        def items(self):
            raise exception.AuthenticationError()
            yield

    monkeypatch.setattr(extractor, "find", lambda u: BrokenExtractor.from_url(u))
    with pytest.raises(DownloadError, match="authentication failed"):
        adapter.handle("https://sample.invalid/post", DownloadContext(tmp_path))


def test_rate_limit_status_is_retryable(tmp_path, monkeypatch):
    monkeypatch.setattr(extractor, "find", lambda u: SampleExtractor.from_url(u))

    def run(self):
        logging.getLogger("sample").error("HTTP 429 Too many requests")
        return 4

    monkeypatch.setattr(job.DownloadJob, "run", run)
    with pytest.raises(RetryableError):
        adapter.handle("https://sample.invalid/post", DownloadContext(tmp_path))


def test_browser_spec_and_cookie_option(tmp_path, simulated_download, monkeypatch):
    spec = ("firefox", "profile", "", "work", "")
    loaded = []
    monkeypatch.setattr(adapter.gallery_cookies, "load_cookies", lambda s: loaded.append(s) or [])
    adapter.handle("https://sample.invalid/post", DownloadContext(
        tmp_path, cookies_from_browser="firefox:profile::work", quiet=True,
    ))
    assert loaded == [spec]
    assert simulated_download[0]["extractor"]["cookies"] == spec
    assert "cookies-from-browser" not in simulated_download[0]["extractor"]


def test_bad_browser_profile_fails_before_download(tmp_path, monkeypatch):
    def fail(spec):
        raise FileNotFoundError("Browser profile missing")

    monkeypatch.setattr(adapter.gallery_cookies, "load_cookies", fail)
    with pytest.raises(DownloadError, match="Cannot read browser cookies"):
        adapter.handle("https://sample.invalid/post", DownloadContext(
            tmp_path, cookies_from_browser="firefox",
        ))


def test_cookie_file_validated_and_passed(tmp_path, simulated_download):
    path = tmp_path / "cookies.txt"
    path.write_text("# Netscape HTTP Cookie File\n")
    adapter.handle("https://sample.invalid/post", DownloadContext(
        tmp_path, cookies=path, quiet=True,
    ))
    assert simulated_download[0]["extractor"]["cookies"] == str(path.resolve())


def test_bad_cookie_file_fails(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("not cookies")
    with pytest.raises(DownloadError, match="Netscape cookie file"):
        adapter.handle("https://sample.invalid/post", DownloadContext(tmp_path, cookies=path))


def test_auth_required_before_gallery_request(tmp_path):
    with pytest.raises(DownloadError, match="requires login cookies"):
        adapter.handle("https://www.instagram.com/p/123/", DownloadContext(
            tmp_path, platform="instagram",
        ))


def test_job_exception_restores_config(tmp_path, monkeypatch):
    before = config._config.copy()

    def fail(url):
        raise RuntimeError("session setup error")

    monkeypatch.setattr(extractor, "find", fail)
    with pytest.raises(DownloadError, match="session setup error"):
        adapter.handle("https://sample.invalid/post", DownloadContext(tmp_path))
    assert config._config == before


def test_each_media_download_obeys_platform_scheduler(tmp_path, simulated_download):
    calls = []
    adapter.handle("https://sample.invalid/post", DownloadContext(
        tmp_path, quiet=True, platform="reddit", slot_callback=calls.append,
    ))
    assert calls == ["reddit", "reddit"]
    opts = simulated_download[0]
    assert opts["extractor"]["retries"] == 0
    assert opts["downloader"]["retries"] == 0
    assert opts["extractor"]["sleep-request"] == 1.5
    raw = opts["downloader"]["ytdl"]["raw-options"]
    assert all(raw[key] == 0 for key in (
        "retries", "fragment_retries", "extractor_retries", "file_access_retries",
    ))
    assert opts["downloader"]["ytdl"]["deprecations"] is True


def test_partial_gallery_preserves_saved_media(tmp_path, simulated_download, monkeypatch):
    original = job.DownloadJob.download

    def download(self, url):
        if url.endswith("1.jpg"):
            return False
        return original(self, url)

    monkeypatch.setattr(job.DownloadJob, "download", download)
    ctx = DownloadContext(tmp_path, quiet=True, platform="reddit")
    results = adapter.handle("https://sample.invalid/post", ctx)
    assert len(results) == 1 and len(results[0].files) == 1
    assert len(ctx.failures) == 1
    assert "status 4" in ctx.failures[0]["message"]


@pytest.mark.parametrize("segment", ["pinterest", "pinterest/alice", "pinterest/alice/123"])
def test_directory_symlinks_cannot_write_outside_output(
    tmp_path, simulated_download, segment,
):
    root = tmp_path / "output"
    outside = tmp_path / "external"
    outside.mkdir()
    target = root / segment
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(DownloadError, match="outside the output root"):
        adapter.handle("https://sample.invalid/post", DownloadContext(
            root, quiet=True, platform="pinterest",
        ))
    assert not list(outside.iterdir())


def test_filename_symlink_checked_before_download(tmp_path, simulated_download):
    root = tmp_path / "output"
    outside = tmp_path / "external.jpg"
    outside.write_bytes(b"original")
    directory = root / "pinterest" / "alice" / "123"
    directory.mkdir(parents=True)
    (directory / "photo-0.jpg").symlink_to(outside)
    with pytest.raises(DownloadError, match="outside the output root"):
        adapter.handle("https://sample.invalid/post", DownloadContext(
            root, quiet=True, platform="pinterest", force=True,
        ))
    assert outside.read_bytes() == b"original"


def test_real_gallery_dl_http_download(tmp_path):
    # No extractor/job mocks: verify the installed API and HTTP downloader together.
    data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/"
        "6X+j1kAAAAASUVORK5CYII="
    )
    requests = []

    class ImageHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ImageHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/fixture.png"
        results = adapter.handle(url, DownloadContext(tmp_path, quiet=True, platform="pinterest"))
        assert len(results) == 1
        assert len(results[0].files) == 1
        assert results[0].files[0].read_bytes() == data
        assert results[0].files[0].is_relative_to(tmp_path)

        # Upstream creates .part after entering its downloader. Existing sidecars
        # must never be opened even with --force or extension adjustment.
        filepath = results[0].files[0]
        outside = tmp_path.parent / f"{tmp_path.name}-external"
        outside.write_bytes(b"KEEP")
        part = Path(str(filepath) + ".part")
        part.symlink_to(outside)
        forced = adapter.handle(url, DownloadContext(
            tmp_path, quiet=True, platform="pinterest", force=True,
        ))
        assert forced[0].files == [filepath]
        assert outside.read_bytes() == b"KEEP"
        assert part.is_symlink()
        assert filepath.read_bytes() == data
        assert len(requests) == 2

        # Repeated collections skip existing native filenames without HTTP traffic
        # or mtime changes, even though downloads use fresh private staging.
        mtime = filepath.stat().st_mtime_ns
        repeated = adapter.handle(url, DownloadContext(tmp_path, quiet=True, platform="pinterest"))
        assert repeated[0].files == [filepath]
        assert filepath.stat().st_mtime_ns == mtime
        assert len(requests) == 2
        assert not list(tmp_path.glob(".easyscrape-gallery-*"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
