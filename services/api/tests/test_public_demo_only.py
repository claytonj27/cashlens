"""A deployment that publishes the synthetic demo and nothing else.

The public production domain shares a database with private businesses, so the restriction that
matters is not "the interface hides the login link". It is that the API refuses, and refuses at
the one place every workspace-scoped route passes through. These tests drive the routes
directly, with a valid session for a real private business, which is the case the interface
cannot protect.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from tests.test_ai_brief import CLIENT, GOOD_TOKEN, VISITOR, WS_USER, WS_VISITOR, FakeDb


def public_demo(monkeypatch: pytest.MonkeyPatch, on: bool = True) -> None:
    monkeypatch.setenv("PUBLIC_DEMO_ONLY", "true" if on else "false")
    get_settings.cache_clear()


def as_owner() -> dict[str, str]:
    return {**CLIENT, "authorization": f"Bearer {GOOD_TOKEN}"}


def visitor(client: TestClient) -> TestClient:
    client.cookies.set("cl_visitor", VISITOR)
    return client


# ------------------------------------------------- the demo still works --
def test_the_synthetic_demo_still_answers(db: FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of the mode: the demo is the part that stays."""
    public_demo(monkeypatch)
    with TestClient(app) as client:
        visitor(client)
        response = client.get(f"/api/workspaces/{WS_VISITOR}/analysis", headers=CLIENT)
    assert response.status_code == 200


def test_the_demo_routes_are_reachable(db: FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    public_demo(monkeypatch)
    with TestClient(app) as client:
        assert client.post("/api/demo/session", headers=CLIENT).status_code in (200, 201)


# --------------------------------------------- private data is refused --
@pytest.mark.parametrize(
    "path",
    [
        "/api/workspaces/{ws}/analysis",
        "/api/workspaces/{ws}/transactions",
        "/api/workspaces/{ws}/accounts",
        "/api/workspaces/{ws}/ai/brief",
    ],
)
def test_a_private_workspace_is_refused_even_with_a_valid_session(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """The owner of this business is signed in and authorised. The deployment still refuses."""
    public_demo(monkeypatch)
    with TestClient(app) as client:
        response = client.get(path.format(ws=WS_USER), headers=as_owner())
    assert response.status_code == 404


def test_the_same_request_succeeds_when_the_mode_is_off(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control case: the refusal is the mode, not a broken route or a broken session."""
    public_demo(monkeypatch, on=False)
    with TestClient(app) as client:
        response = client.get(f"/api/workspaces/{WS_USER}/analysis", headers=as_owner())
    assert response.status_code == 200


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/me"),
        ("POST", "/api/private/provision"),
        ("GET", "/api/private/workspace"),
        ("GET", "/api/admin/session"),
        ("GET", "/api/admin/ai/usage"),
    ],
)
def test_private_and_operator_paths_do_not_exist(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch, method: str, path: str
) -> None:
    public_demo(monkeypatch)
    with TestClient(app) as client:
        response = client.request(method, path, headers=as_owner(), json={})
    assert response.status_code == 404


def test_the_import_path_does_not_exist(db: FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    """No upload surface on a deployment that has no private businesses."""
    public_demo(monkeypatch)
    with TestClient(app) as client:
        response = client.get(f"/api/workspaces/{WS_USER}/imports", headers=as_owner())
    assert response.status_code == 404


def test_the_discovery_portal_does_not_exist(db: FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refused by the public-demo gate itself, before the portal's own switch is consulted."""
    public_demo(monkeypatch)
    monkeypatch.setenv("DISCOVERY_ENABLED", "true")
    get_settings.cache_clear()
    with TestClient(app) as client:
        response = client.get("/api/admin/discovery/session", headers=as_owner())
    assert response.status_code == 404


def test_those_paths_exist_again_when_the_mode_is_off(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So the preview the reviewer was sent keeps its account, import and workspace routes."""
    public_demo(monkeypatch, on=False)
    with TestClient(app) as client:
        assert client.get("/api/me", headers=as_owner()).status_code == 200


def test_the_mode_is_off_unless_a_deployment_asks_for_it(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PUBLIC_DEMO_ONLY", raising=False)
    get_settings.cache_clear()
    assert get_settings().public_demo_only is False
