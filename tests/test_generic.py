import pytest

from easyscrape.extractors import generic
from easyscrape.models import DownloadContext, DownloadError, MediaResult


def test_falls_back_to_gallery_dl_when_yt_dlp_fails(tmp_path, monkeypatch):
    def fail(url, ctx):
        raise DownloadError("Unsupported URL")

    seen = []
    monkeypatch.setattr(generic.yt_dlp_adapter, "handle", fail)
    monkeypatch.setattr(generic, "_gallery_category", lambda url: "imgur")
    monkeypatch.setattr(
        generic.gallery_dl_adapter, "handle",
        lambda url, ctx: seen.append(ctx.platform) or [MediaResult(url, ctx.platform)],
    )
    result = generic.handle("https://imgur.com/a/x", DownloadContext(tmp_path))
    assert seen == ["imgur"] and result[0].platform == "imgur"


def test_original_error_is_kept_when_no_gallery_site_matches(tmp_path, monkeypatch):
    def fail(url, ctx):
        raise DownloadError("Unsupported URL")

    monkeypatch.setattr(generic.yt_dlp_adapter, "handle", fail)
    monkeypatch.setattr(generic, "_gallery_category", lambda url: None)
    with pytest.raises(DownloadError, match="Unsupported URL"):
        generic.handle("https://example.org/x", DownloadContext(tmp_path))


def test_both_errors_are_reported(tmp_path, monkeypatch):
    def fail(url, ctx):
        raise DownloadError("yt-dlp said no")

    def gallery_fail(url, ctx):
        raise DownloadError("gallery said no")

    monkeypatch.setattr(generic.yt_dlp_adapter, "handle", fail)
    monkeypatch.setattr(generic.gallery_dl_adapter, "handle", gallery_fail)
    monkeypatch.setattr(generic, "_gallery_category", lambda url: "imgur")
    ctx = DownloadContext(tmp_path)
    with pytest.raises(DownloadError, match="yt-dlp said no.*gallery said no"):
        generic.handle("https://imgur.com/a/x", ctx)
    assert ctx.platform == "generic"
