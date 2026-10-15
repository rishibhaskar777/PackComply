# Running PackComply on Windows without Docker

This guide runs the backend, frontend and PostgreSQL directly on Windows using PowerShell.

## Prerequisites

- **Python 3.11 – 3.13** (3.12 recommended). PaddlePaddle does not publish wheels for newer Python releases, so check `python --version` or use `py -3.12`.
- **Node.js 20+** with npm.
- **PostgreSQL 16** from the EDB zip binaries (no installer or Windows service needed).

In the commands below, `<PG_HOME>` is the folder containing `bin\` (for example the extracted `pgsql` directory) and `<PG_DATA>` is the folder you choose for the database files.

### PostgreSQL from the zip binaries

Initialise a data directory once:

```powershell
& "<PG_HOME>\bin\initdb.exe" -D "<PG_DATA>" -U postgres -W -E UTF8
```

Start, check and stop the server:

```powershell
& "<PG_HOME>\bin\pg_ctl.exe" -D "<PG_DATA>" -l "<PG_DATA>\..\postgres.log" start
& "<PG_HOME>\bin\pg_ctl.exe" -D "<PG_DATA>" status
& "<PG_HOME>\bin\pg_ctl.exe" -D "<PG_DATA>" stop
```

Started this way it is not a Windows service, so you must start PostgreSQL again after every reboot — unless you register it as a service (next section).

### Start PostgreSQL automatically

Register the server as a Windows service that starts at boot. Run these from an **Administrator** PowerShell, after stopping any instance you started manually with `pg_ctl ... stop`:

```powershell
& "<PG_HOME>\bin\pg_ctl.exe" register -N PackComplyPostgres -D "<PG_DATA>" -S auto
# The service runs as NETWORK SERVICE, which needs full access to the data directory.
icacls "<PG_DATA>" /grant "NT AUTHORITY\NetworkService:(OI)(CI)F" /T
Start-Service PackComplyPostgres
Get-Service PackComplyPostgres
```

From then on use `Start-Service` / `Stop-Service PackComplyPostgres` instead of `pg_ctl start` / `stop`. Server messages go to the Windows Event Log (Application, source `PostgreSQL`) rather than the `-l` log file.

To remove the service (again as Administrator):

```powershell
Stop-Service PackComplyPostgres
& "<PG_HOME>\bin\pg_ctl.exe" unregister -N PackComplyPostgres
```

## 1. Create `.env`

From the repository root:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set:

- `POSTGRES_PASSWORD` to a password of your choice.
- `DATABASE_URL=postgresql+psycopg://packcomply:<your password>@localhost:5432/packcomply` using the same password.
- Optionally `OCR_LANGUAGES=en,hi` (fewer languages means faster OCR).

The backend reads `.env` from the repository root. `.env` is ignored by Git; never commit it.

## 2. Create the database role and database

Run this in PowerShell, replacing the placeholder password with the one in `.env`. You will be prompted for the `postgres` superuser password. `CREATE DATABASE` cannot run inside a multi-statement transaction, so each statement is a separate `-c`.

```powershell
& "<PG_HOME>\bin\psql.exe" -U postgres -h localhost -c "CREATE ROLE packcomply WITH LOGIN PASSWORD '<your-password>';" -c "CREATE DATABASE packcomply OWNER packcomply;"
```

## 3. Python environment

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

PaddlePaddle and PaddleOCR are large downloads.

## 4. Migrations

With the virtual environment active:

```powershell
alembic -c backend/alembic.ini upgrade head
```

## 5. Install frontend dependencies

```powershell
cd frontend
npm install
cd ..
```

## 6. Create the first user

```powershell
python -m backend.scripts.create_user
```

The script prompts for a username, an email and a password (entered twice, not echoed). The account is created with the default role, which is an **OFFICIAL** account.

## 7. Start the app

Use two terminals.

Terminal 1, backend (repository root, virtual environment active):

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn backend.app.main:app --reload
```

Terminal 2, frontend:

```powershell
cd frontend
npm run dev
```

Open <http://localhost:5173> and sign in. API docs are at <http://localhost:8000/docs>.

Two health endpoints are available:

- <http://localhost:8000/api/health> — liveness: the API process is up. It never touches the database.
- <http://localhost:8000/api/health/ready> — readiness: runs `SELECT 1` against PostgreSQL. Returns `200 {"status":"ready","database":"ok"}`, or `503 {"status":"unavailable","database":"unreachable"}` when the database is down.

If PostgreSQL stops while the app is running, API requests fail within about 5 seconds with `503 Database unavailable. Please try again shortly.` and the sign-in page says *Server or database unavailable*, instead of hanging or reporting invalid credentials.

## First OCR run

The first time an image is processed, PaddleOCR downloads its models (several files per language). This can take a minute or more and needs internet access. Later runs use the cached models.

### Warm up the OCR models

Run this once after install and before any demo, so no model downloads happen live:

```powershell
python -m backend.scripts.warm_ocr
```

It downloads and loads the models for every language in `OCR_LANGUAGES`, prints per-language status and time, and does not touch the database.

The API also warms the models itself in a background thread on startup (`OCR_WARMUP_ON_STARTUP=true`); `/api/health` responds immediately while that runs. Secondary languages (Hindi, ...) each run in one long-lived worker process that loads its models once and is restarted automatically if it crashes or times out.

### OCR performance (measured)

Measured on an 8-core Windows 11 laptop, CPU only, paddlepaddle 3.3.1, PaddleOCR 3.7, on a real 833x720 package label (7 ground-truth strings checked). Warm inference, English only:

| Configuration | Inference | Ground-truth hits |
| --- | --- | --- |
| Previous default (oneDNN off, v6 medium det + rec) | 46.5 s | 7/7 |
| oneDNN on everywhere | fails: `NotImplementedError` (detection models) | - |
| **oneDNN on recognition only (new default), same models** | **14.5 s** | **7/7** |
| + PP-OCRv5 mobile det + en mobile rec | 9.9 s | 6/7 (drops the "MRP Rs." line) |
| + PP-OCRv6 small det + rec | 8.1 s | 6/7 ("INCL." loses its period) |
| + `cpu_threads` 8 vs default | no difference | - |
| + denoise removed | 14.2 s | 7/7 (only ~0.2 s saved) |

Hindi (Devanagari) pass: 36.1 s before, 12.4 s with recognition oneDNN.
English + Hindi through `scan()`: about 140 s and Hindi timed out before; about 25.5 s warm now (English and Hindi overlap in separate processes).

Because mobile/small models lose price-critical text on this label, the accurate models stay the default. To trade accuracy for speed, set `OCR_DETECTION_MODEL` / `OCR_RECOGNITION_MODEL` (for example `PP-OCRv5_mobile_det`; recognition model names apply to the first language only) and re-check your own labels.

## Creating an inspector (official) account

Public registration only creates manufacturer accounts. Create inspector accounts from the command line:

```powershell
python -m backend.scripts.create_user --role OFFICIAL --username inspector --email inspector@example.com --full-name "Inspector Name" --organization "Legal Metrology"
```

## Troubleshooting

**Connection refused / server not running.** PostgreSQL is not running. Start it with `pg_ctl.exe -D "<PG_DATA>" -l "<log file>" start` (or `Start-Service PackComplyPostgres` if you registered the service) and confirm with `pg_ctl.exe -D "<PG_DATA>" status`. `http://localhost:8000/api/health/ready` returns 503 until the database answers.

**Port 5432 already in use.** Another PostgreSQL instance is running. Stop it, or start this one on a different port (`port` in `<PG_DATA>\postgresql.conf`) and update `POSTGRES_PORT` and `DATABASE_URL` in `.env`.

**`password authentication failed for user "packcomply"`.** The password in `.env` does not match the role's password. Fix it with `ALTER ROLE packcomply WITH PASSWORD '<your-password>';` via `psql -U postgres -h localhost`, or update `DATABASE_URL`.

**"PaddleOCR is not installed".** The backend is running outside the virtual environment, or `pip install -e ".[dev]"` did not complete. Activate `.venv` and reinstall. If `paddlepaddle` cannot be found, check that you are using Python 3.11 – 3.13.

**OCR is slow.** Make sure `OCR_ENABLE_MKLDNN=true` (default) and check the backend log for `mkldnn_recognition=True`; a warning there means it fell back to the slower path. Reduce `OCR_LANGUAGES` in `.env` (for example `en`) and restart the backend. Lowering `OCR_MAX_IMAGE_DIMENSION` also helps at the cost of accuracy on small print.
