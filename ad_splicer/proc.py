"""Run ffmpeg/ffprobe gently: low OS priority, no console pop-ups, cancellable."""

from __future__ import annotations

import os
import subprocess
import sys
import threading

IS_WIN = sys.platform == "win32"


def _platform_kwargs(low_priority: bool) -> dict:
    kw: dict = {}
    if IS_WIN:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if low_priority:
            flags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        kw["creationflags"] = flags
    elif low_priority:
        kw["preexec_fn"] = lambda: os.nice(10)
    return kw


def run_quiet(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_platform_kwargs(False),
    )


class Cancelled(Exception):
    pass


class FFmpegError(RuntimeError):
    pass


def run_cancellable(cmd: list[str], stop: threading.Event, low_priority: bool = True) -> None:
    """Run a command; kill it if `stop` gets set. Raises FFmpegError on failure."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_platform_kwargs(low_priority),
    )
    err_chunks: list[str] = []

    def _drain():
        assert proc.stderr is not None
        for line in proc.stderr:
            err_chunks.append(line)
            if len(err_chunks) > 200:
                del err_chunks[:100]

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()
    while True:
        try:
            proc.wait(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if stop.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                reader.join(timeout=1)
                raise Cancelled()
    reader.join(timeout=1)
    if proc.returncode != 0:
        raise FFmpegError("".join(err_chunks)[-1500:].strip() or f"ffmpeg exited with {proc.returncode}")
