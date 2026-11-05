"""Read clip properties with ffprobe and decide whether clips can be joined losslessly."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from . import ffmpeg_bin
from .proc import run_quiet

# Codecs we can stream-copy into an .mp4 container safely.
MP4_VIDEO_CODECS = {"h264", "hevc"}
MP4_AUDIO_CODECS = {"aac", "mp3"}


@dataclass
class ClipInfo:
    path: Path
    duration: float
    size: int
    v_codec: str
    width: int
    height: int
    fps: str
    pix_fmt: str
    rotation: int
    v_extradata: str
    has_audio: bool
    a_codec: str = ""
    sample_rate: int = 0
    channels: int = 0
    a_extradata: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def display_size(self) -> tuple[int, int]:
        if self.rotation % 180:
            return self.height, self.width
        return self.width, self.height

    @property
    def fps_float(self) -> float:
        try:
            return float(Fraction(self.fps))
        except (ValueError, ZeroDivisionError):
            return 0.0

    def join_key(self) -> dict[str, object]:
        """Every property that must be identical across clips for a lossless join."""
        return {
            "video codec": self.v_codec,
            "resolution": f"{self.width}x{self.height}",
            "frame rate": self.fps,
            "pixel format": self.pix_fmt,
            "rotation": self.rotation,
            "video encoder settings": self.v_extradata,
            "has audio": self.has_audio,
            "audio codec": self.a_codec,
            "audio sample rate": self.sample_rate,
            "audio channels": self.channels,
            "audio encoder settings": self.a_extradata,
        }

    def describe(self) -> str:
        w, h = self.display_size
        audio = f"{self.a_codec} {self.sample_rate}Hz {self.channels}ch" if self.has_audio else "no audio"
        return f"{self.v_codec} {w}x{h} @ {self.fps_float:.3g}fps, {audio}"


class ProbeError(RuntimeError):
    pass


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    rot = (stream.get("tags") or {}).get("rotate")
    if rot:
        try:
            return int(rot) % 360
        except ValueError:
            pass
    return 0


def probe(path: str | Path) -> ClipInfo:
    path = Path(path)
    cmd = [
        ffmpeg_bin.ffprobe(), "-v", "error", "-show_data_hash", "sha256",
        "-show_streams", "-show_format", "-of", "json", str(path),
    ]
    res = run_quiet(cmd, capture=True)
    if res.returncode != 0:
        raise ProbeError(f"Could not read {path.name}: {res.stderr.strip()[-300:]}")
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams", [])
    video = next(
        (s for s in streams if s.get("codec_type") == "video"
         and not (s.get("disposition") or {}).get("attached_pic")),
        None,
    )
    if video is None:
        raise ProbeError(f"{path.name} has no video track")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    try:
        duration = float(fmt.get("duration") or video.get("duration") or 0)
    except ValueError:
        duration = 0.0
    fps = video.get("avg_frame_rate") or "0/0"
    if fps in ("0/0", "0/1"):
        fps = video.get("r_frame_rate") or "0/0"
    info = ClipInfo(
        path=path,
        duration=duration,
        size=path.stat().st_size,
        v_codec=video.get("codec_name", "?"),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=fps,
        pix_fmt=video.get("pix_fmt", "?"),
        rotation=_rotation(video),
        v_extradata=video.get("extradata_hash", ""),
        has_audio=audio is not None,
    )
    if audio is not None:
        info.a_codec = audio.get("codec_name", "?")
        info.sample_rate = int(audio.get("sample_rate") or 0)
        info.channels = int(audio.get("channels") or 0)
        info.a_extradata = audio.get("extradata_hash", "")
    return info


@dataclass
class Compatibility:
    lossless_ok: bool
    problems: list[str]          # human readable reasons
    reference: ClipInfo | None   # the "majority" clip format


def check_compatibility(clips: list[ClipInfo]) -> Compatibility:
    """Can every clip be joined with a plain stream copy?"""
    if not clips:
        return Compatibility(False, ["No clips found"], None)
    keys = [tuple(c.join_key().items()) for c in clips]
    majority_key, _ = Counter(keys).most_common(1)[0]
    reference = clips[keys.index(majority_key)]
    ref = dict(majority_key)
    problems: list[str] = []

    if reference.v_codec not in MP4_VIDEO_CODECS:
        problems.append(f"Video codec '{reference.v_codec}' can't be copied into MP4 (needs H.264/HEVC)")
    if reference.has_audio and reference.a_codec not in MP4_AUDIO_CODECS:
        problems.append(f"Audio codec '{reference.a_codec}' can't be copied into MP4 (needs AAC)")

    for clip in clips:
        diffs = []
        for k, v in clip.join_key().items():
            if v != ref[k]:
                if k.endswith("encoder settings"):
                    diffs.append(f"different {k}")
                else:
                    diffs.append(f"{k} {v} vs {ref[k]}")
        if diffs:
            problems.append(f"{clip.path.parent.name}/{clip.path.name}: " + ", ".join(diffs))
    return Compatibility(not problems, problems, reference)


def normalize_target(clips: list[ClipInfo]) -> tuple[int, int, str]:
    """Pick the most common display resolution and frame rate as the shared format."""
    sizes = Counter(c.display_size for c in clips)
    (w, h), _ = sizes.most_common(1)[0]
    rates = Counter(c.fps for c in clips if c.fps_float > 0)
    fps = rates.most_common(1)[0][0] if rates else "30/1"
    # x264 with yuv420p needs even dimensions
    return w - (w % 2), h - (h % 2), fps
