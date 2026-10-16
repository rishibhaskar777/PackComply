"""One-time password-reset codes: issue, redeem, apply, and optional e-mail delivery.

Codes are 6 digits, valid for PASSWORD_RESET_CODE_MINUTES, stored only as a
scrypt hash, and burned after PASSWORD_RESET_MAX_ATTEMPTS wrong guesses.  The
plain code is returned to the caller exactly once and is never logged.
"""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import logging
import secrets
import smtplib

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.core.security import DUMMY_PASSWORD_HASH, hash_password, verify_password
from backend.app.models import AuthSession, PasswordResetCode, User

logger = logging.getLogger("uvicorn.error")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    # SQLite (tests) returns naive datetimes; PostgreSQL returns aware ones.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def generate_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def find_user(db: Session, username: str) -> User | None:
    """The account a reset names: by username, or by e-mail like the login form."""
    name = username.strip()
    if not name:
        return None
    return db.scalar(select(User).where((User.username == name) | (User.email == name)))


def _close_open_codes(db: Session, user_id, now: datetime) -> None:
    db.execute(update(PasswordResetCode).where(PasswordResetCode.user_id == user_id, PasswordResetCode.used_at.is_(None)).values(used_at=now))


def issue_code(db: Session, user: User, requested_ip: str | None = None) -> str:
    """Invalidate the user's earlier unused codes, store a new one, and return it in plain text."""
    settings = get_settings(); now = _now(); code = generate_code()
    _close_open_codes(db, user.id, now)
    db.add(PasswordResetCode(user_id=user.id, code_hash=hash_password(code), expires_at=now + timedelta(minutes=settings.password_reset_code_minutes), attempts=0, requested_ip=(requested_ip or None)))
    db.commit()
    return code


def redeem_code(db: Session, user: User | None, code: str) -> bool:
    """True when `code` is the user's current code; consumes it.

    Every path runs exactly one scrypt verification so timing does not reveal
    whether the user or an active code exists.  Each guess counts before it is
    checked, and the code is burned when the attempts run out.
    """
    settings = get_settings(); now = _now()
    active = None
    if user is not None:
        latest = db.scalar(select(PasswordResetCode).where(PasswordResetCode.user_id == user.id, PasswordResetCode.used_at.is_(None)).order_by(PasswordResetCode.created_at.desc()).limit(1))
        if latest is not None and _aware(latest.expires_at) > now and latest.attempts < settings.password_reset_max_attempts:
            active = latest
    open_code = (PasswordResetCode.used_at.is_(None), PasswordResetCode.attempts < settings.password_reset_max_attempts)
    # Count the guess with a conditional UPDATE so parallel guesses cannot exceed the limit.
    counted = active is not None and db.execute(update(PasswordResetCode).where(PasswordResetCode.id == active.id, *open_code).values(attempts=PasswordResetCode.attempts + 1)).rowcount == 1
    db.commit()
    if not counted:
        verify_password(code, DUMMY_PASSWORD_HASH)
        return False
    db.refresh(active)
    if verify_password(code, active.code_hash):
        # Only one redemption can win a race for the same code.
        return _close(db, active.id) == 1
    if active.attempts >= settings.password_reset_max_attempts:
        _close(db, active.id)
        logger.warning("Password reset code burned after too many attempts user_id=%s", user.id)
    return False


def _close(db: Session, code_id) -> int:
    closed = db.execute(update(PasswordResetCode).where(PasswordResetCode.id == code_id, PasswordResetCode.used_at.is_(None)).values(used_at=_now())).rowcount
    db.commit()
    return closed


def apply_new_password(db: Session, user: User, new_password: str) -> int:
    """Set the password, revoke every session and close any open codes. Returns the sessions revoked."""
    now = _now()
    user.password_hash = hash_password(new_password)
    revoked = db.execute(delete(AuthSession).where(AuthSession.user_id == user.id)).rowcount or 0
    _close_open_codes(db, user.id, now)
    db.commit()
    return revoked


def smtp_enabled(settings: Settings | None = None) -> bool:
    return bool((settings or get_settings()).smtp_host.strip())


def send_code_email(to_address: str, code: str, settings: Settings | None = None) -> bool:
    """E-mail a reset code with the standard library. Failures are logged (without the code), never raised."""
    settings = settings or get_settings()
    message = EmailMessage()
    message["Subject"] = "Your PackComply password reset code"
    message["From"] = settings.smtp_from or settings.smtp_username or "packcomply@localhost"
    message["To"] = to_address
    message.set_content(
        f"Your PackComply password reset code is {code}.\n\n"
        f"It is valid for {settings.password_reset_code_minutes} minutes and can be used once.\n"
        "If you did not ask to reset your password, ignore this message; your password has not changed.\n"
    )
    try:
        client_class = smtplib.SMTP_SSL if settings.smtp_ssl else smtplib.SMTP
        with client_class(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds) as client:
            if settings.smtp_starttls and not settings.smtp_ssl:
                client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
        return True
    except (OSError, smtplib.SMTPException) as error:
        logger.error("Password reset e-mail could not be sent host=%s reason=%s", settings.smtp_host, type(error).__name__)
        return False
