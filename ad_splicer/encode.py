"""The two ffmpeg operations: normalize a clip once, and losslessly join clips."""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

from . import ffmpeg_bin
from .probe import ClipInfo
from .proc import run_cancellable

SPEEDS = {"gentle": 0.5, "balanced": 0.75, "fast": 1.0}

# Visually lossless H.264 settings used when clips have to be converted.
CRF = 16
PRESET = "medium"
AUDIO_BITRATE = "256k"


def thread_count(speed: str) -> int:
    cores = os.cpu_count() or 2
    return max(1, int(cores * SPEEDS.get(speed, 0.5)))


def _partial(path: Path) -> Path:
    return path.with_name(path.stem + ".partial" + path.suffix)


def _finish(partial: Path, final: Path) -> None:
    os.replace(partial, final)


def normalized_path(clip: ClipInfo, cache_dir: Path, w: int, h: int, fps: str) -> Path:
    st = clip.path.stat()
    key = f"{clip.path.resolve()}|{st.st_size}|{st.st_mtime_ns}|{w}x{h}|{fps}|{CRF}|{PRESET}|{AUDIO_BITRATE}"
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    return cache_dir / f"{clip.path.stem[:40]}_{digest}.mp4"


def normalize_clip(
    clip: ClipInfo, dest: Path, w: int, h: int, fps: str, speed: str, stop: threading.Event
) -> Path:
    """Re-encode one source clip to the shared format (done once per clip, not per ad)."""
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = _partial(dest)
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},format=yuv420p"
    )
    cmd = [ffmpeg_bin.ffmpeg(), "-hide_banner", "-nostdin", "-y", "-i", str(clip.path)]
    if clip.has_audio:
        audio_map = "0:a:0"
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        audio_map = "1:a:0"
    threads = str(thread_count(speed))
    cmd += [
        "-map", "0:v:0", "-map", audio_map,
        "-vf", vf, "-filter_threads", threads,
        "-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF), "-profile:v", "high",
        "-threads", threads,
        "-af", "aresample=async=1,apad",
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", "48000", "-ac", "2",
        "-shortest", "-video_track_timescale", "90000",
        "-map_metadata", "-1", "-movflags", "+faststart",
        str(partial),
    ]
    try:
        run_cancellable(cmd, stop)
        _finish(partial, dest)
    finally:
        partial.unlink(missing_ok=True)
    return dest


def _concat_line(p: Path) -> str:
    s = p.resolve().as_posix().replace("'", "'\\''")
    return f"file '{s}'\n"


def concat_copy(
    parts: list[Path], output: Path, list_dir: Path, stop: threading.Event, hevc: bool = False
) -> None:
    """Join already-compatible clips with no re-encoding (zero quality loss)."""
    output.parent.mkdir(parents=True, exist_ok=True)
    list_dir.mkdir(parents=True, exist_ok=True)
    list_file = list_dir / f"{output.stem}.txt"
    list_file.write_text("".join(_concat_line(p) for p in parts), encoding="utf-8")
    partial = _partial(output)
    cmd = [
        ffmpeg_bin.ffmpeg(), "-hide_banner", "-nostdin", "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
    ]
    if hevc:
        cmd += ["-tag:v", "hvc1"]  # so QuickTime / iPhone / Meta play HEVC
    cmd += ["-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(partial)]
    try:
        run_cancellable(cmd, stop)
        _finish(partial, output)
    finally:
        partial.unlink(missing_ok=True)
        list_file.unlink(missing_ok=True)
