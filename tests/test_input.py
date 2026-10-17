from pathlib import Path

import pytest

from easyscrape.input import canonicalize_url, load_input


def test_canonicalize_url_accepts_http_and_https_urls() -> None:
    assert canonicalize_url("https://www.youtube.com/watch?v=abc") == (
        "https://www.youtube.com/watch?v=abc"
    )
    assert canonicalize_url("http://example.org/item") == "http://example.org/item"


@pytest.mark.parametrize(
    "value",
    ["", "not a URL", "ftp://example.org/item", "https:///missing-host"],
)
def test_canonicalize_url_rejects_invalid_or_unsupported_urls(value: str) -> None:
    with pytest.raises(ValueError):
        canonicalize_url(value)


def test_text_input_skips_comments_blanks_and_duplicates_and_keeps_going(
    tmp_path: Path,
) -> None:
    source = tmp_path / "queue.txt"
    source.write_text(
        "# download list\n"
        "https://youtu.be/one\n"
        "\n"
        "not a URL\n"
        "https://youtu.be/one\n"
        "  # another comment\n"
        "https://example.org/two\n",
        encoding="utf-8",
    )

    batch = load_input(str(source))

    assert batch.urls == ["https://youtu.be/one", "https://example.org/two"]
    assert batch.duplicates == ["https://youtu.be/one"]
    assert [item["url"] for item in batch.invalid] == ["not a URL"]
    assert batch.source_path == source
    assert batch.default_out == tmp_path / "EasyScrape_Downloads"


def test_canonicalization_preserves_query_and_ignores_fragment_for_deduplication(
    tmp_path: Path,
) -> None:
    source = tmp_path / "variants.txt"
    source.write_text(
        "https://example.org/item?id=1#first\n"
        "https://example.org/item?id=1#second\n"
        "https://example.org/item?id=2#first\n",
        encoding="utf-8",
    )

    batch = load_input(str(source))

    assert batch.urls == ["https://example.org/item?id=1", "https://example.org/item?id=2"]
    assert batch.duplicates == ["https://example.org/item?id=1"]


def test_single_url_input_is_deduplicated_and_has_local_default_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    batch = load_input("https://example.org/media")

    assert batch.urls == ["https://example.org/media"]
    assert batch.invalid == []
    assert batch.duplicates == []
    assert batch.source_path is None
    assert batch.default_out == tmp_path / "EasyScrape_Downloads"


def test_input_accepts_multiple_sources_prose_and_failed_json(tmp_path):
    from easyscrape.input import load_input

    notes = tmp_path / "notes.txt"
    notes.write_text("watch this https://a.example/x, and https://b.example/y\n", encoding="utf-8")
    report = tmp_path / "failed.json"
    report.write_text('[{"url": "https://c.example/z", "message": "boom"}]', encoding="utf-8")
    batch = load_input([str(notes), str(report), "https://a.example/x"])
    assert batch.urls == ["https://a.example/x", "https://b.example/y", "https://c.example/z"]
    assert batch.duplicates == ["https://a.example/x"]
