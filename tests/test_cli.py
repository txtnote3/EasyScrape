from pathlib import Path
from types import SimpleNamespace

import pytest

from easyscrape import cli


def test_cli_builds_context_from_url_and_passes_platform_limit(
    tmp_path: Path, monkeypatch
) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# fake cookie jar\n", encoding="utf-8")
    captured: list[dict[str, object]] = []

    class CapturingDispatcher:
        def __init__(self, context, **kwargs) -> None:
            self.context = context

        def run(self, batch, limit_platforms=None):
            captured.append(
                {"context": self.context, "batch": batch, "limit_platforms": limit_platforms}
            )
            return SimpleNamespace(exit_code=0)

    monkeypatch.setattr(cli, "Dispatcher", CapturingDispatcher)

    result = cli.main(
        [
            "https://youtube.com/watch?v=abc",
            "--out",
            str(tmp_path / "downloads"),
            "--cookies",
            str(cookies),
            "--audio-only",
            "--force",
            "--quiet",
            "--limit-platform",
            "youtube,pinterest",
            "--no-metadata",
        ]
    )

    browser_result = cli.main(
        ["https://youtube.com/watch?v=def", "--cookies-from-browser", "firefox"]
    )

    context = captured[0]["context"]
    assert result == 0
    assert browser_result == 0
    assert captured[0]["batch"].urls == ["https://youtube.com/watch?v=abc"]
    assert captured[0]["limit_platforms"] == {"youtube", "pinterest"}
    assert context.out_dir == tmp_path / "downloads"
    assert context.cookies == cookies
    assert context.cookies_from_browser is None
    assert context.audio_only is True
    assert context.force is True
    assert context.quiet is True
    assert context.metadata is False
    assert captured[1]["context"].cookies is None
    assert captured[1]["context"].cookies_from_browser == "firefox"


def test_cli_accepts_text_file_as_input(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "queue.txt"
    source.write_text("https://example.org/item\n", encoding="utf-8")
    observed: dict[str, object] = {}

    self_context = None

    class CapturingDispatcher:
        def __init__(self, context, **kwargs) -> None:
            nonlocal self_context
            self_context = context

        def run(self, batch, limit_platforms=None):
            observed["batch"] = batch
            return SimpleNamespace(exit_code=0)

    monkeypatch.setattr(cli, "Dispatcher", CapturingDispatcher)

    result = cli.main([str(source)])

    assert result == 0
    batch = observed["batch"]
    assert batch.urls == ["https://example.org/item"]
    assert batch.source_path == source
    assert self_context.out_dir == tmp_path / "EasyScrape_Downloads"


def test_cli_rejects_both_cookie_sources_together(tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# fake cookie jar\n", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "https://example.org/item",
                "--cookies",
                str(cookies),
                "--cookies-from-browser",
                "firefox",
            ]
        )

    assert error.value.code == 2
