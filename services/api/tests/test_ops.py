import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

TOKEN = "test-operator-token"
AUTH = {"x-ops-token": TOKEN}
OPS_GET = (
    "/api/ops/config",
    "/api/ops/echo",
    "/api/ops/stream",
    "/api/ops/cookie",
    "/api/ops/redirect",
)


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_DIAGNOSTICS_ENABLED", "true")
    monkeypatch.setenv("OPS_DIAGNOSTICS_TOKEN", TOKEN)
    get_settings.cache_clear()


def test_ops_routes_do_not_exist_when_disabled() -> None:
    with TestClient(app) as client:
        for path in OPS_GET:
            assert client.get(path, headers=AUTH).status_code == 404, path
        assert client.post("/api/ops/db-probe", headers=AUTH).status_code == 404
        assert client.post("/api/ops/openai-probe", headers=AUTH, json={}).status_code == 404


def test_missing_or_wrong_token_is_unauthorized(enabled: None) -> None:
    with TestClient(app) as client:
        assert client.get("/api/ops/config").status_code == 401
        assert client.get("/api/ops/config", headers={"x-ops-token": "wrong"}).status_code == 401
        assert client.post("/api/ops/db-probe", headers={"x-ops-token": "wrong"}).status_code == 401


def test_config_reports_presence_never_values(
    enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    get_settings.cache_clear()
    with TestClient(app) as client:
        response = client.get("/api/ops/config", headers=AUTH)
    body = response.json()
    assert body["configured"]["openai_api_key"] is True
    assert body["configured"]["openai_model"] is True
    assert body["configured"]["supabase_url"] is False
    assert "sk-test" not in response.text and "test-model" not in response.text


def test_probes_fail_clearly_when_integrations_are_not_configured(enabled: None) -> None:
    with TestClient(app) as client:
        assert client.post("/api/ops/db-probe", headers=AUTH).status_code == 503
        assert client.post("/api/ops/openai-probe", headers=AUTH, json={}).status_code == 503


def test_echo_hashes_headers_instead_of_returning_them(enabled: None) -> None:
    with TestClient(app) as client:
        response = client.get(
            "/api/ops/echo", headers={**AUTH, "x-vercel-protection-bypass": "super-secret-value"}
        )
    body = response.json()
    assert body["bypass_header_sha256_prefix"]
    assert body["ops_token_present"] is True
    assert "super-secret-value" not in response.text and TOKEN not in response.text


def test_cookie_round_trip_uses_httponly_cookie(enabled: None) -> None:
    with TestClient(app) as client:
        first = client.get("/api/ops/cookie", headers=AUTH)
        assert first.json()["round_trip"] is False
        assert "httponly" in first.headers["set-cookie"].lower()
        second = client.get("/api/ops/cookie", headers=AUTH)
        assert second.json()["round_trip"] is True
        assert second.json()["value_sha256_prefix"] == first.json()["value_sha256_prefix"]


def test_stream_delivers_five_chunks(enabled: None) -> None:
    with (
        TestClient(app) as client,
        client.stream("GET", "/api/ops/stream", headers=AUTH) as response,
    ):
        lines = [line for line in response.iter_lines() if line]
    assert len(lines) == 5
    assert lines[0].startswith("chunk 1 at ") and lines[-1].startswith("chunk 5 at ")


def test_redirect_is_returned_not_followed(enabled: None) -> None:
    with TestClient(app) as client:
        response = client.get("/api/ops/redirect", headers=AUTH, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/"
