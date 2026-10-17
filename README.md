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
- 23 UI languages

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

Windows users without Docker: see [docs/local-setup-windows.md](docs/local-setup-windows.md).

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

1. In `frontend/` run `npm run dev:https` (instead of `npm run dev`; plain `npm run dev` is unchanged). The `/api` proxy keeps working: the browser speaks HTTPS to Vite, Vite speaks HTTP to the API on the laptop.
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
