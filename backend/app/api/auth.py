"""Opaque, HTTP-only cookie authentication backed by persisted sessions."""
from datetime import datetime, timedelta, timezone
import logging
import re
import secrets
from collections import OrderedDict
from threading import Lock
from time import monotonic

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.database import get_db
from backend.app.core.security import DUMMY_PASSWORD_HASH as _DUMMY_PASSWORD_HASH, hash_password, token_hash, verify_password
from backend.app.models import AuthSession, User
from backend.app.services import password_reset
from backend.app.services.password_policy import password_problem

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("uvicorn.error")

ROLES = frozenset({"OFFICIAL", "MANUFACTURER"})
MAX_ACTIVE_SESSIONS_PER_USER = 10


class LoginRateLimiter:
    """In-memory failed-login limiter keyed by (username, client IP).

    Entries are kept in creation order, so stale windows are evicted from the
    front in amortized O(1). State is per-process, which suits a single
    local/demo server; use a shared store for multi-worker deployments.
    """

    def __init__(self) -> None:
        self._entries: "OrderedDict[tuple[str, str], list[float]]" = OrderedDict()  # key -> [count, window_start]
        self._lock = Lock()

    def _evict_stale(self, now: float, window: float) -> None:
        while self._entries:
            key, (_, started) = next(iter(self._entries.items()))
            if now - started < window:
                break
            del self._entries[key]

    def retry_after(self, key: tuple[str, str], max_attempts: int, window: float) -> int:
        """Seconds until the key may retry, or 0 when it is not locked out."""
        now = monotonic()
        with self._lock:
            self._evict_stale(now, window)
            entry = self._entries.get(key)
            if entry is None or entry[0] < max_attempts:
                return 0
            return max(1, int(window - (now - entry[1])) + 1)

    def record_failure(self, key: tuple[str, str], window: float) -> None:
        now = monotonic()
        with self._lock:
            self._evict_stale(now, window)
            entry = self._entries.get(key)
            if entry is None:
                self._entries[key] = [1, now]
            else:
                entry[0] += 1

    def clear(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear_username(self, *usernames: str) -> None:
        """Forget every (username, IP) entry for these usernames (compared lower-case)."""
        names = {name.strip().lower() for name in usernames if name}
        with self._lock:
            for key in [key for key in self._entries if key[0] in names]:
                del self._entries[key]

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()


login_limiter = LoginRateLimiter()
# Password-reset requests, keyed ("user", username) and ("ip", address); every request counts.
reset_limiter = LoginRateLimiter()

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)
    # "Keep me signed in on this device": SESSION_REMEMBER_DAYS instead of SESSION_TTL_HOURS.
    remember: bool = False
class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=1024)
    role: str = Field(default="MANUFACTURER")
    full_name: str | None = Field(default=None, max_length=200)
    company_name: str | None = Field(default=None, max_length=200)
    organization: str | None = Field(default=None, max_length=200)
class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str; username: str; email: str; is_active: bool; role: str; full_name: str; organization: str | None = None

class PasswordResetRequest(BaseModel):
    username: str = Field(min_length=1, max_length=320)
class PasswordResetConfirm(BaseModel):
    username: str = Field(min_length=1, max_length=320)
    code: str = Field(min_length=1, max_length=12)
    new_password: str = Field(min_length=1, max_length=1024)

def safe_user(user: User) -> UserResponse:
    return UserResponse(id=str(user.id), username=user.username or user.email, email=user.email, is_active=user.is_active, role=user.role, full_name=user.full_name, organization=user.organization)
def establish_session(user: User, response: Response, db: Session, remember: bool = False) -> None:
    token = secrets.token_urlsafe(32); settings = get_settings()
    now = datetime.now(timezone.utc)
    lifetime = timedelta(days=settings.session_remember_days) if remember else timedelta(hours=settings.session_ttl_hours)
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.expires_at <= now))
    db.add(AuthSession(token_hash=token_hash(token), user_id=user.id, expires_at=now + lifetime)); db.commit()
    response.set_cookie(settings.session_cookie_name, token, httponly=True, secure=settings.session_secure, samesite="lax", max_age=int(lifetime.total_seconds()), path="/")
    # Keep the newest sessions only (the one just created is the newest).
    stale = db.scalars(select(AuthSession.id).where(AuthSession.user_id == user.id).order_by(AuthSession.created_at.desc(), AuthSession.expires_at.desc()).offset(MAX_ACTIVE_SESSIONS_PER_USER)).all()
    if stale: db.execute(delete(AuthSession).where(AuthSession.id.in_(stale))); db.commit()

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token: raise HTTPException(status_code=401, detail="Not authenticated.")
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    expires_at = session.expires_at.replace(tzinfo=timezone.utc) if session and session.expires_at.tzinfo is None else session.expires_at if session else None
    if not session or expires_at <= datetime.now(timezone.utc):
        if session: db.delete(session); db.commit()
        raise HTTPException(status_code=401, detail="Not authenticated.")
    user = db.get(User, session.user_id)
    if not user or not user.is_active: raise HTTPException(status_code=401, detail="Not authenticated.")
    return user

@router.post("/login", response_model=UserResponse)
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> UserResponse:
    settings = get_settings(); window = settings.login_lockout_minutes * 60
    key = (payload.username.strip().lower(), request.client.host if request.client else "unknown")
    wait = login_limiter.retry_after(key, settings.login_max_attempts, window)
    if wait: raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.", headers={"Retry-After": str(wait)})
    user = db.scalar(select(User).where((User.username == payload.username) | (User.email == payload.username)))
    # Always hash-compare, even for unknown users, to avoid timing-based enumeration.
    password_ok = verify_password(payload.password, user.password_hash if user else _DUMMY_PASSWORD_HASH)
    if not user or not user.is_active or not password_ok:
        login_limiter.record_failure(key, window)
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    login_limiter.clear(key)
    establish_session(user, response, db, remember=payload.remember)
    return safe_user(user)
@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, response: Response, db: Session = Depends(get_db)) -> UserResponse:
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", payload.email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    role = payload.role.upper()
    if role not in ROLES: raise HTTPException(status_code=422, detail="Choose a valid account role.")
    if problem := password_problem(payload.password, payload.username): raise HTTPException(status_code=422, detail=problem)
    if role == "OFFICIAL" and not get_settings().allow_official_self_registration:
        raise HTTPException(status_code=403, detail="Official accounts are created by an administrator.")
    if db.scalar(select(User).where((User.username == payload.username) | (User.email == payload.email))):
        raise HTTPException(status_code=409, detail="An account with those details already exists.")
    name = (payload.company_name if role == "MANUFACTURER" else payload.full_name) or payload.username
    if len(name.strip()) < 2: raise HTTPException(status_code=422, detail="Provide the required account name.")
    user = User(email=payload.email, username=payload.username, full_name=name.strip(), organization=(payload.organization or None), role=role, password_hash=hash_password(payload.password), is_active=True)
    try:
        db.add(user); db.commit(); db.refresh(user)
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with those details already exists.") from error
    establish_session(user, response, db)
    return safe_user(user)
@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)) -> UserResponse: return safe_user(user)

def require_role(role: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role != role: raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized.")
        return user
    return dependency

require_official = require_role("OFFICIAL")
require_manufacturer = require_role("MANUFACTURER")
@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Response:
    name = get_settings().session_cookie_name; token = request.cookies.get(name)
    if token: db.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(token))); db.commit()
    response.delete_cookie(name, path="/"); response.status_code = status.HTTP_204_NO_CONTENT; return response


RESET_REQUESTED_DETAIL = "If that account exists, a reset code has been issued. Ask your administrator for the code, or check your e-mail if e-mail is set up on this server."
INVALID_RESET_CODE_DETAIL = "Invalid or expired code"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(payload: PasswordResetRequest, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)) -> dict[str, str]:
    """Issue a one-time code. The answer never reveals whether the username exists."""
    settings = get_settings(); window = settings.password_reset_rate_window_minutes * 60; limit = settings.password_reset_rate_limit
    ip = _client_ip(request); keys = [("user", payload.username.strip().lower()), ("ip", ip)]
    # Throttled the same way for unknown and real usernames, so a 429 says nothing about existence.
    wait = max(reset_limiter.retry_after(key, limit, window) for key in keys)
    if wait: raise HTTPException(status_code=429, detail="Too many reset requests. Try again later.", headers={"Retry-After": str(wait)})
    for key in keys: reset_limiter.record_failure(key, window)
    user = password_reset.find_user(db, payload.username)
    if user is None or not user.is_active:
        # The same scrypt work as storing a real code keeps the response time similar.
        hash_password(password_reset.generate_code())
        logger.info("Password reset requested for an unknown or inactive account ip=%s", ip)
    else:
        code = password_reset.issue_code(db, user, ip)
        delivery = "email" if password_reset.smtp_enabled(settings) and user.email else "administrator"
        if delivery == "email":
            # Sent after the response, so SMTP latency cannot reveal that the account exists.
            background.add_task(password_reset.send_code_email, user.email, code, settings)
        logger.info("Password reset requested user_id=%s ip=%s delivery=%s", user.id, ip, delivery)
    return {"detail": RESET_REQUESTED_DETAIL}


@router.post("/password-reset/confirm")
def confirm_password_reset(payload: PasswordResetConfirm, request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    """Redeem a code and set a new password; signs the account out everywhere."""
    # Checked before any lookup, so a policy error cannot reveal whether the account exists.
    if problem := password_problem(payload.new_password, payload.username): raise HTTPException(status_code=422, detail=problem)
    user = password_reset.find_user(db, payload.username)
    if user is not None and not user.is_active: user = None
    if not password_reset.redeem_code(db, user, payload.code.strip()) or user is None:
        raise HTTPException(status_code=400, detail=INVALID_RESET_CODE_DETAIL)
    revoked = password_reset.apply_new_password(db, user, payload.new_password)
    login_limiter.clear_username(user.username or "", user.email)
    logger.info("Password reset completed user_id=%s ip=%s sessions_revoked=%s", user.id, _client_ip(request), revoked)
    return {"detail": "Password updated. You were signed out on all devices."}
