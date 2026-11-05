"""Turn word timings into a CapCut-style .ass subtitle file.

Look (the common style on top-performing TikTok / Reels ads): heavy white ALL CAPS,
thick black outline + soft drop shadow, lower-middle of the frame.
Flow: 1-3 word phrases that switch in time with the speech. Static: no animation,
no colour highlight.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Bump whenever the look changes so existing captioned videos get re-made.
STYLE_VERSION = 2

MAX_WORDS = 3
MAX_CHARS = 16
PAUSE_BREAK = 0.35   # seconds of silence that starts a new phrase
HOLD_GAP = 0.30      # keep a phrase on screen across gaps shorter than this
MIN_EVENT = 0.04

WHITE = "&H00FFFFFF"
OUTLINE = "&H00000000"
SHADOW = "&H96000000"      # black, ~40% opaque

FONT_NAME = "Montserrat ExtraBold"
FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"

_BREAK_AFTER = re.compile(r"[.!?,;:…]$")
_DROP = re.compile(r"[.,;:…\"“”]")


@dataclass
class Word:
    text: str
    start: float
    end: float


def to_words(raw: list[dict]) -> list[Word]:
    out = []
    for w in raw:
        t = str(w.get("word", "")).strip()
        if t:
            out.append(Word(t, float(w["start"]), float(w["end"])))
    return out


def display_text(word: str) -> str:
    """ALL CAPS, drop sentence punctuation (keep ? ! ' $ %), make it safe for ASS."""
    t = _DROP.sub("", word).upper()
    t = t.replace("\\", "/").replace("{", "(").replace("}", ")")
    return t.strip() or word.strip().upper()


def group_phrases(words: list[Word], max_words: int = MAX_WORDS, max_chars: int = MAX_CHARS,
                  pause: float = PAUSE_BREAK) -> list[list[Word]]:
    phrases: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            prev = cur[-1]
            chars = len(" ".join(display_text(x.text) for x in cur + [w]))
            if (len(cur) >= max_words or chars > max_chars or w.start - prev.end > pause
                    or _BREAK_AFTER.search(prev.text)):
                phrases.append(cur)
                cur = []
        cur.append(w)
    if cur:
        phrases.append(cur)
    return phrases


def _ts(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def style_metrics(width: int, height: int) -> dict:
    size = round(0.095 * min(width, height))
    return {
        "size": size,
        "outline": max(2, round(size * 0.11)),
        "shadow": max(1, round(size * 0.04)),
        "margin_lr": round(width * 0.075),
        "margin_v": round(height * 0.30),
    }


def build_events(words: list[Word], duration: float | None = None) -> list[tuple[float, float, str]]:
    """Return (start, end, ass_text): one static, all-white event per phrase.
    A phrase appears when its first word is spoken and stays put (no motion,
    no colour changes) until the next phrase replaces it."""
    phrases = group_phrases(words)
    events: list[tuple[float, float, str]] = []
    for pi, phrase in enumerate(phrases):
        p_start = phrase[0].start
        p_end = phrase[-1].end
        if pi + 1 < len(phrases):
            nxt = phrases[pi + 1][0].start
            if nxt - p_end < HOLD_GAP:
                p_end = nxt
            p_end = min(p_end, nxt)
        if duration:
            p_end = min(p_end, duration)
        if p_end - p_start < MIN_EVENT:
            p_end = p_start + MIN_EVENT
        events.append((p_start, p_end, " ".join(display_text(w.text) for w in phrase)))
    return events


def write_ass(words: list[Word], width: int, height: int, path: Path,
              duration: float | None = None) -> Path:
    m = style_metrics(width, height)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{FONT_NAME},{m['size']},{WHITE},{WHITE},{OUTLINE},{SHADOW},0,0,0,0,100,100,0,0,1,{m['outline']},{m['shadow']},2,{m['margin_lr']},{m['margin_lr']},{m['margin_v']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [
        f"Dialogue: 0,{_ts(s)},{_ts(e)},Caption,,0,0,0,,{text}"
        for s, e, text in build_events(words, duration)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return path
