import shutil
import subprocess
from pathlib import Path

import pytest

if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
    pytest.skip("ffmpeg not installed", allow_module_level=True)


def make_clip(path: Path, color: str = "red", size: str = "320x240", rate: int = 30,
              seconds: float = 1.0, audio: bool = True, freq: int = 440) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", f"color=c={color}:s={size}:r={rate}:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}:sample_rate=48000"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-g", str(rate)]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture
def clip_factory():
    return make_clip


@pytest.fixture
def folders(tmp_path):
    """3 hooks x 2 bodies x 2 CTAs, all in the same format."""
    colors = ["red", "green", "blue", "yellow", "white", "gray", "orange"]
    d = {k: tmp_path / k for k in ("hooks", "bodies", "ctas")}
    i = 0
    for name, single, n in (("hooks", "hook", 3), ("bodies", "body", 2), ("ctas", "cta", 2)):
        for j in range(1, n + 1):
            make_clip(d[name] / f"{single} {j}.mp4", color=colors[i], freq=300 + 100 * i)
            i += 1
    d["out"] = tmp_path / "out"
    return d
