#!/bin/sh
# Container start: wait for PostgreSQL, apply migrations, then serve the API.
set -eu

python -m backend.scripts.wait_for_db --timeout "${DB_WAIT_TIMEOUT_SECONDS:-30}"
alembic -c backend/alembic.ini upgrade head

if [ "$#" -gt 0 ]; then
    exec "$@"
fi
exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
