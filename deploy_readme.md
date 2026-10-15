# Deploying Screenshot2Meet

This guide covers the whole process of letting other people use Screenshot2Meet without
installing anything but the Chrome extension ("Plan A" in the README roadmap):

1. Run the Python service on a server with HTTPS, so users don't need Python or OCR models.
2. Point the extension at that server.
3. Publish the extension on the Chrome Web Store.

Each step says what you run and why. Commands are shown for **Bash (macOS / Linux)** and
**PowerShell (Windows)**. Replace anything in `<angle brackets>` with your own value.

> **Status: the code isn't deploy-ready yet.** [Part 2](#part-2-code-changes-needed-before-deploying)
> lists the changes the code needs first: a configurable service URL, a Dockerfile, API
> limits, and store assets. None of them are in the repo yet. Parts 3–8 assume they're done.

## Contents

- [Overview](#overview)
- [Part 1: Accounts and tools](#part-1-accounts-and-tools)
- [Part 2: Code changes needed before deploying](#part-2-code-changes-needed-before-deploying)
- [Part 3: Deploy the service on Fly.io](#part-3-deploy-the-service-on-flyio)
- [Part 4: Build and test the extension against the hosted service](#part-4-build-and-test-the-extension-against-the-hosted-service)
- [Part 5: Publish on the Chrome Web Store](#part-5-publish-on-the-chrome-web-store)
- [Part 6: After approval: lock the service to your extension](#part-6-after-approval-lock-the-service-to-your-extension)
- [Part 7: Releasing updates](#part-7-releasing-updates)
- [Part 8: Monitoring, costs and rollback](#part-8-monitoring-costs-and-rollback)
- [Troubleshooting](#troubleshooting)

## Overview

```text
 User's Chrome                                  Your server (Fly.io)
┌──────────────────────────────┐   HTTPS    ┌──────────────────────────────────┐
│ Screenshot2Meet extension    │ ─────────► │ screenshot2meet service (FastAPI) │
│ (from the Chrome Web Store)  │ screenshots│ PaddleOCR models baked into image │
│  side panel + When2meet page │ ◄───────── │ /health  /extract  /fill-plan     │
└──────────────────────────────┘  events    └──────────────────────────────────┘
```

| What | Where it runs | Notes |
|---|---|---|
| Service | Fly.io (or any host that runs Docker images) | CPU only, 4 GB RAM (peaks at about 2.5 GB), about 3–6 s per screenshot |
| Extension | Users' Chrome, installed from the Chrome Web Store | Talks to When2meet only through the open event page |

**Rough time:** a few hours of your own work, plus Chrome Web Store review (usually a few
days, sometimes longer).

**Costs:** a one-time Chrome Web Store developer fee ($5 at the time of writing) and the
hosting bill. A 4 GB machine running all the time costs about $24–25 a
month depending on the provider and settings. Check current pricing before you start.

## Part 1: Accounts and tools

### Accounts

- [ ] **Google account** for the [Chrome Web Store developer dashboard](https://chrome.google.com/webstore/devconsole).
- [ ] **Fly.io account** at [fly.io](https://fly.io) (needs a payment card).
- [ ] **A place to host a privacy policy page.** The Web Store requires one because the
      extension sends user content (screenshots) to a server. GitHub Pages or any static
      page works.

### Tools

- [ ] **git**, **Python 3.13**, **Node.js 20+**: the same as the README's Method 1.
- [ ] **flyctl**: the Fly.io command-line tool.
- [ ] **Docker Desktop** (optional): only to test the image on your own computer.
      Fly.io can build the image for you remotely.

**Bash (macOS / Linux)**

```bash
# Install flyctl
curl -L https://fly.io/install.sh | sh

# Check it works. You should see a version like: flyctl v0.x.x
fly version

# Check Docker (optional). You should see a server version, not "Cannot connect"
docker info --format '{{.ServerVersion}}'
```

**PowerShell (Windows)**

```powershell
# Install flyctl
iwr https://fly.io/install.ps1 -useb | iex

# Close and reopen PowerShell, then check it works. You should see a version like: flyctl v0.x.x
fly version

# Check Docker (optional; start Docker Desktop first). You should see a server version
docker info --format '{{.ServerVersion}}'
```

## Part 2: Code changes needed before deploying

These changes aren't in the repo yet. Each one says what to change and why.

### 2.1 Make the service URL configurable

**Why:** the extension is hard-wired to `http://127.0.0.1:8765` in two places.

- `extension/src/api.ts`: `export const SERVICE_URL = "http://127.0.0.1:8765";`
- `extension/static/manifest.json`: `"host_permissions"` contains `"http://127.0.0.1:8765/*"`.

**Change:**

1. Add a `serviceUrl` setting in `extension/src/settings.ts`. It defaults to the hosted
   URL, e.g. `https://<app-name>.fly.dev`, and can be switched to `http://127.0.0.1:8765`
   for development (for example in Settings when Developer mode is on).
2. In `extension/src/api.ts`, read the URL from settings instead of the constant.
3. In `manifest.json`, set `host_permissions` to the hosted origin, e.g.
   `"https://<app-name>.fly.dev/*"`. Keep the localhost entry only in development builds:
   reviewers ask about every host permission, and an unneeded one can slow review.
4. Update the problem banner. "Start the local service" only makes sense for development.
   For the hosted service, say something like "Can't reach the Screenshot2Meet service.
   Try again later."

### 2.2 Add a Dockerfile

**Why:** Fly.io (and most hosts) run Docker images. Baking the OCR models into the image
means no request waits for a model download.

Create `server/Dockerfile`:

```dockerfile
# Screenshot2Meet service image: Python 3.13, CPU-only PaddleOCR, models baked in.
FROM python:3.13-slim

# Runtime libraries for PaddlePaddle (OpenMP) and OpenCV.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app/server
RUN pip install --no-cache-dir /app/server

# Download the OCR models and build the weekday table at build time,
# so the first request doesn't wait for a few hundred MB of downloads.
RUN screenshot2meet download-models

ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn screenshot2meet.app:app --host 0.0.0.0 --port ${PORT}"]
```

And `server/.dockerignore`, so caches and test output stay out of the image:

```text
__pycache__/
*.egg-info/
.pytest_cache/
tests/
```

> **Untested:** this Dockerfile hasn't been built yet. Test it locally first (below). If
> PaddlePaddle complains about a missing shared library (`lib...so`), add that package to
> the `apt-get install` line.

**Test the image locally (optional, needs Docker Desktop running).** Run from the repo root.

**Bash (macOS / Linux)**

```bash
# Build the image from the server folder (several minutes; downloads a few GB the first time)
docker build -t screenshot2meet server

# Run it on port 8080
docker run --rm -p 8080:8080 screenshot2meet
```

```bash
# In a second terminal: check it answers. You should see: {"status":"ok","version":"0.1.0"}
curl http://127.0.0.1:8080/health
```

**PowerShell (Windows)**

```powershell
# Build the image from the server folder (several minutes; downloads a few GB the first time)
docker build -t screenshot2meet server

# Run it on port 8080
docker run --rm -p 8080:8080 screenshot2meet
```

```powershell
# In a second PowerShell window: check it answers. You should see status "ok" and version "0.1.0"
Invoke-RestMethod http://127.0.0.1:8080/health
```

### 2.3 Harden the API

**Why:** the service was written for one user on `127.0.0.1`. On the internet, anyone can
call it.

- [ ] **Upload limits:** reject requests over e.g. 10 images or 10 MB per image in
      `/extract` (`server/src/screenshot2meet/app.py`) with HTTP 413.
- [ ] **Rate limiting** per IP address, e.g. with a small middleware or the `slowapi`
      package. Fly.io passes the client IP in the `Fly-Client-IP` header.
- [ ] **CORS:** `app.py` currently allows any extension
      (`allow_origin_regex=r"^chrome-extension://[a-p]{32}$"`). Make the allowed origin
      configurable with an environment variable (e.g. `ALLOWED_EXTENSION_ID`), so you can
      lock it to your published extension in [Part 6](#part-6-after-approval-lock-the-service-to-your-extension)
      without code changes.
- [ ] **Optional shared API key:** the extension sends a header, the service checks it.
      This only stops casual misuse, because anyone can read the key from the extension
      package.
- [ ] **No image logging:** make sure nothing writes uploaded screenshots to disk or logs.
      The extension already asks for debug overlays only in Developer mode.
- [ ] **Concurrency:** `/extract` runs under one global lock (`_extract_lock`), so requests
      are handled one at a time. That's fine for a few users. For more, run more machines
      (see [Part 8](#part-8-monitoring-costs-and-rollback)).

### 2.4 Get the extension ready for the store

- [ ] **Icons:** add 16, 48 and 128 px PNG icons to `extension/static/` and reference them
      in `manifest.json` (`"icons"` and `"action": {"default_icon": ...}`).
- [ ] **Description:** `manifest.json` says "processed by a local service". Change it to
      describe the hosted service.
- [ ] **Version:** set `"version"` in `manifest.json` (and `package.json`, to keep them in
      sync). Every upload to the store needs a higher version than the last one.
- [ ] **Privacy policy page**, published at a public URL. It should say:
  - which data is sent (the screenshots you add, plus the When2meet event's dates and mode),
  - where it goes (your service, over HTTPS),
  - that screenshots are processed in memory and not stored or shared,
  - that settings are stored only in the user's browser,
  - and how to contact you.

## Part 3: Deploy the service on Fly.io

Run these from the repo root. Fly.io builds the Docker image on its own servers, so you
don't need Docker locally.

### Step 1: Sign in to Fly.io, so flyctl can create apps in your account

**Bash (macOS / Linux)**

```bash
# Opens a browser to sign in
fly auth login
```

**PowerShell (Windows)**

```powershell
# Opens a browser to sign in
fly auth login
```

### Step 2: Create the app, to get a name, a region and a fly.toml config file

Pick a region close to your users. `ewr` (New Jersey) is close to New York, which matters
because screenshot times are read as New York time.

**Bash (macOS / Linux)**

```bash
# Go to the folder with the Dockerfile
cd server

# Create the app without deploying yet (replace <app-name>, e.g. screenshot2meet-api)
fly launch --name <app-name> --region ewr --no-deploy
```

**PowerShell (Windows)**

```powershell
# Go to the folder with the Dockerfile
cd server

# Create the app without deploying yet (replace <app-name>, e.g. screenshot2meet-api)
fly launch --name <app-name> --region ewr --no-deploy
```

This writes `server/fly.toml`. Open it and check these settings:

```toml
[http_service]
  internal_port = 8080        # must match PORT in the Dockerfile
  force_https = true          # HTTP requests are redirected to HTTPS
  auto_stop_machines = "stop" # stop when idle to save money...
  auto_start_machines = true
  min_machines_running = 0    # ...set to 1 to avoid slow first requests (costs more)

[[vm]]
  memory = "4gb"              # the service peaks at about 2.5 GB while reading
  cpus = 2
```

> **Cold starts:** with `min_machines_running = 0`, the first request after an idle period
> starts the machine and loads the OCR models, which can add tens of seconds. That's fine
> for a hobby project. Use `1` if users complain.

Commit `server/Dockerfile`, `server/.dockerignore` and `server/fly.toml`.

### Step 3: Deploy, to build the image and start the service

**Bash (macOS / Linux)**

```bash
# Build remotely and deploy (the first build takes several minutes)
fly deploy
```

**PowerShell (Windows)**

```powershell
# Build remotely and deploy (the first build takes several minutes)
fly deploy
```

You should see the build finish and end with a line like
`Visit your newly deployed app at https://<app-name>.fly.dev/`.

### Step 4: Verify it worked, by checking the health endpoint and reading a real screenshot

**Bash (macOS / Linux)**

```bash
# Health check. You should see: {"status":"ok","version":"0.1.0"}
curl https://<app-name>.fly.dev/health

# Read the test fixture (run from the repo root). You should see JSON with "events": [...] (12 events)
curl -F files=@fixtures/ios_calendar_mon_tue.png https://<app-name>.fly.dev/extract
```

**PowerShell (Windows)**

```powershell
# Health check. You should see status "ok" and version "0.1.0"
Invoke-RestMethod https://<app-name>.fly.dev/health

# Read the test fixture (run from the repo root). You should see JSON with "events": [...] (12 events)
curl.exe -F files=@fixtures/ios_calendar_mon_tue.png https://<app-name>.fly.dev/extract
```

(`curl.exe`, not `curl`: in Windows PowerShell 5.1, `curl` is an alias for
`Invoke-WebRequest`, which takes different arguments.)

### Step 5 (optional): Use your own domain, for a nicer URL

**Bash (macOS / Linux)**

```bash
# Ask Fly.io for a TLS certificate for your domain (replace <api.example.com>)
fly certs add <api.example.com>

# Shows the DNS records to create at your domain registrar, and the certificate status
fly certs show <api.example.com>
```

**PowerShell (Windows)**

```powershell
# Ask Fly.io for a TLS certificate for your domain (replace <api.example.com>)
fly certs add <api.example.com>

# Shows the DNS records to create at your domain registrar, and the certificate status
fly certs show <api.example.com>
```

If you use a custom domain, use it everywhere below instead of `<app-name>.fly.dev`.

## Part 4: Build and test the extension against the hosted service

### Step 1: Point the extension at the hosted service

Set the default service URL (from [2.1](#21-make-the-service-url-configurable)) and the
`host_permissions` in `manifest.json` to `https://<app-name>.fly.dev`.

### Step 2: Build it

**Bash (macOS / Linux)**

```bash
# From the repo root: install exact dependency versions and build into extension/dist
cd extension
npm ci
npm run typecheck
npm run build
```

**PowerShell (Windows)**

```powershell
# From the repo root: install exact dependency versions and build into extension\dist
cd extension
npm ci
npm run typecheck
npm run build
```

### Step 3: Test the build exactly as users will get it

1. **Stop your local service**, so you're sure the extension uses the hosted one.
2. In `chrome://extensions`, remove any older unpacked copy, then **Load unpacked** and
   select `extension/dist`.
3. Open a When2meet event, sign in, add a screenshot, and click **Fill When2meet**.
4. Check: there's no warning banner, and Settings → Connection shows **Service: Running**.
   The fill works, Undo works, and the colour rules work.

### Step 4: Make the upload package

The zip must have `manifest.json` at its top level, not inside a `dist` folder.

**Bash (macOS / Linux)**

```bash
# From the extension folder: zip the contents of dist (replace <version>, e.g. 0.2.0)
cd dist
zip -r ../screenshot2meet-<version>.zip .
cd ..

# Check manifest.json is at the top level of the zip. You should see "manifest.json" with no folder in front
unzip -l screenshot2meet-<version>.zip | grep manifest.json
```

**PowerShell (Windows)**

```powershell
# From the extension folder: zip the contents of dist (replace <version>, e.g. 0.2.0)
Compress-Archive -Path dist\* -DestinationPath screenshot2meet-<version>.zip -Force

# Check manifest.json is at the top level of the zip. You should see "manifest.json" with no folder in front
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::OpenRead("$PWD\screenshot2meet-<version>.zip").Entries.FullName | Select-String manifest.json
```

> The build includes inline source maps (`sourcemap: "inline"` in `extension/build.mjs`).
> They're allowed, but they roughly double the package size. Consider turning them off for
> store builds.

## Part 5: Publish on the Chrome Web Store

### Step 1: Register as a developer

1. Go to the [developer dashboard](https://chrome.google.com/webstore/devconsole) and sign
   in with the Google account that should own the extension.
2. Accept the developer agreement and pay the one-time registration fee.
3. Fill in the publisher name and contact email, and verify the email.

### Step 2: Create the item

1. Click **New item** and upload `screenshot2meet-<version>.zip`.
2. The dashboard shows any manifest problems right away. Fix them, rebuild, re-zip, and
   upload again.

### Step 3: Fill in the store listing

| Field | What to put |
|---|---|
| Description | What it does, which calendars it reads (iOS Calendar day/multi-day, Google Calendar Schedule view), and that screenshots are sent to your service for reading |
| Category | Productivity |
| Language | English (add more if you translate the listing) |
| Store icon | 128×128 PNG |
| Screenshots | At least one, 1280×800 or 640×400 (the side panel next to a filled When2meet grid works well) |
| Small promo tile | 440×280 PNG |

The dashboard shows the current required and optional images. Follow it if it differs
from this table.

### Step 4: Fill in the Privacy practices tab

This is where most rejections come from. Be specific.

- **Single purpose:** "Fill the user's When2meet availability from screenshots of their calendar."
- **Permission justifications:**
  - `sidePanel`: the extension's interface is a side panel.
  - `storage`: saves the user's settings and the undo history, in the browser only.
  - `https://www.when2meet.com/*` and `https://when2meet.com/*`: reads the open event's
    grid and fills in the user's own availability, the same way their mouse would.
  - `https://<app-name>.fly.dev/*`: sends the screenshots the user adds to the
    Screenshot2Meet service, which reads the events and computes free slots.
- **Remote code:** No. All JavaScript ships in the package; the service only returns data.
- **Data usage:** declare what's collected. The screenshots, and the event dates and mode
  sent to the service, are user-provided content. Then certify that the data isn't sold or
  used for anything unrelated to the single purpose.
- **Privacy policy URL:** the page from [2.4](#24-get-the-extension-ready-for-the-store).

### Step 5: Choose who can see it, then submit

1. **Distribution → Visibility:**
   - **Unlisted:** only people with the link can install it. Good for a first release
     with friends.
   - **Public:** anyone can find it in search.
   - **Private:** only accounts you name as testers.
2. Click **Submit for review**. Optionally choose to publish manually after approval,
   so you control the release moment.
3. Wait for the review email. Extensions with host permissions often get a longer review.

### Step 6: Verify it worked

1. Open the store link from the dashboard in a Chrome profile **without** your unpacked copy.
2. Click **Add to Chrome**, pin the extension, open a When2meet event, and run one fill.

## Part 6: After approval: lock the service to your extension

**Why:** until now, any extension could call your service. The published extension has a
fixed ID (shown in the dashboard and in its store URL), so you can allow only that one.

**Bash (macOS / Linux)**

```bash
# From the server folder: tell the service which extension may call it (replace <extension-id>, 32 letters a-p)
fly secrets set ALLOWED_EXTENSION_ID=<extension-id>
```

**PowerShell (Windows)**

```powershell
# From the server folder: tell the service which extension may call it (replace <extension-id>, 32 letters a-p)
fly secrets set ALLOWED_EXTENSION_ID=<extension-id>
```

Setting a secret restarts the service. This needs the CORS change from
[2.3](#23-harden-the-api). Note that your **unpacked** development copy has a different ID,
so use the local service for development (Developer mode → service URL), or allow a
second, development ID too.

**Verify:** the store version still works. A request from another origin should now get
no `Access-Control-Allow-Origin` header back.

## Part 7: Releasing updates

### Service updates

**Bash (macOS / Linux)**

```bash
# From the server folder: build and roll out the new version
fly deploy
```

**PowerShell (Windows)**

```powershell
# From the server folder: build and roll out the new version
fly deploy
```

**Keep the API backward compatible.** Users' extensions update on their own schedule, so
for a while old and new extension versions call the same service. Add fields instead of
renaming or removing them. That's how `color` and `color_skipped` were added.

### Extension updates

1. Raise `"version"` in `manifest.json` (and `package.json`), e.g. `0.2.0` → `0.2.1`.
2. Build and zip as in [Part 4](#part-4-build-and-test-the-extension-against-the-hosted-service).
3. Dashboard → your item → **Package** → **Upload new package**, then **Submit for review**.
4. After approval, Chrome updates installed copies automatically, usually within a few hours.

If an update needs a new service feature, **deploy the service first**, then submit the
extension.

## Part 8: Monitoring, costs and rollback

**Bash (macOS / Linux)**

```bash
# Live logs (requests, errors, out-of-memory kills)
fly logs

# Machines, their state and region
fly status

# Past deployments, newest first
fly releases

# Run more machines when one isn't enough (each handles one /extract at a time)
fly scale count 2
```

**PowerShell (Windows)**

```powershell
# Live logs (requests, errors, out-of-memory kills)
fly logs

# Machines, their state and region
fly status

# Past deployments, newest first
fly releases

# Run more machines when one isn't enough (each handles one /extract at a time)
fly scale count 2
```

**Rollback:** `fly releases --image` lists the image of each release. Run
`fly deploy --image <image-from-an-earlier-release>` to go back. For the extension, you
can't "un-update" users: upload a fixed package with a higher version number.

**Costs:** check the Fly.io billing page after the first week. The main levers are
`min_machines_running` (always-on vs cold starts), machine size, and the machine count.

## Troubleshooting

**`fly deploy` fails while installing `paddlepaddle`**
The image must use Python 3.13 on x86-64 (Fly.io's default). Check the `FROM` line. If the
error mentions a missing `lib...so`, add the matching Debian package to the `apt-get
install` line.

**The service restarts, or logs show "out of memory" / exit code 137**
The service peaks at about 2.5 GB, so 2 GB machines run out. Check `fly.toml` says
`memory = "4gb"` and run `fly deploy`, or run `fly scale memory 4096`.

**The extension says it can't reach the service, and the browser console shows a CORS error**
The service's allowed extension ID doesn't match the extension calling it. The published
and unpacked copies have different IDs. Check `fly secrets list` and
[Part 6](#part-6-after-approval-lock-the-service-to-your-extension).

**HTTP 413 from `/extract`**
The upload is over the limits from [2.3](#23-harden-the-api). The side panel shows the
error. Lower the limit or ask users for fewer or smaller screenshots.

**The first fill after a quiet period is very slow**
That's the cold start (the machine starts and loads the models). Set
`min_machines_running = 1` in `fly.toml` and run `fly deploy`.

**The store rejects the submission**
The email names the policy. Usual causes:
- a permission without a clear justification, or one the code doesn't need (e.g. the
  localhost host permission left in),
- a missing or vague privacy policy,
- a listing description that doesn't match what the extension does.

Fix it, raise the version, and resubmit.

**Upload fails with "manifest file is missing"**
The zip contains a `dist/` folder. Zip the **contents** of `dist`, as in
[Part 4, Step 4](#step-4-make-the-upload-package).
