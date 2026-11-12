"""Demo sessions and private provisioning against a fake Supabase (no network)."""

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.supabase import SupabaseClient, get_supabase

GOOD_TOKEN = "good-token"
USER_ID = "11111111-1111-4111-8111-111111111111"
CLIENT = {"x-cashlens-client": "web"}
DEMO = {"id": "b1", "slug": "juniper-kitchen", "name": "Juniper Kitchen", "industry": "restaurant"}


class FakeSupabase:
    """Routes the handful of Data API and Auth calls the routers make."""

    def __init__(self) -> None:
        self.visitors: set[str] = set()
        self.memberships: list[dict[str, Any]] = []
        self.workspaces: list[dict[str, Any]] = []
        self.provision_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        if path == "/auth/v1/user":
            token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if token == GOOD_TOKEN:
                return httpx.Response(
                    200, json={"id": USER_ID, "aud": "authenticated", "email": "owner@example.com"}
                )
            return httpx.Response(401, json={"message": "invalid token"})
        if path == "/rest/v1/businesses":
            if params.get("data_mode") == "eq.demo" and (
                params.get("slug") in (None, "eq.juniper-kitchen")
            ):
                return httpx.Response(200, json=[DEMO])
            if params.get("data_mode") == "eq.private":
                return httpx.Response(
                    200, json=[{"id": "p1", "name": "Owner Co"}] if self.memberships else []
                )
            return httpx.Response(200, json=[])
        if path == "/rest/v1/visitors":
            if request.method == "POST":
                self.visitors.add(json.loads(request.content)["id"])
                return httpx.Response(201)
            wanted = params.get("id", "").removeprefix("eq.")
            return httpx.Response(200, json=[{"id": wanted}] if wanted in self.visitors else [])
        if path == "/rest/v1/rpc/ensure_demo_workspace":
            body = json.loads(request.content)
            if body["p_slug"] != "juniper-kitchen":
                return httpx.Response(400, json={"message": "unknown demo business"})
            self.workspaces.append(
                {"id": "w-demo", "visitor_id": body["p_visitor_id"], "business_id": "b1"}
            )
            return httpx.Response(
                200,
                json=[
                    {
                        "workspace_id": "w-demo",
                        "business_id": "b1",
                        **{k: DEMO[k] for k in ("slug", "name", "industry")},
                    }
                ],
            )
        if path == "/rest/v1/workspaces":
            visitor = params.get("visitor_id", "").removeprefix("eq.")
            user = params.get("user_id", "").removeprefix("eq.")
            rows = [
                w
                for w in self.workspaces
                if w.get("visitor_id") == visitor or w.get("user_id") == user
            ]
            return httpx.Response(
                200, json=[{"id": w["id"], "business_id": w["business_id"]} for w in rows]
            )
        if path == "/rest/v1/rpc/provision_private_business":
            body = json.loads(request.content)
            self.provision_calls += 1
            if not self.memberships:
                if not (body.get("p_business_name") or "").strip():
                    return httpx.Response(
                        400, json={"message": "business name required (1 to 120 characters)"}
                    )
                self.memberships.append({"business_id": "p1", "role": "owner"})
                self.workspaces.append({"id": "w-private", "user_id": USER_ID, "business_id": "p1"})
                return httpx.Response(
                    200,
                    json=[
                        {
                            "business_id": "p1",
                            "workspace_id": "w-private",
                            "business_name": body["p_business_name"],
                            "created": True,
                        }
                    ],
                )
            return httpx.Response(
                200,
                json=[
                    {
                        "business_id": "p1",
                        "workspace_id": "w-private",
                        "business_name": "Owner Co",
                        "created": False,
                    }
                ],
            )
        if path == "/rest/v1/business_memberships":
            return httpx.Response(200, json=self.memberships)
        if path in (
            "/rest/v1/accounts",
            "/rest/v1/balance_snapshots",
            "/rest/v1/transactions",
            "/rest/v1/document_imports",
        ):
            if request.headers.get("prefer") == "count=exact":
                return httpx.Response(200, json=[], headers={"content-range": "0-0/0"})
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": f"unhandled {request.method} {path}"})


@pytest.fixture
def fake() -> FakeSupabase:
    fake = FakeSupabase()
    client = SupabaseClient(
        "https://fake.supabase.local", "sb_secret_test", transport=httpx.MockTransport(fake.handler)
    )
    app.dependency_overrides[get_supabase] = lambda: client
    yield fake
    app.dependency_overrides.clear()


def test_supabase_unconfigured_answers_503() -> None:
    with TestClient(app) as client:
        assert client.get("/api/demo/businesses").status_code == 503


def test_production_is_a_health_only_scaffold_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """No feature switch and no Supabase: health works, every product route is 404."""
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    auth = {"authorization": f"Bearer {GOOD_TOKEN}", **CLIENT}
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/demo/businesses").status_code == 404
        assert client.post("/api/demo/session", headers=CLIENT).status_code == 404
        assert client.get("/api/demo/workspaces/juniper-kitchen").status_code == 404
        assert client.get("/api/me", headers=auth).status_code == 404
        assert client.get("/api/private/workspace", headers=auth).status_code == 404
        provision = client.post("/api/private/provision", headers=auth, json={"business_name": "X"})
        assert provision.status_code == 404


def test_feature_switch_enables_product_routes_in_production(
    monkeypatch: pytest.MonkeyPatch, fake: FakeSupabase
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("FEATURES_ENABLED", "true")
    get_settings.cache_clear()
    with TestClient(app) as client:
        assert client.get("/api/demo/businesses").status_code == 200


def test_feature_switch_disables_product_routes_anywhere(
    monkeypatch: pytest.MonkeyPatch, fake: FakeSupabase
) -> None:
    monkeypatch.setenv("FEATURES_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/demo/businesses").status_code == 404


def test_demo_session_sets_httponly_cookie_and_lists_businesses(fake: FakeSupabase) -> None:
    with TestClient(app) as client:
        assert client.post("/api/demo/session").status_code == 403  # no client header: CSRF guard
        response = client.post("/api/demo/session", headers=CLIENT)
        assert response.status_code == 200
        assert response.json()["businesses"][0]["slug"] == "juniper-kitchen"
        cookie = response.headers["set-cookie"].lower()
        assert (
            "cl_visitor=" in cookie
            and "httponly" in cookie
            and "samesite=lax" in cookie
            and "path=/api" in cookie
        )
        assert response.headers["cache-control"] == "no-store"
        again = client.post("/api/demo/session", headers=CLIENT)
        assert "set-cookie" not in again.headers  # resumed, not reissued


def test_demo_workspace_requires_known_visitor_cookie(fake: FakeSupabase) -> None:
    with TestClient(app) as client:
        assert client.get("/api/demo/workspaces/juniper-kitchen").status_code == 401
        client.cookies.set(
            "cl_visitor", "22222222-2222-4222-8222-222222222222"
        )  # well formed but unknown
        assert client.get("/api/demo/workspaces/juniper-kitchen").status_code == 401
        client.cookies.clear()
        client.post("/api/demo/session", headers=CLIENT)
        assert (
            client.get("/api/demo/workspaces/juniper-kitchen").status_code == 404
        )  # not opened yet
        opened = client.post("/api/demo/workspaces/juniper-kitchen", headers=CLIENT)
        assert opened.status_code == 200 and opened.json()["workspace_id"] == "w-demo"
        summary = client.get("/api/demo/workspaces/juniper-kitchen")
        assert (
            summary.status_code == 200 and summary.json()["business"]["name"] == "Juniper Kitchen"
        )
        assert client.post("/api/demo/workspaces/nope", headers=CLIENT).status_code == 404


def test_private_routes_require_a_valid_access_token(fake: FakeSupabase) -> None:
    with TestClient(app) as client:
        assert client.get("/api/me").status_code == 401
        assert (
            client.get("/api/me", headers={"authorization": "Bearer bad-token"}).status_code == 401
        )
        assert (
            client.get(
                "/api/private/workspace", headers={"authorization": "Bearer bad-token"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/private/provision", headers={"authorization": f"Bearer {GOOD_TOKEN}"}, json={}
            ).status_code
            == 403
        )


def test_provisioning_is_idempotent_and_needs_a_name_first(fake: FakeSupabase) -> None:
    auth = {"authorization": f"Bearer {GOOD_TOKEN}", **CLIENT}
    with TestClient(app) as client:
        me = client.get("/api/me", headers=auth).json()
        assert me["business"] is None and me["email"] == "owner@example.com"
        assert client.get("/api/private/workspace", headers=auth).status_code == 404
        assert client.post("/api/private/provision", headers=auth, json={}).status_code == 422
        first = client.post(
            "/api/private/provision", headers=auth, json={"business_name": "Owner Co"}
        )
        assert first.status_code == 200 and first.json()["created"] is True
        second = client.post(
            "/api/private/provision", headers=auth, json={"business_name": "Other name"}
        )
        assert second.status_code == 200 and second.json()["created"] is False
        assert second.json()["business"]["name"] == "Owner Co"
        workspace = client.get("/api/private/workspace", headers=auth).json()
        assert workspace["transaction_count"] == 0
        assert workspace["statements"]["uploads_available"] is True
        assert workspace["statements"]["count"] == 0
        assert fake.provision_calls == 3


def test_token_validation_is_cached_per_token(fake: FakeSupabase) -> None:
    calls = {"n": 0}
    original = fake.handler

    def counting(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/v1/user":
            calls["n"] += 1
        return original(request)

    client_obj = SupabaseClient(
        "https://fake.supabase.local", "sb_secret_test", transport=httpx.MockTransport(counting)
    )
    app.dependency_overrides[get_supabase] = lambda: client_obj
    with TestClient(app) as client:
        for _ in range(3):
            client.get("/api/me", headers={"authorization": f"Bearer {GOOD_TOKEN}"})
    assert calls["n"] == 1


def test_no_cors_so_cross_origin_browsers_cannot_send_the_client_header(fake: FakeSupabase) -> None:
    """A cross-origin page needs preflight approval to send x-cashlens-client; none is granted."""
    with TestClient(app) as client:
        preflight = client.options(
            "/api/demo/session",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-cashlens-client",
            },
        )
        assert "access-control-allow-origin" not in {k.lower() for k in preflight.headers}
        assert preflight.status_code in (400, 404, 405)
        # a plain POST with a foreign Origin and no preflight-approved header is refused
        assert (
            client.post("/api/demo/session", headers={"Origin": "https://evil.example"}).status_code
            == 403
        )


def test_cross_site_form_posts_cannot_mutate_sessions_or_workspaces(fake: FakeSupabase) -> None:
    """Ordinary HTML forms can only send form encodings and no custom headers."""
    with TestClient(app) as client:
        started = client.post("/api/demo/session", headers=CLIENT)
        assert started.status_code == 200
        form = {
            "content-type": "application/x-www-form-urlencoded",
            "Origin": "https://evil.example",
        }
        assert (
            client.post(
                "/api/demo/workspaces/juniper-kitchen", data={"x": "1"}, headers=form
            ).status_code
            == 403
        )
        assert client.post("/api/demo/session", data={"x": "1"}, headers=form).status_code == 403
        assert (
            client.post(
                "/api/private/provision",
                data={"business_name": "x"},
                headers={**form, "authorization": f"Bearer {GOOD_TOKEN}"},
            ).status_code
            == 403
        )
        # the visitor cookie alone never authorizes a mutation
        assert (
            client.get("/api/demo/workspaces/juniper-kitchen").status_code == 404
        )  # opened? no: refused above


def test_a_demo_slug_that_cannot_be_a_slug_is_refused_by_shape(fake: FakeSupabase) -> None:
    """The path value is checked against the schema's own slug rule before any query: anything
    with capitals, spaces, dots, slashes or operator characters is 404 without being looked up."""
    with TestClient(app) as client:
        client.post("/api/demo/session", headers=CLIENT)
        assert client.get("/api/demo/workspaces/juniper-kitchen").status_code in (200, 404)
        for bad in (
            "JUNIPER-KITCHEN",
            "juniper kitchen",
            "juniper.kitchen",
            "x",
            "a" * 41,
            "juniper-kitchen,data_mode.eq.private",
        ):
            response = client.get(f"/api/demo/workspaces/{bad}")
            assert response.status_code == 404, bad
            assert response.json()["detail"] == "unknown demo business"
