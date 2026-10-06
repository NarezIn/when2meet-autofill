# Screenshot2Meet (when2meet-autofill)

Fill in your When2meet availability from screenshots of your phone's calendar app.

- **`server/`**: a local Python service (FastAPI on `127.0.0.1:8765`). It reads the
  screenshots with OpenCV + PaddleOCR, parses weekdays, scores confidence, merges
  screenshots, and computes which When2meet slots are free.
- **`extension/`**: a Chrome MV3 extension (TypeScript). The side panel handles
  upload, review, fill and undo. A content script fills the When2meet grid by driving
  the page's own click-and-drag selection.

Supported screenshots: iOS Calendar day and multi-day views, dark or light mode.
Column headers can be in any CLDR language; the default OCR models cover English,
Simplified and Traditional Chinese, and Spanish.

## Install and run the service

Requires Python 3.13.

```powershell
py -3.13 -m venv .venv
.venv\Scripts\pip install -e server
.venv\Scripts\screenshot2meet serve
```

The first screenshot read downloads the PaddleOCR models (a few hundred MB, cached in
`~\.paddlex`). To fetch them ahead of time, run
`.venv\Scripts\screenshot2meet download-models`.

To use Tesseract instead (Settings → OCR engine): install Tesseract with the
`eng`, `chi_sim`, `chi_tra` and `spa` language data, then run `pip install -e "server[tesseract]"`.

## Load the extension

Requires Node 20+.

```powershell
cd extension
npm install
npm run build
```

Then open `chrome://extensions`, turn on Developer mode, click **Load unpacked**, and
pick `extension/dist`. Click the toolbar button to open the side panel.

## Use it

1. Start the service, open your When2meet event, and sign in with your name.
2. Open the side panel and add one or more calendar screenshots (choose, drag, or paste).
   Click **Read screenshots**.
3. Review the events, grouped by weekday. You can edit start/end times, delete events, or
   uncheck "counts as busy". Low-confidence and cut-off events are highlighted with the
   reason. **Debug overlay** shows what was detected on each screenshot.
4. Click **Fill When2meet**. Cells touched by low-confidence events get a dashed outline.
5. **Undo fill** restores the availability you had before the fill.

Screenshot times are read as America/New_York. Specific-dates events are matched by
date; days-of-the-week events are plain wall clock (When2meet gives them no timezone).

## Privacy

Screenshots go only to the local service on `127.0.0.1`. Nothing is uploaded anywhere
else. The extension talks to When2meet only through the event page you have open, the
same way your own mouse would.

## Development

```powershell
# Python unit tests (includes an end-to-end test on the fixture)
cd server; ..\.venv\Scripts\python -m pytest

# Extractor eval: per-event time error, misses, false positives, low-confidence flags
.venv\Scripts\python eval\run.py --debug eval\out

# Extract from any screenshot and write debug overlays
.venv\Scripts\screenshot2meet extract path\to\shot.png --debug debug_out

# Extension: typecheck, build, and the live end-to-end test (needs the service running)
cd extension; npm run typecheck; npm run build; npm run e2e
```

Docs:
- [`docs/when2meet-dom.md`](docs/when2meet-dom.md): how When2meet's grid works and the fill strategy.
- [`docs/manual-test-checklist.md`](docs/manual-test-checklist.md): manual checks for the extension.
