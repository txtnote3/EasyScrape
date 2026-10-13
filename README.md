# EasyScrape

EasyScrape is a Python command-line tool for collecting media links from a URL or a text file and routing them to small extractor plugins. It uses `yt-dlp` for supported video and audio sources and `gallery-dl` for supported image galleries. It does not bypass DRM or access controls, and it sends no telemetry.

## Requirements and installation

EasyScrape requires Python 3.10 or newer. Install the package and its command-line entry point with pip:

```sh
python -m pip install .
easyscrape --help
```

For local development, install the optional tools as well:

```sh
python -m pip install -e ".[dev]"
```

Runtime dependencies include yt-dlp, gallery-dl, Rich, HTTPX, Mutagen, and ytmusicapi. The development extra adds pytest, Ruff, mypy, and the Python build frontend.

## Use

Pass one HTTP or HTTPS URL, or the path to a UTF-8 text file containing URLs. Blank lines and lines beginning with `#` are ignored; duplicate URLs are downloaded once per run.

```sh
easyscrape "https://www.youtube.com/watch?v=VIDEO_ID"
easyscrape links.txt --out ./downloads
easyscrape links.txt --audio-only --limit-platform youtube,spotify
```

The default output directory is `EasyScrape_Downloads` next to the input text file, or in the current directory for a single URL. Media is grouped by platform, with a `metadata.json` beside each item's files when metadata is enabled. The output root also contains `report.txt`, `failed.json`, and `.easyscrape-state.json` for the latest run and completed-download tracking. A failed URL is recorded while the remaining URLs continue. Existing completed downloads are skipped; pass `--force` to process them again. Use `--no-metadata` to omit per-item metadata files, `--quiet` to reduce console output, `--limit-platform NAME[,NAME...]` to filter handlers, or `--help` to see all options.

Some sites require a signed-in browser session. You can supply a Netscape-format cookie file with `--cookies PATH` or let yt-dlp read a local Firefox, Chrome, or Edge profile with `--cookies-from-browser BROWSER`. These options are mutually exclusive. Cookie files grant account access: keep them private, store them outside the repository, and never commit or share them. EasyScrape does not upload cookies to its own service; extractors may send them to the site being accessed.

## Supported sources and limits

Support depends on the matching adapter, upstream extractor, and specific URL.

| Source | Handling | Limits |
| --- | --- | --- |
| YouTube | yt-dlp extracts videos and collections. | Available formats depend on the source; combining separate video and audio streams may require ffmpeg. |
| YouTube Music | yt-dlp extracts supported music.youtube.com URLs; Spotify matching also searches YouTube Music. | Available results depend on the public catalog and source formats. |
| Other yt-dlp sites | The generic fallback delegates unmatched URLs to yt-dlp. | This is limited to URLs that yt-dlp can extract. |
| Spotify tracks, albums, and playlists | Public Spotify metadata is matched to YouTube Music results by title, artist, and duration (within two seconds). The downloaded audio comes from that YouTube Music match, not Spotify's stream. | Public unauthenticated collection metadata may be incomplete. Private streams are not accessed. Spotify provides no FLAC download source; output uses the matched M4A or Opus audio without re-encoding, with quality capped by the source (often around 256 kbps, not guaranteed). WebM Opus sources are losslessly remuxed to `.opus` with ffmpeg. |
| Instagram | gallery-dl handles photo posts; yt-dlp handles reels and video posts. | A signed-in browser session is required; pass a cookie file or browser profile. |
| Facebook | gallery-dl handles photo posts; yt-dlp handles video posts. | A signed-in browser session is required; pass a cookie file or browser profile. |
| X and Reddit | gallery-dl handles supported mixed-media posts, including supported video URLs. | Available post types depend on gallery-dl's site extractors. |
| Pinterest | gallery-dl handles supported pins. | Limited to content available through gallery-dl. |
| Threads | No adapter currently supports Threads downloads. | Threads requires a signed-in session, but neither yt-dlp nor gallery-dl currently provides a matching extractor. |

The `--audio-only` option asks applicable extractors for audio output. It does not improve the quality of the source. Use the service's official app and access methods when a site does not expose media to the supported extractors.

## Extractor plugins

Extractor modules live under `src/easyscrape/extractors/`. The registry discovers modules that expose `MATCHERS` and a callable `handle(url, context)` function. Specific URL matchers are considered before a generic fallback. Each handler returns a list of `MediaResult` values and delegates extraction to an upstream adapter. See [CONTRIBUTING.md](CONTRIBUTING.md#adding-an-extractor) for a complete plugin wrapper example.

## Development

Run the local checks with:

```sh
python -m pytest
ruff check .
mypy src/easyscrape
python -m build
```

The GitHub Actions workflow is configured to run these checks on Linux, Windows, and macOS with Python 3.10 and 3.12. CI does not download live media; extractor behavior should be tested with local fixtures and mocked upstream calls.
