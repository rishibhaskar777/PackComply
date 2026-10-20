# Setup, demo and operations guide

Detail behind the [README](../README.md) "Getting started" section. Windows-first (no Docker needed); a Docker Compose option is at the end. For a step-by-step PostgreSQL install from the EDB zip files, see [local-setup-windows.md](local-setup-windows.md).


### Prerequisites

- Python 3.11+
- Node.js 20+
- Docker Desktop (recommended for PostgreSQL)

### Run with Docker

1. Copy `.env.example` to `.env` and adjust values only if needed.
2. Run `docker compose up --build`. The backend waits for PostgreSQL (up to 30 s), applies all Alembic migrations, then starts the API.
3. Open `http://localhost:5173` for the frontend.
4. Open `http://localhost:8000/docs` for API documentation.

Uploaded images and the downloaded PaddleOCR models are kept in the named volumes `uploads` and `paddlex_cache`, so they survive rebuilds. The first OCR run downloads the models, which needs internet access.

Health checks: `GET http://localhost:8000/api/health` (liveness) and `GET http://localhost:8000/api/health/ready` (readiness: 200 when PostgreSQL answers, 503 otherwise). The sign-in page polls `GET /api/system/status` (public, cached for 5 s): `{"status": "ok"|"degraded"|"down", "database": bool, "ocr": "ready"|"warming_up"|"unavailable", "ruleset_version", "rule_count", "languages"}`. It always answers 200 while the API runs; `status` is `down` without the database and `degraded` when OCR is unavailable.

### Run locally

Start PostgreSQL with `docker compose up db -d`, then in separate terminals run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn backend.app.main:app --reload
```

```powershell
cd frontend
npm install
npm run dev
```

Apply database migrations once PostgreSQL is running:

```powershell
alembic -c backend/alembic.ini upgrade head
```

Do not commit `.env`; it may contain credentials.

## Demo day (Windows, no Docker)

Everything runs on the laptop: PostgreSQL, the API, the web app and OCR. No paid APIs, no API keys, no cloud calls while it runs.

### One-command start

Prerequisites, once: the Python venv (`python -m venv .venv`, then `pip install -e ".[dev]"`, which also installs the `qrcode` package used for the terminal QR), `npm install` in `frontend/`, the OCR models downloaded (`python -m backend.scripts.warm_ocr`), and PostgreSQL initialised.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-demo.ps1      # add -NoBrowser to skip opening the browser
powershell -ExecutionPolicy Bypass -File scripts\stop-demo.ps1       # -StopPostgres to stop the database too
```

`start-demo.ps1` (the two PostgreSQL paths are variables at the top of the script, default `D:\Development\PGSQL\pgsql\bin` and `D:\Development\PGSQL\data`):

1. checks the venv, `npm` and `frontend\node_modules` (and offers `npm install` if it is missing) and that ports 8000 and 5173 are free;
2. starts PostgreSQL with `pg_ctl` if port 5432 is not already answering (an already-running server is left alone);
3. runs `alembic -c backend/alembic.ini upgrade head`, then prints a **pre-flight summary**: database answers, migrations at head, OCR models present on disk (`.paddlex\official_models`), free disk space, the LAN address and the firewall rule. A critical failure (database, migrations, OCR models, under 0.5 GB free) stops the start with the fix before any window opens; a missing Wi-Fi address or firewall rule is only a warning (the laptop-only demo still works);
4. detects the laptop's Wi-Fi/LAN IPv4 (the adapter with a default gateway; Hyper-V, WSL, VMware, VirtualBox, VPN and similar virtual adapters are ignored);
5. starts uvicorn (`127.0.0.1:8000`) and the HTTPS Vite dev server (`:5173`) in two separate windows;
6. waits for `/api/health/ready`, for the web port and for the OCR warm-up (`/api/system/status` → `ocr: ready`), with a live timer;
7. prints the laptop URL, the phone URL and a **QR code for the phone URL**:

```text
 Laptop : https://localhost:5173/#/overview
 Phone  : https://<laptop-ip>:5173/#/capture   (same Wi-Fi as this laptop)
 <QR code>
```

Failures stop with one clear message and the fix: PostgreSQL does not start (log path and the stale `postmaster.pid` hint), port 8000/5173 busy (who owns it, and `scripts\stop-demo.ps1`), venv or `alembic` missing (the exact commands), `node_modules` missing (offers `npm install`; `-Yes` accepts), and a missing firewall rule: if Windows Firewall has no rule for port 5173 the script prints the exact command to run once in an **Administrator** PowerShell:

```powershell
New-NetFirewallRule -DisplayName "PackComply Demo (5173)" -Direction Inbound -Protocol TCP -LocalPort 5173 -Action Allow -Profile Any
```

The API logs one summary line when it is ready, for example `PackComply ready | DB ok | ruleset LM_PC_2011 v2 (14 rules) | OCR en+hi ready (warm-up 13.2 s) | LAN https://<laptop-ip>:5173/#/capture`.

On a desktop screen the top bar has an **Open on phone** button: it shows the same phone URL as a QR code, drawn in the browser (`GET /api/system/network` returns the address; no image service is involved). `LAN_IP` / `PHONE_URL` in `.env` override the detected address.

### Demo data

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.seed_demo                       # sample folders next to the repository, if present
.\.venv\Scripts\python.exe -m backend.scripts.seed_demo D:\photos\labels      # or any folder(s) of JPG/PNG/WebP photos
.\.venv\Scripts\python.exe -m backend.scripts.seed_demo --inspector-password "..." --maker-password "..."
.\.venv\Scripts\python.exe -m backend.scripts.seed_demo --reset-demo          # remove only what the seed created
```

- Creates **`demo_inspector`** (OFFICIAL, owns most records and sees the risk queue) and **`demo_maker`** (MANUFACTURER, owns its own six products, below). Passwords are printed once, when an account is created (random `demo-NNNNNN`), or set with the flags. Re-running never changes an existing password unless you pass the flag. Both accounts and what each one sees are printed at the end.
- **`demo_maker`'s data** is separate from the inspector's: six products with a mix of *Ready to print* (3), *Fix needed* (2) and *Check on pack* (1), and **Gluco Biscuits checked twice**: a first draft with 3 fixes, then the corrected label with none ("improved from 3 fixes to 0"). Four products come from the sample photos; Gluco Biscuits and Cream Cracker are demo **artwork** (`demo/artwork/*.png`, drawn by `python -m backend.scripts.make_demo_artwork`, with their OCR stored in `demo/artwork/ocr/`). The two Gluco PNGs are also what you upload live in the manufacturer demo.
- Imports one inspection per photo through the real workflow (extraction → rules → risk → repeat patterns). A photo whose OCR is already stored in `backend/tests/fixtures/labels/real/` reuses it (about 10 s for all 23 sample photos, and the record shows the OCR time that photo really took); other photos are read with PaddleOCR (`--no-ocr` skips them).
- Dates are spread over the last four weeks; the three lowest-risk records of the inspector are verified; the highest-risk record is left unreviewed for the live correction.
- Idempotent: an inspection is identified by its photo's file name (`DEMO-<hash>`, the manufacturer's `DEMO-M-<hash>`), so a second run adds nothing; an older manufacturer demo layout is replaced. Seeded rows carry a `demo_seed` tag (migration `20261019_0012`), so `--reset-demo` deletes only tagged inspections, their photo files and the two demo accounts. Linked self-checks (migration `20261020_0013`: `previous_inspection_id`, `product_key`) are handled the same way. Your own accounts and records are never touched.

### Before a demo: backup

Take a backup once the demo data is the way you want it, and again before you let anyone else click around:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup-db.ps1                 # -SkipUploads for the database only
powershell -ExecutionPolicy Bypass -File scripts\restore-db.ps1 -File backups\packcomply-20261021-090000.dump
powershell -ExecutionPolicy Bypass -File scripts\restore-db.ps1 -File backups\packcomply-20261021-090000.dump -UploadsZip backups\packcomply-uploads-20261021-090000.zip
```

- `backup-db.ps1` uses `pg_dump` from the PostgreSQL `bin` folder (the same `$PgBin` setting as `start-demo.ps1`) and the connection in `DATABASE_URL` (`.env`). It writes `backups\packcomply-<timestamp>.dump` (custom format) and, unless `-SkipUploads`, `backups\packcomply-uploads-<timestamp>.zip` with the stored photos (the evidence behind every record). `backups\` is git-ignored.
- `restore-db.ps1` uses `pg_restore --clean --if-exists`. It is destructive, so it first shows what will be replaced and **requires typing the backup's file name**; anything else cancels without a change. It refuses to run while the API port (8000) is in use (run `scripts\stop-demo.ps1` first), brings an older backup up to date with `alembic upgrade head`, and only unpacks the uploads zip after a second typed confirmation.
- Resetting the demo data stays a command-line action (`seed_demo --reset-demo`); there is deliberately no "reset" button in the app.


### Speed and reliability notes

- The first screen loads only English plus the app shell: other UI languages are separate chunks fetched when selected (and cached), and every page is its own route chunk behind a skeleton. Initial JavaScript is about 110 kB gzip (it was 394 kB); the two Geist font files are preloaded.
- If OCR crashes or times out for one photo of a multi-photo inspection, the inspection completes with the others (see the fallbacks in [DEMO.md](DEMO.md)). If no photo at all can be read, the run fails with a clear message and **Try again** resumes it.

### Testing on a real phone (same Wi-Fi)

1. Run the backend as usual: `uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`.
2. Run the frontend with `npm run dev` in `frontend/` (it uses `vite --host`). The dev server proxies `/api` to `http://127.0.0.1:8000` (override with `VITE_PROXY_TARGET`), so the phone talks to one origin and no CORS change is needed.
3. Find the laptop's LAN IP (`ipconfig`, the IPv4 address, e.g. `<laptop-ip>`) and allow ports 5173 in the Windows firewall for private networks.
4. On the phone open `http://<laptop-ip>:5173`, sign in, tap **New inspection**. On screens narrower than 768px this opens the field app (`#/capture`).

#### Live camera preview on a phone (HTTPS dev mode)

Browsers only allow the live camera preview on HTTPS or `localhost`, and a phone opening `http://<LAN-IP>:5173` is neither. For the live rear-camera preview run the dev server over HTTPS with a local self-signed certificate (the free `@vitejs/plugin-basic-ssl`; nothing is downloaded or sent anywhere at runtime):

1. In `frontend/` run `npm run dev:https` (instead of `npm run dev`; plain `npm run dev` is unchanged). The `/api` proxy keeps working: the browser speaks HTTPS to Vite, Vite speaks HTTP to the API on the laptop. The frontend calls the relative `/api` path whenever `VITE_API_URL` is unset (localhost or LAN, http or https), so it never builds `https://localhost:8000` (uvicorn serves plain HTTP). Leave `VITE_API_URL` out of `frontend/.env` for local dev; in dev an `http://` value on an https page is ignored with a console warning. See `frontend/.env.example`.
2. On the phone open `https://<laptop-IP>:5173`, for example `https://<laptop-ip>:5173`.
3. The browser warns that the certificate is not trusted (it is self-signed). Choose **Advanced → Proceed to <laptop-ip> (unsafe)** once.
4. Sign in, tap **New inspection**, then tap the shutter (or **Enable camera**) and allow the camera. The live rear-camera preview, torch toggle (where the phone supports it) and the 1080p capture now work.

The camera is never opened when the page loads: the browser's permission prompt appears only after you tap the shutter or **Enable camera**. If the camera cannot be used (plain `http://`, permission refused, no camera found), the screen says why in one line and the shutter opens the phone's own camera app instead (`<input type="file" accept="image/*" capture="environment">`, opened synchronously inside the tap, which is what mobile browsers require); **Gallery** is a separate picker. Photos are checked for sharpness, light and glare, then resized to the OCR size before upload.

#### Why results are fast: photos are read while you capture

The field app (and `#/new`) creates the inspection when the first photo is accepted and uploads each photo as soon as **Use photo** is tapped, with `POST /api/inspections/{id}/images?process_now=true`. That queues OCR for that one photo only (inspection status, merge and rules are not touched), so it is read while you frame the next panel. The final **Done** / **Start** calls `POST /process`, which waits for any photo still being read, then merges, checks the rules and scores risk. A photo is never read twice (an in-flight read is shared, a stored OCR result is reused, and identical files reuse the earlier reading by checksum). If a read-ahead fails, the final `POST /process` simply reads that photo again. An inspection that is abandoned before processing (closed camera, a removed photo) is deleted with `DELETE /api/inspections/{id}`, which only ever removes unprocessed drafts.

Languages are chosen per panel with `OCR_PANEL_LANGUAGES` (`PANEL=lang+lang` pairs separated by `;`). The default is `DATE_STAMP=en;BACK=en+hi;FRONT=en+hi;OTHER=en+hi`: dates and batch codes are Latin, so a date stamp skips the Hindi pass. Panels not listed use `OCR_LANGUAGES`; languages that are not loaded are ignored. English and Hindi run in parallel, so the progress list shows one *Reading text · EN + हिन्दी* step per photo with its real duration. Models, image size and recognition settings are unchanged.

Uploads are resized in the browser to `ocr_max_image_dimension` (from `GET /api/inspections/config`) with EXIF orientation applied. The resized file is the stored evidence (its SHA-256 is recorded); the original is not kept because OCR never reads more than that size.

