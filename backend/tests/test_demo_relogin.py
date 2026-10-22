"""DEMO_FORCE_RELOGIN: the demo start deletes sessions (only sessions); a normal start leaves them alone."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.auth import hash_password, token_hash
from backend.app.core import startup
from backend.app.core.config import get_settings
from backend.app.models import AuthSession, Base, User


@pytest.fixture
def seeded(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(startup, "SessionLocal", factory)
    with factory() as db:
        user = User(username="demo_maker", full_name="Demo Maker", email="maker@example.com", password_hash=hash_password("Correct-Horse-42"), role="MANUFACTURER")
        db.add(user); db.commit()
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        db.add_all([AuthSession(token_hash=token_hash(t), user_id=user.id, expires_at=expires) for t in ("a", "b")]); db.commit()
    get_settings.cache_clear()
    yield factory
    get_settings.cache_clear()
    engine.dispose()


def counts(factory):
    with factory() as db:
        return len(db.scalars(select(User)).all()), len(db.scalars(select(AuthSession)).all())


def test_sessions_are_kept_without_the_variable(seeded, monkeypatch) -> None:
    monkeypatch.delenv("DEMO_FORCE_RELOGIN", raising=False)
    assert startup.invalidate_sessions_if_requested() == 0
    assert counts(seeded) == (1, 2)


def test_only_sessions_are_removed_when_the_variable_is_set(seeded, monkeypatch) -> None:
    monkeypatch.setenv("DEMO_FORCE_RELOGIN", "1")
    assert startup.invalidate_sessions_if_requested() == 2
    assert counts(seeded) == (1, 0)
