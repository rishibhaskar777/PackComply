"""LAN URL endpoint (the "Open on phone" QR) and the one-line startup summary."""
import logging

import pytest
from fastapi.testclient import TestClient

from backend.app.api.auth import get_current_user
from backend.app.core import network
from backend.app.core.config import get_settings
from backend.app.core.startup import log_startup_summary, startup_summary
from backend.app.main import app


@pytest.fixture(autouse=True)
def fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_configured_lan_ip_wins_and_builds_the_phone_url(monkeypatch) -> None:
    monkeypatch.setenv("LAN_IP", "192.168.1.20")
    assert network.detect_lan_ip() == "192.168.1.20"
    assert network.phone_url("https", 5173) == "https://192.168.1.20:5173/#/capture"
    assert network.phone_url("ftp", None) == "https://192.168.1.20:5173/#/capture"  # only http/https are believed


def test_unusable_addresses_are_rejected(monkeypatch) -> None:
    for bad in ("127.0.0.1", "169.254.10.4", "8.8.8.8", "::1", "not-an-ip"):
        monkeypatch.setenv("LAN_IP", bad)
        get_settings.cache_clear()
        assert network.detect_lan_ip() is None and network.phone_url() is None


def test_phone_url_override(monkeypatch) -> None:
    monkeypatch.setenv("PHONE_URL", "https://demo.local:5173/#/capture")
    assert network.phone_url("http", 9999) == "https://demo.local:5173/#/capture"


def test_network_endpoint_uses_the_proxy_headers(monkeypatch) -> None:
    monkeypatch.setenv("LAN_IP", "10.0.0.7")
    client = TestClient(app)
    body = client.get("/api/system/network", headers={"x-forwarded-proto": "http", "x-forwarded-port": "4000"}).json()
    assert body == {"lan_ip": "10.0.0.7", "phone_url": "http://10.0.0.7:4000/#/capture"}


def test_network_endpoint_requires_sign_in() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    assert TestClient(app).get("/api/system/network").status_code == 401


def test_startup_summary_is_one_line_with_every_fact(monkeypatch) -> None:
    monkeypatch.setenv("LAN_IP", "192.168.1.20")
    line = startup_summary(12.34)
    assert "\n" not in line
    for part in ("DB ", "ruleset LM_PC_2011", "(14 rules)", "OCR en+hi", "warm-up 12.3 s", "LAN https://192.168.1.20:5173"):
        assert part in line, line


def test_startup_summary_survives_an_unreachable_database(monkeypatch, caplog) -> None:
    import backend.app.core.startup as startup

    class Broken:
        def __enter__(self): raise RuntimeError("down")
        def __exit__(self, *args): return False

    monkeypatch.setattr(startup, "SessionLocal", Broken)
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        log_startup_summary()
    assert any("DB UNREACHABLE" in record.getMessage() for record in caplog.records)


# ------------------------------------------------------------ evaluation report endpoint

def test_evaluation_endpoint_serves_the_report_file(monkeypatch, tmp_path) -> None:
    report = tmp_path / "report.json"
    report.write_text('{"schema": 1, "runs": [{"label": "unseen-test", "kind": "unseen"}]}', encoding="utf-8")
    monkeypatch.setenv("EVALUATION_REPORT_PATH", str(report))
    assert TestClient(app).get("/api/system/evaluation").json()["runs"][0]["label"] == "unseen-test"


def test_evaluation_endpoint_without_or_with_a_broken_file_reports_no_runs(monkeypatch, tmp_path) -> None:
    client = TestClient(app)
    monkeypatch.setenv("EVALUATION_REPORT_PATH", str(tmp_path / "missing.json"))
    assert client.get("/api/system/evaluation").json() == {"schema": 1, "runs": []}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("EVALUATION_REPORT_PATH", str(broken))
    get_settings.cache_clear()
    assert client.get("/api/system/evaluation").json() == {"schema": 1, "runs": []}


def test_the_committed_report_is_valid_and_labelled() -> None:
    from backend.app.core.config import Settings

    report = __import__("json").loads(Settings().evaluation_report_path.read_text(encoding="utf-8"))
    run = report["runs"][0]
    assert run["kind"] in ("development", "unseen") and run["label"] and run["generated_at"]
    assert run["groups"] and all(group["photos"] > 0 for group in run["groups"])
