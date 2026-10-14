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

Because it is not a Windows service, you must start PostgreSQL again after every reboot.

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

Open <http://localhost:5173> and sign in. API docs are at <http://localhost:8000/docs> and the health check at <http://localhost:8000/api/health>.

## First OCR run

The first time an image is processed, PaddleOCR downloads its models (several files per language). This can take a minute or more and needs internet access. Later runs use the cached models.

## Troubleshooting

**Connection refused / server not running.** PostgreSQL is not running. Start it with `pg_ctl.exe -D "<PG_DATA>" -l "<log file>" start` and confirm with `pg_ctl.exe -D "<PG_DATA>" status`.

**Port 5432 already in use.** Another PostgreSQL instance is running. Stop it, or start this one on a different port (`port` in `<PG_DATA>\postgresql.conf`) and update `POSTGRES_PORT` and `DATABASE_URL` in `.env`.

**`password authentication failed for user "packcomply"`.** The password in `.env` does not match the role's password. Fix it with `ALTER ROLE packcomply WITH PASSWORD '<your-password>';` via `psql -U postgres -h localhost`, or update `DATABASE_URL`.

**"PaddleOCR is not installed".** The backend is running outside the virtual environment, or `pip install -e ".[dev]"` did not complete. Activate `.venv` and reinstall. If `paddlepaddle` cannot be found, check that you are using Python 3.11 – 3.13.

**OCR is slow.** Reduce `OCR_LANGUAGES` in `.env` (for example `en` or `en,hi`) and restart the backend. Lowering `OCR_MAX_IMAGE_DIMENSION` also helps at the cost of accuracy on small print.
