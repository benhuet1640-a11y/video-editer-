"""Map an edited transcript back onto the original word timings."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_NORM = re.compile(r"[^\w']+")


def _norm(t: str) -> str:
    return _NORM.sub("", t.lower())


def realign(original: list[dict], new_text: str, duration: float | None = None) -> list[dict]:
    """`original` = [{word, start, end}] from Whisper. Returns the same shape for `new_text`.

    Unchanged words keep their timing; replaced words share the replaced span;
    inserted words are spread between their neighbours; deleted words vanish."""
    new_tokens = new_text.split()
    if not new_tokens:
        return []
    old = [dict(w, word=str(w["word"]).strip()) for w in original if str(w["word"]).strip()]
    if not old:
        end = duration if duration and duration > 0 else 0.4 * len(new_tokens)
        return _spread(new_tokens, 0.0, end)

    sm = SequenceMatcher(None, [_norm(w["word"]) for w in old], [_norm(t) for t in new_tokens],
                         autojunk=False)
    out: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        toks = new_tokens[j1:j2]
        if tag == "equal":
            out += [{"word": t, "start": w["start"], "end": w["end"]} for t, w in zip(toks, old[i1:i2])]
        elif tag == "replace":
            if i2 - i1 == j2 - j1:
                out += [{"word": t, "start": w["start"], "end": w["end"]} for t, w in zip(toks, old[i1:i2])]
            else:
                out += _spread(toks, old[i1]["start"], old[i2 - 1]["end"])
        elif tag == "insert":
            lo = out[-1]["end"] if out else (old[0]["start"] if i1 == 0 else old[i1 - 1]["end"])
            hi = old[i1]["start"] if i1 < len(old) else lo + 0.35 * len(toks)
            if duration and i1 >= len(old):
                hi = min(hi, max(duration, lo))
            if hi - lo < 0.08 * len(toks) and out:
                # no room: share the previous word's time with the new ones
                prev = out.pop()
                out += _spread([prev["word"], *toks], prev["start"], max(hi, prev["end"]))
            else:
                out += _spread(toks, lo, max(hi, lo + 0.08 * len(toks)))
        # "delete": drop words
    return out


def _spread(tokens: list[str], start: float, end: float) -> list[dict]:
    step = (end - start) / max(len(tokens), 1)
    return [{"word": t, "start": round(start + i * step, 3), "end": round(start + (i + 1) * step, 3)}
            for i, t in enumerate(tokens)]
