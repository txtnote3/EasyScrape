"""Shared pytest configuration."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _skip_when_symlinks_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Creating symlinks on Windows needs Developer Mode or an elevated shell
    # (WinError 1314). Skip the symlink regressions there instead of failing.
    original = Path.symlink_to

    def symlink_to(self: Path, target: object, target_is_directory: bool = False) -> None:
        try:
            original(self, target, target_is_directory)  # type: ignore[arg-type]
        except OSError as exc:
            caller = sys._getframe(1).f_code.co_filename
            if getattr(exc, "winerror", None) == 1314 and Path(caller).parent.name == "tests":
                pytest.skip("symlink creation requires privileges on this Windows account")
            raise

    monkeypatch.setattr(Path, "symlink_to", symlink_to)
