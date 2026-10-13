import json
from pathlib import Path, PureWindowsPath

import pytest

from easyscrape.dispatcher import Dispatcher
from easyscrape.models import (
    DownloadContext,
    DownloadError,
    MediaResult,
    RetryableError,
    safe_filename,
)
from easyscrape.registry import Registry, Route


def make_registry(handle, platform: str = "example") -> Registry:
    return Registry(
        routes=[
            Route(
                platform=platform,
                handle=handle,
                matchers=[r"^https://example\.org/"],
            )
        ]
    )


def result_for(url: str, out_dir: Path, name: str = "media.bin") -> MediaResult:
    output = out_dir / name
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"media")
    return MediaResult(url=url, platform="example", title="sample", files=[output])


def test_dispatcher_continues_after_one_url_fails_and_writes_report_files(
    tmp_path: Path,
) -> None:
    def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
        if url.endswith("bad"):
            raise DownloadError("extractor rejected this item")
        return [result_for(url, ctx.out_dir)]

    ctx = DownloadContext(out_dir=tmp_path)
    report = Dispatcher(ctx, registry=make_registry(handle), delay=0).run(
        _batch("https://example.org/bad", "https://example.org/good")
    )

    assert len(report.successful) == 1
    assert report.successful[0]["url"] == "https://example.org/good"
    assert len(report.failed) == 1
    assert report.failed[0]["url"] == "https://example.org/bad"
    assert "extractor rejected" in report.failed[0]["message"]
    assert report.exit_code == 1
    assert (tmp_path / "report.txt").is_file()
    failures = json.loads((tmp_path / "failed.json").read_text(encoding="utf-8"))
    assert len(failures) == 1
    assert failures[0]["url"] == "https://example.org/bad"


def test_retryable_error_retries_at_most_three_times_with_exponential_backoff(
    tmp_path: Path,
) -> None:
    attempts = 0
    waits: list[float] = []

    def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RetryableError("temporary rate limit")
        return [result_for(url, ctx.out_dir)]

    report = Dispatcher(
        DownloadContext(out_dir=tmp_path),
        registry=make_registry(handle),
        delay=0,
        sleep=waits.append,
    ).run(_batch("https://example.org/retry"))

    assert attempts == 3
    assert waits == [1, 2]
    assert len(report.successful) == 1
    assert report.failed == []


def test_retryable_error_exhaustion_fails_after_three_attempts(
    tmp_path: Path,
) -> None:
    attempts = 0
    waits: list[float] = []

    def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
        nonlocal attempts
        attempts += 1
        raise RetryableError("service remains unavailable")

    report = Dispatcher(
        DownloadContext(out_dir=tmp_path),
        registry=make_registry(handle),
        delay=0,
        sleep=waits.append,
    ).run(_batch("https://example.org/exhausted"))

    assert attempts == 3
    assert waits == [1, 2]
    assert len(report.failed) == 1
    assert "service remains unavailable" in report.failed[0]["message"]


def test_non_retryable_error_is_not_retried(tmp_path: Path) -> None:
    attempts = 0

    def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
        nonlocal attempts
        attempts += 1
        raise DownloadError("permanent failure")

    report = Dispatcher(
        DownloadContext(out_dir=tmp_path),
        registry=make_registry(handle),
        delay=0,
        sleep=lambda _: None,
    ).run(_batch("https://example.org/fail"))

    assert attempts == 1
    assert len(report.failed) == 1


def test_empty_extractor_result_is_reported_as_a_failure(tmp_path: Path) -> None:
    report = Dispatcher(
        DownloadContext(out_dir=tmp_path),
        registry=make_registry(lambda url, ctx: []),
        delay=0,
    ).run(_batch("https://example.org/empty"))

    assert report.successful == []
    assert report.failed[0]["url"] == "https://example.org/empty"
    assert report.exit_code == 1


def test_idempotence_survives_dispatcher_instances_and_force_overrides_it(
    tmp_path: Path,
) -> None:
    calls = 0

    def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
        nonlocal calls
        calls += 1
        return [result_for(url, ctx.out_dir, f"item-{calls}.bin")]

    registry = make_registry(handle)
    batch = _batch("https://example.org/item")
    first = Dispatcher(DownloadContext(out_dir=tmp_path), registry=registry, delay=0).run(
        batch
    )
    second = Dispatcher(DownloadContext(out_dir=tmp_path), registry=registry, delay=0).run(
        batch
    )
    forced = Dispatcher(
        DownloadContext(out_dir=tmp_path, force=True), registry=registry, delay=0
    ).run(batch)

    assert len(first.successful) == 1
    assert len(second.skipped) == 1
    assert second.skipped[0]["url"] == "https://example.org/item"
    assert len(forced.successful) == 1
    assert calls == 2


def test_no_metadata_still_skips_existing_and_deleted_output_is_retried(
    tmp_path: Path,
) -> None:
    calls = 0

    def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
        nonlocal calls
        calls += 1
        return [result_for(url, ctx.out_dir, "one.bin")]

    registry = make_registry(handle)
    batch = _batch("https://example.org/item")
    ctx = DownloadContext(out_dir=tmp_path, metadata=False)
    first = Dispatcher(ctx, registry=registry, delay=0).run(batch)
    skipped = Dispatcher(ctx, registry=registry, delay=0).run(batch)
    (tmp_path / "one.bin").unlink()
    recovered = Dispatcher(ctx, registry=registry, delay=0).run(batch)

    assert len(first.successful) == 1
    assert len(skipped.skipped) == 1
    assert len(recovered.successful) == 1
    assert calls == 2


def test_partial_context_failures_and_skips_are_collected(tmp_path: Path) -> None:
    def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
        ctx.record_failure(url, "one child item failed", platform="example")
        ctx.record_skip(url, "already present")
        return [result_for(url, ctx.out_dir)]

    report = Dispatcher(
        DownloadContext(out_dir=tmp_path), registry=make_registry(handle), delay=0
    ).run(_batch("https://example.org/partial"))

    assert len(report.successful) == 1
    assert any("child item failed" in item["message"] for item in report.failed)
    assert any("already present" in item["message"] for item in report.skipped)


def test_partial_collection_failure_is_not_cached_complete_and_can_resume(
    tmp_path: Path,
) -> None:
    calls = 0
    child_one = "https://example.org/collection/one"
    child_two = "https://example.org/collection/two"

    def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
        nonlocal calls
        calls += 1
        if calls == 1:
            ctx.record_failure(child_two, "child unavailable", platform="example")
            return [result_for(child_one, ctx.out_dir, "one.bin")]
        assert ctx.is_downloaded(child_one)
        return [result_for(child_two, ctx.out_dir, "two.bin")]

    registry = make_registry(handle)
    batch = _batch("https://example.org/collection")
    first = Dispatcher(DownloadContext(out_dir=tmp_path), registry=registry, delay=0).run(batch)
    resumed = Dispatcher(DownloadContext(out_dir=tmp_path), registry=registry, delay=0).run(batch)
    repeated = Dispatcher(DownloadContext(out_dir=tmp_path), registry=registry, delay=0).run(batch)

    assert first.failed[0]["url"] == child_two
    assert first.successful[0]["url"] == child_one
    assert resumed.failed == []
    assert resumed.successful[0]["url"] == child_two
    assert len(repeated.skipped) == 1
    assert calls == 2


def test_forced_partial_collection_invalidates_parent_and_only_failed_child_cache(
    tmp_path: Path,
) -> None:
    calls = 0
    collection = "https://example.org/playlist"
    child_one = "https://example.org/playlist/one"
    child_two = "https://example.org/playlist/two"

    def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [
                result_for(child_one, ctx.out_dir, "one.bin"),
                result_for(child_two, ctx.out_dir, "two.bin"),
            ]
        if ctx.force:
            ctx.record_failure(child_two, "second child failed after force", platform="example")
            return [result_for(child_one, ctx.out_dir, "one.bin")]
        assert ctx.is_downloaded(child_one)
        assert not ctx.is_downloaded(child_two)
        return [result_for(child_two, ctx.out_dir, "two.bin")]

    registry = make_registry(handle)
    batch = _batch(collection)
    initial = Dispatcher(DownloadContext(out_dir=tmp_path), registry=registry, delay=0).run(batch)
    forced = Dispatcher(
        DownloadContext(out_dir=tmp_path, force=True), registry=registry, delay=0
    ).run(batch)

    state_path = tmp_path / ".easyscrape-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    cache = DownloadContext(out_dir=tmp_path, completed=state)
    assert initial.failed == []
    assert len(initial.successful) == 2
    assert forced.failed[0]["url"] == child_two
    assert child_one in state
    assert collection not in state
    assert cache.is_downloaded(child_one)
    assert not cache.is_downloaded(child_two)

    resumed = Dispatcher(DownloadContext(out_dir=tmp_path), registry=registry, delay=0).run(batch)

    assert resumed.failed == []
    assert resumed.successful[0]["url"] == child_two
    assert calls == 3


def test_forced_single_url_failure_invalidates_previous_success_cache(tmp_path: Path) -> None:
    calls = 0
    url = "https://example.org/single"

    def handle(item: str, ctx: DownloadContext) -> list[MediaResult]:
        nonlocal calls
        calls += 1
        if ctx.force:
            raise DownloadError("forced refresh failed")
        return [result_for(item, ctx.out_dir, "single.bin")]

    registry = make_registry(handle)
    batch = _batch(url)
    first = Dispatcher(DownloadContext(out_dir=tmp_path), registry=registry, delay=0).run(batch)
    forced = Dispatcher(
        DownloadContext(out_dir=tmp_path, force=True), registry=registry, delay=0
    ).run(batch)
    after_failure = Dispatcher(
        DownloadContext(out_dir=tmp_path), registry=registry, delay=0
    ).run(batch)

    assert len(first.successful) == 1
    assert len(forced.failed) == 1
    assert len(after_failure.successful) == 1
    assert after_failure.skipped == []
    assert calls == 3


def test_forced_child_failure_invalidates_parent_collection_cache(tmp_path: Path) -> None:
    calls: list[str] = []
    collection = "https://example.org/playlist"
    child_one = "https://example.org/playlist/one"
    child_two = "https://example.org/playlist/two"

    def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
        calls.append(url)
        if url == collection:
            if len(calls) == 1:
                return [
                    result_for(child_one, ctx.out_dir, "one.bin"),
                    result_for(child_two, ctx.out_dir, "two.bin"),
                ]
            if ctx.is_downloaded(child_one):
                ctx.record_skip(child_one, "successful child is still present")
            else:
                raise AssertionError("the successful child should remain cached")
            if ctx.is_downloaded(child_two):
                raise AssertionError("the failed child cache should have been invalidated")
            return [result_for(child_two, ctx.out_dir, "two.bin")]
        if url == child_two and ctx.force:
            raise DownloadError("standalone child refresh failed")
        raise AssertionError(f"unexpected input URL: {url}")

    registry = make_registry(handle)
    initial = Dispatcher(
        DownloadContext(out_dir=tmp_path), registry=registry, delay=0
    ).run(_batch(collection))
    forced_child = Dispatcher(
        DownloadContext(out_dir=tmp_path, force=True), registry=registry, delay=0
    ).run(_batch(child_two))

    state_path = tmp_path / ".easyscrape-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    cache = DownloadContext(out_dir=tmp_path, completed=state)
    assert len(initial.successful) == 2
    assert len(forced_child.failed) == 1
    assert collection not in state
    assert child_one in state
    assert child_two not in state
    assert cache.is_downloaded(child_one)
    assert not cache.is_downloaded(child_two)

    resumed = Dispatcher(
        DownloadContext(out_dir=tmp_path), registry=registry, delay=0
    ).run(_batch(collection))
    repeated = Dispatcher(
        DownloadContext(out_dir=tmp_path), registry=registry, delay=0
    ).run(_batch(collection))

    assert resumed.successful[0]["url"] == child_two
    assert any(row["url"] == child_one for row in resumed.skipped)
    assert repeated.skipped[0]["url"] == collection
    assert calls == [collection, child_two, collection]


def test_output_target_rejects_symlink_escape_when_supported(tmp_path: Path) -> None:
    output = tmp_path / "downloads"
    outside = tmp_path / "external"
    output.mkdir()
    outside.mkdir()
    link = output / "example"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    with pytest.raises(DownloadError, match="outside the output root"):
        DownloadContext(out_dir=output).target_dir("example")

    assert list(outside.iterdir()) == []


def test_symlinked_cache_file_outside_output_is_not_considered_downloaded(
    tmp_path: Path,
) -> None:
    output = tmp_path / "downloads"
    outside = tmp_path / "external"
    output.mkdir()
    outside.mkdir()
    actual = outside / "media.bin"
    actual.write_bytes(b"media")
    alias = output / "media.bin"
    try:
        alias.symlink_to(actual)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"file symlinks are unavailable: {exc}")

    ctx = DownloadContext(
        out_dir=output,
        completed={"https://example.org/item": {"files": [str(alias)]}},
    )

    assert not ctx.is_downloaded("https://example.org/item")


def test_plugin_cannot_report_outside_output_file_as_success(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.bin"
    outside.write_bytes(b"must stay outside")

    def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
        return [MediaResult(url=url, platform="example", title="escape", files=[outside])]

    report = Dispatcher(
        DownloadContext(out_dir=tmp_path), registry=make_registry(handle), delay=0
    ).run(_batch("https://example.org/escape"))

    assert report.successful == []
    assert len(report.failed) == 1
    assert report.failed[0]["url"] == "https://example.org/escape"
    assert not (tmp_path / "metadata.json").exists()
    assert outside.is_file()


def test_child_slot_requests_use_per_platform_scheduler(tmp_path: Path) -> None:
    sleeps: list[float] = []
    now = 0.0

    def sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    def clock() -> float:
        return now

    def handle(url: str, ctx: DownloadContext) -> list[MediaResult]:
        ctx.wait_for_slot()
        ctx.wait_for_slot("other")
        return [result_for(url, ctx.out_dir)]

    report = Dispatcher(
        DownloadContext(out_dir=tmp_path),
        registry=make_registry(handle),
        delay=1.5,
        sleep=sleep,
        clock=clock,
    ).run(_batch("https://example.org/paced"))

    assert len(report.successful) == 1
    assert sleeps == [1.5]


def test_unsafe_metadata_filename_cannot_escape_output_directory() -> None:
    name = safe_filename("../../CON: holiday/song.mp4")

    assert "/" not in name
    assert "\\" not in name
    assert name not in {".", "..", "CON"}


@pytest.mark.parametrize("value", ["COM¹", "LPT²", "CON .jpg"])
def test_windows_reserved_names_are_sanitized(value: str) -> None:
    name = safe_filename(value)

    assert not PureWindowsPath(name).is_reserved()


def _batch(*urls: str):
    from easyscrape.input import InputBatch

    return InputBatch(
        urls=list(urls), invalid=[], duplicates=[], source_path=None, default_out=Path.cwd()
    )
