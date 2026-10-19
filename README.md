# PackComply

Legal Metrology compliance checks for packaged goods — from a single label photo.

## What it does

PackComply takes a photo of a package label and runs it through a pipeline: image → OCR (PaddleOCR) → field extraction (MRP, net quantity, manufacturer, dates, consumer care) → rule-based compliance checks aligned with the Legal Metrology (Packaged Commodities) Rules, 2011 → risk prioritisation → PDF report.

PackComply assists inspectors. Its results are not legally binding decisions.

## Features

- Image upload with validation (JPG/PNG only, size limit, content checked against the declared type)
- Multilingual OCR with PaddleOCR, configured for `en, hi, gom, mai, mr, ne, sa, ta, te, ur, ka`
- Explainable field extraction with heuristic confidence, review flags, and source evidence
- A versioned Legal Metrology (Packaged Commodities) Rules 2011 ruleset: 14 rules, each citing its legal reference, with PASS / FAIL / REVIEW / NOT_APPLICABLE results and an overall verdict
- Declared-quantity verification (what the label states; no physical weighing)
- Risk-based targeting queue for officials
- Repeat-pattern detection across a user's completed inspections
- Inspection history with search and filters
- PDF inspection reports
- Cookie-based authentication with OFFICIAL and MANUFACTURER roles, login lockout, "keep me signed in", and an offline password reset (see [Accounts and password reset](#accounts-and-password-reset))
- 23 UI languages (loaded on demand)
- One-command Windows demo start with a phone QR code, seeded demo data and an "About & accuracy" page showing measured results (see [Demo day](#demo-day-windows-no-docker))

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 19, TypeScript, Vite |
| Backend | Python 3.11+, FastAPI, Pydantic |
| Database | PostgreSQL 16, SQLAlchemy, Alembic |
| AI / OCR | PaddleOCR, OpenCV |
| Reports | ReportLab |
| DevOps | Docker, Docker Compose |
| Tests | pytest, Vitest |

## How it works

1. **Upload** — `backend/app/services/uploads.py` validates the image and stores it under a generated file name.
2. **OCR** — `backend/app/services/ocr/ocr_service.py` runs one PaddleOCR recognizer per configured language and removes duplicate text boxes.
3. **Extraction** — `backend/app/services/extraction/extraction_service.py` deterministically interprets the OCR text into label fields, each with confidence, a review flag, and evidence.
4. **Compliance** — `backend/app/services/compliance/rule_engine.py` evaluates the extracted fields against the versioned ruleset in `backend/app/services/compliance/rulesets/` (see [Compliance ruleset](#compliance-ruleset)).
5. **Risk** — `backend/app/services/risk.py` turns persisted findings into a transparent 0–100 prioritisation score for officials.
6. **Patterns** — `backend/app/services/patterns.py` groups issues that repeat across completed inspections.
7. **Report** — `backend/app/services/report.py` renders the PDF report from persisted evidence.
8. **Orchestration** — `backend/app/services/workflow.py` runs the unfinished stages in order and is safe to call again after a failure.

## Project structure

```text
backend/
  alembic/            Database migrations
  app/
    api/              HTTP routes (auth, inspections, patterns, risk targeting, roles, health)
    core/             Settings and database session
    models/           SQLAlchemy models
    schemas/          Pydantic request/response schemas
    services/         OCR, extraction, compliance, risk, patterns, reports, workflow
  scripts/            Utility scripts (e.g. create_user.py)
  tests/              pytest suite
frontend/
  src/                React application, i18n, and locales/
database/             Database notes (migrations live in backend/alembic)
docker-compose.yml
```

## Getting started

Windows users without Docker: see [docs/local-setup-windows.md](docs/local-setup-windows.md), and for a demo [Demo day](#demo-day-windows-no-docker): `scripts\start-demo.ps1` does the rest.

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
3. runs `alembic -c backend/alembic.ini upgrade head`;
4. detects the laptop's Wi-Fi/LAN IPv4 (the adapter with a default gateway; Hyper-V, WSL, VMware, VirtualBox, VPN and similar virtual adapters are ignored);
5. starts uvicorn (`127.0.0.1:8000`) and the HTTPS Vite dev server (`:5173`) in two separate windows;
6. waits for `/api/health/ready`, for the web port and for the OCR warm-up (`/api/system/status` → `ocr: ready`), with a live timer;
7. prints the laptop URL, the phone URL and a **QR code for the phone URL**:

```text
 Laptop : https://localhost:5173/#/overview
 Phone  : https://192.168.1.105:5173/#/capture   (same Wi-Fi as this laptop)
 <QR code>
```

Failures stop with one clear message and the fix: PostgreSQL does not start (log path and the stale `postmaster.pid` hint), port 8000/5173 busy (who owns it, and `scripts\stop-demo.ps1`), venv or `alembic` missing (the exact commands), `node_modules` missing (offers `npm install`; `-Yes` accepts), and a missing firewall rule: if Windows Firewall has no rule for port 5173 the script prints the exact command to run once in an **Administrator** PowerShell:

```powershell
New-NetFirewallRule -DisplayName "PackComply Demo (5173)" -Direction Inbound -Protocol TCP -LocalPort 5173 -Action Allow -Profile Any
```

The API logs one summary line when it is ready, for example `PackComply ready | DB ok | ruleset LM_PC_2011 v2 (14 rules) | OCR en+hi ready (warm-up 13.2 s) | LAN https://192.168.1.105:5173/#/capture`.

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

### Two portals, one app

The sign-in role decides which product you see (`<html data-portal>`; the server still scopes every record to its owner):

| | Inspector portal (indigo) | Manufacturer portal (teal) |
|---|---|---|
| Home | `#/overview` — *Today's inspection priorities*: KPI tiles, risk queue, violations by rule, repeat issues | `#/products` — *My products*: readiness ring, counters, one card per product |
| Work | `#/new` / phone **New inspection**, `#/inspections/:id` official record (markers, corrections, verify, audit) | `#/check` / phone **Check my label**, `#/products/:id` self-check result (fix cards with examples, *Show on label*) |
| Other pages | Risk queue, Repeat issues, Rulesets, Reports (official PDF), About | Fix list, Self-check reports (pre-market PDF), LM(PC) rules guide, About |
| Wording | Inspection, Violation, Risk, Verify, Official report | Check, Fix, Ready to print, Self-check report (a self-assessment aid, not an approval) |

Opening the other role's route redirects (a manufacturer's `#/inspections/:id` becomes `#/products/:id`; an inspector's `#/products` becomes `#/overview`). Plain-language fix text comes from `backend/app/services/compliance/rulesets/remediation_v1.yaml`, one entry per rule (tested to cover every rule in the active ruleset).

### 5-minute judge demo

Have the laptop on Wi-Fi (or a phone hotspot), the phone on the same network, `start-demo.ps1` finished and `seed_demo` run once. Open the laptop URL; on the phone, accept the certificate warning once beforehand and keep the page open.

1. **Log in** as `demo_inspector`. Say: everything runs on this laptop, nothing leaves it (the "Runs offline" card in the sidebar).
2. **Overview**: verdict mix, the risk queue and the repeat issues are already populated from real photos.
3. **Open the highest-risk record** (top of the risk queue, score 100). The photo shows numbered evidence markers; the findings list shows each failed rule with its legal reference.
4. **Click a finding**: the matching evidence box lights up on the photo, with the exact text OCR read.
5. **Make a correction** on a finding that is a mis-read, not a violation (for example the MRP or the net quantity): enter the value, give a reason, save. The verdict and risk update at once; the audit trail records who changed what.
6. **Show a hidden violation**: a finding that a casual look would miss (for example no consumer-care contact details, or an unit sale price that does not match MRP ÷ net quantity). Point at its legal reference and recommended action.
7. **Verify** the record (inspector review), then **download the PDF**.
8. **Phone capture of a new pack**: on the laptop click **Open on phone** and scan the QR (or open the phone URL), sign in as `demo_inspector` on the phone, tap **New inspection** (the field app), and photograph the back of a real pack; add the front or the date stamp if time allows. Each photo is uploaded and read while you frame the next one.
9. **Live progress**: *Done* shows the real step list (reading text EN + हिन्दी per photo, linking, rules, risk) with true durations, no fake percentages.
10. **Result**: the phone shows the verdict; open the record on the laptop. It has three photo tabs (back, front, date) with their own evidence boxes.
11. **Audit trail**: scroll to it: creation, each photo with its SHA-256, OCR time, ruleset version, correction, verification.
12. **Manufacturer portal** (second login, `demo_maker`): *My products* ("3 of 6 ready to print") → **Check a label** → upload `demo/artwork/gluco-biscuits-draft.png` → live checking → **Fix 3 items before printing** with plain-language problem, rule chip, *How to fix* and an *Example* → **Show on label** → **Upload corrected label** (`gluco-biscuits-corrected.png`) → **Ready to print ✓ · Improved from 3 fixes to 0** → **Download self-check report** → *My products* now shows "Gluco Biscuits improved from 3 fixes to 0".
13. If asked about accuracy, open **About & accuracy** (sidebar → Resources): precision, recall, rule agreement and the verdict confusion matrix measured by the evaluation script, labelled development or unseen, with the date.

Fallbacks:

- **Wi-Fi fails or the phone cannot reach the laptop**: turn on the phone's hotspot, connect the laptop to it, run `scripts\stop-demo.ps1` then `scripts\start-demo.ps1` again (a new LAN address is detected) and re-scan the QR. Or skip the phone: on the laptop use **New inspection** (`#/new`), drop the same photos onto the upload form and follow the same progress and result screens.
- **Phone says "can't connect"**: the firewall rule above is the usual cause; the script prints the command whenever it is missing. Check that both devices are on the same network and that the phone URL begins with `https://`.
- **Camera preview does not open on the phone**: the page must be `https://`; if the browser still blocks it, the shutter opens the phone's own camera app instead.
- **One photo cannot be read** (OCR crash or timeout): the inspection still completes with the other photos. Rules that depend on the unread photo are marked REVIEW with a note ("The date stamp photo could not be read…"), never PASS or FAIL, the progress list shows the failed step, and **Retry photo** (on the record) reads only that photo.
- **A screen crashes**: the global error screen offers **Reload**; saved inspections are unaffected. If a session expires, any request returning 401 sends you back to sign-in with a notice.

### About & accuracy and honest metrics

`#/about` shows what the 14 rules check (loaded from the ruleset API, each with its legal reference), a diagram of the pipeline, the privacy statement, and the **measured** accuracy: precision and recall per field, rule agreement, the verdict confusion matrix, the count of false NON_COMPLIANT verdicts and of false PASS findings on mandatory rules, dataset sizes (development / phone hold-out retired to development / unseen) and the median OCR time. All numbers come from the JSON written by the evaluation script (`backend/app/assets/evaluation_report.json`, served by `GET /api/system/evaluation`); nothing is typed into the page. Each run is labelled **Development score** (extraction was tuned while looking at those photos, so the numbers are optimistic) or **Unseen score**, with its dataset name and date.

Refresh the development report from the stored OCR fixtures (no photos needed):

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.eval_real --report-json backend/app/assets/evaluation_report.json --run-label development-2026-10 --run-note "23 labelled photos, development"
```

#### Adding a fresh UNSEEN set

1. Put the new phone photos in one folder, for example `D:\unseen-photos` (only photos nobody has run through the pipeline or tuned on).
2. Generate the ground-truth template, a separate fixture folder keeps this set apart from the development photos: `python -m backend.scripts.make_ground_truth_template D:\unseen-photos` (writes `backend/tests/fixtures/labels/unseen/expected_real.json` and `split.json`; add `--fixture-dir` and `--group` to choose another folder or batch name). Every field is `null` and every photo is marked `"filled": false`; every photo is HOLD-OUT.
3. **Before running the pipeline on them**, read each photo yourself and fill the expected values by hand (the format is described at the top of `backend/tests/fixtures/labels/real/expected_real.json`; omit or leave `null` what the photo does not show), then set `"filled": true` for each finished photo. Unfilled photos are not scored.
4. Evaluate once, without tuning:

   ```powershell
   .\.venv\Scripts\python.exe -m backend.scripts.batch_evaluate D:\unseen-photos --fixture-dir backend\tests\fixtures\labels\unseen --report-json backend\app\assets\evaluation_report.json --run-label unseen-2026-10-29 --run-note "N new phone photos, labelled by hand before the run"
   ```

   This runs the real OCR, extraction and rules, prints the scores and **adds** the run to the report file (runs with other labels are kept, so development and unseen results sit side by side on the page). Do not change extraction or rule code in response to the numbers and then re-run: that turns the unseen set into a development set. Commit the updated `evaluation_report.json` if you want it in the repository.

### Speed and reliability notes

- The first screen loads only English plus the app shell: other UI languages are separate chunks fetched when selected (and cached), and every page is its own route chunk behind a skeleton. Initial JavaScript is about 110 kB gzip (it was 394 kB); the two Geist font files are preloaded.
- If OCR crashes or times out for one photo of a multi-photo inspection, the inspection completes with the others (see the fallbacks above). If no photo at all can be read, the run fails with a clear message and **Try again** resumes it.

## Running tests

```powershell
pytest backend/tests
python -m backend.scripts.eval_extraction   # field-level precision/recall on the labelled fixtures
python -m backend.scripts.eval_compliance   # rule-outcome and verdict accuracy on the same fixtures
cd frontend; npm test
```

`eval_compliance` extracts every fixture, stores it the way an inspection does, runs the current ruleset and compares each rule outcome and the verdict with the `rules` / `verdict` entries in `expected.json`.

`eval_extraction` runs extraction over every label in `backend/tests/fixtures/labels` (one real Parle-G back label exported from OCR, plus synthetic layouts) and compares the result with `expected.json`. Add a fixture and its ground truth whenever a new label layout is supported; `--failures` prints only the rows that differ.

### Evaluating on real label photos

One command runs the full pipeline (real PaddleOCR with the `.env` settings, extraction, compliance; no database writes) over a folder of photos and prints the scores:

```powershell
python -m backend.scripts.batch_evaluate D:\path\to\label-photos
```

- OCR output (never the photos) is saved to `backend/tests/fixtures/labels/real/<name>.json`, so `--reuse-ocr` re-runs extraction and compliance in seconds after a code change. `python -m backend.scripts.eval_real` scores the stored fixtures without the photo folder.
- `reports/batch/report.html` (git-ignored, photos embedded) shows each photo with its OCR evidence boxes, extracted fields and rule findings for quick review.
- `split.json` fixes a seeded DEV / HOLD-OUT split by file name the first time a photo is seen; existing assignments never change. Look at DEV failures (`--failures`) to improve extraction; HOLD-OUT is for measuring only — its failing rows are hidden unless `eval_real --show-holdout` is given.
- The first hold-out was retired on 2026-10-15 after its failures were fixed: all 9 original photos are now DEV, and `"new_photos": "holdout"` in `split.json` sends every photo added later to a fresh HOLD-OUT.
- The second hold-out, 14 phone photos (`phone_holdout_2026_10`), was retired on 2026-10-16 after its failures were inspected to change the extraction approach. All 23 photos are now DEV: every score printed is a development score, and there is no unseen-label score until new photos are added. Photos added together keep a group (`_meta.group` in `expected_real.json`) that is still scored as its own column.
- Besides precision / recall and rule agreement, the scores count verdict-safety errors: **false NON_COMPLIANT** verdicts (the ground truth is not NON_COMPLIANT) and **false PASS** findings on mandatory rules (a PASS the ground truth does not give, or a PASS on a wrongly read value). The ground-truth rules see the same photo: its OCR coverage comes from extraction, and a declaration the person did not list counts as not detected unless they recorded its key without a value.
- Scores are printed for DEV, HOLD-OUT, the real phone photos (`"_meta": {"real_photo": true}`) and all photos, once ground truth for a photo is in `real/expected_real.json` (format described at the top of that file). Photos without ground truth are listed but not scored.
- Use `--fixture-dir` to keep a different photo set's fixtures, split and ground truth apart.

OCR re-reads a photo taken sideways: when most text boxes are tall and narrow, the photo is read turned 90° and 270° and the orientation with the most confidently read text is kept (`OCR_AUTO_ROTATE=false` turns this off). Boxes are always stored in original photo pixels; each detection records the `rotation` it was read at so extraction can rebuild the reading order. This costs two extra OCR passes, only on sideways photos. Upside-down (180°) photos are not detected.

## OCR

PaddleOCR requires the `paddlepaddle`, `paddleocr`, and OpenCV packages declared in `pyproject.toml`. CPU inference is the default; consult PaddlePaddle's installation guidance if your platform needs a platform-specific wheel or GPU support. Model downloads may occur the first time an image is processed.

PackComply runs one recognizer per configured language against the same image. Every detection is tagged with the language that produced it. A secondary-language recognizer also re-reads English text, so a secondary detection is kept only if it contains that language's own script (for example Devanagari for `hi`/`mr`) or its box does not overlap a primary-language box; the number dropped is logged. Configuration is validated against the documented PP-OCRv3 language codes used by this project, so an unsupported code fails clearly at startup.

The configured languages are `en,hi,gom,mai,mr,ne,sa,ta,te,ur,ka`: English, Hindi, Konkani, Maithili, Marathi, Nepali, Sanskrit, Tamil, Telugu, Urdu, and Kannada (`ka` is PaddleOCR's Kannada model code). They are selected with `OCR_LANGUAGES`, and the OCR response reports the codes actually used.

To add another OCR language, first verify a compatible PaddleOCR model and its exact `lang` code for the installed release, then add it to `SUPPORTED_PADDLE_OCR_LANGUAGES` and `OCR_LANGUAGES` and cover it with a unit test.

After uploading an image, OCR can be run directly with `POST /api/inspections/{inspection_id}/images/{image_id}/ocr`; the usual route is `POST /api/inspections/{inspection_id}/process`, which runs every unfinished stage.

## Information extraction

`POST /api/inspections/{inspection_id}/extract` interprets the preserved OCR text into fields such as entities, addresses, MRP, net quantity, dates, and consumer-care details. Confidence values are explainable heuristics, not calibrated probabilities. Missing information is never fabricated and raw OCR evidence is never changed.

Extraction is layout-aware: OCR boxes are grouped into lines, and a value is only taken from text attached to an explicit key (`Net Wt.`, `M.R.P.`, `Marketed by`, `LIC. No.`, Hindi equivalents), either on the key's line, in the box to its right, or directly below it. Each field records the OCR line(s) it came from (`source_text`), how it was found (`matched_pattern`, e.g. `net_quantity:right_of_key`), and the exact boxes (`source_bboxes`). Results stored without boxes fall back to reading the next line. A photo whose text boxes are almost all tall and narrow but carry no recorded rotation is read sideways: the end where the boxes line up (left-aligned text) gives the reading direction.

A second, **value-first** pass (`backend/app/services/extraction/values.py`) links the values the key-first pass cannot reach on phone photos:

1. It finds every value on the label with its position: prices (₹ / Rs / INR / रु, and the shapes OCR gives ₹ such as `T499.00` or `२58/-` when the amount is price-shaped), unit sale prices (`₹0.89/ml`, with `/mn` read as ml and `0.36/9` as per g), net quantities (also glued `550mlnet` and dual `100 ml / 3.38 fl. oz.`, never nutrition rows, dosage strengths, dilutions or `per 100 g`), and dates (also behind a letter or symbol code: `M 12/24`, `¥12/28`).
2. It finds every key **fuzzily** (`fuzzy.py`, standard library only): text and key are folded (M↔VI, l↔1↔I, O↔0, rn↔m, S↔5) and compared with an edit distance of at most 1 for keys of 4–7 characters and 2 for 8 or more, so `VIRP`, `Net Contemt` and `Manufactued by` count. Three-letter keys read with one error (`MAP`, `MFO`) count only when the line confirms them (a price, "incl. of all taxes", another key, a redirect). It also reads combined key blocks (`MRP (incl. of all taxes); USP, MFD (M) & Use Before (U): see below`): the codes they define and the redirect that applies to all their keys.
3. It scores every key/value pair — same line to the right, then a tilted next row, then directly below, then nearest in the same region; a code binds a value to its coded field (conventional `M`/`U`/`E`/`P` codes too); a key/value table on a curved pack pairs row by row; a redirect (`see below`, `see coding area`, `see bottom`, …) allows a stamp-like value elsewhere — and assigns greedily, one value per field and one field per value. Prices offered in a sentence (`also available @ ₹40/-`) are never the MRP. An unkeyed price is taken as the MRP only when the unit sale price and net quantity confirm it (`mrp:usp_consistency`).

Value-first matches record how they were made (`mrp:redirect_coding_area`, `manufacturing_date:letter_code_M`, `net_quantity:glued_key`) and are flagged for review when they come from a redirect, a coding area or the nearest-in-region rule.

Besides the original fields, extraction reports `brand_owner` (manufactured for / marketed by), `manufacturing_units` (every `(CODE)-company` unit of a multi-unit label), `mrp_declaration` (`present_with_value`, `present_without_value` when the price is stamped separately, or `absent`), `tax_inclusive`, `fssai_license`, `country_of_origin`, `unit_sale_price`, `batch_number` and `product_category` (`food`, `drug`, `personal_care`, `household` or `other`; food only with a nutrition panel, FSSAI text, a 14-digit licence, or a best-before date with an ingredients list of food words). Dates also carry an ISO-like `normalized_value` (`2026-03`, `2026-03-12`, or `P9M` for "9 months from manufacture").

Two further fields are evidence for compliance and are not shown as label fields: `declaration_status` records for each mandatory declaration whether it was read with its value (`DECLARED_WITH_VALUE`), only its key or a redirect was found (`KEY_FOUND_VALUE_UNREAD`), or nothing was detected (`NOT_DETECTED`); `ocr_coverage` records how much of the photo OCR read (text lines, mean confidence, share of poorly read lines).

The product name is left empty and flagged for review when no candidate clearly stands out: headings (matched fuzzily), instructions, lists, addresses, company names, foreign-language marketing copy and unreadable text are never candidates, and a name needs either a generic commodity word (shampoo, biscuits, liquid, …) or large type. A wrong name would be reported as a fact; an empty one only asks the inspector to look.

After improving extraction, `POST /api/inspections/{inspection_id}/reanalyse` re-runs extraction and compliance on the stored OCR result without repeating OCR; `python -m backend.scripts.reanalyse_all` does this for every completed inspection.

## Inspection record, corrections and audit trail

An inspection's page (`#/inspections/:id`) shows the photo with numbered evidence markers, the findings, an inspector-review footer and an audit trail.

- **Label identity.** The extracted product name, brand owner and manufacturer are stored on the inspection (`product_name`, `brand_name`, `maker_name`), so history and search work without joining extraction rows. Search matches product, brand, maker and inspection number case-insensitively; nothing extracted shows as "Untitled label". For inspections that predate this, run `python -m backend.scripts.backfill_product_names` once after `alembic upgrade head` (idempotent; it does not re-run OCR, extraction or compliance).
- **Corrections** (OFFICIAL only). `POST /api/inspections/{id}/corrections` with `{field_name, corrected_value, reason?}` validates the value with the extractor's own parsers (MRP `10` / `₹10.00` become `₹10.00`; quantities `70 g`; dates `08/2026`), returns 422 with a message when invalid, then re-runs extraction on the stored OCR, re-applies all corrections (confidence 1.0, source `INSPECTOR`), and re-evaluates compliance and risk. OCR is never repeated. `DELETE /api/inspections/{id}/corrections/{correction_id}` undoes one. Corrections survive "Re-analyse".
- **Verification.** `POST /api/inspections/{id}/verify` with `{remarks}` (OFFICIAL, completed inspections only) records who and when. Any later correction returns the record to `UNREVIEWED`.
- **Audit.** `GET /api/inspections/{id}/audit` returns creation, photo upload (with SHA-256), OCR (with duration), compliance evaluation (with ruleset version), corrections, re-analysis, undo and verification in time order.
- The PDF report gains an "Inspector review" section and marks inspector-corrected values.

Access stays scoped to the inspection's owner; the role checks above additionally reject manufacturers with 403.

## Multi-photo inspections, live progress and the phone field app

An inspection holds up to four photos, one per panel: `BACK`, `FRONT`, `DATE_STAMP`, `OTHER`. Each photo is read once (OCR results are stored per image and never repeated for the same checksum), extracted on its own, and merged field by field: the highest confidence wins, and a tie goes to the panel that owns the field (product and brand names: front; dates and batch: date stamp; everything else: back). Every field keeps the `image_id` it came from, with its boxes in that photo's pixel space. When photos disagree on a field a rule depends on (for example two net quantities), the rule is marked REVIEW with a note instead of one value being dropped. A new photo for a panel replaces the old one for processing; the old file and checksum stay as evidence.

Processing runs in a background thread pool inside the API (no broker; works on Windows). `POST /api/inspections/{id}/process` returns `202` with a progress snapshot; a second start while one is running returns `409`. Follow it with `GET .../progress` (polling, every second) or `GET .../progress/stream` (Server-Sent Events). Percentages are completed steps over total steps. Steps are kept in memory; after a restart the stored inspection status is used.

### Testing on a real phone (same Wi-Fi)

1. Run the backend as usual: `uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`.
2. Run the frontend with `npm run dev` in `frontend/` (it uses `vite --host`). The dev server proxies `/api` to `http://127.0.0.1:8000` (override with `VITE_PROXY_TARGET`), so the phone talks to one origin and no CORS change is needed.
3. Find the laptop's LAN IP (`ipconfig`, the IPv4 address, e.g. `192.168.1.23`) and allow ports 5173 in the Windows firewall for private networks.
4. On the phone open `http://192.168.1.23:5173`, sign in, tap **New inspection**. On screens narrower than 768px this opens the field app (`#/capture`).

#### Live camera preview on a phone (HTTPS dev mode)

Browsers only allow the live camera preview on HTTPS or `localhost`, and a phone opening `http://<LAN-IP>:5173` is neither. For the live rear-camera preview run the dev server over HTTPS with a local self-signed certificate (the free `@vitejs/plugin-basic-ssl`; nothing is downloaded or sent anywhere at runtime):

1. In `frontend/` run `npm run dev:https` (instead of `npm run dev`; plain `npm run dev` is unchanged). The `/api` proxy keeps working: the browser speaks HTTPS to Vite, Vite speaks HTTP to the API on the laptop. The frontend calls the relative `/api` path whenever `VITE_API_URL` is unset (localhost or LAN, http or https), so it never builds `https://localhost:8000` (uvicorn serves plain HTTP). Leave `VITE_API_URL` out of `frontend/.env` for local dev; in dev an `http://` value on an https page is ignored with a console warning. See `frontend/.env.example`.
2. On the phone open `https://<laptop-IP>:5173`, for example `https://192.168.1.23:5173`.
3. The browser warns that the certificate is not trusted (it is self-signed). Choose **Advanced → Proceed to 192.168.1.23 (unsafe)** once.
4. Sign in, tap **New inspection**, then tap the shutter (or **Enable camera**) and allow the camera. The live rear-camera preview, torch toggle (where the phone supports it) and the 1080p capture now work.

The camera is never opened when the page loads: the browser's permission prompt appears only after you tap the shutter or **Enable camera**. If the camera cannot be used (plain `http://`, permission refused, no camera found), the screen says why in one line and the shutter opens the phone's own camera app instead (`<input type="file" accept="image/*" capture="environment">`, opened synchronously inside the tap, which is what mobile browsers require); **Gallery** is a separate picker. Photos are checked for sharpness, light and glare, then resized to the OCR size before upload.

#### Why results are fast: photos are read while you capture

The field app (and `#/new`) creates the inspection when the first photo is accepted and uploads each photo as soon as **Use photo** is tapped, with `POST /api/inspections/{id}/images?process_now=true`. That queues OCR for that one photo only (inspection status, merge and rules are not touched), so it is read while you frame the next panel. The final **Done** / **Start** calls `POST /process`, which waits for any photo still being read, then merges, checks the rules and scores risk. A photo is never read twice (an in-flight read is shared, a stored OCR result is reused, and identical files reuse the earlier reading by checksum). If a read-ahead fails, the final `POST /process` simply reads that photo again. An inspection that is abandoned before processing (closed camera, a removed photo) is deleted with `DELETE /api/inspections/{id}`, which only ever removes unprocessed drafts.

Languages are chosen per panel with `OCR_PANEL_LANGUAGES` (`PANEL=lang+lang` pairs separated by `;`). The default is `DATE_STAMP=en;BACK=en+hi;FRONT=en+hi;OTHER=en+hi`: dates and batch codes are Latin, so a date stamp skips the Hindi pass. Panels not listed use `OCR_LANGUAGES`; languages that are not loaded are ignored. English and Hindi run in parallel, so the progress list shows one *Reading text · EN + हिन्दी* step per photo with its real duration. Models, image size and recognition settings are unchanged.

Uploads are resized in the browser to `ocr_max_image_dimension` (from `GET /api/inspections/config`) with EXIF orientation applied. The resized file is the stored evidence (its SHA-256 is recorded); the original is not kept because OCR never reads more than that size.

## Accounts and password reset

Create users with `python -m backend.scripts.create_user` (inspector accounts are only created this way unless `ALLOW_OFFICIAL_SELF_REGISTRATION=true`). Passwords must be at least 8 characters, differ from the username, and not be one of the 20 most common passwords.

A sign-in normally lasts `SESSION_TTL_HOURS` (12). Ticking **Keep me signed in on this device** makes it last `SESSION_REMEMBER_DAYS` (30). After `LOGIN_MAX_ATTEMPTS` (5) failed attempts the account is locked for `LOGIN_LOCKOUT_MINUTES` (15).

**Forgot password** works offline: there is no SMS or paid e-mail service. On the sign-in page, *Forgot password?* asks for a username and then for a one-time 6-digit code. The page never says whether a username exists.

The administrator issues the code on the server and hands it over in person:

```powershell
python -m backend.scripts.reset_password --username inspector
```

This prints `Reset code for inspector: 123456`. The code is valid for 15 minutes and for one use; any earlier code stops working. Five wrong guesses burn it. Alternatively, set a new password directly (typed twice, not echoed):

```powershell
python -m backend.scripts.reset_password --username inspector --set-password
```

Either way, a completed reset signs the user out on every device. A reset through the sign-in page also clears the login lockout. The CLI runs in a separate process and cannot clear that in-memory lock; it expires after `LOGIN_LOCKOUT_MINUTES`.

Optional e-mail delivery uses the Python standard library (`smtplib`) and is off until `SMTP_HOST` is set (see `.env.example`). The code is then e-mailed to the account's address as well. Codes are stored only as scrypt hashes and never written to logs. Reset requests are limited to 3 per username and 3 per IP address per 15 minutes; a further request gets 429.

| Endpoint | Body | Response |
|---|---|---|
| `POST /api/auth/password-reset/request` | `{"username"}` | Always `202` with the same message for known and unknown usernames; `429` when rate-limited |
| `POST /api/auth/password-reset/confirm` | `{"username", "code", "new_password"}` | `200` on success; `400 {"detail": "Invalid or expired code"}` for any code failure; `422` when the new password breaks the policy |

## UI languages

The interface offers 23 fully translated languages: English, Hindi, Assamese, Bengali, Bodo, Dogri, Gujarati, Kannada, Kashmiri, Konkani, Maithili, Malayalam, Manipuri (Meitei), Marathi, Nepali, Odia, Punjabi, Sanskrit, Santali, Sindhi, Tamil, Telugu, and Urdu. The header selector persists the choice under `packcomply-language` and falls back to English when the stored value is invalid.

Translations live in `frontend/src/locales/`. Every locale file must contain every UI key; the app rejects incomplete locale files rather than silently rendering English. The brand name stays "PackComply" in every language.

UI language and OCR language are separate. Assamese, Bengali, Bodo, Dogri, Gujarati, Kashmiri, Malayalam, Manipuri (Meitei), Odia, Punjabi, Santali, and Sindhi are UI options but are not enabled for OCR. OCR text is never translated; it remains as printed on the package.

## Compliance ruleset

The checks follow the mandatory declarations of the Legal Metrology (Packaged Commodities) Rules, 2011, as amended (including 2022). Rule metadata — title, inspector-facing description, legal reference, severity, applicability, enable/disable and configuration — lives in a versioned YAML file, `backend/app/services/compliance/rulesets/lm_pc_2011_v2.yaml` (v1 is kept so earlier results stay traceable). The check logic stays in Python, one function per rule code in `rule_engine.py`. The YAML is validated when the API starts and copied into the `compliance_rules` table (rules are updated, never deleted). Every stored result records the ruleset id and version, shown in the API (`ruleset_label`) and the PDF ("Checked against: LM(PC) 2011 ruleset v2"). `GET /api/compliance/rules` lists the active rules.

Each finding is one of:

- **FAIL** — a violation visible on the label (e.g. no net-quantity declaration at all on a well-read photo).
- **REVIEW** — cannot be confirmed from this photo (e.g. the MRP key is printed but the price is stamped elsewhere; the manufacture date is not in the frame). Ask the inspector to check the physical pack.

A tool for regulators must not accuse falsely, so for the mandatory declarations (manufacturer, net quantity, MRP, "inclusive of all taxes", unit sale price, manufacture date, consumer care) a missing value is judged by its `declaration_status`:

- `DECLARED_WITH_VALUE` — the value is checked as usual (PASS or FAIL).
- `KEY_FOUND_VALUE_UNREAD` — the key or a redirect ("see below", "see coding area", "printed on pack", "for MRP and other details see bottom of the pack") is printed but no value was linked: REVIEW, "Declaration present; value could not be read — verify on the pack".
- `NOT_DETECTED` — FAIL only when the photo's OCR coverage is adequate by the ruleset's `evidence` thresholds (`min_text_lines: 8`, `min_mean_confidence: 0.80`, `max_low_confidence_line_share: 0.30`), otherwise REVIEW, "Not detected — photo may not show this panel". The manufacture date is never FAIL.

Results stored before `declaration_status` existed keep their previous outcomes (only the MRP and consumer-care headings were recorded as "key without value", and the photo counts as adequately read).
- **PASS** — the declaration was found and is well formed.
- **NOT_APPLICABLE** — the rule does not apply to this label (e.g. importer rules on a domestic product). Not counted as an issue, in risk scores or in repeat patterns.

The verdict is **NON_COMPLIANT** (any FAIL), **NEEDS_REVIEW** (no FAIL, any REVIEW) or **COMPLIANT**. `overall_status` (FAIL / REVIEW / PASS) is still returned for compatibility.

| Rule code | Checks | Legal reference | Severity |
|---|---|---|---|
| MANUFACTURER_INFORMATION | Manufacturer / packer / brand-owner (or, if imported, importer) name and address | Rule 6(1)(a) | HIGH |
| IMPORTER_INFORMATION | Importer name and address — imported goods only | Rule 6(1)(a) | HIGH |
| COUNTRY_OF_ORIGIN | Country of origin — imported goods only | Rule 6(1)(aa) | HIGH |
| PACKER_INFORMATION | Address of a separately named packer | Rule 6(1)(a) | INFO |
| PRODUCT_IDENTITY | Common or generic name | Rule 6(1)(b) | WARNING |
| NET_QUANTITY_FORMAT | Net quantity in a standard unit | Rule 6(1)(c), Rule 9 | HIGH |
| NET_QUANTITY_UNIT_SUITABILITY | Unit clearly unsuited to the commodity (e.g. a liquid by count) | Rule 9, Fourth Schedule | INFO |
| MRP_FORMAT | MRP in Indian rupees | Rule 6(1)(e) | HIGH |
| MRP_TAX_INCLUSIVE | "Inclusive of all taxes" | Rule 6(1)(e) | WARNING |
| UNIT_SALE_PRICE | Unit sale price present and consistent with MRP ÷ net quantity | Rule 6(11) (2022) | WARNING |
| MANUFACTURE_DATE | Month and year of manufacture / packing (REVIEW, never FAIL, when not visible) | Rule 6(1)(d) | WARNING |
| DATE_CONSISTENCY | Manufacture/packing not after expiry/use-by, not in the future | Rule 6(1)(d) | WARNING |
| CONSUMER_CARE_FORMAT | Consumer-care telephone / e-mail | Rule 6(2) | WARNING |
| FSSAI_LICENSE_PRESENT | 14-digit FSSAI licence on food labels — an FSSAI cross-check, not Legal Metrology | FSS labelling regulations | INFO |

To change a rule's meaning, copy the YAML to a new version (`lm_pc_2011_v3.yaml`), bump `version`, point `DEFAULT_RULESET` in `ruleset.py` at it and re-run `python -m backend.scripts.reanalyse_all`. The unit-sale-price base units and tolerance are configuration and must be verified against the official notification.

## Risk-based targeting

Risk targeting is a transparent, deterministic prioritisation aid for completed inspections. It is not a machine-learning model and does not create legal rules or enforcement decisions. Officials see a queue based only on persisted compliance findings, missing extraction values that need review, and repeated evidence in their own completed-inspection history. Manufacturers cannot access the queue or the numerical score.

Scores are capped at 100: 0–24 LOW, 25–49 MEDIUM, 50–74 HIGH, and 75–100 CRITICAL.

- A FAIL contributes 35 / 25 / 15 / 10 points for HIGH / WARNING / INFO / LOW severity.
- A REVIEW contributes 20 / 12 / 6 / 4 points.
- A NOT_APPLICABLE result contributes nothing.
- A missing field contributes 6 points (maximum 18) only when it is a `required_fields` entry of a rule that FAILed; under a REVIEW the review points already cover it. Optional or informational fields (website, batch number, best before, a packer beside a declared manufacturer) never add points on their own, so a fully compliant label scores 0.
- Repeats count violations only: a rule that FAILs again adds 12, and a missing required field of a failing rule that recurs adds 8. Repeated REVIEW findings still appear on the patterns page but add no risk points.
- Repeats are counted per distinct photo: inspections whose latest image has the same checksum (the same photo uploaded again) are one occurrence, for both risk and the patterns page.
- Three or more distinct FAIL rules (violations) add 8.

Repeat evidence is calculated only within the authenticated owner's completed inspections. `GET /api/risk-targeting` is official-only, paginated, and excludes OCR text and image data.

`GET /api/dashboard/summary?period=7d|30d|all` (default `all`) feeds the Overview page: inspection count, verdict counts (including `PENDING` for inspections without results), the six highest-risk completed labels, the top three rules flagged FAIL or REVIEW on at least `PATTERN_MIN_OCCURRENCES` distinct photos, the median upload-to-completion time and the active ruleset version. Like the history endpoint it covers only the caller's own inspections; manufacturers receive the queue ordered by verdict, with `risk_score` and `risk_level` set to null.

## Declared quantity verification

Quantity verification reuses the existing OCR, extraction, and `NET_QUANTITY_FORMAT` compliance rule. It verifies only the **quantity declared on the label**; it does not weigh a product or claim that the physical contents were measured. The inspection result and PDF show the declared quantity, a normalized representation, its evidence, and the authoritative compliance result.

Supported formats include common mass and volume units (`mg`, `g`, `kg`, `ml`, `L`, `cm3`/`cc`) and count declarations (`N`, `nos`, `pcs`). Harmless spacing, capitalization, and common OCR character variants are normalized for validation while the declared display value is kept. Missing, unsupported, or ambiguous declarations continue through the existing compliance, risk, and repeat-pattern mechanisms.

## Disclaimer

PackComply is an inspection-assistance tool. It is not legal advice and its output is not an enforcement decision.

## Team

Team CORTEX

Originally developed by VersatileSid; maintained by rishibhaskar777.
