import os
import shutil
import subprocess
import sys

import pytest

from ad_splicer import engine
from ad_splicer.captions import store
from ad_splicer.captions.edit import realign
from ad_splicer.captions.style import (HIGHLIGHT, build_events, group_phrases, to_words,
                                       write_ass)

SPEECH = [("This", 0.10, 0.30), ("is", 0.30, 0.42), ("the", 0.42, 0.55), ("easiest", 0.55, 0.95),
          ("way.", 0.95, 1.20), ("Save", 1.80, 2.00), ("money", 2.00, 2.30), ("on", 2.30, 2.40),
          ("Shopfy", 2.40, 2.80)]


def raw(words=SPEECH):
    return [{"word": w, "start": s, "end": e} for w, s, e in words]


# ------------------------------------------------------------------ style
def test_phrases_max_three_words_and_break_on_punctuation_and_pauses():
    phrases = group_phrases(to_words(raw()))
    texts = [[w.text for w in p] for p in phrases]
    assert texts == [["This", "is", "the"], ["easiest", "way."], ["Save", "money", "on"], ["Shopfy"]]
    assert all(len(p) <= 3 for p in phrases)


def test_events_highlight_only_current_word_and_pop_only_on_phrase_start():
    events = build_events(to_words(raw()), duration=3.0)
    assert len(events) == len(SPEECH)  # one event per spoken word
    for (s, e, text) in events:
        assert text.count(HIGHLIGHT) == 1 and e > s
    pops = [t for _, _, t in events if "\\fscx85" in t]
    assert len(pops) == 4  # one per phrase
    assert events[0][2].startswith("{\\fad") and "THIS" in events[0][2]
    assert "IS THE" in events[0][2] and "WAY" not in events[0][2]
    # words of the same phrase butt up against each other (no blinking)
    assert events[0][1] == events[1][0] and events[1][1] == events[2][0]
    # 'way.' ends 1.20, next phrase at 1.80: a real pause, so the caption clears
    assert events[4][1] == pytest.approx(1.20)
    assert "." not in events[4][2]  # sentence punctuation is dropped


def test_write_ass(tmp_path):
    p = write_ass(to_words(raw()), 1080, 1920, tmp_path / "c.ass", duration=3)
    text = p.read_text()
    assert "PlayResX: 1080" in text and "PlayResY: 1920" in text
    assert "Montserrat ExtraBold" in text
    assert text.count("Dialogue:") == len(SPEECH)
    style = next(l for l in text.splitlines() if l.startswith("Style: Caption"))
    assert style.split(",")[-2] == str(round(1920 * 0.30))  # lower-middle


# ------------------------------------------------------------------ edits
def test_realign_fix_typo_keeps_timing():
    out = realign(raw(), "This is the easiest way. Save money on Shopify")
    assert [w["word"] for w in out][-1] == "Shopify"
    assert (out[-1]["start"], out[-1]["end"]) == (2.40, 2.80)
    assert [(w["start"], w["end"]) for w in out] == [(s, e) for _, s, e in SPEECH]


def test_realign_insert_delete_and_split():
    out = realign(raw(), "This is the easiest way. Save money on Shopify Plus")
    assert out[-1]["word"] == "Plus" and out[-1]["start"] >= out[-2]["start"]
    out = realign(raw(), "This is the easiest way. Save on Shopify")
    assert [w["word"] for w in out] == ["This", "is", "the", "easiest", "way.", "Save", "on", "Shopify"]
    out = realign(raw(), "This is the most easy way. Save money on Shopify")
    words = [w["word"] for w in out]
    assert words[3:5] == ["most", "easy"]
    assert out[3]["start"] == pytest.approx(0.55) and out[4]["end"] == pytest.approx(0.95)
    starts = [w["start"] for w in out]
    assert starts == sorted(starts)


def test_realign_without_any_detected_speech():
    out = realign([], "Link in bio", duration=3.0)
    assert [w["word"] for w in out] == ["Link", "in", "bio"]
    assert out[0]["start"] == 0 and out[-1]["end"] == pytest.approx(3.0)


def test_store_round_trip(tmp_path, clip_factory):
    clip = clip_factory(tmp_path / "in" / "hook 1.mp4")
    out = tmp_path / "out"
    doc = store.new(out, clip, 1.0, raw(), "en", "small")
    assert doc.json_path.is_file()
    doc.set_text(doc.text.replace("Shopfy", "Shopify"))
    doc.save()
    again = store.load(out, clip, 1.0)
    assert "Shopify" in again.text and again.words[-1]["word"] == "Shopify"
    assert again.auto_words[-1]["word"] == "Shopfy"
    assert again.fingerprint() == doc.fingerprint()


# ------------------------------------------------------------------ end to end
class FakeTranscriber:
    model_name = "fake"

    def __init__(self):
        self.calls = 0

    def transcribe(self, path, stop=None, language=None):
        self.calls += 1
        return [{"word": "Hello", "start": 0.05, "end": 0.4},
                {"word": path.stem.split()[0], "start": 0.4, "end": 0.8}], "en"


def gray_band(path, t, y0, y1, size="320x240"):
    w, h = map(int, size.split("x"))
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
         "-vf", f"crop={w}:{int(h * (y1 - y0))}:0:{int(h * y0)}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True).stdout
    return out


def mean_diff(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / max(len(a), 1)


def test_captions_end_to_end(folders):
    a = engine.analyze(folders["hooks"], folders["bodies"], folders["ctas"], folders["out"], captions=True)
    assert a.needs_conversion and "Captions ON" in a.summary()
    fake = FakeTranscriber()
    docs = engine.transcribe_all(a, transcriber=fake)
    assert fake.calls == 7 and len(docs) == 7
    assert engine.transcribe_all(a, transcriber=fake) and fake.calls == 7  # cached

    with pytest.raises(ValueError):
        engine.run(a, mode="lossless", captions=docs)
    r = engine.run(a, captions=docs)
    assert r.made == 12 and not r.failed and r.normalized
    outs = sorted(folders["out"].glob("*_CC.mp4"))
    assert len(outs) == 12 and (folders["out"] / "manifest_CC.csv").is_file()

    first = folders["out"] / "H01_B01_C01__hook-1_body-1_cta-1_CC.mp4"
    src = folders["hooks"] / "hook 1.mp4"
    # caption band (lower-middle) changed, top of the frame essentially untouched
    assert mean_diff(gray_band(first, 0.6, 0.55, 0.78), gray_band(src, 0.6, 0.55, 0.78)) > 3
    assert mean_diff(gray_band(first, 0.6, 0.0, 0.3), gray_band(src, 0.6, 0.0, 0.3)) < 2
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(first), "-f", "null", "-"], check=True)

    # editing one CTA's captions remakes only the ads that use it
    cta = a.ctas[0].path
    docs[cta].set_text("Link in bio")
    docs[cta].save()
    a2 = engine.analyze(folders["hooks"], folders["bodies"], folders["ctas"], folders["out"], captions=True)
    docs2 = engine.transcribe_all(a2, transcriber=fake)
    assert docs2[cta].text == "Link in bio" and fake.calls == 7
    r2 = engine.run(a2, captions=docs2)
    assert (r2.made, r2.skipped) == (6, 6)

    # plain (no-caption) versions can live next to captioned ones
    a3 = engine.analyze(folders["hooks"], folders["bodies"], folders["ctas"], folders["out"])
    r3 = engine.run(a3)
    assert r3.made == 12 and not r3.normalized


def test_cli_review_then_render(folders, monkeypatch, capsys):
    from ad_splicer import cli
    from ad_splicer.captions import transcribe

    fake = FakeTranscriber()
    monkeypatch.setattr(transcribe, "Transcriber", lambda *a, **k: fake)
    args = ["--hooks", str(folders["hooks"]), "--bodies", str(folders["bodies"]),
            "--ctas", str(folders["ctas"]), "--out", str(folders["out"]), "--captions", "fast"]
    assert cli.main(args + ["--review"]) == 0
    assert len(list((folders["out"] / "captions").glob("*.json"))) == 7
    assert not list(folders["out"].glob("*.mp4"))
    assert cli.main(args) == 0
    assert len(list(folders["out"].glob("*_CC.mp4"))) == 12 and fake.calls == 7


# ------------------------------------------------------------------ real Whisper (CI on macOS)
@pytest.mark.skipif(not (os.environ.get("AD_SPLICER_WHISPER_TEST") and shutil.which("say")),
                    reason="needs macOS 'say' + AD_SPLICER_WHISPER_TEST=1 (downloads a model)")
def test_real_whisper_transcription(tmp_path):
    from ad_splicer.captions.transcribe import Transcriber

    aiff = tmp_path / "speech.aiff"
    subprocess.run(["say", "-o", str(aiff), "This is the easiest way to save money."], check=True)
    mp4 = tmp_path / "speech.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30",
                    "-i", str(aiff), "-shortest", "-c:v", "libx264", "-c:a", "aac", str(mp4)], check=True)
    words, lang = Transcriber("fast", threads=2).transcribe(mp4)
    text = " ".join(w["word"] for w in words).lower()
    assert lang == "en"
    assert "easiest" in text and "money" in text
    starts = [w["start"] for w in words]
    assert starts == sorted(starts) and all(w["end"] >= w["start"] for w in words)
    print(words, file=sys.stderr)
