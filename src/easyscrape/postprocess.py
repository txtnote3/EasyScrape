"""Atomic metadata, download history, and batch reports."""

from __future__ import annotations

import io
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .models import DownloadContext, MediaResult, RunReport, timestamp


def atomic_write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = stream.name
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def save_result(result: MediaResult, ctx: DownloadContext) -> list[str]:
    paths = [str(Path(p).resolve()) for p in result.files]
    if not paths or not all(Path(p).is_file() for p in paths):
        raise ValueError("Extractor returned no existing media files")
    if not all(Path(p).is_relative_to(ctx.out_dir.resolve()) for p in paths):
        raise ValueError("Extractor returned files outside the output directory")
    if ctx.metadata:
        record = asdict(result)
        record.update(files=paths, date=result.date or timestamp())
        for folder in {Path(p).parent for p in paths}:
            path = folder / "metadata.json"
            previous = read_json(path, [])
            records = previous if isinstance(previous, list) else [previous]
            records = [
                item for item in records if isinstance(item, dict) and item.get("url") != result.url
            ]
            records.append(record)
            write_json(path, records[0] if len(records) == 1 else records)
    return paths


def render_report(report: RunReport) -> str:
    table = Table(title="EasyScrape download report")
    for label in ("Status", "Platform", "URL", "Details"):
        table.add_column(label, overflow="fold")
    for label, entries in (
        ("Downloaded", report.successful),
        ("Failed", report.failed),
        ("Skipped", report.skipped),
    ):
        for row in entries:
            table.add_row(
                label, Text(row.get("platform", "")), Text(row["url"]), Text(row.get("message", ""))
            )
    stream = io.StringIO()
    console = Console(file=stream, width=120, force_terminal=False)
    console.print(table)
    console.print(
        f"{len(report.successful)} successful / {len(report.failed)} failed / "
        f"{len(report.skipped)} skipped"
    )
    return stream.getvalue()


def save_report(report: RunReport, out_dir: Path) -> str:
    write_json(out_dir / "failed.json", report.failed)
    rendered = render_report(report)
    atomic_write(out_dir / "report.txt", rendered)
    return rendered
