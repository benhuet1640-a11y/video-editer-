"""Scan + analyze folders, then run the whole job (used by both the GUI and the CLI)."""

from __future__ import annotations

import csv
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import encode
from .plan import Combo, build_combos, estimate_bytes, human_bytes
from .probe import ClipInfo, Compatibility, check_compatibility, normalize_target, probe
from .proc import Cancelled
from .scan import list_videos

CACHE_DIRNAME = ".ad_splicer_cache"
MANIFEST = "manifest.csv"

LogFn = Callable[[str], None]
ProgressFn = Callable[[int, int, str], None]


def _noop(*_a, **_k):
    pass


def _fps_label(fps: str) -> str:
    try:
        from fractions import Fraction
        return f"{float(Fraction(fps)):.3g}"
    except (ValueError, ZeroDivisionError):
        return fps


@dataclass
class Analysis:
    hooks: list[ClipInfo]
    bodies: list[ClipInfo]
    ctas: list[ClipInfo]
    out_dir: Path
    combos: list[Combo]
    compat: Compatibility

    @property
    def all_clips(self) -> list[ClipInfo]:
        return self.hooks + self.bodies + self.ctas

    @property
    def pending(self) -> list[Combo]:
        return [c for c in self.combos if not c.done]

    def unique_clips(self) -> list[ClipInfo]:
        seen, out = set(), []
        for c in self.all_clips:
            key = c.path.resolve()
            if key not in seen:
                seen.add(key)
                out.append(c)
        return out

    def estimated_bytes(self, normalize: bool) -> int:
        est = estimate_bytes(self.pending)
        if normalize:
            est += sum(c.size for c in self.unique_clips())  # temporary normalized copies
        return est

    def free_bytes(self) -> int:
        d = self.out_dir
        while not d.exists() and d != d.parent:
            d = d.parent
        return shutil.disk_usage(d).free

    def summary(self) -> str:
        n = len(self.combos)
        pending = len(self.pending)
        normalize = not self.compat.lossless_ok
        lines = [
            f"{len(self.hooks)} hooks x {len(self.bodies)} bodies x {len(self.ctas)} CTAs = {n} videos"
            + (f" ({n - pending} already done, {pending} to make)" if pending != n else ""),
        ]
        if self.compat.lossless_ok:
            lines.append("Clips match: joining LOSSLESSLY (no re-encoding, original quality, very light on CPU).")
        else:
            lines.append(
                f"Clips don't all match, so each of the {len(self.unique_clips())} source clips will be converted "
                f"ONCE to visually-lossless H.264 (CRF {encode.CRF}), then all ads are joined losslessly."
            )
            w, h, fps = normalize_target(self.all_clips)
            lines.append(f"Shared format: {w}x{h} @ {_fps_label(fps)} fps")
            lines.append("Why:")
            lines += [f"  - {p}" for p in self.compat.problems[:12]]
            if len(self.compat.problems) > 12:
                lines.append(f"  - ...and {len(self.compat.problems) - 12} more")
        est = self.estimated_bytes(normalize)
        free = self.free_bytes()
        lines.append(f"Disk space needed: ~{human_bytes(est)} (free: {human_bytes(free)})")
        if est > free * 0.95:
            lines.append("WARNING: probably not enough free disk space!")
        return "\n".join(lines)


def analyze(
    hooks_dir: str | Path, bodies_dir: str | Path, ctas_dir: str | Path, out_dir: str | Path,
    log: LogFn = _noop,
) -> Analysis:
    dirs = {"Hooks": Path(hooks_dir), "Bodies": Path(bodies_dir), "CTAs": Path(ctas_dir)}
    out = Path(out_dir)
    for label, d in dirs.items():
        if not str(d).strip() or not d.is_dir():
            raise ValueError(f"{label} folder not found: {d}")
        if out.exists() and out.resolve() == d.resolve():
            raise ValueError(f"Output folder must be different from the {label} folder")
    groups: dict[str, list[ClipInfo]] = {}
    for label, d in dirs.items():
        files = list_videos(d)
        if not files:
            raise ValueError(f"No videos found in the {label} folder ({d})")
        log(f"Reading {len(files)} {label.lower()}...")
        groups[label] = [probe(f) for f in files]
    all_clips = groups["Hooks"] + groups["Bodies"] + groups["CTAs"]
    combos = build_combos(groups["Hooks"], groups["Bodies"], groups["CTAs"], out)
    return Analysis(groups["Hooks"], groups["Bodies"], groups["CTAs"], out, combos,
                    check_compatibility(all_clips))


@dataclass
class Controls:
    stop: threading.Event = field(default_factory=threading.Event)
    pause: threading.Event = field(default_factory=threading.Event)

    def checkpoint(self) -> None:
        while self.pause.is_set() and not self.stop.is_set():
            time.sleep(0.2)
        if self.stop.is_set():
            raise Cancelled()


@dataclass
class RunResult:
    made: int
    skipped: int
    failed: list[tuple[str, str]]
    cancelled: bool
    normalized: bool
    seconds: float


def run(
    analysis: Analysis,
    mode: str = "auto",          # auto | lossless | normalize
    speed: str = "gentle",
    keep_cache: bool = False,
    controls: Controls | None = None,
    log: LogFn = _noop,
    progress: ProgressFn = _noop,
) -> RunResult:
    controls = controls or Controls()
    start = time.time()
    if mode == "lossless" and not analysis.compat.lossless_ok:
        raise ValueError("Clips can't be joined losslessly:\n" + "\n".join(analysis.compat.problems))
    normalize = mode == "normalize" or (mode == "auto" and not analysis.compat.lossless_ok)

    out_dir = analysis.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = out_dir / CACHE_DIRNAME
    pending = analysis.pending
    skipped = len(analysis.combos) - len(pending)
    uniq = analysis.unique_clips() if (normalize and pending) else []
    total = len(uniq) + len(pending)
    step = 0
    made = 0
    failed: list[tuple[str, str]] = []
    cancelled = False
    source_for: dict[Path, Path] = {}

    if skipped:
        log(f"Skipping {skipped} videos that already exist.")
    try:
        if uniq:
            w, h, fps = normalize_target(analysis.all_clips)
            log(f"Converting {len(uniq)} source clips once to {w}x{h} @ {fps} fps (CRF {encode.CRF})...")
            for clip in uniq:
                controls.checkpoint()
                progress(step, total, f"Converting {clip.path.name}")
                dest = encode.normalized_path(clip, cache, w, h, fps)
                encode.normalize_clip(clip, dest, w, h, fps, speed, controls.stop)
                source_for[clip.path.resolve()] = dest
                step += 1
                log(f"  converted {clip.path.name}")

        hevc = (not normalize) and analysis.compat.reference is not None \
            and analysis.compat.reference.v_codec == "hevc"
        for combo in pending:
            controls.checkpoint()
            progress(step, total, f"Joining {combo.output.name}")
            parts = [source_for.get(c.path.resolve(), c.path) for c in combo.clips]
            try:
                encode.concat_copy(parts, combo.output, cache, controls.stop, hevc=hevc)
                made += 1
                log(f"  made {combo.output.name}")
            except Cancelled:
                raise
            except Exception as e:  # keep going with the rest
                failed.append((combo.output.name, str(e)))
                log(f"  FAILED {combo.output.name}: {str(e)[-300:]}")
            step += 1
        progress(total, total, "Done")
    except Cancelled:
        cancelled = True
        log("Stopped. Run again any time - finished videos are kept and skipped.")

    write_manifest(analysis)
    if not keep_cache and not cancelled and not failed and cache.exists():
        shutil.rmtree(cache, ignore_errors=True)
    return RunResult(made, skipped, failed, cancelled, normalize, time.time() - start)


def write_manifest(analysis: Analysis) -> Path:
    path = analysis.out_dir / MANIFEST
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ad_name", "file", "hook_file", "body_file", "cta_file",
                    "duration_sec", "size_bytes", "status"])
        for c in analysis.combos:
            done = c.done
            w.writerow([
                c.ad_name, c.output.name, c.hook.path.name, c.body.path.name, c.cta.path.name,
                f"{c.duration:.2f}", c.output.stat().st_size if done else "", "done" if done else "missing",
            ])
    return path
