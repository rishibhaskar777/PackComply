from fastapi.testclient import TestClient

from backend.app.main import app


def test_health_endpoint_reports_api_availability() -> None:
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "packcomply-api"}
