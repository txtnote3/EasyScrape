"""Sequential, rate-limited batch execution with isolated URL failures."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.progress import Progress

from .input import InputBatch
from .models import DownloadContext, DownloadError, MediaResult, RunReport, timestamp
from .postprocess import read_json, save_report, save_result, write_json
from .registry import Registry, Route


class Dispatcher:
    def __init__(
        self,
        ctx: DownloadContext,
        registry: Registry | None = None,
        delay: float = 1.5,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.ctx, self.registry = ctx, registry if registry is not None else Registry()
        self.delay, self.max_attempts = max(0.0, delay), min(3, max(1, max_attempts))
        self.sleep, self.clock = sleep, clock
        self.last_request: dict[str, float] = {}

    def _pace(self, platform: str) -> None:
        remaining = self.delay - (self.clock() - self.last_request.get(platform, float("-inf")))
        if remaining > 0:
            self.sleep(remaining)
        self.last_request[platform] = self.clock()

    def _handle(self, route: Route, url: str) -> list[MediaResult]:
        for attempt in range(self.max_attempts):
            self._pace(route.platform)
            failure_count, skip_count = len(self.ctx.failures), len(self.ctx.skips)
            try:
                return route.handle(url, self.ctx)
            except DownloadError as exc:
                if not exc.retryable or attempt + 1 == self.max_attempts:
                    raise
                del self.ctx.failures[failure_count:]
                del self.ctx.skips[skip_count:]
                self.sleep(2**attempt)
        raise RuntimeError("No download attempt was made")

    def run(self, batch: InputBatch, limit_platforms: set[str] | None = None) -> RunReport:
        ctx = self.ctx
        ctx.out_dir = Path(ctx.out_dir).expanduser().resolve()
        ctx.out_dir.mkdir(parents=True, exist_ok=True)
        history_path = ctx.out_dir / ".easyscrape-state.json"
        history = read_json(history_path, {})
        ctx.completed = history if isinstance(history, dict) else {}
        report = RunReport(
            failed=[dict(row, platform="input", timestamp=timestamp()) for row in batch.invalid],
            skipped=[
                {"url": url, "platform": "input", "message": "Duplicate input URL"}
                for url in batch.duplicates
            ],
        )
        for error in self.registry.errors:
            ctx.log(error)
        with Progress(console=Console(stderr=True), disable=ctx.quiet, transient=True) as progress:
            task = progress.add_task("Downloading", total=len(batch.urls))
            original_callback = ctx.progress_callback
            original_slot = ctx.slot_callback
            ctx.slot_callback = self._pace
            try:
                for url in batch.urls:
                    ctx.failures.clear()
                    ctx.skips.clear()
                    platform = "unknown"
                    try:
                        route = self.registry.resolve(url)
                        if route is None:
                            raise DownloadError("No extractor matches this URL")
                        platform = ctx.platform = route.platform
                        progress.update(task, description=escape(f"{platform}: {url}"))
                        if limit_platforms is not None and platform not in limit_platforms:
                            ctx.record_skip(url, "Platform excluded by --limit-platform")
                        elif ctx.is_downloaded(url):
                            ctx.record_skip(
                                url, "Already downloaded; use --force to download again"
                            )
                        else:
                            ctx.invalidate_completed(url)
                            ctx.progress_callback = lambda percent, message, label=platform: (
                                progress.update(
                                    task,
                                    description=escape(f"{label}: {message}")
                                    + (f" {percent * 100:.0f}%" if percent is not None else ""),
                                )
                            )
                            results = self._handle(route, url)
                            if not results and not ctx.skips and not ctx.failures:
                                raise DownloadError("Extractor returned no media files")
                            source_files: list[str] = []
                            source_members: list[str] = []
                            for result in results:
                                try:
                                    files = save_result(result, ctx)
                                    source_files.extend(files)
                                    if result.url != url:
                                        source_members.append(result.url)
                                        ctx.completed[result.url] = {
                                            "files": files,
                                            "platform": result.platform,
                                        }
                                    report.successful.append(
                                        {
                                            "url": result.url,
                                            "platform": result.platform,
                                            "message": result.title or f"{len(files)} file(s)",
                                        }
                                    )
                                except Exception as exc:
                                    ctx.record_failure(result.url, str(exc), result.platform)
                            for skipped in ctx.skips:
                                if ctx.is_downloaded(skipped["url"]):
                                    source_files.extend(ctx.completed[skipped["url"]]["files"])
                                    source_members.append(skipped["url"])
                            if source_files and not ctx.failures:
                                ctx.completed[url] = {
                                    "files": source_files,
                                    "platform": platform,
                                    "children": list(dict.fromkeys(source_members)),
                                }
                    except Exception as exc:
                        ctx.record_failure(url, str(exc), platform)
                    try:
                        write_json(history_path, ctx.completed)
                    except OSError as exc:
                        ctx.record_failure(url, f"Could not save download history: {exc}", platform)
                    report.failed.extend(ctx.failures)
                    report.skipped.extend(ctx.skips)
                    progress.advance(task)
            finally:
                ctx.progress_callback = original_callback
                ctx.slot_callback = original_slot
        rendered = save_report(report, ctx.out_dir)
        if not ctx.quiet:
            print(rendered, end="")
        return report
