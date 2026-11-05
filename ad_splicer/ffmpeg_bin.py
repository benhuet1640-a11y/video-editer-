"""Locate the ffmpeg / ffprobe executables.

Search order:
1. AD_SPLICER_FFMPEG / AD_SPLICER_FFPROBE environment variables
2. Binaries bundled inside the packaged app (PyInstaller) or ad_splicer/bin/
3. Whatever is on the system PATH
"""

from __future__ import annotations

import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path

EXE = ".exe" if sys.platform == "win32" else ""


class FFmpegNotFound(RuntimeError):
    pass


def _bundled_dirs() -> list[Path]:
    dirs = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass) / "ffmpeg")
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent / "ffmpeg")
    dirs.append(Path(__file__).resolve().parent / "bin")
    return dirs


@lru_cache(maxsize=None)
def find(tool: str) -> str:
    """Return the full path to `tool` ("ffmpeg" or "ffprobe")."""
    env = os.environ.get(f"AD_SPLICER_{tool.upper()}")
    if env and Path(env).is_file():
        return env
    for d in _bundled_dirs():
        candidate = d / f"{tool}{EXE}"
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(tool)
    if found:
        return found
    raise FFmpegNotFound(
        f"Could not find {tool}. Use the packaged app (FFmpeg is included), "
        f"or install FFmpeg: Mac `brew install ffmpeg`, Windows `winget install ffmpeg`."
    )


def ffmpeg() -> str:
    return find("ffmpeg")


def ffprobe() -> str:
    return find("ffprobe")
