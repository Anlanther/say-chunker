"""Shared pytest helpers."""

from __future__ import annotations

import stat
from pathlib import Path


def make_executable_script(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IRUSR | stat.S_IWUSR)
    return path
