"""Command line: `python -m ad_splicer --hooks H --bodies B --ctas C --out OUT`.
With no arguments the desktop window opens instead."""

from __future__ import annotations

import argparse
import sys

from . import __version__, engine
from .ffmpeg_bin import FFmpegNotFound


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ad_splicer", description="Make every hook x body x CTA ad variant.")
    p.add_argument("--hooks", required=True, help="folder of hook clips")
    p.add_argument("--bodies", required=True, help="folder of body clips")
    p.add_argument("--ctas", required=True, help="folder of CTA clips")
    p.add_argument("--out", required=True, help="output folder")
    p.add_argument("--mode", choices=["auto", "lossless", "normalize"], default="auto",
                   help="auto = lossless when clips match, otherwise convert each clip once")
    p.add_argument("--speed", choices=["gentle", "balanced", "fast"], default="gentle")
    p.add_argument("--keep-cache", action="store_true", help="keep converted clips for re-runs")
    p.add_argument("--dry-run", action="store_true", help="only show what would happen")
    p.add_argument("--version", action="version", version=__version__)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        from .gui import main as gui_main
        return gui_main()
    args = build_parser().parse_args(argv)
    try:
        analysis = engine.analyze(args.hooks, args.bodies, args.ctas, args.out, log=print)
    except (ValueError, OSError, FFmpegNotFound, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    print(analysis.summary())
    if args.dry_run:
        return 0

    def progress(done: int, total: int, label: str) -> None:
        print(f"[{done}/{total}] {label}", flush=True)

    try:
        result = engine.run(analysis, mode=args.mode, speed=args.speed, keep_cache=args.keep_cache,
                            log=print, progress=progress)
    except KeyboardInterrupt:
        print("Interrupted.")
        return 130
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    print(f"Made {result.made}, skipped {result.skipped}, failed {len(result.failed)} "
          f"in {result.seconds:.0f}s. Output: {analysis.out_dir}")
    return 1 if result.failed else 0
