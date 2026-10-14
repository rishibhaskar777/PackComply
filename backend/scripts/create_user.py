import argparse
import getpass

from sqlalchemy import select

from backend.app.api.auth import hash_password
from backend.app.core.database import SessionLocal
from backend.app.models import User

parser = argparse.ArgumentParser(description="Create a local PackComply user")
parser.add_argument("--username")
parser.add_argument("--email")
parser.add_argument("--role", choices=["OFFICIAL", "MANUFACTURER"], default="OFFICIAL")
parser.add_argument("--full-name", dest="full_name")
parser.add_argument("--organization")

def main() -> None:
    args = parser.parse_args(); username = args.username or input("Username: ").strip(); email = args.email or input("Email: ").strip()
    password = getpass.getpass("Password: "); confirmation = getpass.getpass("Confirm password: ")
    if not username or not email or not password or password != confirmation: raise SystemExit("Username, email, and matching password are required.")
    with SessionLocal() as db:
        if db.scalar(select(User).where((User.username == username) | (User.email == email))): raise SystemExit("Username or email is already in use.")
        db.add(User(username=username, email=email, full_name=(args.full_name or username).strip(), organization=args.organization or None, role=args.role, password_hash=hash_password(password), is_active=True)); db.commit()
    print(f"Created {args.role} user {username}.")
if __name__ == "__main__": main()
