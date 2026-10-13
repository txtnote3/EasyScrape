"""Read URL batches without letting one invalid line reject the batch."""

from __future__ import annotations

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


def load_input(source: str) -> InputBatch:
    path: Path | None = None
    if source.lower().startswith(("http:", "https:")):
        lines = [source]
    else:
        path = Path(source).expanduser().resolve()
        if path.suffix.lower() != ".txt":
            lines = [source]
            path = None
        else:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
    batch = InputBatch(
        [], [], [], path, (path.parent if path else Path.cwd()) / "EasyScrape_Downloads"
    )
    seen: set[str] = set()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            url = canonicalize_url(line)
        except ValueError as exc:
            batch.invalid.append({"url": line, "message": str(exc)})
            continue
        if url in seen:
            batch.duplicates.append(url)
        else:
            batch.urls.append(url)
            seen.add(url)
    return batch
