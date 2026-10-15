"""Block until the configured database accepts connections, or give up.

Used by the Docker entrypoint before migrations run:
    python -m backend.scripts.wait_for_db [--timeout SECONDS]

Exits 0 once ``SELECT 1`` succeeds, 1 after the timeout.
"""
import argparse
import sys
from time import monotonic, sleep

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from backend.app.core.database import engine


def wait_for_database(timeout_seconds: float, interval_seconds: float = 2.0) -> bool:
    deadline = monotonic() + timeout_seconds
    attempt = 0
    while True:
        attempt += 1
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            print(f"Database is ready (attempt {attempt}).", flush=True)
            return True
        except OperationalError as error:
            if monotonic() + interval_seconds > deadline:
                print(f"Database still unreachable after {timeout_seconds:.0f} s: {error.orig}", file=sys.stderr, flush=True)
                return False
            print(f"Waiting for database (attempt {attempt})...", flush=True)
            sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=float, default=30.0, help="Seconds to wait before failing (default 30).")
    sys.exit(0 if wait_for_database(parser.parse_args().timeout) else 1)


if __name__ == "__main__":
    main()
