"""Desktop window (Tkinter - built into Python on Mac and Windows)."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, engine

FOLDERS = [("hooks", "Hooks folder"), ("bodies", "Bodies folder"),
           ("ctas", "CTAs folder"), ("out", "Output folder")]


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"Ad Splicer {__version__}")
        root.minsize(720, 560)
        self.q: queue.Queue = queue.Queue()
        self.vars = {k: tk.StringVar() for k, _ in FOLDERS}
        self.speed = tk.StringVar(value="gentle")
        self.keep_cache = tk.BooleanVar(value=False)
        self.analysis: engine.Analysis | None = None
        self.controls: engine.Controls | None = None
        self.worker: threading.Thread | None = None

        frm = ttk.Frame(root, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        for row, (key, label) in enumerate(FOLDERS):
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(frm, textvariable=self.vars[key]).grid(row=row, column=1, sticky="ew", padx=6)
            ttk.Button(frm, text="Choose...", command=lambda k=key: self.pick(k)).grid(row=row, column=2)

        opts = ttk.Frame(frm)
        opts.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        ttk.Label(opts, text="Speed:").pack(side="left")
        for val, text in [("gentle", "Gentle (cool & quiet)"), ("balanced", "Balanced"), ("fast", "Fast")]:
            ttk.Radiobutton(opts, text=text, value=val, variable=self.speed).pack(side="left", padx=4)
        ttk.Checkbutton(opts, text="Keep converted clips", variable=self.keep_cache).pack(side="right")

        self.summary = tk.Text(frm, height=9, wrap="word", relief="flat")
        self.summary.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=4)
        self._set_summary("Choose your 3 clip folders and an output folder, then press Check.")

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=3, sticky="ew", pady=4)
        self.check_btn = ttk.Button(btns, text="Check", command=self.check)
        self.start_btn = ttk.Button(btns, text="Start", command=self.start, state="disabled")
        self.pause_btn = ttk.Button(btns, text="Pause", command=self.toggle_pause, state="disabled")
        self.stop_btn = ttk.Button(btns, text="Stop", command=self.stop, state="disabled")
        self.open_btn = ttk.Button(btns, text="Open output folder", command=self.open_output)
        for b in (self.check_btn, self.start_btn, self.pause_btn, self.stop_btn):
            b.pack(side="left", padx=(0, 6))
        self.open_btn.pack(side="right")

        self.bar = ttk.Progressbar(frm, mode="determinate")
        self.bar.grid(row=7, column=0, columnspan=3, sticky="ew", pady=4)
        self.status = ttk.Label(frm, text="")
        self.status.grid(row=8, column=0, columnspan=3, sticky="w")

        self.log = tk.Text(frm, height=10, wrap="none", state="disabled")
        self.log.grid(row=9, column=0, columnspan=3, sticky="nsew", pady=(4, 0))
        frm.rowconfigure(9, weight=1)
        frm.rowconfigure(5, weight=1)

        for v in self.vars.values():
            v.trace_add("write", lambda *_: self.start_btn.configure(state="disabled"))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll)

    # ---- helpers -------------------------------------------------------
    def pick(self, key: str) -> None:
        d = filedialog.askdirectory(title=dict(FOLDERS)[key], mustexist=(key != "out"))
        if d:
            self.vars[key].set(d)
            if key == "hooks" and not self.vars["out"].get():
                self.vars["out"].set(str(Path(d).parent / "Spliced Ads"))

    def _set_summary(self, text: str) -> None:
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("end", text)
        self.summary.configure(state="disabled")

    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _busy(self, running: bool) -> None:
        self.check_btn.configure(state="disabled" if running else "normal")
        self.start_btn.configure(state="disabled" if running or not self.analysis else "normal")
        self.pause_btn.configure(state="normal" if running else "disabled", text="Pause")
        self.stop_btn.configure(state="normal" if running else "disabled")

    def _bg(self, fn) -> None:
        self.worker = threading.Thread(target=fn, daemon=True)
        self.worker.start()

    # ---- actions -------------------------------------------------------
    def check(self) -> None:
        vals = {k: v.get().strip() for k, v in self.vars.items()}
        if not all(vals.values()):
            messagebox.showwarning("Ad Splicer", "Please choose all four folders.")
            return
        self.analysis = None
        self._busy(True)
        self.stop_btn.configure(state="disabled")
        self.pause_btn.configure(state="disabled")
        self._set_summary("Reading clips...")

        def work():
            try:
                a = engine.analyze(vals["hooks"], vals["bodies"], vals["ctas"], vals["out"],
                                   log=lambda m: self.q.put(("log", m)))
                self.q.put(("analysis", a))
            except Exception as e:
                self.q.put(("error", str(e)))
        self._bg(work)

    def start(self) -> None:
        a = self.analysis
        if not a:
            return
        normalize = not a.compat.lossless_ok
        if not a.pending:
            messagebox.showinfo("Ad Splicer", "All videos already exist in the output folder.")
            return
        if a.estimated_bytes(normalize) > a.free_bytes() * 0.95:
            if not messagebox.askyesno("Low disk space",
                                       "There may not be enough free disk space. Start anyway?"):
                return
        if normalize and not messagebox.askokcancel(
            "Conversion needed",
            "Your clips don't all share the same format, so each source clip will be converted once "
            "to visually-lossless H.264 before joining. Continue?"):
            return
        self.controls = engine.Controls()
        self._busy(True)
        speed, keep, controls = self.speed.get(), self.keep_cache.get(), self.controls

        def work():
            try:
                r = engine.run(a, mode="auto", speed=speed, keep_cache=keep, controls=controls,
                               log=lambda m: self.q.put(("log", m)),
                               progress=lambda d, t, l: self.q.put(("progress", (d, t, l))))
                self.q.put(("done", r))
            except Exception as e:
                self.q.put(("error", str(e)))
        self._bg(work)

    def toggle_pause(self) -> None:
        if not self.controls:
            return
        if self.controls.pause.is_set():
            self.controls.pause.clear()
            self.pause_btn.configure(text="Pause")
            self._log("Resumed.")
        else:
            self.controls.pause.set()
            self.pause_btn.configure(text="Resume")
            self._log("Pausing after the current video...")

    def stop(self) -> None:
        if self.controls:
            self.controls.stop.set()
            self._log("Stopping...")

    def open_output(self) -> None:
        out = self.vars["out"].get().strip()
        if not out or not Path(out).is_dir():
            return
        if sys.platform == "win32":
            os.startfile(out)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", out])
        else:
            subprocess.Popen(["xdg-open", out])

    def on_close(self) -> None:
        if self.worker and self.worker.is_alive() and self.controls:
            if not messagebox.askyesno("Quit", "A job is running. Stop it and quit?"):
                return
            self.controls.stop.set()
            self.worker.join(timeout=10)
        self.root.destroy()

    # ---- worker messages ------------------------------------------------
    def poll(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "analysis":
                    self.analysis = payload
                    self._set_summary(payload.summary())
                    self._busy(False)
                elif kind == "progress":
                    d, t, label = payload
                    self.bar.configure(maximum=max(t, 1), value=d)
                    self.status.configure(text=f"{d}/{t}  {label}")
                elif kind == "done":
                    r = payload
                    msg = (f"Made {r.made} videos, skipped {r.skipped}, failed {len(r.failed)} "
                           f"in {r.seconds / 60:.1f} min.")
                    self._log(msg)
                    self.status.configure(text=("Stopped. " if r.cancelled else "Finished! ") + msg)
                    self._busy(False)
                    if self.analysis:
                        self._set_summary(self.analysis.summary())
                elif kind == "error":
                    self._busy(False)
                    self._set_summary(f"Problem: {payload}")
                    messagebox.showerror("Ad Splicer", payload)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def main() -> int:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam" if sys.platform.startswith("linux") else ttk.Style().theme_use())
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0
