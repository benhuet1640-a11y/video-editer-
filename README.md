# Ad Splicer

Turn folders of **hooks**, **bodies** and **CTAs** into every ad combination, already labeled.

10 hooks × 5 bodies × 2 CTAs = **100 finished ads**, with one click. It's free, runs on **Mac and Windows**, and everything stays on your computer.

## Why it won't hurt quality, or your computer

| Your clips | What happens | Quality | Load on your computer |
|---|---|---|---|
| All exported with the same settings (usual if they come from the same editor/preset) | Clips are **joined losslessly**. Nothing is re-encoded. | Identical to your originals | Very light. A few seconds per ad. |
| Mixed formats (different resolution, fps, phone vs. editor exports…) | Each **source clip** is converted **once** to visually lossless H.264 (CRF 16), then all ads are joined losslessly | Visually identical | 17 conversions for a 10/5/2 project, not 100 |

Other safeguards:
- Only one video is processed at a time.
- **Gentle** speed (the default) uses about half your CPU cores, at low priority, so your computer stays cool and usable.
- **Pause** and **Stop** work at any time. Videos that are already finished get skipped next time, so you can always pick up where you left off.
- Before starting, the app shows how many videos it will make and how much disk space they need.

## Install

### Easiest: download the app
1. On GitHub, open this repo's **Actions** tab, click the latest green **Build apps** run, and download:
   - `AdSplicer-mac-apple-silicon` for M1/M2/M3/M4 Macs
   - `AdSplicer-mac-intel` for older Macs
   - `AdSplicer-windows`

   If there's a release (tag `v1.0.0` etc.), the same zips are attached under **Releases**.
2. Unzip it. FFmpeg is included, so there's nothing else to install.
3. Open the app:
   - **Mac:** the app isn't from the App Store, so the first time, **right-click "Ad Splicer" → Open → Open**. If macOS still says it's "damaged", run this once in Terminal:

     ```bash
     xattr -dr com.apple.quarantine "/Applications/Ad Splicer.app"
     ```
   - **Windows:** open the `Ad Splicer` folder and double-click `Ad Splicer.exe`. If SmartScreen appears, click **More info → Run anyway**.

### Or run from source
You need Python 3.10+ and FFmpeg. For captions, also install the speech package:

```bash
pip install -r requirements.txt
```

- **Mac:** install Python from python.org (it includes the window toolkit), then run:

  ```bash
  brew install ffmpeg
  ```
- **Windows:** install Python from python.org, then run:

  ```bash
  winget install ffmpeg
  ```

Then start it:

```bash
python -m ad_splicer
```

## How to use it
1. Put your clips in three folders, for example `Hooks/`, `Bodies/`, `CTAs/`. Clips are numbered in name order, so `hook 2` comes before `hook 10`.
2. Open Ad Splicer and choose the three folders plus an **Output** folder.
3. Click **Check**. You'll see the number of videos, whether joining is lossless, and the disk space needed. If clips don't match, the app lists exactly which ones and why.
4. Click **Start**.

Each output file is named so you can always tell what's inside:

```
H03_B05_C02__my-hook-name_body-name_cta-name.mp4
```

`H03_B05_C02` means hook #3, body #5, CTA #2. The output folder also gets a `manifest.csv` that lists every ad, its source clips, duration and size. Use it as your tracking sheet.

### Command line (optional)

```bash
python -m ad_splicer --hooks Hooks --bodies Bodies --ctas CTAs --out "Spliced Ads" --speed gentle
```

Useful options:
- `--dry-run` previews the run without making anything.
- `--mode lossless` refuses to re-encode anything.
- `--keep-cache` keeps the converted clips.

## Getting lots of ads into Meta
1. **Upload in bulk.** In Ads Manager, go to **Media Library** (or the Ads Manager *Media* section) and drag the whole output folder in. Meta processes the videos in the background.
2. **Don't build 100 ads by hand.** Pick one of these:
   - **Flexible ad format / Dynamic Creative.** One ad holds up to 10 videos, and Meta shows the best ones. A clean setup is *one ad per hook*, with that hook's 10 body × CTA versions inside it. This also helps you find your winning hook quickly.
   - **Bulk import.** In Ads Manager, use **Export & Import → Import ads in bulk**. Download the template, then paste ad names and file names from `manifest.csv` so every ad is named `H03_B05_C02` and matches its video.
3. **Read results by component.** Because every ad name has the `H##_B##_C##` code, you can export results to a spreadsheet and average by hook, body or CTA. That shows which *piece* is winning, not just which ad.
4. **Start small.** Test hooks first (for example, 10 hooks with your best body and CTA). Then test bodies on the winning hooks. Then test CTAs. That's far cheaper than running all 100 at once.

## Captions (free, on your computer)
Tick **Add captions** before you press Start, and every ad gets CapCut-style captions, so it makes sense with the sound off:
- **Bold white ALL-CAPS** with a black outline and soft shadow, in the lower-middle of the frame (clear of your face and the app buttons).
- **2–3 words at a time.** Each phrase pops in gently, and **the word being spoken turns yellow**. Nothing else moves.

How it works:
1. **Transcribe.** Each source clip is transcribed **once**, on your computer, with the open-source Whisper model. That's 17 clips for a 10/5/2 project, not 100 ads. It's free, with no account and no API key, and nothing is uploaded.
   - The first time, the app downloads the speech model once: **Accurate** is about 480 MB, **Fast** about 145 MB.
2. **Review.** A window lists every clip with its words. Fix anything it got wrong, like brand names or slang. Just edit the text; the timing is matched for you. Press **Looks good – make the videos**.
3. **Render.** Each clip is converted once with its captions burned in (visually lossless), then all the ads are joined losslessly.
   - Captioned files end in `_CC.mp4`, so they can sit next to the no-caption versions.
   - The list of ads is saved as `manifest_CC.csv`.

Your fixes are saved in `<output>/captions/` and reused next time. If you change a clip's captions later, only the ads that use that clip are re-made.

From the command line:

```bash
python -m ad_splicer --hooks Hooks --bodies Bodies --ctas CTAs --out "Spliced Ads" --captions --review
```

That transcribes and stops. Edit the `"text"` lines in `Spliced Ads/captions/*.json`, then render:

```bash
python -m ad_splicer --hooks Hooks --bodies Bodies --ctas CTAs --out "Spliced Ads" --captions
```

Use `--captions fast` for the smaller model.

The caption font is Montserrat ExtraBold (SIL Open Font License, see `ad_splicer/fonts/OFL.txt`).

## Troubleshooting
- **"Clips don't all match"**: this is normal when clips come from different sources. Let the app convert each clip once, or re-export everything from your editor with the same preset to get the fully lossless path.
- **Videos not playing on Mac or Meta**: make sure your sources are H.264 or HEVC. ProRes or other codecs automatically go through the conversion step.
- **Running out of space**: the app warns you first. Lossless output is roughly (hook + body + CTA size) per ad.

## For developers
```bash
pip install -r requirements-dev.txt
```

```bash
pytest
```

The tests generate tiny clips with FFmpeg. They check that lossless outputs are pixel-identical to the sources, and that mismatched clips are detected, converted and joined. `.github/workflows/build.yml` runs the tests on macOS and Windows and builds the apps with PyInstaller and a bundled static FFmpeg.
