"""Build the list of hook x body x CTA combinations and their output names."""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import product
from pathlib import Path

from .probe import ClipInfo

MAX_LABEL = 24


def clean_label(stem: str, max_len: int = MAX_LABEL) -> str:
    label = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-")
    return (label[:max_len].rstrip("-")) or "clip"


@dataclass
class Combo:
    hook_index: int
    body_index: int
    cta_index: int
    hook: ClipInfo
    body: ClipInfo
    cta: ClipInfo
    ad_name: str
    output: Path

    @property
    def clips(self) -> list[ClipInfo]:
        return [self.hook, self.body, self.cta]

    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.clips)

    @property
    def done(self) -> bool:
        return self.output.is_file() and self.output.stat().st_size > 0


def build_combos(
    hooks: list[ClipInfo], bodies: list[ClipInfo], ctas: list[ClipInfo], out_dir: str | Path,
    suffix: str = "",
) -> list[Combo]:
    out_dir = Path(out_dir)
    pad = max(2, len(str(max(len(hooks), len(bodies), len(ctas), 1))))
    combos = []
    for (hi, h), (bi, b), (ci, c) in product(enumerate(hooks, 1), enumerate(bodies, 1), enumerate(ctas, 1)):
        code = f"H{hi:0{pad}d}_B{bi:0{pad}d}_C{ci:0{pad}d}"
        name = f"{code}__{clean_label(h.path.stem)}_{clean_label(b.path.stem)}_{clean_label(c.path.stem)}"
        combos.append(Combo(hi, bi, ci, h, b, c, code, out_dir / f"{name}{suffix}.mp4"))
    return combos


def estimate_bytes(combos: list[Combo]) -> int:
    """Output size estimate: a joined file is roughly the sum of its parts."""
    return sum(sum(c.size for c in combo.clips) for combo in combos)


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
