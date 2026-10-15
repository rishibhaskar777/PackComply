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
- Cookie-based authentication with OFFICIAL and MANUFACTURER roles
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

Health checks: `GET http://localhost:8000/api/health` (liveness) and `GET http://localhost:8000/api/health/ready` (readiness: 200 when PostgreSQL answers, 503 otherwise).

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

## OCR

PaddleOCR requires the `paddlepaddle`, `paddleocr`, and OpenCV packages declared in `pyproject.toml`. CPU inference is the default; consult PaddlePaddle's installation guidance if your platform needs a platform-specific wheel or GPU support. Model downloads may occur the first time an image is processed.

PackComply runs one recognizer per configured language against the same image. Every detection is tagged with the language that produced it. A secondary-language recognizer also re-reads English text, so a secondary detection is kept only if it contains that language's own script (for example Devanagari for `hi`/`mr`) or its box does not overlap a primary-language box; the number dropped is logged. Configuration is validated against the documented PP-OCRv3 language codes used by this project, so an unsupported code fails clearly at startup.

The configured languages are `en,hi,gom,mai,mr,ne,sa,ta,te,ur,ka`: English, Hindi, Konkani, Maithili, Marathi, Nepali, Sanskrit, Tamil, Telugu, Urdu, and Kannada (`ka` is PaddleOCR's Kannada model code). They are selected with `OCR_LANGUAGES`, and the OCR response reports the codes actually used.

To add another OCR language, first verify a compatible PaddleOCR model and its exact `lang` code for the installed release, then add it to `SUPPORTED_PADDLE_OCR_LANGUAGES` and `OCR_LANGUAGES` and cover it with a unit test.

After uploading an image, OCR can be run directly with `POST /api/inspections/{inspection_id}/images/{image_id}/ocr`; the usual route is `POST /api/inspections/{inspection_id}/process`, which runs every unfinished stage.

## Information extraction

`POST /api/inspections/{inspection_id}/extract` interprets the preserved OCR text into fields such as entities, addresses, MRP, net quantity, dates, and consumer-care details. Confidence values are explainable heuristics, not calibrated probabilities. Missing information is never fabricated and raw OCR evidence is never changed.

Extraction is layout-aware: OCR boxes are grouped into lines, and a value is only taken from text attached to an explicit key (`Net Wt.`, `M.R.P.`, `Marketed by`, `LIC. No.`, Hindi equivalents), either on the key's line, in the box to its right, or directly below it. Each field records the OCR line(s) it came from (`source_text`), how it was found (`matched_pattern`, e.g. `net_quantity:right_of_key`), and the exact boxes (`source_bboxes`). Results stored without boxes fall back to reading the next line.

Besides the original fields, extraction reports `brand_owner` (manufactured for / marketed by), `manufacturing_units` (every `(CODE)-company` unit of a multi-unit label), `mrp_declaration` (`present_with_value`, `present_without_value` when the price is stamped separately, or `absent`), `tax_inclusive`, `fssai_license`, `country_of_origin`, `unit_sale_price` and `batch_number`. Dates also carry an ISO-like `normalized_value` (`2026-03`, `2026-03-12`, or `P9M` for "9 months from manufacture").

After improving extraction, `POST /api/inspections/{inspection_id}/reanalyse` re-runs extraction and compliance on the stored OCR result without repeating OCR; `python -m backend.scripts.reanalyse_all` does this for every completed inspection.

## UI languages

The interface offers 23 fully translated languages: English, Hindi, Assamese, Bengali, Bodo, Dogri, Gujarati, Kannada, Kashmiri, Konkani, Maithili, Malayalam, Manipuri (Meitei), Marathi, Nepali, Odia, Punjabi, Sanskrit, Santali, Sindhi, Tamil, Telugu, and Urdu. The header selector persists the choice under `packcomply-language` and falls back to English when the stored value is invalid.

Translations live in `frontend/src/locales/`. Every locale file must contain every UI key; the app rejects incomplete locale files rather than silently rendering English. The brand name stays "PackComply" in every language.

UI language and OCR language are separate. Assamese, Bengali, Bodo, Dogri, Gujarati, Kashmiri, Malayalam, Manipuri (Meitei), Odia, Punjabi, Santali, and Sindhi are UI options but are not enabled for OCR. OCR text is never translated; it remains as printed on the package.

## Compliance ruleset

The checks follow the mandatory declarations of the Legal Metrology (Packaged Commodities) Rules, 2011, as amended (including 2022). Rule metadata — title, inspector-facing description, legal reference, severity, applicability, enable/disable and configuration — lives in a versioned YAML file, `backend/app/services/compliance/rulesets/lm_pc_2011_v1.yaml`. The check logic stays in Python, one function per rule code in `rule_engine.py`. The YAML is validated when the API starts and copied into the `compliance_rules` table (rules are updated, never deleted). Every stored result records the ruleset id and version, shown in the API (`ruleset_label`) and the PDF ("Checked against: LM(PC) 2011 ruleset v1"). `GET /api/compliance/rules` lists the active rules.

Each finding is one of:

- **FAIL** — a violation visible on the label (e.g. no net-quantity declaration at all).
- **REVIEW** — cannot be confirmed from this photo (e.g. the MRP key is printed but the price is stamped elsewhere; the manufacture date is not in the frame). Ask the inspector to check the physical pack.
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

To change a rule's meaning, copy the YAML to a new version (`lm_pc_2011_v2.yaml`), bump `version`, point `DEFAULT_RULESET` in `ruleset.py` at it and re-run `python -m backend.scripts.reanalyse_all`. The unit-sale-price base units and tolerance are configuration and must be verified against the official notification.

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

## Declared quantity verification

Quantity verification reuses the existing OCR, extraction, and `NET_QUANTITY_FORMAT` compliance rule. It verifies only the **quantity declared on the label**; it does not weigh a product or claim that the physical contents were measured. The inspection result and PDF show the declared quantity, a normalized representation, its evidence, and the authoritative compliance result.

Supported formats include common mass and volume units (`mg`, `g`, `kg`, `ml`, `L`, `cm3`/`cc`) and count declarations (`N`, `nos`, `pcs`). Harmless spacing, capitalization, and common OCR character variants are normalized for validation while the declared display value is kept. Missing, unsupported, or ambiguous declarations continue through the existing compliance, risk, and repeat-pattern mechanisms.

## Disclaimer

PackComply is an inspection-assistance tool. It is not legal advice and its output is not an enforcement decision.

## Team

Team CORTEX

Originally developed by VersatileSid; maintained by rishibhaskar777.
