# Screenshot2Meet (when2meet-autofill)

Screenshot2Meet fills in your [When2meet](https://www.when2meet.com) availability from
screenshots of your phone's calendar app. You add one or more calendar screenshots in a
Chrome side panel and click **Fill When2meet**. It marks you available everywhere you're
free and can undo the fill. Supported screenshots (dark or light mode):

- **iOS Calendar** day and multi-day views (time grid with hour labels on the left).
- **Google Calendar** Schedule view (a list of event cards, each showing its time, such as
  `14:30–15:00` or `2:30 – 3:30pm`). Events with no time shown, such as holidays, are read as
  all-day events. All-day events don't count as busy unless you turn on
  **All-day events count as busy** in Settings.

It has two parts that run on your own computer:

- **`server/`**: a small Python service (FastAPI on `http://127.0.0.1:8765`) that reads the
  screenshots with OpenCV + OCR (PaddleOCR by default, Tesseract optional) and works out
  which When2meet slots are free.
- **`extension/`**: a Chrome extension (side panel) that sends the screenshots to that
  service and fills the When2meet grid for you.

Day names (column headers, or day labels in a list view) can be in any CLDR language. The default OCR models read English,
Simplified and Traditional Chinese, and Spanish.

## Contents

- [Choose how to run it](#choose-how-to-run-it)
- [Method 1: Clone the project](#method-1-clone-the-project)
- [Load the extension in Chrome](#load-the-extension-in-chrome)
- [First use walkthrough](#first-use-walkthrough)
- [Optional: use Tesseract instead of PaddleOCR](#optional-use-tesseract-instead-of-paddleocr)
- [Stopping the service](#stopping-the-service)
- [Updating](#updating)
- [Uninstalling](#uninstalling)
- [Troubleshooting](#troubleshooting)
- [Privacy](#privacy)
- [Development](#development)
- [Roadmap: letting other people use it](#roadmap-letting-other-people-use-it)

## Choose how to run it

There is currently one way to run Screenshot2Meet. There is no packaged release, PyPI
package, or Chrome Web Store listing yet (see the [Roadmap](#roadmap-letting-other-people-use-it)).

| Method | Who it's for | What you need | Setup time |
|---|---|---|---|
| [Method 1: Clone the project](#method-1-clone-the-project) | Everyone (for now), and developers who want the latest code | git, Python 3.13+, Node.js 20+, Chrome 120+ | About 15 minutes, plus a few hundred MB of OCR model downloads |

## Method 1: Clone the project

You download the source code, install the Python service into a virtual environment,
build the Chrome extension, and load it into Chrome.

### a. Prerequisites

Check each item. The "Check" command should print a version at least as new as the one
listed. If it doesn't, use the install command below the checklist.

- [ ] **git** (any recent version)
- [ ] **Python 3.13 or newer**
- [ ] **Node.js 20 or newer** (includes `npm`; only used to build the extension)
- [ ] **Google Chrome 120 or newer** (check at `chrome://settings/help`)
- [ ] **OCR engine**: nothing to install. PaddleOCR comes with the Python service.
      Tesseract is optional, see [below](#optional-use-tesseract-instead-of-paddleocr).

**Bash (macOS / Linux)**

```bash
# Check git is installed. You should see something like: git version 2.45.0
git --version

# Check Python 3.13 is installed. You should see: Python 3.13.x (or newer)
python3.13 --version

# Check Node.js is installed. You should see v20.x.x or newer
node --version

# Check npm (comes with Node.js). You should see a version number like 10.x.x
npm --version
```

**PowerShell (Windows)**

```powershell
# Check git is installed. You should see something like: git version 2.45.0.windows.1
git --version

# Check Python 3.13 is installed (uses the Python launcher). You should see: Python 3.13.x
py -3.13 --version

# Check Node.js is installed. You should see v20.x.x or newer
node --version

# Check npm (comes with Node.js). You should see a version number like 10.x.x
npm --version
```

**Missing something? Install it:**

**Bash (macOS, with [Homebrew](https://brew.sh))**

```bash
# Install git, Python 3.13 and Node.js
brew install git python@3.13 node
```

**Bash (Ubuntu / Debian Linux)**

```bash
# Install git and the venv tools
sudo apt update && sudo apt install -y git python3.13 python3.13-venv
```

If `apt` can't find `python3.13`, your distribution doesn't ship it yet. Install it from
[python.org](https://www.python.org/downloads/) or the
[deadsnakes PPA](https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa) (Ubuntu). For
Node.js 20+, use [nodejs.org](https://nodejs.org/en/download) or
[nvm](https://github.com/nvm-sh/nvm), because distribution packages are often older.

**PowerShell (Windows)**

```powershell
# Install git, Python 3.13 and Node.js LTS with winget
winget install --id Git.Git -e
winget install --id Python.Python.3.13 -e
winget install --id OpenJS.NodeJS.LTS -e
```

After installing on Windows, **close and reopen PowerShell** so the new programs are on
your PATH, then run the check commands again.

### b. Steps

#### Step 1: Download the code, to get the service and extension source onto your computer

Pick a folder where you keep projects. The commands below create a `when2meet-autofill`
folder inside the current folder. Everything after this step runs from inside it.

**Bash (macOS / Linux)**

```bash
# Download the repository
git clone https://github.com/NarezIn/when2meet-autofill.git

# Go into the project folder
cd when2meet-autofill
```

**PowerShell (Windows)**

```powershell
# Download the repository
git clone https://github.com/NarezIn/when2meet-autofill.git

# Go into the project folder
cd when2meet-autofill
```

#### Step 2: Create a virtual environment, so the service's packages don't touch your system Python

**Bash (macOS / Linux)**

```bash
# Create a virtual environment in the .venv folder using Python 3.13
python3.13 -m venv .venv

# Turn it on for this terminal. Your prompt should now start with (.venv)
source .venv/bin/activate
```

**PowerShell (Windows)**

```powershell
# Create a virtual environment in the .venv folder using Python 3.13
py -3.13 -m venv .venv

# Turn it on for this terminal. Your prompt should now start with (.venv)
.venv\Scripts\Activate.ps1
```

> **Windows: "running scripts is disabled on this system"?** PowerShell blocks the
> activation script by default. Allow local scripts for your user account (one time),
> then run the activation command again:
>
> ```powershell
> # Allow scripts you created locally to run (only affects your Windows user)
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```

#### Step 3: Install the service, to download its Python dependencies (OpenCV, PaddleOCR, FastAPI)

This downloads several hundred MB and can take a few minutes. Make sure the virtual
environment from Step 2 is active (`(.venv)` at the start of your prompt).

**Bash (macOS / Linux)**

```bash
# Upgrade pip, then install the service from the server folder
python -m pip install --upgrade pip
python -m pip install -e server
```

**PowerShell (Windows)**

```powershell
# Upgrade pip, then install the service from the server folder
python -m pip install --upgrade pip
python -m pip install -e server
```

You should see a final line starting with `Successfully installed ... screenshot2meet-0.1.0 ...`.

#### Step 4 (optional, recommended): Download the OCR models now, so your first screenshot isn't slow

Without this step, the models download the first time you read a screenshot, and that
first read may take a minute or more. The models (a few hundred MB) are cached in the
`.paddlex` folder in your home directory.

**Bash (macOS / Linux)**

```bash
# Download and cache the default PaddleOCR models
screenshot2meet download-models
```

**PowerShell (Windows)**

```powershell
# Download and cache the default PaddleOCR models
screenshot2meet download-models
```

You should see: `OCR models and weekday table are ready.`

#### Step 5: Start the service, so the extension has something to send screenshots to

The service runs in the foreground. **Leave this terminal open** while you use the
extension, and open a new terminal for the next steps.

**Bash (macOS / Linux)**

```bash
# Start the local service on http://127.0.0.1:8765 (venv must be active)
screenshot2meet serve
```

**PowerShell (Windows)**

```powershell
# Start the local service on http://127.0.0.1:8765 (venv must be active)
screenshot2meet serve
```

You should see:

```text
INFO:     Uvicorn running on http://127.0.0.1:8765 (Press CTRL+C to quit)
```

Next time, you can start it from the project folder without activating the venv:

**Bash (macOS / Linux)**

```bash
# Start the service directly from the venv (run inside the when2meet-autofill folder)
.venv/bin/screenshot2meet serve
```

**PowerShell (Windows)**

```powershell
# Start the service directly from the venv (run inside the when2meet-autofill folder)
.venv\Scripts\screenshot2meet serve
```

#### Step 6: Check the service answers, to confirm it's running before you set up Chrome

Run this in a **second** terminal while the service is running.

**Bash (macOS / Linux)**

```bash
# Ask the service's health endpoint whether it is up
curl http://127.0.0.1:8765/health
```

You should see: `{"status":"ok","version":"0.1.0"}`

**PowerShell (Windows)**

```powershell
# Ask the service's health endpoint whether it is up
Invoke-RestMethod http://127.0.0.1:8765/health
```

You should see:

```text
status version
------ -------
ok     0.1.0
```

#### Step 7: Build the Chrome extension, to turn the TypeScript source into a loadable extension folder

Run this in the second terminal, from the `when2meet-autofill` folder. You don't need the
Python venv for this step.

**Bash (macOS / Linux)**

```bash
# Go into the extension folder
cd extension

# Install the build tools (esbuild, TypeScript)
npm install

# Build the extension into extension/dist
npm run build
```

**PowerShell (Windows)**

```powershell
# Go into the extension folder
cd extension

# Install the build tools (esbuild, TypeScript)
npm install

# Build the extension into extension\dist
npm run build
```

The build should end without errors, and a new `extension/dist` folder should contain
`manifest.json`, `background.js`, `content.js`, `main-world.js`, `sidepanel.html`,
`sidepanel.css` and `sidepanel.js`.

#### Step 8: Load the extension into Chrome

Follow [Load the extension in Chrome](#load-the-extension-in-chrome) below.

### c. Verify it worked

1. The service terminal shows `Uvicorn running on http://127.0.0.1:8765`, and the health
   check in Step 6 returns `status: ok`.
2. `chrome://extensions` lists **Screenshot2Meet 0.1.0** with no red **Errors** button.
3. Clicking the Screenshot2Meet toolbar button opens the side panel. On a When2meet event
   page where you're signed in, there is **no** yellow warning banner at the top.
4. In the panel, open **Settings** → **Connection**: **Service** says **Running** and
   **Signed in as** shows your When2meet name.

## Load the extension in Chrome

1. Open Chrome and go to `chrome://extensions` (type it in the address bar).
2. Turn on **Developer mode** (toggle in the top-right corner).
3. Click **Load unpacked**.
4. Select the **`extension/dist`** folder inside your clone, for example
   `<path-to-repo>/extension/dist` (macOS / Linux) or `<path-to-repo>\extension\dist`
   (Windows), where `<path-to-repo>` is where you ran `git clone`. Select the `dist` folder
   itself, **not** `extension/` or `extension/static/`.
5. **Screenshot2Meet** appears in the list. The extension has no custom icon yet, so Chrome
   shows a plain grey tile with the letter "S".
6. Click the puzzle-piece **Extensions** button in the toolbar, then click the pin next to
   **Screenshot2Meet** so it stays visible.
7. Click the Screenshot2Meet button to open the side panel.

Whenever you rebuild the extension (`npm run build`), go back to `chrome://extensions` and
click the **reload** (circular arrow) button on the Screenshot2Meet card.

## First use walkthrough

1. **Start the service** ([Step 5](#step-5-start-the-service-so-the-extension-has-something-to-send-screenshots-to))
   and keep its terminal open.
2. **Open your When2meet event** in Chrome and sign in with your name, as you normally would.
3. **Open the side panel** with the Screenshot2Meet toolbar button. If something is wrong
   (service not running, no When2meet event in this tab, not signed in), a yellow banner at
   the top says what to do. Fix it and click **Check again**. **Settings** → **Connection**
   shows the details: service, who you're signed in as, event type, and timezone.
4. **Add screenshots** covering the event's days: iOS Calendar (day or multi-day view) or
   Google Calendar (Schedule view). You can choose files, drag them in, or paste with Ctrl+V / Cmd+V. Each one appears
   as a thumbnail with its file name. Click a thumbnail to see it full size (Previous /
   Next, Esc to close), or click **×** to remove it.
5. Click **Fill When2meet**. The panel reads the screenshots and fills the grid in one go.
   The first run takes longer while the OCR models load. When it's done you see a line like
   `Filled 288 slots · 3 uncertain slots outlined on the grid` next to **Undo fill**.
   - If a screenshot's weekday can't be read, that screenshot gets a small
     **Which day is this column?** prompt instead. Pick the day and click **Fill When2meet**
     again.
   - If the event covers more than one week, the panel asks which date each weekday is.
     Pick the dates and click **Fill When2meet** again.
6. **Check the result on the When2meet grid** and fix anything by hand, the usual
   When2meet way (click and drag). Cells affected by uncertain events have a dashed orange
   outline.
7. Changed your mind? **Undo fill** restores the availability you had before the fill.

Notes:

- Screenshot times are read as **America/New_York**. Specific-dates events are matched by
  date. Days-of-the-week events use plain wall-clock time (When2meet gives them no
  timezone).
- Events whose title contains one of your **keyword rules** (Settings) don't count as busy.
  The default rule is `Plan out Tomorrow`.
- **Color rules** (Settings) work by calendar color. Click **Find colors in my screenshots**
  (or fill once), then click a swatch to add that color, or choose any color and click
  **Add color**. With the **Only count events in these colors** switch off, events in those
  colors don't count as busy. With it on, only events in those colors count. With no colors
  added, color rules are off. Changing them doesn't re-read the screenshots, so clicking
  **Fill When2meet** again is quick. After a fill, the panel says how many events the color
  rules left out.
- The side panel's **Settings** also let you add a buffer around busy events, set a minimum
  free block, change the confidence threshold, and pick the OCR engine.
- **Developer mode** (Settings, off by default): the full-size screenshot view shows what
  was detected (time axis, day columns, and each event box with its times and
  confidence). The overlay appears after you click **Fill When2meet**.

## Optional: use Tesseract instead of PaddleOCR

PaddleOCR works out of the box. Only do this if you want to pick **Tesseract** under
Settings → OCR engine in the side panel. You need the Tesseract program with the
English, Simplified Chinese, Traditional Chinese and Spanish language data
(`eng`, `chi_sim`, `chi_tra`, `spa`), plus one extra Python package.

**Bash (macOS, with Homebrew)**

```bash
# Install Tesseract and its extra language packs
brew install tesseract tesseract-lang
```

**Bash (Ubuntu / Debian Linux)**

```bash
# Install Tesseract with English, Chinese (simplified + traditional) and Spanish data
sudo apt install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim tesseract-ocr-chi-tra tesseract-ocr-spa
```

**PowerShell (Windows)**

```powershell
# Install Tesseract with its installer window, so you can pick languages.
# In the installer, under "Additional language data", tick Chinese (Simplified),
# Chinese (Traditional) and Spanish.
winget install --id UB-Mannheim.TesseractOCR -e --interactive
```

On Windows the installer does **not** add Tesseract to your PATH. Add it (default install
folder shown; change it if you installed elsewhere), then **close and reopen PowerShell**:

```powershell
# Permanently add the Tesseract folder to your user PATH
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";C:\Program Files\Tesseract-OCR", "User")
```

```powershell
# Or, for the current PowerShell window only
$env:Path += ";C:\Program Files\Tesseract-OCR"
```

Then check the languages, and install the Python wrapper into the venv (run from the
`when2meet-autofill` folder with the venv active):

**Bash (macOS / Linux)**

```bash
# List installed Tesseract languages. The list should include chi_sim, chi_tra, eng and spa
tesseract --list-langs

# Install the Tesseract extra for the service
python -m pip install -e "server[tesseract]"
```

**PowerShell (Windows)**

```powershell
# List installed Tesseract languages. The list should include chi_sim, chi_tra, eng and spa
tesseract --list-langs

# Install the Tesseract extra for the service
python -m pip install -e "server[tesseract]"
```

Restart the service (Ctrl+C, then `screenshot2meet serve`) and select **Tesseract** in the
side panel's Settings.

## Stopping the service

Go to the terminal where `screenshot2meet serve` is running and press **Ctrl+C**. You
should see `Shutting down` and `Finished server process`. The side panel then shows
the warning banner "Start the local service" after you click **Check again**.

To leave the virtual environment in that terminal, run `deactivate` (same in Bash and
PowerShell).

## Updating

Stop the service first, then run these from the `when2meet-autofill` folder.

**Bash (macOS / Linux)**

```bash
# Get the latest code
git pull

# Reinstall the service in case its dependencies changed
.venv/bin/python -m pip install -e server

# Rebuild the extension
cd extension
npm install
npm run build
cd ..
```

**PowerShell (Windows)**

```powershell
# Get the latest code
git pull

# Reinstall the service in case its dependencies changed
.venv\Scripts\python -m pip install -e server

# Rebuild the extension
cd extension
npm install
npm run build
cd ..
```

Then open `chrome://extensions` and click **reload** on the Screenshot2Meet card, and
start the service again.

## Uninstalling

1. Stop the service (Ctrl+C).
2. In `chrome://extensions`, click **Remove** on the Screenshot2Meet card.
3. Delete the project folder and the cached OCR models and weekday table. Run this from
   the folder that **contains** `when2meet-autofill`:

**Bash (macOS / Linux)**

```bash
# Delete the project (including its .venv and built extension)
rm -rf when2meet-autofill

# Delete the cached PaddleOCR models
rm -rf ~/.paddlex

# Delete the cached weekday table
rm -rf ~/.cache/screenshot2meet
```

**PowerShell (Windows)**

```powershell
# Delete the project (including its .venv and built extension)
Remove-Item -Recurse -Force when2meet-autofill

# Delete the cached PaddleOCR models
Remove-Item -Recurse -Force "$HOME\.paddlex"

# Delete the cached weekday table
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\screenshot2meet"
```

Only delete `~/.paddlex` (`$HOME\.paddlex`) if no other PaddleOCR projects use it.

## Troubleshooting

**`python3.13: command not found` / `py: No suitable Python runtime found`**
Python 3.13 isn't installed, or isn't on PATH. Install it (see
[Prerequisites](#a-prerequisites)) and open a new terminal. On Windows, `python --version`
may show a different version: always use `py -3.13` to create the venv.

**PowerShell: `.venv\Scripts\Activate.ps1 cannot be loaded because running scripts is disabled`**
Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again.

**`screenshot2meet: command not found` / `screenshot2meet is not recognized`**
The virtual environment isn't active in this terminal. Activate it
(`source .venv/bin/activate` or `.venv\Scripts\Activate.ps1`) from the
`when2meet-autofill` folder, or call it by path: `.venv/bin/screenshot2meet serve`
(Bash) / `.venv\Scripts\screenshot2meet serve` (PowerShell).

**`pip install -e server` fails while installing `paddlepaddle`**
`paddlepaddle==3.2.0` must have a wheel for your Python version and platform. Check that
the venv uses Python 3.13 (`python --version` with the venv active) and a 64-bit Python.
If it still fails, see the
[PaddlePaddle install guide](https://www.paddlepaddle.org.cn/en/install/quick) for your OS.

**Side panel banner says "Start the local service"**
The service isn't running, or is on a different address. Check the service terminal for
errors, run the health check from [Step 6](#step-6-check-the-service-answers-to-confirm-its-running-before-you-set-up-chrome),
then click **Check again** in the banner. The extension only talks to `http://127.0.0.1:8765`,
so don't change the port.

**`error while attempting to bind on address ('127.0.0.1', 8765)` / address already in use**
Another copy of the service (or another program) already uses port 8765. Close the other
terminal running `screenshot2meet serve`, or find the process:

**Bash (macOS / Linux)**

```bash
# Show which process is using port 8765
lsof -i :8765
```

**PowerShell (Windows)**

```powershell
# Show which process is using port 8765
Get-Process -Id (Get-NetTCPConnection -LocalPort 8765).OwningProcess
```

**Side panel banner says "Open your When2meet event in this tab" or "Sign in with your name"**
Switch to the tab with your When2meet event, sign in with your name on the page, then
click **Check again** in the banner. If you opened the event before installing the extension, reload the
When2meet tab.

**The first "Fill When2meet" takes a long time**
The OCR models are downloading. Run `screenshot2meet download-models` once
([Step 4](#step-4-optional-recommended-download-the-ocr-models-now-so-your-first-screenshot-isnt-slow))
so this happens ahead of time.

**"Couldn't read this screenshot: …" (thumbnail outlined in red)**
Nothing is filled until every screenshot can be read. Remove that screenshot (**×**) or
replace it, then click **Fill When2meet** again. A time-grid screenshot needs a visible
time axis (the hour labels on the left): use iOS Calendar's day or multi-day view and don't
crop off the hour labels. A list screenshot needs the day labels on the left (such as
`WED` over `14`) and a time on each event: use Google Calendar's Schedule view.

**Events are shifted by some hours**
Screenshot times are read as America/New_York. If your event is in another timezone, check
the filled grid on When2meet and fix it by hand, or click **Undo fill**.

**Tesseract selected: "The tesseract binary was not found on PATH"**
Tesseract isn't installed or isn't on PATH. On Windows, add `C:\Program Files\Tesseract-OCR`
to PATH ([see above](#optional-use-tesseract-instead-of-paddleocr)), open a new terminal, and
restart the service.

**Tesseract selected: "pytesseract is not installed"**
Run `python -m pip install -e "server[tesseract]"` with the venv active, then restart the
service.

**`npm run build` fails or `node --version` shows v18 or lower**
Install Node.js 20 or newer, open a new terminal, delete `extension/node_modules`, and run
`npm install` and `npm run build` again.

**Chrome: "Manifest file is missing or unreadable"**
You selected the wrong folder. Pick `extension/dist`, and make sure you ran
`npm run build` first.

**I changed the code but nothing changed in Chrome**
Run `npm run build` again, then click **reload** on the Screenshot2Meet card in
`chrome://extensions`, and reload the When2meet tab.

## Privacy

Screenshots go only to the local service on `127.0.0.1`. Nothing is uploaded anywhere
else. The extension talks to When2meet only through the event page you have open, the
same way your own mouse would.

## Development

Run these from the `when2meet-autofill` folder. The dev tools need the `dev` extra:
`python -m pip install -e "server[dev]"` with the venv active.

**Bash (macOS / Linux)**

```bash
# Python unit tests (includes an end-to-end test on the fixture)
cd server
../.venv/bin/python -m pytest
cd ..

# Extractor eval: per-event time error, misses, false positives, low-confidence flags
.venv/bin/python eval/run.py --debug eval/out

# Extract events from any screenshot and write debug overlays (replace <path-to-screenshot>)
.venv/bin/screenshot2meet extract <path-to-screenshot> --debug debug_out

# Extension: typecheck, build, and the live end-to-end test (needs the service running)
cd extension
npm run typecheck
npm run build
npm run e2e
```

**PowerShell (Windows)**

```powershell
# Python unit tests (includes an end-to-end test on the fixture)
cd server
..\.venv\Scripts\python -m pytest
cd ..

# Extractor eval: per-event time error, misses, false positives, low-confidence flags
.venv\Scripts\python eval\run.py --debug eval\out

# Extract events from any screenshot and write debug overlays (replace <path-to-screenshot>)
.venv\Scripts\screenshot2meet extract <path-to-screenshot> --debug debug_out

# Extension: typecheck, build, and the live end-to-end test (needs the service running)
cd extension
npm run typecheck
npm run build
npm run e2e
```

`npm run watch` rebuilds the extension on every save (you still need to click reload in
`chrome://extensions`).

Docs:
- [`docs/when2meet-dom.md`](docs/when2meet-dom.md): how When2meet's grid works and the fill strategy.
- [`docs/manual-test-checklist.md`](docs/manual-test-checklist.md): manual checks for the extension.

## Roadmap: letting other people use it

Right now every user needs the Python service running locally, which is too much to ask
of most people. There are two planned ways out. Plan A comes first; Plan B is the
long-term goal. Either way, the extension is published on the Chrome Web Store.

### Publishing the extension (needed for both plans)

- [ ] Register a Chrome Web Store developer account (one-time $5 fee).
- [ ] Add icons (16/48/128 px) to `extension/static/` and reference them in `manifest.json`.
- [ ] Write a privacy policy page. For Plan A, say screenshots are sent to our server,
      processed in memory, and never stored. For Plan B, say they never leave the browser.
- [x] Remove the "Load fixture sample" link from the side panel (it's a dev aid).
- [ ] `npm run build`, zip the contents of `extension/dist`, and upload it with a listing
      (description, screenshots) and a justification for each permission: `sidePanel`,
      `storage`, `when2meet.com`, and the service host. Review takes a few days.

### Plan A: hosted service (do this first)

Run the existing FastAPI service on a server, so users only install the extension.

- [ ] **Service URL setting:** replace the hard-coded `SERVICE_URL` in
      `extension/src/api.ts` with a setting that defaults to the hosted URL. Keep
      `http://127.0.0.1:8765` available for development. Update `host_permissions` in
      `manifest.json` to the hosted domain.
- [ ] **Dockerfile** for `server/`: Python 3.13, `pip install ./server`, and run
      `screenshot2meet download-models` at build time so the image contains the models.
      Serve with `uvicorn screenshot2meet.app:app --host 0.0.0.0`.
- [ ] **Deploy** to Fly.io, Railway, or a small VPS: CPU only, about 2 GB RAM (PaddleOCR).
      Expect about 4 s per screenshot.
- [ ] **Harden the API:**
  - HTTPS only.
  - Upload size limit (e.g. 10 MB per image, 10 images per request).
  - Rate limit per IP.
  - Optional shared API key sent by the extension.
  - CORS restricted to the published extension's ID (`app.py` currently allows any
    `chrome-extension://` origin).
- [ ] **Privacy:** don't log or store images. (Done in the extension: debug overlays are
      only requested when Developer mode is on.)
- [ ] **Concurrency:** `/extract` runs under one global lock (`_extract_lock`).
      That's fine for a few users; for more, run several workers or a small queue.

### Plan B: run extraction in the browser (long-term)

No server at all, and screenshots never leave the user's machine. This means porting the
extraction pipeline from Python to TypeScript/WASM inside the extension.

- [ ] **Image processing:** port `blocks.py`, `timeaxis.py` and the column logic in
      `extractor.py` with OpenCV.js (or plain canvas pixel code; the pipeline only needs
      masks, connected components and a 3×3 close).
- [ ] **OCR:** try the PaddleOCR detection and recognition models exported to ONNX and run
      with ONNX Runtime Web; Tesseract.js is the fallback. Check model size: a single
      extension package has a size limit, so models may need to download on first use.
- [ ] **Weekdays:** ship the CLDR weekday table as a JSON file generated by
      `screenshot2meet.weekdays.build_weekday_table()`, instead of building it at runtime.
- [ ] **Slot computation** (`slots.py`) and **merging** (`merge.py`) are small and can be
      ported directly.
- [ ] **Keep the Python version as the reference:** run the same fixtures through both
      implementations and require identical results from `eval/run.py` before switching
      over. Add more fixtures first, especially light mode, English UI, and a
      12-hour clock.
