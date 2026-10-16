"""GET /api/system/status: public, cached, and honest about each subsystem."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from backend.app import main as main_module
from backend.app.api.auth import get_current_user
from backend.app.api.inspections import get_ocr_service
from backend.app.api.system import status_cache
from backend.app.core.database import get_db
from backend.app.core.ocr_status import get_ocr_state, set_ocr_state
from backend.app.main import app
from backend.app.services.compliance.ruleset import default_ruleset


class _Database:
    def __init__(self, up: bool = True) -> None:
        self.up = up
        self.queries = 0

    def execute(self, *_args, **_kwargs):
        self.queries += 1
        if not self.up:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))


@pytest.fixture
def database():
    db = _Database()
    app.dependency_overrides[get_db] = lambda: db
    return db


@pytest.fixture
def client(database):
    return TestClient(app)


def test_ok_when_database_and_ocr_are_ready(client, database) -> None:
    set_ocr_state("ready")
    response = client.get("/api/system/status")
    assert response.status_code == 200
    body = response.json()
    ruleset = default_ruleset()
    assert body == {"status": "ok", "database": True, "ocr": "ready", "ruleset_version": ruleset.version, "rule_count": len(ruleset.enabled_rules), "languages": ["en", "hi"]}
    assert body["rule_count"] == 14


def test_needs_no_authentication(client) -> None:
    # The suite installs a test principal globally; remove it so this request carries no session.
    app.dependency_overrides.pop(get_current_user, None)
    assert client.get("/api/system/status").status_code == 200


def test_warming_up_is_still_ok(client) -> None:
    set_ocr_state("warming_up")
    body = client.get("/api/system/status").json()
    assert body["status"] == "ok" and body["ocr"] == "warming_up"


def test_degraded_when_ocr_is_unavailable(client) -> None:
    set_ocr_state("unavailable")
    body = client.get("/api/system/status").json()
    assert body["status"] == "degraded" and body["database"] is True and body["ocr"] == "unavailable"


def test_down_when_the_database_is_unreachable(client, database) -> None:
    database.up = False
    set_ocr_state("ready")
    response = client.get("/api/system/status")
    assert response.status_code == 200
    assert response.json()["status"] == "down" and response.json()["database"] is False


def test_status_is_cached_briefly(client, database) -> None:
    set_ocr_state("ready")
    assert client.get("/api/system/status").json()["status"] == "ok"
    database.up = False
    # Within the cache window the database is not queried again.
    assert client.get("/api/system/status").json()["status"] == "ok"
    assert database.queries == 1
    status_cache.clear()
    assert client.get("/api/system/status").json()["status"] == "down"


def test_exposes_nothing_beyond_the_contract(client) -> None:
    assert set(client.get("/api/system/status").json()) == {"status", "database", "ocr", "ruleset_version", "rule_count", "languages"}


def test_without_a_warm_up_the_state_reflects_whether_the_engine_is_installed(monkeypatch) -> None:
    from backend.app.core import ocr_status
    monkeypatch.setattr(ocr_status, "find_spec", lambda name: object())
    assert get_ocr_state() == "ready"
    monkeypatch.setattr(ocr_status, "find_spec", lambda name: None)
    assert get_ocr_state() == "unavailable"


def test_warm_up_records_ready_and_unavailable() -> None:
    class Warm:
        def warm(self) -> None: assert get_ocr_state() == "warming_up"

    class Broken:
        def warm(self) -> None: raise RuntimeError("models missing")

    app.dependency_overrides[get_ocr_service] = Warm
    main_module._warm_ocr_models()
    assert get_ocr_state() == "ready"
    app.dependency_overrides[get_ocr_service] = Broken
    main_module._warm_ocr_models()
    assert get_ocr_state() == "unavailable"
