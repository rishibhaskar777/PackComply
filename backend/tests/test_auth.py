"""Dedicated authentication, session, and inspection-ownership coverage."""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from backend.app.api.auth import get_current_user, hash_password, token_hash
from backend.app.core.config import get_settings
from backend.app.core.database import get_db
from backend.app.main import app
from backend.app.models import AuthSession, Base, Inspection, User


@pytest.fixture
def auth_database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        with sessions() as session:
            yield session

    # The legacy test suite supplies an inspection-only principal globally.
    # Dedicated auth tests must instead use the real cookie/session dependency.
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides[get_db] = override_db
    yield sessions
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def auth_client(auth_database):
    with TestClient(app) as client:
        yield client


def create_user(sessions, username: str, password: str = "correct-horse-battery-staple", role: str = "OFFICIAL") -> User:
    with sessions() as db:
        user = User(
            email=f"{username}@example.invalid",
            username=username,
            full_name=username.title(),
            password_hash=hash_password(password),
            is_active=True,
            role=role,
        )
        db.add(user)
        db.commit()
        return user


@pytest.fixture
def test_user(auth_database) -> User:
    return create_user(auth_database, "first-user")


@pytest.fixture
def second_test_user(auth_database) -> User:
    return create_user(auth_database, "second-user", "another-correct-password")


@pytest.fixture
def authenticated_client(auth_client, test_user) -> TestClient:
    response = auth_client.post("/api/auth/login", json={"username": test_user.username, "password": "correct-horse-battery-staple"})
    assert response.status_code == 200
    return auth_client


@pytest.fixture
def second_authenticated_client(auth_database, second_test_user) -> TestClient:
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"username": second_test_user.username, "password": "another-correct-password"})
        assert response.status_code == 200
        yield client


@pytest.fixture
def authenticated_session(authenticated_client, auth_database, test_user) -> AuthSession:
    with auth_database() as db:
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == test_user.id))
        assert session is not None
        return session


def test_user_creation_hashes_password_and_never_stores_plaintext(auth_database) -> None:
    password = "correct-horse-battery-staple"
    user = create_user(auth_database, "created-user", password)
    with auth_database() as db:
        stored = db.get(User, user.id)
        assert stored is not None
        assert stored.password_hash.startswith("scrypt$")
        assert stored.password_hash != password
        assert password not in stored.password_hash


def test_duplicate_username_is_rejected(auth_database) -> None:
    create_user(auth_database, "duplicate-user")
    with pytest.raises(IntegrityError):
        create_user(auth_database, "duplicate-user")


def test_valid_login_establishes_session_and_me_returns_user(authenticated_client, authenticated_session, test_user) -> None:
    response = authenticated_client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json() == {
        "id": str(test_user.id), "username": test_user.username,
        "email": test_user.email, "is_active": True, "role": "OFFICIAL", "full_name": test_user.full_name, "organization": None,
    }
    assert authenticated_session.token_hash != authenticated_client.cookies[get_settings().session_cookie_name]


@pytest.mark.parametrize("username,password", [
    ("first-user", "wrong-password"),
    ("missing-user", "correct-horse-battery-staple"),
])
def test_invalid_login_credentials_are_generic(auth_client, test_user, username: str, password: str) -> None:
    response = auth_client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid username or password."}


def test_me_without_a_session_returns_401(auth_client) -> None:
    response = auth_client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated."}


def test_expired_session_returns_401(auth_client, auth_database, test_user) -> None:
    token = "expired-test-token"
    with auth_database() as db:
        db.add(AuthSession(token_hash=token_hash(token), user_id=test_user.id, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        db.commit()
    auth_client.cookies.set(get_settings().session_cookie_name, token)
    assert auth_client.get("/api/auth/me").status_code == 401


def test_logout_revokes_the_authenticated_session(authenticated_client) -> None:
    assert authenticated_client.post("/api/auth/logout").status_code == 204
    assert authenticated_client.get("/api/auth/me").status_code == 401


def test_unauthenticated_inspection_creation_returns_401(auth_client) -> None:
    assert auth_client.post("/api/inspections", json={}).status_code == 401


def test_authenticated_inspection_creation_records_the_current_user(authenticated_client, auth_database, test_user) -> None:
    response = authenticated_client.post("/api/inspections", json={})
    assert response.status_code == 201
    with auth_database() as db:
        inspection = db.get(Inspection, UUID(response.json()["id"]))
        assert inspection is not None
        assert inspection.user_id == test_user.id


def test_owner_can_access_own_inspection(authenticated_client) -> None:
    inspection = authenticated_client.post("/api/inspections", json={}).json()
    assert authenticated_client.get(f"/api/inspections/{inspection['id']}").status_code == 200


def test_pdf_report_requires_authentication_and_enforces_ownership(
    authenticated_client, second_authenticated_client, owned_inspection,
) -> None:
    inspection_id = owned_inspection["id"]
    with TestClient(app) as unauthenticated_client:
        assert unauthenticated_client.get(f"/api/inspections/{inspection_id}/report").status_code == 401
    assert second_authenticated_client.get(f"/api/inspections/{inspection_id}/report").status_code == 404


def test_owner_can_download_a_valid_pdf_report_with_persisted_data(authenticated_client, auth_database, test_user) -> None:
    with auth_database() as db:
        inspection = Inspection(inspection_number="LG-REPORT-PERSISTED", user_id=test_user.id, status="COMPLETED")
        db.add(inspection); db.commit(); db.refresh(inspection)
    response = authenticated_client.get(f"/api/inspections/{inspection.id}/report")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
    assert "attachment; filename=\"inspection_" in response.headers["content-disposition"]
    # PDF metadata streams retain this persisted inspection identifier.
    assert b"LG-REPORT-PERSISTED" in response.content


def test_history_is_paginated_newest_first_and_never_leaks_other_users(
    authenticated_client, second_authenticated_client, auth_database, test_user, second_test_user,
) -> None:
    """The collection endpoint applies ownership before every filter and page."""
    with auth_database() as db:
        older = Inspection(inspection_number="LG-HISTORY-OLDER", user_id=test_user.id, status="CREATED")
        newer = Inspection(inspection_number="LG-HISTORY-NEWER", user_id=test_user.id, status="COMPLETED")
        other = Inspection(inspection_number="LG-HISTORY-OTHER", user_id=second_test_user.id, status="COMPLETED")
        db.add_all((older, newer, other)); db.commit()
        newer.created_at = datetime.now(timezone.utc) + timedelta(seconds=1); db.commit()

    response = authenticated_client.get("/api/inspections?page=1&page_size=1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2 and payload["total_pages"] == 2
    assert payload["items"][0]["inspection_number"] == "LG-HISTORY-NEWER"
    assert authenticated_client.get("/api/inspections?search=OLDER").json()["items"][0]["inspection_number"] == "LG-HISTORY-OLDER"
    assert authenticated_client.get("/api/inspections?status=COMPLETED").json()["total"] == 1
    assert second_authenticated_client.get("/api/inspections").json()["items"][0]["inspection_number"] == "LG-HISTORY-OTHER"
    assert authenticated_client.get("/api/inspections?page=0").status_code == 422
    assert authenticated_client.get("/api/inspections?page_size=101").status_code == 422


@pytest.fixture
def owned_inspection(authenticated_client) -> dict:
    response = authenticated_client.post("/api/inspections", json={})
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize("operation", [
    "access", "upload", "ocr", "extract", "compliance", "process", "retry",
])
def test_second_user_cannot_access_or_operate_on_another_users_inspection(second_authenticated_client, owned_inspection, operation: str) -> None:
    inspection_id = owned_inspection["id"]
    if operation == "access":
        response = second_authenticated_client.get(f"/api/inspections/{inspection_id}")
    elif operation == "upload":
        response = second_authenticated_client.post(f"/api/inspections/{inspection_id}/image", files={"image": ("label.png", b"not-read", "image/png")})
    elif operation == "ocr":
        response = second_authenticated_client.post(f"/api/inspections/{inspection_id}/images/{uuid4()}/ocr")
    elif operation == "extract":
        response = second_authenticated_client.post(f"/api/inspections/{inspection_id}/extract")
    elif operation == "compliance":
        response = second_authenticated_client.post(f"/api/inspections/{inspection_id}/compliance")
    else:
        response = second_authenticated_client.post(f"/api/inspections/{inspection_id}/process")
    assert response.status_code == 404
    assert response.json() == {"detail": "Inspection not found."}


def test_auth_endpoints_do_not_expose_sensitive_authentication_data(authenticated_client, test_user) -> None:
    login_response = authenticated_client.post("/api/auth/login", json={"username": test_user.username, "password": "correct-horse-battery-staple"})
    me_response = authenticated_client.get("/api/auth/me")
    for response in (login_response, me_response):
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"id", "username", "email", "is_active", "role", "full_name", "organization"}
        serialized = str(payload).lower()
        assert "password" not in serialized
        assert "hash" not in serialized
        assert "token" not in serialized
        assert "session" not in serialized


def test_registration_creates_a_hashed_user_and_authenticated_session(auth_client, auth_database) -> None:
    password = "register-password"
    response = auth_client.post("/api/auth/register", json={"username": "registered-user", "email": "registered@example.invalid", "password": password})
    assert response.status_code == 201
    assert set(response.json()) == {"id", "username", "email", "is_active", "role", "full_name", "organization"}
    assert password not in str(response.json())
    with auth_database() as db:
        user = db.scalar(select(User).where(User.username == "registered-user"))
        assert user and user.password_hash.startswith("scrypt$") and password not in user.password_hash
    assert auth_client.get("/api/auth/me").json()["username"] == "registered-user"


def test_default_registration_creates_a_manufacturer(auth_client) -> None:
    response = auth_client.post("/api/auth/register", json={"username": "default-role", "email": "default-role@example.invalid", "password": "register-password"})
    assert response.status_code == 201 and response.json()["role"] == "MANUFACTURER"


def test_public_registration_cannot_create_an_official(auth_client, auth_database) -> None:
    response = auth_client.post("/api/auth/register", json={"username": "sneaky", "email": "sneaky@example.invalid", "password": "register-password", "role": "OFFICIAL"})
    assert response.status_code == 403
    assert response.json() == {"detail": "Official accounts are created by an administrator."}
    with auth_database() as db:
        assert db.scalar(select(User).where(User.username == "sneaky")) is None


def test_official_self_registration_flag_allows_official(auth_client, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "allow_official_self_registration", True)
    response = auth_client.post("/api/auth/register", json={"username": "flagged", "email": "flagged@example.invalid", "password": "register-password", "role": "OFFICIAL"})
    assert response.status_code == 201 and response.json()["role"] == "OFFICIAL"


def test_role_registration_me_and_server_side_authorization(auth_client, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "allow_official_self_registration", True)
    official = {"username": "official-role", "email": "official-role@example.invalid", "password": "register-password", "role": "OFFICIAL", "full_name": "Official User", "organization": "Label Office"}
    response = auth_client.post("/api/auth/register", json=official)
    assert response.status_code == 201 and response.json()["role"] == "OFFICIAL"
    assert auth_client.get("/api/auth/me").json()["role"] == "OFFICIAL"
    assert auth_client.get("/api/roles/official").status_code == 200
    assert auth_client.get("/api/roles/manufacturer").status_code == 403
    assert auth_client.post("/api/auth/logout").status_code == 204
    manufacturer = {"username": "manufacturer-role", "email": "manufacturer-role@example.invalid", "password": "register-password", "role": "MANUFACTURER", "company_name": "Safe Foods Ltd"}
    response = auth_client.post("/api/auth/register", json=manufacturer)
    assert response.status_code == 201 and response.json()["role"] == "MANUFACTURER"
    assert auth_client.get("/api/auth/me").json()["role"] == "MANUFACTURER"
    assert auth_client.get("/api/roles/manufacturer").status_code == 200
    assert auth_client.get("/api/roles/official").status_code == 403


def test_registration_rejects_duplicate_username_and_email(auth_client, test_user) -> None:
    username = test_user.username
    response = auth_client.post("/api/auth/register", json={"username": username, "email": "new@example.invalid", "password": "register-password"})
    assert response.status_code == 409
    response = auth_client.post("/api/auth/register", json={"username": "new-user", "email": test_user.email, "password": "register-password"})
    assert response.status_code == 409


@pytest.mark.parametrize("payload", [
    {"username": "!", "email": "valid@example.invalid", "password": "register-password"},
    {"username": "valid-user", "email": "not-an-email", "password": "register-password"},
    {"username": "valid-user", "email": "valid@example.invalid", "password": "short"},
])
def test_registration_validates_username_email_and_password(auth_client, payload) -> None:
    assert auth_client.post("/api/auth/register", json=payload).status_code == 422


def test_newly_registered_user_can_log_in_after_logout(auth_client) -> None:
    payload = {"username": "login-after-register", "email": "login-after-register@example.invalid", "password": "register-password"}
    assert auth_client.post("/api/auth/register", json=payload).status_code == 201
    assert auth_client.post("/api/auth/logout").status_code == 204
    assert auth_client.post("/api/auth/login", json={"username": payload["username"], "password": payload["password"]}).status_code == 200


def test_login_is_locked_after_repeated_failures_and_success_resets(auth_client, test_user) -> None:
    bad = {"username": test_user.username, "password": "wrong-password"}
    ok = {"username": test_user.username, "password": "correct-horse-battery-staple"}
    for _ in range(4):
        assert auth_client.post("/api/auth/login", json=bad).status_code == 401
    # Success clears the counter.
    assert auth_client.post("/api/auth/login", json=ok).status_code == 200
    for _ in range(5):
        assert auth_client.post("/api/auth/login", json=bad).status_code == 401
    locked = auth_client.post("/api/auth/login", json=bad)
    assert locked.status_code == 429
    assert locked.json() == {"detail": "Too many failed attempts. Try again later."}
    assert int(locked.headers["Retry-After"]) > 0
    # Even the correct password is refused while locked out.
    assert auth_client.post("/api/auth/login", json=ok).status_code == 429


def test_unknown_user_failures_are_also_limited_and_generic(auth_client) -> None:
    payload = {"username": "ghost", "password": "whatever-password"}
    for _ in range(5):
        response = auth_client.post("/api/auth/login", json=payload)
        assert response.status_code == 401 and response.json() == {"detail": "Invalid username or password."}
    assert auth_client.post("/api/auth/login", json=payload).status_code == 429


def test_unknown_user_login_still_runs_a_password_hash(auth_client, monkeypatch) -> None:
    from backend.app.api import auth as auth_module
    calls: list[str | None] = []
    original = auth_module.verify_password
    monkeypatch.setattr(auth_module, "verify_password", lambda password, encoded: calls.append(encoded) or original(password, encoded))
    assert auth_client.post("/api/auth/login", json={"username": "ghost", "password": "whatever-password"}).status_code == 401
    assert calls == [auth_module._DUMMY_PASSWORD_HASH]


def test_login_removes_expired_sessions_and_caps_active_sessions(auth_client, auth_database, test_user) -> None:
    now = datetime.now(timezone.utc)
    with auth_database() as db:
        db.add(AuthSession(token_hash=token_hash("old-expired"), user_id=test_user.id, expires_at=now - timedelta(hours=1)))
        for index in range(12):
            db.add(AuthSession(token_hash=token_hash(f"active-{index}"), user_id=test_user.id, expires_at=now + timedelta(hours=1)))
        db.commit()
    response = auth_client.post("/api/auth/login", json={"username": test_user.username, "password": "correct-horse-battery-staple"})
    assert response.status_code == 200
    with auth_database() as db:
        sessions = db.scalars(select(AuthSession).where(AuthSession.user_id == test_user.id)).all()
        assert len(sessions) == 10
        assert token_hash("old-expired") not in {item.token_hash for item in sessions}
    assert auth_client.get("/api/auth/me").status_code == 200


def test_security_headers_are_present(auth_client) -> None:
    response = auth_client.get("/api/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Permissions-Policy"] == "camera=(self), geolocation=(self), microphone=()"
