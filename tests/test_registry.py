import pytest

from easyscrape.input import canonicalize_url
from easyscrape.registry import Registry


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc",
        "https://youtube.com/shorts/abc",
        "https://youtu.be/abc",
        "https://music.youtube.com/watch?v=abc",
    ],
)
def test_youtube_hosts_resolve_to_youtube_route(url: str) -> None:
    route = Registry().resolve(url)

    assert route is not None
    assert route.platform == "youtube"
    assert route.fallback is False


def test_unknown_host_uses_generic_fallback() -> None:
    route = Registry().resolve("https://media.example.org/a/video")

    assert route is not None
    assert route.fallback is True
    assert route.platform == "generic"


def test_host_suffix_spoof_does_not_match_youtube() -> None:
    route = Registry().resolve("https://youtube.com.evil.example/watch?v=abc")

    assert route is not None
    assert route.platform != "youtube"
    assert route.fallback is True


@pytest.mark.parametrize("url", ["", "https:///missing-host", "file:///etc/passwd"])
def test_invalid_or_unsupported_urls_are_rejected_before_routing(url: str) -> None:
    with pytest.raises(ValueError):
        canonicalize_url(url)


def test_injected_registry_selects_first_matching_plugin() -> None:
    from easyscrape.models import DownloadContext, MediaResult
    from easyscrape.registry import Route

    def generic(url: str, context: DownloadContext) -> list[MediaResult]:
        del url, context
        return []

    route = Route(
        platform="example",
        handle=generic,
        matchers=[r"^https://media\.example\.org/"],
    )
    registry = Registry(routes=[route])

    assert registry.resolve("https://media.example.org/a") is route
    assert registry.resolve("https://other.example.org/a") is None
