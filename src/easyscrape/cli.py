"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .dispatcher import Dispatcher
from .input import load_input
from .models import DownloadContext


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="easyscrape", description="Download media from a URL or a text file of URLs."
    )
    parser.add_argument("source", help="HTTP(S) URL or .txt file")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-o", "--out", type=Path)
    cookies = parser.add_mutually_exclusive_group()
    cookies.add_argument("--cookies", type=Path, help="Netscape-format cookies file")
    cookies.add_argument("--cookies-from-browser", choices=["firefox", "chrome", "edge"])
    parser.add_argument("--audio-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--limit-platform", help="Comma-separated platform names")
    parser.add_argument("--no-metadata", action="store_true")
    args = parser.parse_args(argv)
    try:
        batch = load_input(args.source)
        if args.cookies and not args.cookies.expanduser().is_file():
            parser.error(f"Cookie file does not exist: {args.cookies}")
        ctx = DownloadContext(
            out_dir=args.out or batch.default_out,
            cookies=args.cookies.expanduser().resolve() if args.cookies else None,
            cookies_from_browser=args.cookies_from_browser,
            audio_only=args.audio_only,
            force=args.force,
            quiet=args.quiet,
            metadata=not args.no_metadata,
        )
        limits = (
            {part.strip().lower() for part in args.limit_platform.split(",") if part.strip()}
            if args.limit_platform
            else None
        )
        if args.limit_platform is not None and not limits:
            parser.error("--limit-platform must contain at least one platform name")
        return Dispatcher(ctx).run(batch, limit_platforms=limits).exit_code
    except (OSError, UnicodeError) as exc:
        print(f"easyscrape: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("easyscrape: interrupted", file=sys.stderr)
        return 130
