"""Scan + analyze folders, then run the whole job (used by both the GUI and the CLI)."""

from __future__ import annotations

import csv
import json
import shutil
import threading
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Callable

from . import encode
from .captions import store
from .captions.style import FONTS_DIR, to_words, write_ass
from .plan import Combo, build_combos, estimate_bytes, human_bytes
from .probe import ClipInfo, Compatibility, check_compatibility, normalize_target, probe
from .proc import Cancelled
from .scan import list_videos

CACHE_DIRNAME = ".ad_splicer_cache"
MANIFEST = "manifest.csv"
STATE_FILE = ".ad_splicer_state.json"
CAPTION_SUFFIX = "_CC"

LogFn = Callable[[str], None]
ProgressFn = Callable[[int, int, str], None]
Captions = dict[Path, store.CaptionDoc]


def _noop(*_a, **_k):
    pass


def _fps_label(fps: str) -> str:
    try:
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
    captions_on: bool = False

    @property
    def all_clips(self) -> list[ClipInfo]:
        return self.hooks + self.bodies + self.ctas

    def set_captions(self, on: bool) -> None:
        """Captioned videos get a `_CC` filename so they can live next to plain ones."""
        self.captions_on = on
        self.combos = build_combos(self.hooks, self.bodies, self.ctas, self.out_dir,
                                   CAPTION_SUFFIX if on else "")

    @property
    def pending(self) -> list[Combo]:
        return [c for c in self.combos if not c.done]

    def unique_clips(self) -> list[ClipInfo]:
        seen, out = set(), []
        for c in self.all_clips:
            if c.path not in seen:
                seen.add(c.path)
                out.append(c)
        return out

    @property
    def needs_conversion(self) -> bool:
        return self.captions_on or not self.compat.lossless_ok

    def estimated_bytes(self, normalize: bool | None = None) -> int:
        normalize = self.needs_conversion if normalize is None else normalize
        est = estimate_bytes(self.pending)
        if normalize:
            est += sum(c.size for c in self.unique_clips())  # temporary converted copies
        return est

    def free_bytes(self) -> int:
        d = self.out_dir
        while not d.exists() and d != d.parent:
            d = d.parent
        return shutil.disk_usage(d).free

    def summary(self) -> str:
        n = len(self.combos)
        pending = len(self.pending)
        nclips = len(self.unique_clips())
        lines = [
            f"{len(self.hooks)} hooks x {len(self.bodies)} bodies x {len(self.ctas)} CTAs = {n} videos"
            + (f" ({n - pending} already done, {pending} to make)" if pending != n else ""),
        ]
        if self.captions_on:
            lines.append(
                f"Captions ON: your {nclips} source clips are transcribed on this computer (free), you review "
                f"the text, then each clip is converted ONCE with captions burned in (visually lossless, "
                f"CRF {encode.CRF}) and all ads are joined losslessly. Files end in {CAPTION_SUFFIX}.mp4"
            )
        elif self.compat.lossless_ok:
            lines.append("Clips match: joining LOSSLESSLY (no re-encoding, original quality, very light on CPU).")
        else:
            lines.append(
                f"Clips don't all match, so each of the {nclips} source clips will be converted "
                f"ONCE to visually-lossless H.264 (CRF {encode.CRF}), then all ads are joined losslessly."
            )
        if self.needs_conversion:
            w, h, fps = normalize_target(self.all_clips)
            lines.append(f"Shared format: {w}x{h} @ {_fps_label(fps)} fps")
        if not self.compat.lossless_ok:
            lines.append("Clips that differ:")
            lines += [f"  - {p}" for p in self.compat.problems[:12]]
            if len(self.compat.problems) > 12:
                lines.append(f"  - ...and {len(self.compat.problems) - 12} more")
        est = self.estimated_bytes()
        free = self.free_bytes()
        lines.append(f"Disk space needed: ~{human_bytes(est)} (free: {human_bytes(free)})")
        if est > free * 0.95:
            lines.append("WARNING: probably not enough free disk space!")
        return "\n".join(lines)


def analyze(
    hooks_dir: str | Path, bodies_dir: str | Path, ctas_dir: str | Path, out_dir: str | Path,
    log: LogFn = _noop, captions: bool = False,
) -> Analysis:
    dirs = {"Hooks": Path(hooks_dir), "Bodies": Path(bodies_dir), "CTAs": Path(ctas_dir)}
    if not str(out_dir).strip():
        raise ValueError("Please choose an output folder")
    out = Path(out_dir).expanduser().resolve()
    for label, d in dirs.items():
        if not str(d).strip() or not d.is_dir():
            raise ValueError(f"{label} folder not found: {d}")
        if out == d.resolve():
            raise ValueError(f"Output folder must be different from the {label} folder")
    groups: dict[str, list[ClipInfo]] = {}
    for label, d in dirs.items():
        files = list_videos(d)
        if not files:
            raise ValueError(f"No videos found in the {label} folder ({d})")
        log(f"Reading {len(files)} {label.lower()}...")
        groups[label] = [probe(f) for f in files]
    all_clips = groups["Hooks"] + groups["Bodies"] + groups["CTAs"]
    a = Analysis(groups["Hooks"], groups["Bodies"], groups["CTAs"], out, [],
                 check_compatibility(all_clips))
    a.set_captions(captions)
    return a


@dataclass
class Controls:
    stop: threading.Event = field(default_factory=threading.Event)
    pause: threading.Event = field(default_factory=threading.Event)

    def checkpoint(self) -> None:
        while self.pause.is_set() and not self.stop.is_set():
            time.sleep(0.2)
        if self.stop.is_set():
            raise Cancelled()


# ---------------------------------------------------------------- captions
def transcribe_all(
    analysis: Analysis,
    quality: str = "accurate",
    speed: str = "gentle",
    controls: Controls | None = None,
    log: LogFn = _noop,
    progress: ProgressFn = _noop,
    transcriber=None,
) -> Captions:
    """Transcribe every source clip once (cached in <output>/captions/)."""
    controls = controls or Controls()
    clips = analysis.unique_clips()
    docs: Captions = {}
    todo = []
    for clip in clips:
        doc = store.load(analysis.out_dir, clip.path, clip.duration)
        if doc is not None:
            docs[clip.path] = doc
        else:
            todo.append(clip)
    if docs:
        log(f"Reusing saved captions for {len(docs)} clips.")
    if todo:
        if transcriber is None:
            from .captions.transcribe import Transcriber
            transcriber = Transcriber(quality, encode.thread_count(speed), status=log)
        for i, clip in enumerate(todo):
            controls.checkpoint()
            progress(i, len(todo), f"Transcribing {clip.path.name}")
            words, lang = transcriber.transcribe(clip.path, controls.stop)
            docs[clip.path] = store.new(analysis.out_dir, clip.path, clip.duration, words, lang,
                                        getattr(transcriber, "model_name", ""))
            log(f"  transcribed {clip.path.name}: {len(words)} words")
        progress(len(todo), len(todo), "Transcription done")
    return {c.path: docs[c.path] for c in clips}


# ---------------------------------------------------------------- render
def _load_state(out_dir: Path) -> dict:
    try:
        return json.loads((out_dir / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(out_dir: Path, state: dict) -> None:
    try:
        (out_dir / STATE_FILE).write_text(json.dumps(state, indent=0), encoding="utf-8")
    except OSError:
        pass


def _recipe(combo: Combo, captions: Captions | None) -> str:
    parts = []
    for c in combo.clips:
        cap = captions.get(c.path) if captions else None
        parts.append(f"{store.clip_id(c.path)}:{cap.fingerprint() if cap else '-'}")
    return "|".join(parts)


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
    captions: Captions | None = None,
) -> RunResult:
    controls = controls or Controls()
    start = time.time()
    if captions is not None and not analysis.captions_on:
        analysis.set_captions(True)
    if mode == "lossless" and (captions or not analysis.compat.lossless_ok):
        raise ValueError("Can't join losslessly: " + (
            "burning in captions needs one conversion per clip" if captions
            else "\n".join(analysis.compat.problems)))
    normalize = bool(captions) or mode == "normalize" or (mode == "auto" and not analysis.compat.lossless_ok)

    out_dir = analysis.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = out_dir / CACHE_DIRNAME
    state = _load_state(out_dir)
    recipes = {c.output.name: _recipe(c, captions) for c in analysis.combos}
    # done = file exists and was made from the same clips/captions (edited captions => remake)
    pending = [c for c in analysis.combos
               if not (c.done and state.get(c.output.name, recipes[c.output.name]) == recipes[c.output.name])]
    skipped = len(analysis.combos) - len(pending)
    needed = {c.path for combo in pending for c in combo.clips}
    uniq = [c for c in analysis.unique_clips() if c.path in needed] if normalize else []
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
            what = "with captions " if captions else ""
            log(f"Converting {len(uniq)} source clips once {what}to {w}x{h} @ {_fps_label(fps)} fps "
                f"(CRF {encode.CRF})...")
            for clip in uniq:
                controls.checkpoint()
                progress(step, total, f"Converting {clip.path.name}")
                doc = captions.get(clip.path) if captions else None
                ass = None
                extra = ""
                if doc is not None and doc.words:
                    extra = doc.fingerprint()
                    ass = cache / f"captions_{store.clip_id(clip.path)}_{extra}.ass"
                    write_ass(to_words(doc.words), w, h, ass, duration=clip.duration)
                dest = encode.normalized_path(clip, cache, w, h, fps, extra)
                encode.normalize_clip(clip, dest, w, h, fps, speed, controls.stop,
                                      ass_path=ass, fonts_dir=FONTS_DIR)
                source_for[clip.path] = dest
                step += 1
                log(f"  converted {clip.path.name}" + (" + captions" if ass else ""))

        hevc = (not normalize) and analysis.compat.reference is not None \
            and analysis.compat.reference.v_codec == "hevc"
        for combo in pending:
            controls.checkpoint()
            progress(step, total, f"Joining {combo.output.name}")
            parts = [source_for.get(c.path, c.path) for c in combo.clips]
            try:
                combo.output.unlink(missing_ok=True)
                encode.concat_copy(parts, combo.output, cache, controls.stop, hevc=hevc)
                state[combo.output.name] = recipes[combo.output.name]
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
    finally:
        _save_state(out_dir, state)

    write_manifest(analysis, captions)
    if not keep_cache and not cancelled and not failed and cache.exists():
        shutil.rmtree(cache, ignore_errors=True)
    return RunResult(made, skipped, failed, cancelled, normalize, time.time() - start)


def write_manifest(analysis: Analysis, captions: Captions | None = None) -> Path:
    path = analysis.out_dir / (f"manifest{CAPTION_SUFFIX}.csv" if analysis.captions_on else MANIFEST)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ad_name", "file", "hook_file", "body_file", "cta_file",
                    "duration_sec", "size_bytes", "captions", "status"])
        for c in analysis.combos:
            done = c.done
            w.writerow([
                c.ad_name, c.output.name, c.hook.path.name, c.body.path.name, c.cta.path.name,
                f"{c.duration:.2f}", c.output.stat().st_size if done else "",
                "yes" if captions else "no", "done" if done else "missing",
            ])
    return path
