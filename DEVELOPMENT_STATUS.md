# Development environment status

This checkout uses a Python 3.10+ setuptools project with a `src/` package layout. The CLI is installed as `easyscrape`. Runtime dependencies are declared in `pyproject.toml`; the `dev` extra provides pytest, Ruff, mypy, and the build frontend.

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

In the current cloud workspace, `/workspace/easyscrape-install.sh` repeats setup and all four checks. CI is configured for Linux, Windows, and macOS with Python 3.10 and 3.12. The cross-platform matrix has not been run from this workspace.

## Verification recorded

The final local setup run used Linux and Python 3.12.14. It completed with:

- 186 pytest tests passed, with no failures or skips.
- Ruff reported no issues.
- Mypy reported no issues across 18 package source files.
- `pip check` reported no broken requirements.
- `python -m build` produced both the source distribution and wheel.
- The wheel installed in a fresh virtual environment with its runtime dependencies. The installed CLI returned its version and help, and its registry resolved YouTube, Spotify, Instagram, Facebook, X, Reddit, Pinterest, Threads, and generic fallback URLs.

Unit and integration tests use local fixtures and mocked network services. The gallery adapter also downloaded from a local HTTP fixture. No live media was downloaded during the final setup run.

## Current limitations

Live Spotify metadata access could not be checked because the environment's request to `open.spotify.com` received an HTTP 403 from the network proxy. Spotify behavior is covered by offline schema and media-tagging tests; its source audio is a strict YouTube Music match, not Spotify's stream. Threads URLs are recognized, but neither installed upstream extractor currently supports Threads downloads. Instagram and Facebook gallery/video paths require a signed-in session provided through a local cookie file or browser profile.

The CI operating-system and Python-version matrix is configured but remains unverified until GitHub Actions runs it. Site availability, authentication, source format selection, and live platform behavior can vary independently of these offline checks.
