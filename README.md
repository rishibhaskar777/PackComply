# PackComply

Legal Metrology compliance checks for packaged goods — from a single label photo.

## What it does

PackComply takes a photo of a package label and runs it through a pipeline: image → OCR (PaddleOCR) → field extraction (MRP, net quantity, manufacturer, dates, consumer care) → rule-based compliance checks aligned with the Legal Metrology (Packaged Commodities) Rules, 2011 → risk prioritisation → PDF report.

PackComply assists inspectors. Its results are not legally binding decisions.

## Features

- Image upload with validation (JPG/PNG only, size limit, content checked against the declared type)
- Multilingual OCR with PaddleOCR, configured for `en, hi, gom, mai, mr, ne, sa, ta, te, ur, ka`
- Explainable field extraction with heuristic confidence, review flags, and source evidence
- 8 compliance rules with PASS / FAIL / REVIEW results
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
4. **Compliance** — `backend/app/services/compliance/rule_engine.py` evaluates the extracted fields against the rules: product identity, manufacturer, packer, importer, MRP format, net-quantity format, date consistency, and consumer-care format.
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
2. Run `docker compose up --build`.
3. Open `http://localhost:5173` for the frontend.
4. Open `http://localhost:8000/docs` for API documentation.

The backend health check is available at `GET http://localhost:8000/api/health`.

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
cd frontend; npm test
```

## OCR

PaddleOCR requires the `paddlepaddle`, `paddleocr`, and OpenCV packages declared in `pyproject.toml`. CPU inference is the default; consult PaddlePaddle's installation guidance if your platform needs a platform-specific wheel or GPU support. Model downloads may occur the first time an image is processed.

PackComply runs one recognizer per configured language against the same image and removes exact duplicate text-box results. Configuration is validated against the documented PP-OCRv3 language codes used by this project, so an unsupported code fails clearly at startup.

The configured languages are `en,hi,gom,mai,mr,ne,sa,ta,te,ur,ka`: English, Hindi, Konkani, Maithili, Marathi, Nepali, Sanskrit, Tamil, Telugu, Urdu, and Kannada (`ka` is PaddleOCR's Kannada model code). They are selected with `OCR_LANGUAGES`, and the OCR response reports the codes actually used.

To add another OCR language, first verify a compatible PaddleOCR model and its exact `lang` code for the installed release, then add it to `SUPPORTED_PADDLE_OCR_LANGUAGES` and `OCR_LANGUAGES` and cover it with a unit test.

After uploading an image, OCR can be run directly with `POST /api/inspections/{inspection_id}/images/{image_id}/ocr`; the usual route is `POST /api/inspections/{inspection_id}/process`, which runs every unfinished stage.

## Information extraction

`POST /api/inspections/{inspection_id}/extract` interprets the preserved OCR text into fields such as entities, addresses, MRP, net quantity, dates, and consumer-care details. Confidence values are explainable heuristics, not calibrated probabilities. Missing information is never fabricated and raw OCR evidence is never changed.

## UI languages

The interface offers 23 fully translated languages: English, Hindi, Assamese, Bengali, Bodo, Dogri, Gujarati, Kannada, Kashmiri, Konkani, Maithili, Malayalam, Manipuri (Meitei), Marathi, Nepali, Odia, Punjabi, Sanskrit, Santali, Sindhi, Tamil, Telugu, and Urdu. The header selector persists the choice under `packcomply-language` and falls back to English when the stored value is invalid.

Translations live in `frontend/src/locales/`. Every locale file must contain every UI key; the app rejects incomplete locale files rather than silently rendering English. The brand name stays "PackComply" in every language.

UI language and OCR language are separate. Assamese, Bengali, Bodo, Dogri, Gujarati, Kashmiri, Malayalam, Manipuri (Meitei), Odia, Punjabi, Santali, and Sindhi are UI options but are not enabled for OCR. OCR text is never translated; it remains as printed on the package.

## Risk-based targeting

Risk targeting is a transparent, deterministic prioritisation aid for completed inspections. It is not a machine-learning model and does not create legal rules or enforcement decisions. Officials see a queue based only on persisted compliance findings, missing extraction values that need review, and repeated evidence in their own completed-inspection history. Manufacturers cannot access the queue or the numerical score.

Scores are capped at 100: 0–24 LOW, 25–49 MEDIUM, 50–74 HIGH, and 75–100 CRITICAL.

- A FAIL contributes 35 / 25 / 15 / 10 points for HIGH / WARNING / INFO / LOW severity.
- A REVIEW contributes 20 / 12 / 6 / 4 points.
- Each absent extracted field contributes 6 points (maximum 18).
- A repeated non-pass rule adds 12, and a repeated missing field adds 8.
- Three or more distinct non-pass rules add 8.

Repeat evidence is calculated only within the authenticated owner's completed inspections. `GET /api/risk-targeting` is official-only, paginated, and excludes OCR text and image data.

## Declared quantity verification

Quantity verification reuses the existing OCR, extraction, and `NET_QUANTITY_FORMAT` compliance rule. It verifies only the **quantity declared on the label**; it does not weigh a product or claim that the physical contents were measured. The inspection result and PDF show the declared quantity, a normalized representation, its evidence, and the authoritative compliance result.

Supported formats include common mass and volume units (`mg`, `g`, `kg`, `ml`, `L`, `cm3`/`cc`) and count declarations (`N`, `nos`, `pcs`). Harmless spacing, capitalization, and common OCR character variants are normalized for validation while the declared display value is kept. Missing, unsupported, or ambiguous declarations continue through the existing compliance, risk, and repeat-pattern mechanisms.

## Disclaimer

PackComply is an inspection-assistance tool. It is not legal advice and its output is not an enforcement decision.

## Team

Team CORTEX

Originally developed by VersatileSid; maintained by rishibhaskar777.
