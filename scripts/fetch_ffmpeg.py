"""Download static ffmpeg + ffprobe for this OS into vendor/ffmpeg (used by the build)."""
import shutil
import stat
import sys
from pathlib import Path

from static_ffmpeg.run import get_or_fetch_platform_executables_else_raise

dest = Path(sys.argv[1] if len(sys.argv) > 1 else "vendor/ffmpeg")
dest.mkdir(parents=True, exist_ok=True)
for src in get_or_fetch_platform_executables_else_raise():
    target = dest / Path(src).name
    shutil.copy2(src, target)
    target.chmod(target.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print("bundled", target)
