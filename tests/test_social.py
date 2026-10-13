import re

import pytest

from easyscrape.extractors import social, threads_adapter
from easyscrape.models import DownloadContext, DownloadError, MediaResult


@pytest.mark.parametrize("url, platform", [
    ("https://www.instagram.com/p/ABC/", "instagram"),
    ("https://m.facebook.com/photo.php?fbid=1", "facebook"),
    ("https://fb.watch/ABC/", "facebook"),
    ("https://x.com/user/status/1", "twitter"),
    ("https://twitter.com/user/status/1", "twitter"),
    ("https://www.reddit.com/r/sub/comments/ABC/", "reddit"),
    ("https://v.redd.it/ABC", "reddit"),
    ("https://www.pinterest.com/pin/123/", "pinterest"),
    ("https://pin.it/ABC", "pinterest"),
])
def test_social_routes(url, platform):
    assert any(re.search(p, url) for p in social.MATCHERS)
    assert social.platform_for(url) == platform


@pytest.mark.parametrize("url", [
    "https://instagram.com.evil.test/p/1", "https://evilinstagram.com/p/1",
    "https://facebook.com@evil.test/photo", "https://x.com.evil.test/status/1",
    "https://pinterest.comevil/pin/1", "https://reddit.com.evil.test/",
])
def test_hostile_suffix_not_routed(url):
    assert not any(re.search(p, url) for p in social.MATCHERS)
    with pytest.raises(DownloadError):
        social.platform_for(url)


@pytest.mark.parametrize("url", [
    "https://www.threads.net/@user/post/ABC", "https://www.threads.com/@user/post/ABC",
])
def test_threads_delegates_gallery(tmp_path, monkeypatch, url):
    calls = []
    monkeypatch.setattr(threads_adapter.gallery_dl_adapter, "handle", lambda u, c: calls.append(
        (u, c.platform)
    ) or [])
    assert any(re.search(p, url) for p in threads_adapter.MATCHERS)
    threads_adapter.handle(url, DownloadContext(tmp_path))
    assert calls == [(url, "threads")]


def test_threads_hostile_suffix():
    assert not any(re.search(p, "https://threads.net.evil.test/@u/post/a")
                   for p in threads_adapter.MATCHERS)


@pytest.mark.parametrize("url", [
    "https://x.com/user/status/1", "https://www.reddit.com/r/sub/comments/ABC/",
    "https://www.pinterest.com/pin/123/",
])
def test_mixed_posts_keep_galleries(tmp_path, monkeypatch, url):
    calls = []
    monkeypatch.setattr(social.gallery_dl_adapter, "handle", lambda u, c: calls.append(u) or [])
    social.handle(url, DownloadContext(tmp_path))
    assert calls == [url]


def test_explicit_video_calls_reusable_yt(tmp_path, monkeypatch):
    calls = []
    expected = [MediaResult("https://fb.watch/ABC", "facebook")]
    monkeypatch.setattr(social.yt_dlp_adapter, "handle", lambda u, c: calls.append(
        (u, c.platform, c.cookies_from_browser)
    ) or expected)
    result = social.handle("https://fb.watch/ABC", DownloadContext(
        tmp_path, cookies_from_browser="firefox",
    ))
    assert result == expected
    assert calls == [("https://fb.watch/ABC", "facebook", "firefox")]


def test_absent_video_falls_back_to_gallery(tmp_path, monkeypatch):
    def no_video(url, ctx):
        raise social.yt_dlp_adapter.NoMediaError("No video")

    calls = []
    monkeypatch.setattr(social.yt_dlp_adapter, "handle", no_video)
    monkeypatch.setattr(social.gallery_dl_adapter, "handle", lambda u, c: calls.append(u) or [])
    social.handle("https://x.com/user/status/1/video/1", DownloadContext(tmp_path))
    assert calls == ["https://x.com/user/status/1/video/1"]


def test_auth_failure_does_not_fall_back(tmp_path, monkeypatch):
    def fail(url, ctx):
        raise DownloadError("Login required")

    def forbidden(url, ctx):
        pytest.fail("Authentication failure must remain visible")

    monkeypatch.setattr(social.yt_dlp_adapter, "handle", fail)
    monkeypatch.setattr(social.gallery_dl_adapter, "handle", forbidden)
    with pytest.raises(DownloadError, match="Login required"):
        social.handle("https://fb.watch/ABC", DownloadContext(
            tmp_path, cookies_from_browser="firefox",
        ))


def test_instagram_requires_cookies(tmp_path):
    with pytest.raises(DownloadError, match="requires login cookies"):
        social.handle("https://www.instagram.com/p/ABC/", DownloadContext(tmp_path))
