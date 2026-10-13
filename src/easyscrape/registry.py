"""Discover extractor modules; specific matchers precede generic fallbacks."""

from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from .models import DownloadContext, MediaResult


@dataclass
class Route:
    platform: str
    handle: Callable[[str, DownloadContext], list[MediaResult]]
    matchers: list[Any] = field(default_factory=list)
    fallback: bool = False
    platform_for: Callable[[str], str] | None = None

    def matches(self, url: str) -> bool:
        return any(bool(m(url)) if callable(m) else bool(re.search(m, url)) for m in self.matchers)


class Registry:
    def __init__(self, package: str = "easyscrape.extractors", routes: list[Route] | None = None):
        self.errors: list[str] = []
        self.routes = list(routes) if routes is not None else self._discover(package)
        self.routes.sort(key=lambda route: route.fallback)

    def _discover(self, package: str) -> list[Route]:
        root = importlib.import_module(package)
        routes = []
        for item in sorted(pkgutil.iter_modules(root.__path__), key=lambda item: item.name):
            if item.name.startswith("_"):
                continue
            try:
                module = importlib.import_module(f"{package}.{item.name}")
                if hasattr(module, "MATCHERS") and callable(getattr(module, "handle", None)):
                    routes.append(
                        Route(
                            getattr(module, "PLATFORM", item.name),
                            module.handle,
                            list(module.MATCHERS),
                            getattr(module, "FALLBACK", item.name == "generic"),
                            getattr(module, "platform_for", None),
                        )
                    )
            except Exception as exc:
                self.errors.append(f"Extractor {item.name} could not load: {exc}")
        return routes

    def resolve(self, url: str) -> Route | None:
        for route in self.routes:
            if route.matches(url):
                return (
                    replace(route, platform=route.platform_for(url))
                    if route.platform_for
                    else route
                )
        return None
