import csv
import json
import subprocess

import pytest

from ad_splicer import engine
from ad_splicer.cli import main as cli_main
from ad_splicer.plan import clean_label
from ad_splicer.probe import probe
from ad_splicer.scan import list_videos


def ffprobe_json(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def video_frame_md5s(path):
    """Hash of every decoded video frame - identical means zero quality loss."""
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
                          "-f", "framemd5", "-"], check=True, capture_output=True, text=True).stdout
    return [line.rsplit(",", 1)[-1].strip() for line in out.splitlines() if line and not line.startswith("#")]


def test_natural_sort(tmp_path, clip_factory):
    for n in ("hook 10.mp4", "hook 2.mp4", "Hook 1.mov"):
        (tmp_path / n).write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / ".hidden.mp4").write_bytes(b"x")
    assert [p.name for p in list_videos(tmp_path)] == ["Hook 1.mov", "hook 2.mp4", "hook 10.mp4"]


def test_clean_label():
    assert clean_label("My Hook #1 (final).mov".rsplit(".", 1)[0]) == "My-Hook-1-final"
    assert clean_label("!!!") == "clip"
    assert len(clean_label("x" * 100)) <= 24


def test_lossless_join(folders):
    a = engine.analyze(folders["hooks"], folders["bodies"], folders["ctas"], folders["out"])
    assert a.compat.lossless_ok, a.compat.problems
    assert len(a.combos) == 12
    r = engine.run(a)
    assert (r.made, r.skipped, r.failed, r.cancelled, r.normalized) == (12, 0, [], False, False)

    outs = sorted(folders["out"].glob("*.mp4"))
    assert len(outs) == 12
    first = folders["out"] / "H01_B01_C01__hook-1_body-1_cta-1.mp4"
    assert first.is_file()
    # every decoded frame is pixel-identical to the sources => no quality loss
    src = sum((video_frame_md5s(folders[k] / f"{n} 1.mp4") for k, n in (("hooks", "hook"), ("bodies", "body"), ("ctas", "cta"))), [])
    assert video_frame_md5s(first) == src
    info = ffprobe_json(first)
    assert float(info["format"]["duration"]) == pytest.approx(3.0, abs=0.15)
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert not (folders["out"] / engine.CACHE_DIRNAME).exists()

    rows = list(csv.DictReader(open(folders["out"] / "manifest.csv")))
    assert len(rows) == 12 and all(r["status"] == "done" for r in rows)
    assert rows[0]["ad_name"] == "H01_B01_C01"
    assert rows[-1]["ad_name"] == "H03_B02_C02"


def test_resume_skips_existing(folders):
    a = engine.analyze(folders["hooks"], folders["bodies"], folders["ctas"], folders["out"])
    engine.run(a)
    victim = sorted(folders["out"].glob("*.mp4"))[5]
    victim.unlink()
    a2 = engine.analyze(folders["hooks"], folders["bodies"], folders["ctas"], folders["out"])
    assert len(a2.pending) == 1
    r = engine.run(a2)
    assert (r.made, r.skipped) == (1, 11)
    assert victim.is_file()


def test_mismatch_is_detected_and_normalized(folders, clip_factory):
    # a vertical, 25fps, silent CTA mixed in with 320x240/30fps clips
    clip_factory(folders["ctas"] / "cta 3.mp4", color="purple", size="240x320", rate=25, audio=False)
    a = engine.analyze(folders["hooks"], folders["bodies"], folders["ctas"], folders["out"])
    assert not a.compat.lossless_ok
    assert any("cta 3.mp4" in p for p in a.compat.problems)
    assert "converted ONCE" in a.summary()
    with pytest.raises(ValueError):
        engine.run(a, mode="lossless")

    r = engine.run(a, mode="auto")
    assert r.normalized and r.made == 18 and not r.failed
    for out in folders["out"].glob("*.mp4"):
        info = ffprobe_json(out)
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert (v["width"], v["height"], v["avg_frame_rate"]) == (320, 240, "30/1")
        assert any(s["codec_type"] == "audio" for s in info["streams"])
        assert float(info["format"]["duration"]) == pytest.approx(3.0, abs=0.2)
        # decodes cleanly end to end
        subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(out), "-f", "null", "-"], check=True)


def test_stop_then_resume(folders):
    a = engine.analyze(folders["hooks"], folders["bodies"], folders["ctas"], folders["out"])
    controls = engine.Controls()
    count = {"n": 0}

    def progress(done, total, label):
        count["n"] += 1
        if count["n"] == 4:
            controls.stop.set()

    r = engine.run(a, controls=controls, progress=progress)
    assert r.cancelled and 3 <= r.made < 12
    assert not list(folders["out"].glob("*.partial.mp4"))
    a2 = engine.analyze(folders["hooks"], folders["bodies"], folders["ctas"], folders["out"])
    r2 = engine.run(a2)
    assert (r2.made + r2.skipped, r2.skipped) == (12, r.made)


def test_output_cannot_be_input(folders):
    with pytest.raises(ValueError):
        engine.analyze(folders["hooks"], folders["bodies"], folders["ctas"], folders["hooks"])


def test_cli_dry_run_and_run(folders, capsys):
    args = ["--hooks", str(folders["hooks"]), "--bodies", str(folders["bodies"]),
            "--ctas", str(folders["ctas"]), "--out", str(folders["out"])]
    assert cli_main(args + ["--dry-run"]) == 0
    assert "= 12 videos" in capsys.readouterr().out
    assert not folders["out"].exists() or not list(folders["out"].glob("*.mp4"))
    assert cli_main(args) == 0
    assert len(list(folders["out"].glob("*.mp4"))) == 12


def test_probe_reads_properties(clip_factory, tmp_path):
    info = probe(clip_factory(tmp_path / "a.mp4", size="640x360", rate=24))
    assert (info.v_codec, info.width, info.height, info.fps, info.has_audio) == ("h264", 640, 360, "24/1", True)
    assert info.v_extradata.startswith("SHA256:")
