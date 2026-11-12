"""The Discovery portal's foundation: roles, the guards, and the one route that exists so far.

Phase 1 ships no portal. What it ships is the boundary the portal will stand behind, so this
file tests the boundary from both sides: what a role is and where it comes from, which
deployments refuse the routes outright, that the admin session route answers rather than
refuses, that the per-route admin guard refuses everyone else, and that the two resolvers give
a stranger, an admin and a malformed id the same 404.

The fake enforces no foreign keys, no triggers and no grants, and the API bypasses RLS anyway.
Everything the database itself refuses is asserted against the real development database in
`db/checks/discovery_rls_check.py`, not here.

Modelled on `tests/test_cashflow.py`: `FakeBackend` from the import tests, with the discovery
tables added and two more signed-in identities the base fake does not know.
"""

from typing import Annotated, Any

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.discovery.auth import (
    AssignmentScope,
    DiscoveryAdmin,
    Mine,
    resolve_assignment_for,
    role_of,
)
from app.main import app
from app.supabase import SupabaseClient, get_supabase
from tests.test_imports import (
    AUTH_A,
    AUTH_B,
    CLIENT,
    USER_A,
    USER_B,
    VISITOR,
    FakeBackend,
    now,
)

# Two identities the base fake does not have: an admin, and an account with no profile row at
# all, which is what every public signup looks like the instant before the trigger runs.
USER_ADMIN, TOKEN_ADMIN = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3", "token-admin"
USER_NOBODY, TOKEN_NOBODY = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4", "token-nobody"
AUTH_ADMIN = {"authorization": f"Bearer {TOKEN_ADMIN}", **CLIENT}
AUTH_NOBODY = {"authorization": f"Bearer {TOKEN_NOBODY}", **CLIENT}

TEMPLATE_LEAD = "77777777-7777-4777-8777-777777777771"
ASSIGNMENT_A = "88888888-8888-4888-8888-888888888881"

SESSION = "/api/admin/discovery/session"
SESSION_OP = ("GET", SESSION)
ME = "/api/me"


class DiscoveryBackend(FakeBackend):
    """The import fake, plus profiles, templates and assignments, plus two more sign-ins.

    USER_A is a stakeholder with an assignment. USER_B is an ordinary account with an explicit
    `member` profile. USER_ADMIN is the author. USER_NOBODY has no profile row.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tables["profiles"] = [
            {"user_id": USER_A, "role": "stakeholder", "first_name": "Lead"},
            {"user_id": USER_B, "role": "member", "first_name": None},
            {"user_id": USER_ADMIN, "role": "admin", "first_name": "Clayton"},
        ]
        self.tables["discovery_templates"] = [
            {
                "id": TEMPLATE_LEAD,
                "slug": "founder-lead",
                "title": "Business + Underwriting Discovery",
                "created_at": now(),
            }
        ]
        self.tables["discovery_assignments"] = [
            {
                "id": ASSIGNMENT_A,
                "user_id": USER_A,
                "template_id": TEMPLATE_LEAD,
                "title_override": None,
                "current_section_id": None,
                "status": "not_started",
                "created_at": now(),
            }
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/v1/user":
            token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            extra = {TOKEN_ADMIN: USER_ADMIN, TOKEN_NOBODY: USER_NOBODY}
            if token in extra:
                return httpx.Response(
                    200,
                    json={"id": extra[token], "aud": "authenticated", "email": f"{token}@x.org"},
                )
        return super().handler(request)

    def client(self) -> SupabaseClient:
        """A service-role client bound to this fake, for calling a dependency directly."""
        return SupabaseClient(
            "https://fake.supabase.local",
            "sb_secret_test",
            transport=httpx.MockTransport(self.handler),
        )

    def profile(self, user_id: str) -> dict[str, Any]:
        return next(p for p in self.tables["profiles"] if p["user_id"] == user_id)

    def assignment(self, assignment_id: str) -> dict[str, Any]:
        return next(a for a in self.tables["discovery_assignments"] if a["id"] == assignment_id)


def configure(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


@pytest.fixture
def discovery(monkeypatch: pytest.MonkeyPatch):
    """The fake behind the real app, with the portal switched on. Tests switch it off by name."""
    import app.supabase as module

    monkeypatch.setattr(module, "RETRY_DELAYS", (0.0, 0.0))
    configure(monkeypatch, DISCOVERY_ENABLED="true")
    fake = DiscoveryBackend()
    app.dependency_overrides[get_supabase] = lambda: fake.client()
    yield fake
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def probe(fake: DiscoveryBackend) -> TestClient:
    """A throwaway app exposing the three dependencies directly, so their mechanics can be driven
    without a phase-2 route to hang them on. It shares the fake with the real app."""
    probe_app = FastAPI()

    @probe_app.get("/protected")
    def protected(admin: DiscoveryAdmin) -> dict[str, str]:
        return {"admin": admin}

    @probe_app.get("/own/{assignment_id}")
    def own(scope: Mine) -> dict[str, str]:
        return {"assignment_id": scope.assignment_id, "user_id": scope.user_id, "role": scope.role}

    @probe_app.get("/for/{user_id}")
    def for_user(
        scope: Annotated[AssignmentScope, Depends(resolve_assignment_for)],
    ) -> dict[str, str]:
        return {"assignment_id": scope.assignment_id, "user_id": scope.user_id, "role": scope.role}

    probe_app.dependency_overrides[get_supabase] = lambda: fake.client()
    return TestClient(probe_app)


# ------------------------------------------------------------------------------ roles --
def test_a_missing_profile_is_member(discovery: DiscoveryBackend) -> None:
    """The instant before the trigger runs, and any row that goes missing later: never elevate."""
    assert role_of(discovery.client(), USER_NOBODY) == "member"


def test_an_ordinary_profile_is_member(discovery: DiscoveryBackend) -> None:
    assert role_of(discovery.client(), USER_B) == "member"


def test_a_stakeholder_resolves_to_stakeholder(discovery: DiscoveryBackend) -> None:
    assert role_of(discovery.client(), USER_A) == "stakeholder"


def test_the_author_resolves_to_admin(discovery: DiscoveryBackend) -> None:
    assert role_of(discovery.client(), USER_ADMIN) == "admin"


def test_a_value_the_enum_does_not_know_is_member(discovery: DiscoveryBackend) -> None:
    """A row that somehow carries something else is treated as missing, not as elevated."""
    discovery.profile(USER_ADMIN)["role"] = "superuser"
    assert role_of(discovery.client(), USER_ADMIN) == "member"


def test_the_role_comes_from_profiles_not_the_allowlist(
    discovery: DiscoveryBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADMIN_USER_IDS says nothing about discovery. Only the profile row does."""
    configure(monkeypatch, ADMIN_USER_IDS=USER_A)
    with TestClient(app) as client:
        # Allowlisted for the AI dashboard, a stakeholder here.
        assert client.get(SESSION, headers=AUTH_A).json() == {"is_discovery_admin": False}
        # Not allowlisted anywhere, an admin here, because the profile says so.
        assert client.get(SESSION, headers=AUTH_ADMIN).json() == {"is_discovery_admin": True}


def test_the_allowlist_still_gates_the_ai_dashboard_alone(
    discovery: DiscoveryBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two notions of admin, deliberately, until a later phase converges them. Neither leaks."""
    configure(monkeypatch, ADMIN_USER_IDS=USER_A)
    with TestClient(app) as client:
        # The existing behaviour, unchanged: the allowlisted id is the dashboard's admin.
        assert client.get("/api/admin/session", headers=AUTH_A).json() == {"is_admin": True}
        # A profiles admin is not the dashboard's admin, and the guard refuses before any read.
        assert client.get("/api/admin/session", headers=AUTH_ADMIN).json() == {"is_admin": False}
        assert client.get("/api/admin/ai/usage", headers=AUTH_ADMIN).status_code == 403


def test_discovery_settings_are_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.discovery_enabled is False
    assert settings.discovery_self_assign is False
    assert settings.integration_presence()["discovery_enabled"] is False


# ------------------------------------------------------------------- deployment gates --
def test_discovery_is_absent_unless_switched_on(
    discovery: DiscoveryBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off by default: 404 for everyone, the admin included. /api/me reports the neutral values
    for both portal fields without consulting a table, so a deployment without the portal pays
    nothing for it and nothing can point a person at a route that is not there. That no read is
    made is proved by the other suites, whose fakes 404 on the profiles table and still pass."""
    configure(monkeypatch, DISCOVERY_ENABLED="false")
    with TestClient(app) as client:
        assert client.get(SESSION, headers=AUTH_ADMIN).status_code == 404
        assert client.get(SESSION, headers=AUTH_A).status_code == 404
        me = client.get(ME, headers=AUTH_A).json()
    assert me["role"] == "member"
    assert me["discovery"] is None


def test_discovery_is_absent_on_a_public_demo_deployment(
    discovery: DiscoveryBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public-demo gate refuses before the portal's own switch is consulted."""
    configure(monkeypatch, PUBLIC_DEMO_ONLY="true", DISCOVERY_ENABLED="true")
    with TestClient(app) as client:
        assert client.get(SESSION, headers=AUTH_ADMIN).status_code == 404


# --------------------------------------------------------------- the admin session route --
def test_the_session_route_refuses_an_anonymous_caller(discovery: DiscoveryBackend) -> None:
    with TestClient(app) as client:
        assert client.get(SESSION, headers=CLIENT).status_code == 401


def test_the_session_route_refuses_a_visitor_cookie(discovery: DiscoveryBackend) -> None:
    """A demo visitor is not a signed-in person and has no role to report."""
    with TestClient(app) as client:
        client.cookies.set("cl_visitor", VISITOR)
        assert client.get(SESSION, headers=CLIENT).status_code == 401


def test_the_session_route_needs_the_client_header(discovery: DiscoveryBackend) -> None:
    with TestClient(app) as client:
        response = client.get(SESSION, headers={"authorization": f"Bearer {TOKEN_ADMIN}"})
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        (AUTH_NOBODY, False),  # no profile row
        (AUTH_B, False),  # member
        (AUTH_A, False),  # stakeholder
        (AUTH_ADMIN, True),  # admin
    ],
    ids=["no-profile", "member", "stakeholder", "admin"],
)
def test_the_session_route_answers_rather_than_refusing(
    discovery: DiscoveryBackend, headers: dict[str, str], expected: bool
) -> None:
    """It is the question, not a protected answer: 200 for every signed-in caller."""
    with TestClient(app) as client:
        response = client.get(SESSION, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"is_discovery_admin": expected}


# ------------------------------------------------------- every other admin operation --
def discovery_admin_operations() -> set[tuple[str, str]]:
    """Every (method, path) the application actually exposes under the discovery admin prefix,
    derived from the OpenAPI document rather than typed by hand, so a route added later without
    its guard appears here on its own and fails the tests below rather than shipping."""
    operations: set[tuple[str, str]] = set()
    for path, methods in app.openapi()["paths"].items():
        if not path.startswith("/api/admin/discovery"):
            continue
        for method in methods:
            if method.lower() in ("get", "post", "put", "patch", "delete"):
                operations.add((method.upper(), path))
    return operations


ALL_OPERATIONS = discovery_admin_operations()
# The one operation that is unguarded BY ROLE, on purpose, with its own tests above. Anything
# else the application exposes under the prefix must refuse a stakeholder.
ROLE_UNPROTECTED = {SESSION_OP}
PROTECTED_OPERATIONS = sorted(ALL_OPERATIONS - ROLE_UNPROTECTED)


def test_the_session_route_is_the_only_role_unprotected_operation() -> None:
    """Exactly one exclusion, and it is the session route. A second cannot be added quietly."""
    assert SESSION_OP in ALL_OPERATIONS
    assert ALL_OPERATIONS - set(PROTECTED_OPERATIONS) == ROLE_UNPROTECTED
    assert len(ROLE_UNPROTECTED) == 1


@pytest.mark.parametrize(("method", "path"), PROTECTED_OPERATIONS)
def test_every_protected_operation_refuses_a_stakeholder(
    discovery: DiscoveryBackend, method: str, path: str
) -> None:
    """Phase 1 exposes no privileged operation yet, so this has no cases and pytest reports it
    skipped. It is armed: the first privileged route that ships appears in PROTECTED_OPERATIONS
    on its own and must answer 403 to a stakeholder here."""
    with TestClient(app) as client:
        response = client.request(method, path, headers=AUTH_A)
    assert response.status_code == 403


def test_the_admin_guard_refuses_everyone_but_an_admin(discovery: DiscoveryBackend) -> None:
    """The mechanism every privileged route will name, driven directly: the same 403 for a
    stakeholder, a member and an account with no profile; the id back for an admin; 401 for
    nobody. This is what the parametrised test above will exercise once a route exists."""
    with probe(discovery) as client:
        assert client.get("/protected", headers=AUTH_A).status_code == 403
        assert client.get("/protected", headers=AUTH_B).status_code == 403
        assert client.get("/protected", headers=AUTH_NOBODY).status_code == 403
        assert client.get("/protected", headers=CLIENT).status_code == 401
        allowed = client.get("/protected", headers=AUTH_ADMIN)
    assert allowed.status_code == 200
    assert allowed.json() == {"admin": USER_ADMIN}


# ------------------------------------------------------------------------------ /api/me --
def test_me_reports_the_role_and_the_assignment_for_a_stakeholder(
    discovery: DiscoveryBackend,
) -> None:
    with TestClient(app) as client:
        me = client.get(ME, headers=AUTH_A).json()
    assert me["role"] == "stakeholder"
    assert me["discovery"] == {
        "assignment_id": ASSIGNMENT_A,
        "template_id": TEMPLATE_LEAD,
        "title": "Business + Underwriting Discovery",
        "status": "not_started",
        "core_complete": False,
    }


def test_me_keeps_its_existing_shape_for_an_ordinary_account(
    discovery: DiscoveryBackend,
) -> None:
    """Every field existing callers read is still there and still means the same thing."""
    with TestClient(app) as client:
        me = client.get(ME, headers=AUTH_B).json()
    for key in ("user_id", "email", "business", "workspace_id", "businesses"):
        assert key in me
    assert me["user_id"] == USER_B
    assert me["business"] == {"id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2", "name": "Owner B"}
    assert me["role"] == "member"
    assert me["discovery"] is None


def test_me_reports_member_for_an_account_with_no_profile(discovery: DiscoveryBackend) -> None:
    with TestClient(app) as client:
        me = client.get(ME, headers=AUTH_NOBODY).json()
    assert me["role"] == "member"
    assert me["discovery"] is None


def test_me_core_complete_follows_the_cached_status(discovery: DiscoveryBackend) -> None:
    """The assignment's status is a cache written after each save; /api/me reads it as-is."""
    discovery.assignment(ASSIGNMENT_A)["status"] = "core_complete"
    with TestClient(app) as client:
        me = client.get(ME, headers=AUTH_A).json()
    assert me["discovery"]["status"] == "core_complete"
    assert me["discovery"]["core_complete"] is True


def test_me_lets_an_assignment_title_override_the_template(
    discovery: DiscoveryBackend,
) -> None:
    discovery.assignment(ASSIGNMENT_A)["title_override"] = "Lead's Discovery"
    with TestClient(app) as client:
        me = client.get(ME, headers=AUTH_A).json()
    assert me["discovery"]["title"] == "Lead's Discovery"


# -------------------------------------------------------------------------- resolvers --
def test_resolve_own_assignment_is_filtered_to_the_caller(discovery: DiscoveryBackend) -> None:
    """The owner gets their scope. A stranger, an admin, a guessed id and a malformed id all get
    the same 404, because the filter is on the caller's own user id in the query itself."""
    with probe(discovery) as client:
        own = client.get(f"/own/{ASSIGNMENT_A}", headers=AUTH_A)
        assert own.status_code == 200
        assert own.json() == {
            "assignment_id": ASSIGNMENT_A,
            "user_id": USER_A,
            "role": "stakeholder",
        }
        assert client.get(f"/own/{ASSIGNMENT_A}", headers=AUTH_B).status_code == 404
        assert client.get(f"/own/{ASSIGNMENT_A}", headers=AUTH_ADMIN).status_code == 404
        assert client.get("/own/not-a-uuid", headers=AUTH_A).status_code == 404
        unknown = "88888888-8888-4888-8888-888888888889"
        assert client.get(f"/own/{unknown}", headers=AUTH_A).status_code == 404
        assert client.get(f"/own/{ASSIGNMENT_A}", headers=CLIENT).status_code == 401


def test_resolve_assignment_for_requires_admin_before_it_reads(
    discovery: DiscoveryBackend,
) -> None:
    """403 first, for everyone but an admin, so a non-admin never learns whether the person has
    an assignment. Then 404 for a person with none, or a malformed id."""
    with probe(discovery) as client:
        assert client.get(f"/for/{USER_A}", headers=AUTH_A).status_code == 403
        assert client.get(f"/for/{USER_A}", headers=AUTH_B).status_code == 403
        assert client.get(f"/for/{USER_A}", headers=AUTH_NOBODY).status_code == 403
        assert client.get(f"/for/{USER_A}", headers=CLIENT).status_code == 401
        found = client.get(f"/for/{USER_A}", headers=AUTH_ADMIN)
        assert found.status_code == 200
        assert found.json() == {
            "assignment_id": ASSIGNMENT_A,
            "user_id": USER_A,
            "role": "stakeholder",
        }
        assert client.get(f"/for/{USER_B}", headers=AUTH_ADMIN).status_code == 404
        assert client.get("/for/not-a-uuid", headers=AUTH_ADMIN).status_code == 404


def test_the_two_resolvers_take_different_path_parameters() -> None:
    """A stakeholder route cannot be handed admin scope without changing its URL: the own
    resolver is keyed on an assignment id and the admin one on a user id."""
    import inspect

    from app.discovery import auth

    assert "assignment_id" in inspect.signature(auth.resolve_own_assignment).parameters
    assert "user_id" not in inspect.signature(auth.resolve_own_assignment).parameters
    assert "user_id" in inspect.signature(auth.resolve_assignment_for).parameters
    assert "assignment_id" not in inspect.signature(auth.resolve_assignment_for).parameters
    assert not hasattr(auth, "resolve_any_assignment")
