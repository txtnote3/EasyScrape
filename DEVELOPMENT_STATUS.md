# Development environment status

This checkout uses a Python 3.10+ setuptools project with a `src/` package layout. The CLI is installed as `easyscrape`. Runtime dependencies are declared in `pyproject.toml`; the yt-dlp `default` extra installs the matching `yt-dlp-ejs` challenge package. The `dev` extra provides pytest, Ruff, mypy, and the build frontend.

## Local workflow

From the repository root, create or reuse a virtual environment and install the editable package:

```sh
python -m venv /workspace/.venvs/easyscrape
/workspace/.venvs/easyscrape/bin/python -m pip install -e '.[dev]'
```

Run the offline checks with:

```sh
/workspace/.venvs/easyscrape/bin/python -m pytest
/workspace/.venvs/easyscrape/bin/ruff check .
/workspace/.venvs/easyscrape/bin/mypy src/easyscrape
/workspace/.venvs/easyscrape/bin/python -m build
```

In the current cloud workspace, `/workspace/easyscrape-install.sh` repeats setup and all four checks. CI is configured for Linux, Windows, and macOS with Python 3.10 and 3.12. The cross-platform matrix has not been run from this workspace. For Windows Deno, FFmpeg, and ffprobe setup, follow [Windows media tools in the README](README.md#windows-media-tools).

## Initial setup verification (baseline)

The initial local setup run used Linux and Python 3.12.14, before the later Windows path, video-stream validation, and JavaScript challenge dependency changes. It completed with:

- 186 pytest tests passed, with no failures or skips.
- Ruff reported no issues.
- Mypy reported no issues across 18 package source files.
- `pip check` reported no broken requirements.
- `python -m build` produced both the source distribution and wheel.
- The wheel installed in a fresh virtual environment with its runtime dependencies. The installed CLI returned its version and help, and its registry resolved YouTube, Spotify, Instagram, Facebook, X, Reddit, Pinterest, Threads, and generic fallback URLs.

Unit and integration tests use local fixtures and mocked network services. The gallery adapter also downloaded from a local HTTP fixture. No live media was downloaded during the final setup run.

The baseline count above is historical and predates the later fixes.

## Current fix validation

The latest acceptance run used Linux and Python 3.12.14:

- `pytest -q`: 233 passed in 15.38 seconds, including the symlink regressions on Linux.
- `pytest -k 'not symlink' -q`: 215 passed and 18 deselected in 10.88 seconds. Native Windows was unavailable, so this filtered run was also on Linux.
- Ruff reported no issues; mypy reported no issues across 18 package source files.
- `pip check` reported no broken requirements. The updated editable install resolved yt-dlp 2026.8.19 with yt-dlp-ejs 0.8.0.
- `python -m build` produced both the source distribution and wheel.
- A local-format integration check used yt-dlp and FFmpeg to merge separate audio and video streams; ffprobe confirmed the result contained both streams.

These results verify Linux behavior only. They do not establish native Windows path, Winget, PATH-refresh, or FFmpeg behavior.

## Current limitations

Live Spotify metadata access could not be checked because the environment's request to `open.spotify.com` received an HTTP 403 from the network proxy. A live YouTube request also remains blocked by a proxy CONNECT 403, even with Deno 2.9.6 installed; no successful live YouTube download has been verified. Spotify behavior is covered by offline schema and media-tagging tests; its source audio is a strict YouTube Music match, not Spotify's stream. Threads URLs are recognized, but neither installed upstream extractor currently supports Threads downloads. Instagram and Facebook gallery/video paths require a signed-in session provided through a local cookie file or browser profile.

The CI operating-system and Python-version matrix is configured but remains unverified until GitHub Actions runs it. Site availability, authentication, source format selection, and live platform behavior can vary independently of these offline checks.

## JavaScript challenge runtime

The runtime dependency now uses `yt-dlp[default]`, allowing pip to install the compatible `yt-dlp-ejs` version with yt-dlp. The editable environment was refreshed with `python -m pip install --upgrade -e '.[dev]'`; pip resolved yt-dlp 2026.8.19 and yt-dlp-ejs 0.8.0, and `pip check` found no dependency conflicts. Deno or another supported JavaScript runtime is still installed separately and must be available on `PATH`; see the [Windows setup](README.md#windows-media-tools).
