"""Read URL batches without letting one invalid line reject the batch."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


@dataclass
class InputBatch:
    urls: list[str]
    invalid: list[dict[str, str]]
    duplicates: list[str]
    source_path: Path | None
    default_out: Path


def canonicalize_url(value: str) -> str:
    value = value.strip()
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise ValueError("Expected an HTTP or HTTPS URL")
        if any(c.isspace() or ord(c) < 32 for c in value) or parts.username or parts.password:
            raise ValueError("URL contains whitespace or embedded credentials")
        host = parts.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = f"[{host}]"
        port = parts.port
        if port and (parts.scheme.lower(), port) not in {("http", 80), ("https", 443)}:
            host += f":{port}"
        return urlunsplit((parts.scheme.lower(), host, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Invalid URL: {exc}") from exc


_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'“”]+", re.IGNORECASE)


def _json_urls(text: str) -> list[str]:
    """Accept a failed.json report or any JSON list of URLs / {"url": ...} records."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    items = data if isinstance(data, list) else [data]
    urls: list[str] = []
    for item in items:
        value = item.get("url") if isinstance(item, dict) else item
        if isinstance(value, str):
            urls.append(value)
    return urls


def load_input(source: str | list[str]) -> InputBatch:
    """Load one or more sources: URLs, .txt files (any prose around URLs is ignored) or .json."""
    sources = [source] if isinstance(source, str) else list(source)
    lines: list[str] = []
    paths: list[Path] = []
    for item in sources:
        if item.lower().startswith(("http:", "https:")):
            lines.append(item)
            continue
        path = Path(item).expanduser().resolve()
        if path.suffix.lower() in {".txt", ".json", ".md", ".csv", ".list"} or path.is_file():
            text = path.read_text(encoding="utf-8-sig")
            paths.append(path)
            if path.suffix.lower() == ".json":
                lines.extend(_json_urls(text))
            else:
                lines.extend(text.splitlines())
        else:
            lines.append(item)
    single = paths[0] if len(paths) == 1 and len(sources) == 1 else None
    batch = InputBatch(
        [], [], [], single, (single.parent if single else Path.cwd()) / "EasyScrape_Downloads"
    )
    seen: set[str] = set()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        candidates = [line]
        if re.search(r"\s", line):
            candidates = _URL_IN_TEXT.findall(line) or [line]
        for candidate in candidates:
            candidate = candidate.rstrip(",;)]}")
            try:
                url = canonicalize_url(candidate)
            except ValueError as exc:
                batch.invalid.append({"url": candidate, "message": str(exc)})
                continue
            if url in seen:
                batch.duplicates.append(url)
            else:
                batch.urls.append(url)
                seen.add(url)
    return batch
