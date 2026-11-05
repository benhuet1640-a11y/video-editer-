"""Per-clip caption files: <output>/captions/<clip>_<id>.json.

`text` is what you see/edit; `auto_words` is Whisper's original timing; `words` is
`text` aligned onto those timings. Editing `text` in the file by hand also works."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .edit import realign

CAPTIONS_DIRNAME = "captions"


def clip_id(path: Path) -> str:
    st = path.stat()
    key = f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


@dataclass
class CaptionDoc:
    source: Path
    json_path: Path
    duration: float
    auto_words: list[dict] = field(default_factory=list)
    text: str = ""
    words: list[dict] = field(default_factory=list)
    language: str = ""
    model: str = ""

    @property
    def auto_text(self) -> str:
        return " ".join(w["word"] for w in self.auto_words)

    def set_text(self, text: str) -> None:
        text = " ".join(text.split())
        self.text = text
        self.words = (list(self.auto_words) if text == self.auto_text
                      else realign(self.auto_words, text, self.duration))

    def fingerprint(self) -> str:
        return hashlib.sha1(json.dumps(self.words, sort_keys=True).encode()).hexdigest()[:12]

    def save(self) -> None:
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "source": self.source.name, "language": self.language, "model": self.model,
            "duration": self.duration, "text": self.text,
            "words": self.words, "auto_words": self.auto_words,
        }
        tmp = self.json_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.json_path)


def doc_path(out_dir: Path, source: Path) -> Path:
    stem = "".join(c if c.isalnum() or c in "-_" else "-" for c in source.stem)[:40]
    return out_dir / CAPTIONS_DIRNAME / f"{stem}_{clip_id(source)}.json"


def load(out_dir: Path, source: Path, duration: float) -> CaptionDoc | None:
    p = doc_path(out_dir, source)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    doc = CaptionDoc(source, p, duration, data.get("auto_words", []), "", [],
                     data.get("language", ""), data.get("model", ""))
    doc.set_text(data.get("text", doc.auto_text))
    return doc


def new(out_dir: Path, source: Path, duration: float, words: list[dict],
        language: str, model: str) -> CaptionDoc:
    doc = CaptionDoc(source, doc_path(out_dir, source), duration, words, "", [], language, model)
    doc.set_text(doc.auto_text)
    doc.save()
    return doc
