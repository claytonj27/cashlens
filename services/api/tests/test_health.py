from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import SERVICE_NAME, SERVICE_VERSION, app


def test_health_reports_process_status_with_expected_schema() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    # Exact key set: no database, storage or model fields may be implied.
    assert set(body) == {"status", "service", "version", "environment", "time"}
    assert body["status"] == "ok"
    assert body["service"] == SERVICE_NAME
    assert body["version"] == SERVICE_VERSION
    assert body["environment"] == get_settings().app_env
    reported = datetime.fromisoformat(body["time"])
    assert reported.tzinfo is not None
    assert abs((datetime.now(UTC) - reported).total_seconds()) < 60


def test_unknown_api_route_is_not_found() -> None:
    with TestClient(app) as client:
        assert client.get("/api/does-not-exist").status_code == 404


def test_docs_and_schema_are_served_outside_production_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The interactive docs and the OpenAPI schema are development conveniences; a production
    deployment answers 404 for all three, because the schema lists every route."""
    with TestClient(app) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 200, path
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            for path in ("/docs", "/redoc", "/openapi.json"):
                assert client.get(path).status_code == 404, path
            assert client.get("/api/health").status_code == 200
    finally:
        get_settings.cache_clear()
