"""Upstream failures: bounded read retries, structured 502s with references, no bare 500 pages."""

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.supabase import SupabaseClient, SupabaseError, get_supabase

USER_ID = "11111111-1111-4111-8111-111111111111"


class Flaky:
    """Answers a scripted status for a path the first N times, then a normal body."""

    def __init__(self) -> None:
        self.failures: dict[str, list[int]] = {}
        self.calls: list[tuple[str, str]] = []

    def fail(self, path: str, *statuses: int) -> None:
        self.failures[path] = list(statuses)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        queued = self.failures.get(path)
        if queued:
            status = queued.pop(0)
            if status == 0:
                raise httpx.ReadTimeout("simulated", request=request)
            return httpx.Response(status, json={"message": "Gateway Timeout"})
        if path == "/auth/v1/user":
            return httpx.Response(
                200, json={"id": USER_ID, "aud": "authenticated", "email": "o@example.org"}
            )
        if path == "/rest/v1/business_memberships":
            return httpx.Response(200, json=[{"business_id": "b1", "role": "owner"}])
        if path == "/rest/v1/businesses":
            return httpx.Response(200, json=[{"id": "b1", "name": "Owner Co"}])
        if path == "/rest/v1/workspaces":
            return httpx.Response(200, json=[{"id": "w1"}])
        if path == "/rest/v1/things" and request.method == "POST":
            return httpx.Response(201, json=[{"id": "new"}])
        return httpx.Response(200, json=[])


@pytest.fixture
def flaky(monkeypatch: pytest.MonkeyPatch) -> Flaky:
    import app.supabase as module

    monkeypatch.setattr(module, "RETRY_DELAYS", (0.0, 0.0))
    fake = Flaky()
    app.dependency_overrides[get_supabase] = lambda: SupabaseClient(
        "https://fake.supabase.local", "sb_secret_test", transport=httpx.MockTransport(fake.handler)
    )
    yield fake
    app.dependency_overrides.clear()


def client_for(fake: Flaky) -> SupabaseClient:
    return SupabaseClient(
        "https://fake.supabase.local", "sb_secret_test", transport=httpx.MockTransport(fake.handler)
    )


def test_reads_retry_a_bounded_number_of_times_on_gateway_errors(flaky: Flaky) -> None:
    flaky.fail("/rest/v1/workspaces", 0)  # one transport timeout, then success
    assert client_for(flaky).rest("GET", "workspaces", params={"select": "id"}) == [{"id": "w1"}]
    assert [c for c in flaky.calls if c[1] == "/rest/v1/workspaces"] == [
        ("GET", "/rest/v1/workspaces")
    ] * 2
    flaky.fail("/rest/v1/workspaces", 504, 504)
    with pytest.raises(SupabaseError) as excinfo:
        client_for(flaky).rest("GET", "workspaces", params={"select": "id"})
    assert excinfo.value.transient and excinfo.value.status == 504


def test_writes_are_never_retried(flaky: Flaky) -> None:
    flaky.fail("/rest/v1/things", 504)
    with pytest.raises(SupabaseError) as excinfo:
        client_for(flaky).rest("POST", "things", json_body={"x": 1})
    assert excinfo.value.transient
    assert flaky.calls.count(("POST", "/rest/v1/things")) == 1


def test_upstream_timeout_answers_502_with_a_reference_not_a_bare_500(flaky: Flaky) -> None:
    flaky.fail("/rest/v1/workspaces", 504, 504)
    with TestClient(app) as client:
        response = client.get("/api/me", headers={"authorization": "Bearer t"})
    assert response.status_code == 502
    body = response.json()
    assert body["retryable"] is True and "try again" in body["detail"]
    assert body["reference"] and response.headers["x-request-id"] == body["reference"]
    assert response.headers["cache-control"] == "no-store"


def test_platform_request_id_becomes_the_reference(flaky: Flaky) -> None:
    flaky.fail("/rest/v1/workspaces", 503, 503)
    with TestClient(app) as client:
        response = client.get(
            "/api/me",
            headers={"authorization": "Bearer t", "x-vercel-id": "sfo1::iad1::abc123-1789-def"},
        )
    assert response.status_code == 502 and response.json()["reference"] == "abc123-1789-def"


def test_unexpected_errors_answer_json_500_with_a_reference(
    flaky: Flaky, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.private as private

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated")

    monkeypatch.setattr(private, "_memberships", boom)  # the lookup /api/me actually calls
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/me", headers={"authorization": "Bearer t"})
    assert response.status_code == 500
    body = json.loads(response.content)
    assert body["reference"] and "nothing was changed" in body["detail"]
    assert "simulated" not in response.text  # exception text never reaches the client
