"""On-device speech-to-text with faster-whisper (free, open source, nothing uploaded)."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Callable

from .. import ffmpeg_bin
from ..proc import Cancelled, run_quiet_bytes

SAMPLE_RATE = 16000

QUALITY_MODELS = {
    "fast": ("base", "~145 MB"),
    "accurate": ("small", "~480 MB"),
}


def models_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    d = base / "AdSplicer" / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Transcriber:
    def __init__(self, quality: str = "accurate", threads: int = 2,
                 status: Callable[[str], None] = lambda m: None):
        self.quality = quality if quality in QUALITY_MODELS else "accurate"
        self.threads = threads
        self.status = status
        self._model = None

    @property
    def model_name(self) -> str:
        return QUALITY_MODELS[self.quality][0]

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("Captions need the 'faster-whisper' package: pip install faster-whisper") from e
        name, size = QUALITY_MODELS[self.quality]
        kwargs = dict(device="cpu", compute_type="int8", cpu_threads=self.threads,
                      download_root=str(models_dir()))
        try:
            self._model = WhisperModel(name, local_files_only=True, **kwargs)
        except Exception:
            self.status(f"Downloading the free speech model '{name}' ({size}, one time only)...")
            self._model = WhisperModel(name, **kwargs)
        return self._model

    def transcribe(self, path: Path, stop: threading.Event | None = None,
                   language: str | None = None) -> tuple[list[dict], str]:
        """Return ([{word, start, end}], detected_language)."""
        model = self._load()
        audio = load_audio(path)
        if audio.size < SAMPLE_RATE // 4:  # no audio track / under 0.25 s
            return [], language or ""
        segments, info = model.transcribe(
            audio, word_timestamps=True, vad_filter=True, beam_size=5,
            condition_on_previous_text=False, language=language,
        )
        words: list[dict] = []
        for seg in segments:
            if stop is not None and stop.is_set():
                raise Cancelled()
            for w in seg.words or []:
                text = w.word.strip()
                if text:
                    words.append({"word": text, "start": round(float(w.start), 3), "end": round(float(w.end), 3)})
        return words, info.language


def load_audio(path: Path):
    """Decode to 16 kHz mono float32 with our own FFmpeg (no extra media libraries needed)."""
    import numpy as np

    cmd = [ffmpeg_bin.ffmpeg(), "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0?",
           "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
    res = run_quiet_bytes(cmd)
    if res.returncode != 0:
        raise RuntimeError(f"Could not read audio from {path.name}: {res.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(res.stdout, dtype=np.float32).copy()
