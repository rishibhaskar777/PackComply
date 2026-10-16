"""Offline password reset: enumeration safety, throttling, one-time codes, and the admin CLI."""
from datetime import datetime, timedelta, timezone
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api import auth as auth_module
from backend.app.api.auth import INVALID_RESET_CODE_DETAIL, RESET_REQUESTED_DETAIL, get_current_user
from backend.app.core.config import get_settings
from backend.app.core.database import get_db
from backend.app.core.security import hash_password, verify_password
from backend.app.main import app
from backend.app.models import AuthSession, Base, PasswordResetCode, User
from backend.app.services import password_reset
from backend.scripts import reset_password as reset_cli

PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "a-brand-new-passphrase"
CODE = "482913"


@pytest.fixture
def sessions(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        with factory() as session:
            yield session

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides[get_db] = override_db
    # Deterministic codes so tests can redeem them; production uses secrets.randbelow.
    monkeypatch.setattr(password_reset, "generate_code", lambda: CODE)
    yield factory
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def client(sessions):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def user(sessions) -> User:
    with sessions() as db:
        account = User(email="inspector@example.invalid", username="inspector", full_name="Inspector", password_hash=hash_password(PASSWORD), is_active=True, role="OFFICIAL")
        db.add(account); db.commit()
        return account


def request_reset(client, username="inspector"):
    return client.post("/api/auth/password-reset/request", json={"username": username})


def confirm(client, code=CODE, password=NEW_PASSWORD, username="inspector"):
    return client.post("/api/auth/password-reset/confirm", json={"username": username, "code": code, "new_password": password})


def codes(sessions) -> list[PasswordResetCode]:
    with sessions() as db:
        return list(db.scalars(select(PasswordResetCode).order_by(PasswordResetCode.created_at)))


def test_request_answers_identically_for_real_and_unknown_usernames(client, user, monkeypatch) -> None:
    hashes: list[str] = []
    original = auth_module.hash_password
    monkeypatch.setattr(auth_module, "hash_password", lambda value, salt=None: hashes.append("dummy") or original(value, salt))
    issued: list[str] = []
    original_issue = password_reset.issue_code
    monkeypatch.setattr(password_reset, "issue_code", lambda db, account, ip=None: issued.append("real") or original_issue(db, account, ip))

    real = request_reset(client, "inspector")
    unknown = request_reset(client, "nobody-here")
    assert real.status_code == unknown.status_code == 202
    assert real.json() == unknown.json() == {"detail": RESET_REQUESTED_DETAIL}
    # The unknown username still pays for one scrypt hash, like storing a real code.
    assert hashes == ["dummy"] and issued == ["real"]


def test_stores_only_a_hash_of_the_code(client, user, sessions) -> None:
    request_reset(client)
    [stored] = codes(sessions)
    assert stored.code_hash.startswith("scrypt$") and CODE not in stored.code_hash
    assert verify_password(CODE, stored.code_hash)
    assert stored.used_at is None and stored.attempts == 0 and stored.requested_ip
    assert timedelta(minutes=14) < stored.expires_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc) <= timedelta(minutes=15)


def test_requests_are_rate_limited_per_username_even_when_unknown(client) -> None:
    for _ in range(3):
        assert request_reset(client, "ghost").status_code == 202
    limited = request_reset(client, "ghost")
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0


def test_requests_are_rate_limited_per_ip(client) -> None:
    for name in ("one", "two", "three"):
        assert request_reset(client, name).status_code == 202
    assert request_reset(client, "four").status_code == 429


def test_a_new_request_invalidates_earlier_codes(client, user, sessions, monkeypatch) -> None:
    request_reset(client)
    monkeypatch.setattr(password_reset, "generate_code", lambda: "111222")
    request_reset(client)
    first, second = codes(sessions)
    assert first.used_at is not None and second.used_at is None
    assert confirm(client, code=CODE).status_code == 400
    assert confirm(client, code="111222").status_code == 200


def test_confirm_sets_the_password_and_the_old_one_stops_working(client, user) -> None:
    request_reset(client)
    response = confirm(client)
    assert response.status_code == 200
    assert client.post("/api/auth/login", json={"username": "inspector", "password": PASSWORD}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "inspector", "password": NEW_PASSWORD}).status_code == 200


def test_a_code_works_only_once(client, user) -> None:
    request_reset(client)
    assert confirm(client).status_code == 200
    again = confirm(client, password="yet-another-passphrase")
    assert again.status_code == 400 and again.json() == {"detail": INVALID_RESET_CODE_DETAIL}


def test_expired_codes_are_refused(client, user, sessions) -> None:
    request_reset(client)
    with sessions() as db:
        stored = db.scalar(select(PasswordResetCode))
        stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    response = confirm(client)
    assert response.status_code == 400 and response.json() == {"detail": INVALID_RESET_CODE_DETAIL}


def test_five_wrong_guesses_burn_the_code(client, user, sessions) -> None:
    request_reset(client)
    for _ in range(5):
        assert confirm(client, code="000000").json() == {"detail": INVALID_RESET_CODE_DETAIL}
    [stored] = codes(sessions)
    assert stored.attempts == 5 and stored.used_at is not None
    # Even the right code no longer works.
    assert confirm(client).status_code == 400


def test_every_failure_has_the_same_generic_answer(client, user) -> None:
    request_reset(client)
    answers = [confirm(client, code="999999"), confirm(client, username="nobody-here"), confirm(client, code="abc")]
    assert {response.status_code for response in answers} == {400}
    assert all(response.json() == {"detail": INVALID_RESET_CODE_DETAIL} for response in answers)


@pytest.mark.parametrize("password,reason", [("short", "at least 8"), ("inspector", "same as the username"), ("Password1", "too common"), ("12345678", "too common")])
def test_new_password_follows_the_policy(client, user, sessions, password, reason) -> None:
    request_reset(client)
    response = confirm(client, password=password)
    assert response.status_code == 422 and reason in response.json()["detail"]
    # A policy error does not use up a guess.
    assert [stored.attempts for stored in codes(sessions)] == [0]


def test_reset_signs_out_every_session(client, user, sessions) -> None:
    assert client.post("/api/auth/login", json={"username": "inspector", "password": PASSWORD}).status_code == 200
    with TestClient(app) as other_device:
        assert other_device.post("/api/auth/login", json={"username": "inspector", "password": PASSWORD}).status_code == 200
        assert other_device.get("/api/auth/me").status_code == 200
        request_reset(client)
        assert confirm(client).status_code == 200
        assert other_device.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    with sessions() as db:
        assert db.scalar(select(AuthSession)) is None


def test_reset_clears_the_login_lockout(client, user) -> None:
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "inspector", "password": "wrong-password"})
    assert client.post("/api/auth/login", json={"username": "inspector", "password": PASSWORD}).status_code == 429
    request_reset(client)
    assert confirm(client).status_code == 200
    assert client.post("/api/auth/login", json={"username": "inspector", "password": NEW_PASSWORD}).status_code == 200


def test_smtp_delivery_when_configured(client, user, monkeypatch) -> None:
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent.append(("connect", host, port))
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def starttls(self): sent.append(("starttls",))
        def login(self, username, password): sent.append(("login", username))
        def send_message(self, message): sent.append(("send", message["To"], message.get_content()))

    settings = get_settings()
    for name, value in {"smtp_host": "mail.example.invalid", "smtp_port": 2525, "smtp_username": "mailer", "smtp_password": "secret", "smtp_from": "noreply@example.invalid"}.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(password_reset.smtplib, "SMTP", FakeSMTP)
    response = request_reset(client)
    assert response.status_code == 202 and response.json() == {"detail": RESET_REQUESTED_DETAIL}
    assert sent[0] == ("connect", "mail.example.invalid", 2525)
    assert ("starttls",) in sent and ("login", "mailer") in sent
    to, body = sent[-1][1], sent[-1][2]
    assert to == "inspector@example.invalid" and CODE in body


def test_smtp_is_off_by_default(client, user, monkeypatch) -> None:
    def refuse(*_args, **_kwargs): raise AssertionError("SMTP must not be used without SMTP_HOST")
    monkeypatch.setattr(password_reset.smtplib, "SMTP", refuse)
    assert request_reset(client).status_code == 202


def test_the_code_never_appears_in_logs(client, user, monkeypatch, caplog) -> None:
    class FailingSMTP:
        def __init__(self, *_args, **_kwargs): raise OSError(f"cannot connect (would have sent {CODE})")

    monkeypatch.setattr(get_settings(), "smtp_host", "mail.example.invalid")
    monkeypatch.setattr(password_reset.smtplib, "SMTP", FailingSMTP)
    with caplog.at_level(logging.DEBUG):
        request_reset(client)
        for _ in range(5):
            confirm(client, code="000000")
        request_reset(client)
        confirm(client)
    assert "Password reset requested" in caplog.text
    assert CODE not in caplog.text


def test_cli_issues_a_code_that_redeems(client, user, sessions, capsys) -> None:
    assert reset_cli.main(["--username", "inspector"], session_factory=sessions) == 0
    output = capsys.readouterr().out
    assert f"Reset code for inspector: {CODE}" in output
    assert confirm(client).status_code == 200


def test_cli_sets_a_password_directly_and_signs_out(client, user, sessions, capsys) -> None:
    assert client.post("/api/auth/login", json={"username": "inspector", "password": PASSWORD}).status_code == 200
    answers = iter([NEW_PASSWORD, NEW_PASSWORD])
    assert reset_cli.main(["--username", "inspector", "--set-password"], session_factory=sessions, prompt=lambda _: next(answers)) == 0
    assert "Password updated for inspector. 1 session(s) signed out." in capsys.readouterr().out
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"username": "inspector", "password": NEW_PASSWORD}).status_code == 200


def test_cli_refuses_mismatched_or_weak_passwords_and_unknown_users(user, sessions) -> None:
    answers = iter(["first-passphrase", "second-passphrase"])
    with pytest.raises(SystemExit, match="do not match"):
        reset_cli.main(["--username", "inspector", "--set-password"], session_factory=sessions, prompt=lambda _: next(answers))
    with pytest.raises(SystemExit, match="too common"):
        reset_cli.main(["--username", "inspector", "--set-password"], session_factory=sessions, prompt=lambda _: "password1")
    with pytest.raises(SystemExit, match="No account"):
        reset_cli.main(["--username", "nobody-here"], session_factory=sessions)


def test_keep_me_signed_in_extends_the_session(client, user, sessions) -> None:
    short = client.post("/api/auth/login", json={"username": "inspector", "password": PASSWORD})
    assert f"Max-Age={12 * 3600}" in short.headers["set-cookie"]
    long = client.post("/api/auth/login", json={"username": "inspector", "password": PASSWORD, "remember": True})
    assert f"Max-Age={30 * 86400}" in long.headers["set-cookie"]
    with sessions() as db:
        latest = db.scalars(select(AuthSession).order_by(AuthSession.created_at.desc())).first()
        assert latest.expires_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc) > timedelta(days=29)


def test_registration_applies_the_password_policy(client) -> None:
    response = client.post("/api/auth/register", json={"username": "new-maker", "email": "maker@example.invalid", "password": "password1"})
    assert response.status_code == 422 and "too common" in response.json()["detail"]
    response = client.post("/api/auth/register", json={"username": "new-maker", "email": "maker@example.invalid", "password": "new-maker"})
    assert response.status_code == 422
