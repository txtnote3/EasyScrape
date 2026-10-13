# Contributing

## Development setup

Use Python 3.10 or newer. Create a virtual environment and install the editable package with development tools:

```sh
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the same checks used by continuous integration before opening a change:

```sh
python -m pytest
ruff check .
mypy src/easyscrape
python -m build
```

Tests should be deterministic and must not fetch live media or require account credentials. Mock network-facing extractors or use local fixtures. Keep cookie files, browser data, downloaded media, and credentials out of commits.

## Adding an extractor

Add a module below `src/easyscrape/extractors/` with `PLATFORM`, `MATCHERS`, and a `handle(url, context)` function. Use the shared `DownloadContext` for output paths and options, and return `MediaResult` instances for completed files. Raise `DownloadError` for a URL that cannot be handled and `RetryableError` only when retrying a transient failure is useful. Keep matchers specific so they run before the generic yt-dlp fallback.

When yt-dlp can handle a source, a plugin can register a specific route by delegating to its existing adapter:

```python
from easyscrape.extractors.yt_dlp_adapter import handle as download_with_ytdlp
from easyscrape.models import DownloadContext, MediaResult

PLATFORM = "example"
MATCHERS = [r"^https://media\.example\.org/"]


def handle(url: str, context: DownloadContext) -> list[MediaResult]:
    return download_with_ytdlp(url, context)
```

Use a source-specific upstream adapter when yt-dlp does not support the site. The plugin should route URLs and pass the shared context through; keep media extraction in an existing or dedicated upstream adapter.

Add offline tests for URL matching, output naming, failure handling, and metadata behavior. Do not make routine tests depend on credentials, browser profiles, or live service availability.

## Scope and safety

EasyScrape uses upstream extractor capabilities and does not bypass DRM, paywalls, or access controls. Do not add code that circumvents a service's access protections. Treat cookie material as a secret: consume it locally, never log it, and never include it in fixtures or reports. Preserve the no-telemetry behavior.
