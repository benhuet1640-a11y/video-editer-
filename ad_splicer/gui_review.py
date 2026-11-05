"""Review window: fix the auto transcript of each clip before captions are burned in."""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable

from .captions.store import CaptionDoc


def open_file(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class ReviewWindow:
    """Modal window. Calls on_done(True) after saving edits, on_done(False) on cancel."""

    def __init__(self, parent: tk.Misc, sections: list[tuple[str, list[CaptionDoc]]],
                 on_done: Callable[[bool], None]):
        self.on_done = on_done
        self.items: list[CaptionDoc] = []
        self.current: int | None = None

        self.win = tk.Toplevel(parent)
        self.win.title("Review captions")
        self.win.geometry("860x520")
        self.win.transient(parent)
        self.win.protocol("WM_DELETE_WINDOW", lambda: self.finish(False))

        ttk.Label(self.win, padding=(12, 10, 12, 0), wraplength=820, text=(
            "Check each clip's words and fix anything the transcription got wrong (brand names, slang). "
            "Just edit the text - timing is matched automatically. Each clip only needs fixing once, "
            "even though it's used in many ads.")).pack(fill="x")

        body = ttk.Frame(self.win, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(body, width=32, exportselection=False, activestyle="none")
        self.listbox.grid(row=0, column=0, sticky="ns")
        for label, docs in sections:
            for doc in docs:
                self.items.append(doc)
                empty = "  (no speech)" if not doc.auto_words else ""
                self.listbox.insert("end", f"{label}: {doc.source.name}{empty}")
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self.select(self.listbox.curselection()))

        right = ttk.Frame(body, padding=(12, 0, 0, 0))
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        self.title = ttk.Label(right, text="", font=("TkDefaultFont", 12, "bold"))
        self.title.grid(row=0, column=0, sticky="w")
        self.text = tk.Text(right, wrap="word", font=("TkDefaultFont", 14), undo=True, height=8)
        self.text.grid(row=1, column=0, sticky="nsew", pady=6)
        row = ttk.Frame(right)
        row.grid(row=2, column=0, sticky="ew")
        ttk.Button(row, text="Play clip", command=self.play).pack(side="left")
        ttk.Button(row, text="Reset to original", command=self.reset).pack(side="left", padx=6)
        ttk.Button(row, text="Next clip →", command=self.next).pack(side="right")

        bottom = ttk.Frame(self.win, padding=12)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Cancel", command=lambda: self.finish(False)).pack(side="left")
        ttk.Button(bottom, text="Looks good - make the videos",
                   command=lambda: self.finish(True)).pack(side="right")

        if self.items:
            self.listbox.selection_set(0)
            self.select((0,))
        self.win.after(50, self._grab)

    def _grab(self) -> None:
        try:
            self.win.grab_set()  # modal (needs the window to be visible first)
        except tk.TclError:
            self.win.after(100, self._grab)

    def _store_current(self) -> None:
        if self.current is not None:
            doc = self.items[self.current]
            new = " ".join(self.text.get("1.0", "end").split())
            if new != doc.text:
                doc.set_text(new)
                doc.save()

    def select(self, sel) -> None:
        if not sel:
            return
        self._store_current()
        self.current = int(sel[0])
        doc = self.items[self.current]
        self.title.configure(text=doc.source.name)
        self.text.delete("1.0", "end")
        self.text.insert("1.0", doc.text)
        self.text.edit_reset()
        self.text.focus_set()

    def next(self) -> None:
        if self.current is None or self.current + 1 >= len(self.items):
            return
        i = self.current + 1
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(i)
        self.listbox.see(i)
        self.select((i,))

    def reset(self) -> None:
        if self.current is not None:
            self.text.delete("1.0", "end")
            self.text.insert("1.0", self.items[self.current].auto_text)

    def play(self) -> None:
        if self.current is not None:
            open_file(self.items[self.current].source)

    def finish(self, ok: bool) -> None:
        if ok:
            self._store_current()
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()
        self.on_done(ok)
