"""Administrator password reset, run on the server.

    python -m backend.scripts.reset_password --username X
        Issue a fresh one-time 6-digit code and print it, to hand to the user in person.
        Any earlier unused code for that user stops working.

    python -m backend.scripts.reset_password --username X --set-password
        Prompt for a new password (hidden input) and set it directly.
        Every session of that user is signed out.
"""
import argparse
import getpass
from typing import Callable

from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.database import SessionLocal
from backend.app.services import password_reset
from backend.app.services.password_policy import password_problem

parser = argparse.ArgumentParser(description="Reset a PackComply user's password")
parser.add_argument("--username", required=True, help="Username or e-mail address of the account")
parser.add_argument("--set-password", dest="set_password", action="store_true", help="Prompt for a new password and set it directly instead of issuing a code")


def main(argv: list[str] | None = None, session_factory: Callable[[], Session] = SessionLocal, prompt: Callable[[str], str] = getpass.getpass) -> int:
    args = parser.parse_args(argv)
    with session_factory() as db:
        user = password_reset.find_user(db, args.username)
        if user is None:
            raise SystemExit(f"No account named {args.username!r}.")
        name = user.username or user.email
        if args.set_password:
            password = prompt("New password: "); confirmation = prompt("Confirm new password: ")
            if password != confirmation:
                raise SystemExit("Passwords do not match.")
            if problem := password_problem(password, name):
                raise SystemExit(problem)
            revoked = password_reset.apply_new_password(db, user, password)
            print(f"Password updated for {name}. {revoked} session(s) signed out.")
            print("If the account is locked after failed sign-ins, the lock clears within LOGIN_LOCKOUT_MINUTES or on an API restart.")
            return 0
        code = password_reset.issue_code(db, user, requested_ip="cli")
        minutes = get_settings().password_reset_code_minutes
        print(f"Reset code for {name}: {code}")
        print(f"Valid for {minutes} minutes and for one use. Hand it over in person; earlier codes no longer work.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
