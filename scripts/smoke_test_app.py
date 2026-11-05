"""End-to-end test of the *packaged* app: real speech -> captions -> 2 finished ads.

usage: python scripts/smoke_test_app.py <app executable> <ffmpeg dir> <work dir>
"""
import json
import subprocess
import sys
from pathlib import Path

app, ffdir, work = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
ext = ".exe" if sys.platform == "win32" else ""
ffmpeg = str(ffdir / f"ffmpeg{ext}")
lines = {"hooks/hook 1": "Stop scrolling, this changed everything.",
         "hooks/hook 2": "Nobody talks about this trick.",
         "bodies/body 1": "It is the easiest way to save money every week.",
         "ctas/cta 1": "Tap the link and try it free today."}


def speak(text: str, wav: Path) -> None:
    if sys.platform == "darwin":
        aiff = wav.with_suffix(".aiff")
        subprocess.run(["say", "-o", str(aiff), text], check=True)
        subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(aiff), str(wav)], check=True)
    elif sys.platform == "win32":
        ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$s.SetOutputToWaveFile('{wav}'); $s.Speak('{text}'); $s.Dispose()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    else:
        subprocess.run(["espeak-ng", "-w", str(wav), text], check=True)


for rel, text in lines.items():
    mp4 = work / f"{rel}.mp4"
    mp4.parent.mkdir(parents=True, exist_ok=True)
    wav = work / f"{mp4.stem.replace(' ', '_')}.wav"
    speak(text, wav)
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=navy:s=540x960:r=30",
                    "-i", str(wav), "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(mp4)], check=True)

out = work / "out"
cmd = [str(app), "--hooks", str(work / "hooks"), "--bodies", str(work / "bodies"),
       "--ctas", str(work / "ctas"), "--out", str(out), "--captions", "fast", "--speed", "fast"]
print("running", cmd, flush=True)
subprocess.run(cmd, check=True, timeout=1800)

ads = sorted(out.glob("*_CC.mp4"))
assert len(ads) == 2, ads
docs = [json.loads(p.read_text(encoding="utf-8")) for p in (out / "captions").glob("*.json")]
assert len(docs) == 4, docs
for d in docs:
    print(d["source"], "->", d["text"])
    assert len(d["words"]) >= 3, d
assert any("easiest" in d["text"].lower() for d in docs)
print("SMOKE TEST PASSED:", [a.name for a in ads])
