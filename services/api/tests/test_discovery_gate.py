"""Phase 3: the security and isolation gate, driven adversarially against the real routes.

Two stakeholders who must never see each other, on two templates that share one section, and
every identity the application knows, against every operation the application exposes. The
operations are enumerated from the OpenAPI document, not typed by hand, so a route added later
is in the matrix on its own. The fake is the phase 2 one, with a second stakeholder and a bank
built to have something to hide: a shared section, two private sections, a branched question,
one question of every type.

What the fake cannot prove (grants, foreign keys, the real triggers, the real loader against
real rows) is proved by `db/checks/discovery_isolation_check.py`.
"""

import importlib.util
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.discovery.seed import question_id, section_id, template_id
from app.main import app
from tests.test_discovery import AUTH_ADMIN, AUTH_NOBODY, configure
from tests.test_discovery_portal import PortalBackend
from tests.test_imports import AUTH_A, AUTH_B, CLIENT, USER_A, now

# A second stakeholder: different auth user, profile, business and assignment.
USER_C, TOKEN_C = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa5", "token-c"
AUTH_C = {"authorization": f"Bearer {TOKEN_C}", **CLIENT}
BIZ_C, WS_C = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb5", "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee5"

TEMPLATE_A, TEMPLATE_B = template_id("gate-a"), template_id("gate-b")
S_SHARED, S_A, S_B = section_id("gate-shared"), section_id("gate-a-only"), section_id("gate-b-only")
Q_S_TEXT = question_id("gate.shared.text")
Q_S_GATE = question_id("gate.shared.gate")
Q_S_BRANCH = question_id("gate.shared.branch")
Q_S_SCENARIO = question_id("gate.shared.scenario")
Q_A_TEXT = question_id("gate.a.text")
Q_A_LONG = question_id("gate.a.long")
Q_A_MULTI = question_id("gate.a.multi")
Q_A_NUMBER = question_id("gate.a.number")
Q_A_PERCENT = question_id("gate.a.percent")
Q_A_SCALE = question_id("gate.a.scale")
Q_A_RANK = question_id("gate.a.rank")
Q_A_CURRENCY = question_id("gate.a.currency")
Q_B_TEXT = question_id("gate.b.text")
ASSIGN_A = "88888888-8888-4888-8888-888888888883"
ASSIGN_C = "88888888-8888-4888-8888-888888888884"

HIDDEN_TEXT = "Which processor? HIDDEN_QUESTION_SENTINEL"
A_SECRET = "A_PRIVATE_ANSWER_SENTINEL"
C_SECRET = "C_PRIVATE_ANSWER_SENTINEL"
OPTIONS_ABC = [{"value": v, "label": v.upper()} for v in ("a", "b", "c")]
REASONS = [{"value": "nsf_recent", "label": "NSF"}, {"value": "thin_history", "label": "Thin"}]
SCENARIO = {"scenario": {"decision": "approve", "confidence": 3, "reason_codes": ["nsf_recent"]}}


def qrow(qid: str, section: str, key: str, kind: str, order: int, **extra: Any) -> dict[str, Any]:
    row = {
        "id": qid,
        "section_id": section,
        "question_key": key,
        "question_version": 1,
        "question_text": f"{key}?",
        "helper_text": None,
        "question_type": kind,
        "options": [],
        "scenario_brief": None,
        "required_core": True,
        "priority": 50,
        "sensitivity": "low",
        "order": order,
        "branch_condition": None,
        "retired_at": None,
    }
    row.update(extra)
    return row


class GateBackend(PortalBackend):
    """Two stakeholders, two templates sharing one section, a question of every type."""

    def __init__(self) -> None:
        super().__init__()
        self.tables["profiles"].append(
            {"user_id": USER_C, "role": "stakeholder", "first_name": "Second"}
        )
        self.tables["businesses"].append(
            {
                "id": BIZ_C,
                "slug": "private-c",
                "name": "Owner C",
                "industry": "unspecified",
                "data_mode": "private",
            }
        )
        self.tables["business_memberships"].append(
            {"business_id": BIZ_C, "user_id": USER_C, "role": "owner", "created_at": now()}
        )
        self.tables["workspaces"].append(
            {
                "id": WS_C,
                "business_id": BIZ_C,
                "business_data_mode": "private",
                "visitor_id": None,
                "user_id": USER_C,
            }
        )
        self.tables["discovery_templates"] = [
            {
                "id": TEMPLATE_A,
                "slug": "gate-a",
                "title": "Gate A",
                "subtitle": None,
                "estimated_minutes": 5,
                "created_at": now(),
            },
            {
                "id": TEMPLATE_B,
                "slug": "gate-b",
                "title": "Gate B",
                "subtitle": None,
                "estimated_minutes": 5,
                "created_at": now(),
            },
        ]
        self.tables["discovery_sections"] = [
            {
                "id": S_SHARED,
                "slug": "gate-shared",
                "title": "Shared",
                "description": None,
                "is_core": True,
                "estimated_minutes": 2,
            },
            {
                "id": S_A,
                "slug": "gate-a-only",
                "title": "A only",
                "description": None,
                "is_core": True,
                "estimated_minutes": 2,
            },
            {
                "id": S_B,
                "slug": "gate-b-only",
                "title": "B only",
                "description": None,
                "is_core": True,
                "estimated_minutes": 2,
            },
        ]
        self.tables["discovery_template_sections"] = [
            {"template_id": TEMPLATE_A, "section_id": S_SHARED, "order": 0, "created_at": now()},
            {"template_id": TEMPLATE_A, "section_id": S_A, "order": 1, "created_at": now()},
            {"template_id": TEMPLATE_B, "section_id": S_SHARED, "order": 0, "created_at": now()},
            {"template_id": TEMPLATE_B, "section_id": S_B, "order": 1, "created_at": now()},
        ]
        self.tables["discovery_questions"] = [
            qrow(Q_S_TEXT, S_SHARED, "gate.shared.text", "short_text", 0),
            qrow(Q_S_GATE, S_SHARED, "gate.shared.gate", "boolean", 1),
            qrow(
                Q_S_BRANCH,
                S_SHARED,
                "gate.shared.branch",
                "short_text",
                2,
                required_core=False,
                question_text=HIDDEN_TEXT,
                branch_condition={"question_key": "gate.shared.gate", "equals": True},
            ),
            qrow(
                Q_S_SCENARIO,
                S_SHARED,
                "gate.shared.scenario",
                "scenario_decision",
                3,
                options=REASONS,
                scenario_brief={"synthetic": True, "applicant": "made up"},
            ),
            qrow(Q_A_TEXT, S_A, "gate.a.text", "short_text", 0),
            qrow(Q_A_LONG, S_A, "gate.a.long", "long_text", 1, required_core=False),
            qrow(
                Q_A_MULTI,
                S_A,
                "gate.a.multi",
                "multi_select",
                2,
                options=OPTIONS_ABC,
                required_core=False,
            ),
            qrow(Q_A_NUMBER, S_A, "gate.a.number", "number", 3, required_core=False),
            qrow(Q_A_PERCENT, S_A, "gate.a.percent", "percentage", 4, required_core=False),
            qrow(Q_A_SCALE, S_A, "gate.a.scale", "scale", 5, required_core=False),
            qrow(
                Q_A_RANK, S_A, "gate.a.rank", "ranking", 6, options=OPTIONS_ABC, required_core=False
            ),
            qrow(
                Q_A_CURRENCY,
                S_A,
                "gate.a.currency",
                "currency",
                7,
                required_core=False,
                sensitivity="high",
            ),
            qrow(Q_B_TEXT, S_B, "gate.b.text", "short_text", 0),
        ]
        self.tables["discovery_assignments"] = [
            {
                "id": ASSIGN_A,
                "user_id": USER_A,
                "template_id": TEMPLATE_A,
                "title_override": None,
                "current_section_id": None,
                "status": "not_started",
                "started_at": None,
                "core_completed_at": None,
                "completed_at": None,
                "created_at": now(),
            },
            {
                "id": ASSIGN_C,
                "user_id": USER_C,
                "template_id": TEMPLATE_B,
                "title_override": None,
                "current_section_id": None,
                "status": "not_started",
                "started_at": None,
                "core_completed_at": None,
                "completed_at": None,
                "created_at": now(),
            },
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/v1/user":
            token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if token == TOKEN_C:
                return httpx.Response(
                    200, json={"id": USER_C, "aud": "authenticated", "email": "c@x.org"}
                )
        return super().handler(request)

    def question(self, qid: str) -> dict[str, Any]:
        return next(q for q in self.tables["discovery_questions"] if q["id"] == qid)


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch):
    import app.supabase as module
    from app.supabase import get_supabase

    monkeypatch.setattr(module, "RETRY_DELAYS", (0.0, 0.0))
    configure(monkeypatch, DISCOVERY_ENABLED="true")
    fake = GateBackend()
    app.dependency_overrides[get_supabase] = lambda: fake.client()
    yield fake
    app.dependency_overrides.clear()


@pytest.fixture
def client(gate: GateBackend):
    with TestClient(app) as c:
        yield c


def url(assignment: str, tail: str = "") -> str:
    return f"/api/discovery/assignments/{assignment}{tail}"


def put(
    client: TestClient, assignment: str, question: str, body: Any, headers=AUTH_A
) -> httpx.Response:
    return client.put(url(assignment, f"/responses/{question}"), json=body, headers=headers)


# ------------------------------------------------------------- the matrix --
def discovery_operations() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path, methods in app.openapi()["paths"].items():
        if path.startswith("/api/discovery") or path.startswith("/api/admin/discovery"):
            found.extend(
                (m.upper(), path) for m in methods if m in ("get", "post", "put", "patch", "delete")
            )
    return sorted(found)


OPERATIONS = discovery_operations()
IDENTITIES = [
    ("anonymous", CLIENT),
    ("member", AUTH_B),
    ("no_profile", AUTH_NOBODY),
    ("stakeholder_other", AUTH_C),
    ("admin", AUTH_ADMIN),
    ("stakeholder_owner", AUTH_A),
]
OPEN_TO_ANY_SESSION = {"/api/discovery/me", "/api/admin/discovery/session"}

# The control that produces each expected answer. Documented next to the assertion so the
# matrix in the gate report is the same table the test enforces.
GUARDS = {
    "/api/discovery/me": (
        "current_user (401); role_of reads profiles; "
        "names no template to anyone without an assignment"
    ),
    "/api/admin/discovery/session": (
        "current_user (401); CurrentRole; router: require_client_header, "
        "refuse_on_public_demo, require_discovery"
    ),
    "assignment": (
        "Mine = resolve_own_assignment(): id AND user_id = caller in the query; "
        "404 for every other identity"
    ),
}


ADMIN_PREFIX = "/api/admin/discovery"


def expected_status(path: str, identity: str) -> int:
    if identity == "anonymous":
        return 401
    if path in OPEN_TO_ANY_SESSION:
        return 200
    if path.startswith(ADMIN_PREFIX):
        # Every analysis route is guarded by role: the admin reads, every other role is refused
        # with the same 403, so a stakeholder learns nothing about what the route holds. The
        # history route also scopes by assignment: the gate's templates are not real ones, so
        # even the admin gets 404 there.
        if identity != "admin":
            return 403
        return 404 if "/history/" in path else 200
    return 200 if identity == "stakeholder_owner" else 404


def concrete(path: str) -> str:
    return (
        path.replace("{assignment_id}", ASSIGN_A)
        .replace("{slug}", "gate-a-only")
        .replace("{question_id}", Q_A_CURRENCY if path.endswith("/skip") else Q_A_TEXT)
        .replace("{name}", "progress")
    )


ADMIN_ANALYSIS_ROUTES = [
    "/api/admin/discovery/business",
    "/api/admin/discovery/calibration",
    "/api/admin/discovery/comparisons",
    "/api/admin/discovery/dataset",
    "/api/admin/discovery/engagement",
    "/api/admin/discovery/engineering",
    "/api/admin/discovery/export/{name}",
    "/api/admin/discovery/history/{assignment_id}/{question_id}",
    "/api/admin/discovery/open-questions",
    "/api/admin/discovery/overview",
    "/api/admin/discovery/respondents",
    "/api/admin/discovery/scenarios",
    "/api/admin/discovery/underwriting",
]


def test_the_matrix_covers_every_operation_the_application_exposes() -> None:
    assert len(OPERATIONS) == 9 + len(ADMIN_ANALYSIS_ROUTES), OPERATIONS
    assert sorted(p for _, p in OPERATIONS if p.startswith(ADMIN_PREFIX)) == sorted(
        ["/api/admin/discovery/session", *ADMIN_ANALYSIS_ROUTES]
    )
    # The admin surface is read-only: no method but GET exists under its prefix.
    assert {m for m, p in OPERATIONS if p.startswith(ADMIN_PREFIX)} == {"GET"}
    for _, path in OPERATIONS:
        assert path in GUARDS or "{assignment_id}" in path or path.startswith(ADMIN_PREFIX)


@pytest.mark.parametrize(("identity", "headers"), IDENTITIES, ids=[i for i, _ in IDENTITIES])
@pytest.mark.parametrize(("method", "path"), OPERATIONS, ids=[f"{m} {p}" for m, p in OPERATIONS])
def test_authorization_matrix(
    client: TestClient, method: str, path: str, identity: str, headers: dict[str, str]
) -> None:
    body = {"answer": "gate"} if method == "PUT" else None
    response = client.request(method, concrete(path), json=body, headers=headers)
    assert response.status_code == expected_status(path, identity), response.text
    if response.status_code == 404:
        assert "gate-a" not in response.text and "Gate A" not in response.text


def test_ownership_derives_from_the_session_and_nothing_the_client_sends(
    client: TestClient,
) -> None:
    """A user id, an email or a name in the query, a header or the body changes nothing."""
    noise = {"x-user-id": USER_A, "x-user-email": "a@x.org", "x-profile-name": "Lead"}
    for path in ("/api/discovery/me", url(ASSIGN_A)):
        mine = client.get(f"{path}?user_id={USER_C}&email=c@x.org", headers={**AUTH_A, **noise})
        assert mine.status_code == 200
        assert (
            mine.json().get("assignment_id", mine.json().get("assignment", {}).get("assignment_id"))
            == ASSIGN_A
        )
        theirs = client.get(f"{url(ASSIGN_A)}?user_id={USER_A}", headers={**AUTH_C, **noise})
        assert theirs.status_code == 404
    other = client.get(f"/api/discovery/me?user_id={USER_A}", headers=AUTH_C).json()
    assert other["assignment"]["assignment_id"] == ASSIGN_C
    # In a body the field is refused outright rather than read.
    assert put(client, ASSIGN_A, Q_A_TEXT, {"answer": "x", "user_id": USER_C}).status_code == 422
    assert (
        put(client, ASSIGN_A, Q_A_TEXT, {"answer": "x", "assignment_id": ASSIGN_C}).status_code
        == 422
    )


# ------------------------------------------------- cross-stakeholder attacks --
def attacks(assignment: str, question: str, optional: str) -> list[tuple[str, str, Any]]:
    return [
        ("GET", url(assignment), None),
        ("GET", url(assignment, "/sections/gate-shared"), None),
        ("GET", url(assignment, "/sections/gate-a-only"), None),
        ("GET", url(assignment, "/sections/gate-b-only"), None),
        ("GET", url(assignment, "/responses"), None),
        ("GET", url(assignment, f"/responses/{question}/history"), None),
        ("PUT", url(assignment, f"/responses/{Q_S_TEXT}"), {"answer": "intruder"}),
        ("PUT", url(assignment, f"/responses/{question}"), {"answer": "intruder"}),
        ("POST", url(assignment, f"/responses/{optional}/skip"), None),
        ("POST", url(assignment, "/sessions"), None),
    ]


def test_nothing_of_a_is_disclosed_or_changed_by_c(client: TestClient, gate: GateBackend) -> None:
    assert put(client, ASSIGN_A, Q_S_TEXT, {"answer": A_SECRET}).status_code == 200
    assert put(client, ASSIGN_A, Q_A_TEXT, {"answer": A_SECRET + " two"}).status_code == 200
    before = json.dumps(sorted(gate.responses(), key=lambda r: r["id"]), sort_keys=True)
    current = gate.assignment(ASSIGN_A)["current_section_id"]
    for method, path, body in attacks(ASSIGN_A, Q_A_TEXT, Q_A_CURRENCY):
        response = client.request(method, path, json=body, headers=AUTH_C)
        assert response.status_code == 404, (method, path, response.text)
        assert A_SECRET not in response.text
        assert "gate.a" not in response.text and "A only" not in response.text
    assert json.dumps(sorted(gate.responses(), key=lambda r: r["id"]), sort_keys=True) == before
    assert gate.assignment(ASSIGN_A)["current_section_id"] == current
    assert gate.tables["discovery_sessions"] == []
    assert [r for r in gate.responses() if r["user_id"] == USER_C] == []


def test_nothing_of_c_is_disclosed_or_changed_by_a(client: TestClient, gate: GateBackend) -> None:
    assert put(client, ASSIGN_C, Q_S_TEXT, {"answer": C_SECRET}, headers=AUTH_C).status_code == 200
    assert (
        put(client, ASSIGN_C, Q_B_TEXT, {"answer": C_SECRET + " two"}, headers=AUTH_C).status_code
        == 200
    )
    for method, path, body in attacks(ASSIGN_C, Q_B_TEXT, Q_S_BRANCH):
        response = client.request(method, path, json=body, headers=AUTH_A)
        assert response.status_code == 404, (method, path)
        assert C_SECRET not in response.text
    assert [r for r in gate.responses() if r["user_id"] == USER_C and r["answer"] == C_SECRET]


def test_a_question_from_the_other_stakeholders_private_section_is_unreachable(
    client: TestClient, gate: GateBackend
) -> None:
    """Through A's own assignment: the section is 404, the question is 404, nothing is written,
    and A's resume pointer cannot come to rest on a section outside A's template."""
    assert client.get(url(ASSIGN_A, "/sections/gate-b-only"), headers=AUTH_A).status_code == 404
    assert put(client, ASSIGN_A, Q_B_TEXT, {"answer": "x"}).status_code == 404
    assert (
        client.post(url(ASSIGN_A, f"/responses/{Q_B_TEXT}/skip"), headers=AUTH_A).status_code == 404
    )
    assert gate.responses() == []
    assert gate.assignment(ASSIGN_A)["current_section_id"] is None
    assert client.get(url(ASSIGN_C, "/sections/gate-a-only"), headers=AUTH_C).status_code == 404
    assert put(client, ASSIGN_C, Q_A_TEXT, {"answer": "x"}, headers=AUTH_C).status_code == 404
    # Nothing any stakeholder route serves names the other template's private section.
    for headers, assignment in ((AUTH_A, ASSIGN_A), (AUTH_C, ASSIGN_C)):
        overview = client.get(url(assignment), headers=headers).text
        assert ("gate-b-only" if assignment == ASSIGN_A else "gate-a-only") not in overview


# --------------------------------------------------------- shared question --
def test_sharing_a_question_does_not_share_an_answer(client: TestClient, gate: GateBackend) -> None:
    a = put(client, ASSIGN_A, Q_S_TEXT, {"answer": "A says yes"}).json()
    c = put(client, ASSIGN_C, Q_S_TEXT, {"answer": "C says no"}, headers=AUTH_C).json()
    rows = [r for r in gate.responses() if r["question_id"] == Q_S_TEXT]
    assert len(rows) == 2
    assert {r["user_id"] for r in rows} == {USER_A, USER_C}
    assert {r["assignment_id"] for r in rows} == {ASSIGN_A, ASSIGN_C}
    assert len({r["id"] for r in rows}) == 2
    assert a["response"]["question_id"] == c["response"]["question_id"] == Q_S_TEXT

    a_view = client.get(url(ASSIGN_A, "/sections/gate-shared"), headers=AUTH_A).json()
    c_view = client.get(url(ASSIGN_C, "/sections/gate-shared"), headers=AUTH_C).json()
    assert a_view["questions"][0]["response"]["answer"] == "A says yes"
    assert c_view["questions"][0]["response"]["answer"] == "C says no"
    assert "C says no" not in json.dumps(a_view) and "A says yes" not in json.dumps(c_view)
    a_list = client.get(url(ASSIGN_A, "/responses"), headers=AUTH_A).json()
    c_list = client.get(url(ASSIGN_C, "/responses"), headers=AUTH_C).json()
    assert [r["answer"] for r in a_list] == ["A says yes"]
    assert [r["answer"] for r in c_list] == ["C says no"]


def test_progress_on_a_shared_section_is_independent(client: TestClient, gate: GateBackend) -> None:
    assert put(client, ASSIGN_A, Q_S_TEXT, {"answer": "A"}).status_code == 200
    assert put(client, ASSIGN_A, Q_S_GATE, {"answer": False}).status_code == 200
    a = client.get(url(ASSIGN_A), headers=AUTH_A).json()
    c = client.get(url(ASSIGN_C), headers=AUTH_C).json()
    assert a["sections"][0]["progress"]["answered"] == 2
    assert c["sections"][0]["progress"]["answered"] == 0
    assert a["status"] == "in_progress" and c["status"] == "not_started"
    assert gate.assignment(ASSIGN_C)["current_section_id"] is None


def test_editing_a_shared_question_bumps_one_definition_and_no_answer(
    client: TestClient, gate: GateBackend
) -> None:
    assert put(client, ASSIGN_A, Q_S_TEXT, {"answer": "under v1"}).status_code == 200
    assert (
        put(client, ASSIGN_C, Q_S_TEXT, {"answer": "C under v1"}, headers=AUTH_C).status_code == 200
    )
    # The loader would do this in place: one row, version 2, new wording.
    gate.question(Q_S_TEXT).update(
        {"question_version": 2, "question_text": "gate.shared.text, reworded?"}
    )
    for headers, assignment in ((AUTH_A, ASSIGN_A), (AUTH_C, ASSIGN_C)):
        view = client.get(url(assignment, "/sections/gate-shared"), headers=headers).json()
        question = view["questions"][0]
        assert question["question_version"] == 2
        assert question["question_text"].endswith("reworded?")
        assert question["response"]["question_version"] == 1
    saved = put(client, ASSIGN_A, Q_S_TEXT, {"answer": "under v2"}).json()
    assert saved["response"]["question_version"] == 2
    history = client.get(url(ASSIGN_A, f"/responses/{Q_S_TEXT}/history"), headers=AUTH_A).json()
    assert [(e["answer"], e["question_version"]) for e in history["entries"]] == [("under v1", 1)]
    c_row = next(r for r in gate.responses() if r["user_id"] == USER_C)
    assert c_row["question_version"] == 1 and c_row["answer"] == "C under v1"


# --------------------------------------------------------- server-side branch --
def test_a_hidden_question_leaves_no_trace_in_any_payload(
    client: TestClient, gate: GateBackend
) -> None:
    for path in (
        url(ASSIGN_A),
        url(ASSIGN_A, "/sections/gate-shared"),
        url(ASSIGN_A, "/responses"),
        "/api/discovery/me",
    ):
        text = client.get(path, headers=AUTH_A).text
        assert "HIDDEN_QUESTION_SENTINEL" not in text and Q_S_BRANCH not in text, path
    view = client.get(url(ASSIGN_A, "/sections/gate-shared"), headers=AUTH_A).json()
    assert [q["id"] for q in view["questions"]] == [Q_S_TEXT, Q_S_GATE, Q_S_SCENARIO]
    assert view["progress"]["total"] == 3
    assert put(client, ASSIGN_A, Q_S_BRANCH, {"answer": "early"}).status_code == 404
    assert (
        client.post(url(ASSIGN_A, f"/responses/{Q_S_BRANCH}/skip"), headers=AUTH_A).status_code
        == 404
    )
    assert gate.responses() == []


def test_a_branch_reveals_only_for_the_assignment_that_answered(client: TestClient) -> None:
    assert put(client, ASSIGN_A, Q_S_GATE, {"answer": True}).status_code == 200
    a_view = client.get(url(ASSIGN_A, "/sections/gate-shared"), headers=AUTH_A).json()
    c_view = client.get(url(ASSIGN_C, "/sections/gate-shared"), headers=AUTH_C).json()
    assert Q_S_BRANCH in [q["id"] for q in a_view["questions"]]
    assert Q_S_BRANCH not in [q["id"] for q in c_view["questions"]]
    assert (
        put(
            client, ASSIGN_C, Q_S_BRANCH, {"answer": "still hidden for C"}, headers=AUTH_C
        ).status_code
        == 404
    )
    assert put(client, ASSIGN_A, Q_S_BRANCH, {"answer": "visible for A"}).status_code == 200


# --------------------------------------------------- adversarial validation --
REFUSED: list[tuple[str, Any]] = [
    (Q_A_LONG, {"answer": "x" * 10_001}),
    (Q_A_LONG, {"answer": 12}),
    (Q_A_LONG, {"answer": ""}),
    (Q_A_MULTI, {"answer": ["z"]}),
    (Q_A_MULTI, {"answer": ["a", "a"]}),
    (Q_A_MULTI, {"answer": []}),
    (Q_A_MULTI, {"answer": "a"}),
    (Q_A_MULTI, {"answer": [1]}),
    (Q_A_MULTI, {"answer": [{"value": "a"}]}),
    (Q_A_NUMBER, {"answer": "5"}),
    (Q_A_NUMBER, {"answer": True}),
    (Q_A_NUMBER, {"answer": 1e16}),
    (Q_A_NUMBER, {"answer": None}),
    (Q_A_PERCENT, {"answer": -1}),
    (Q_A_PERCENT, {"answer": 101}),
    (Q_A_PERCENT, {"answer": "50"}),
    (Q_A_SCALE, {"answer": -1}),
    (Q_A_SCALE, {"answer": 11}),
    (Q_A_SCALE, {"answer": 2.5}),
    (Q_A_SCALE, {"answer": "3"}),
    (Q_A_SCALE, {"answer": True}),
    (Q_A_RANK, {"answer": []}),
    (Q_A_RANK, {"answer": ["a", "a", "b"]}),
    (Q_A_RANK, {"answer": ["a", "b", "c", "d"]}),
    (Q_A_RANK, {"answer": ["a", "b", 1]}),
    (Q_A_RANK, {"answer": "abc"}),
    (Q_A_CURRENCY, {"answer": 1.0}),
    (Q_A_CURRENCY, {"answer": "1"}),
    (Q_A_CURRENCY, {"answer": [100]}),
    (Q_S_GATE, {"answer": "true"}),
    (Q_S_GATE, {"answer": 0}),
    (Q_S_GATE, {"answer": None}),
    (Q_A_TEXT, {"answer": "ok", "unexpected": 1}),
    (Q_A_TEXT, {"answer": "ok", "note": "n" * 2001}),
    (Q_A_TEXT, {"answer": {"text": "ok"}}),
    (Q_A_TEXT, ["ok"]),
    (Q_A_TEXT, "ok"),
    (Q_S_SCENARIO, {"scenario": {"decision": "approve", "confidence": 3, "user_id": USER_C}}),
    (
        Q_S_SCENARIO,
        {"scenario": {"decision": "approve", "confidence": 3, "reason_codes": "nsf_recent"}},
    ),
    (
        Q_S_SCENARIO,
        {"scenario": {"decision": "approve", "confidence": 3, "reason_codes": ["nsf_recent"] * 21}},
    ),
    (
        Q_S_SCENARIO,
        {"scenario": {"decision": "approve", "confidence": 3, "maximum_exposure_minor": 10.5}},
    ),
    (
        Q_S_SCENARIO,
        {"scenario": {"decision": "approve", "confidence": 3, "maximum_exposure_minor": "10"}},
    ),
    (Q_S_SCENARIO, {"scenario": {"decision": "approve", "confidence": "3"}}),
    (Q_S_SCENARIO, {"scenario": {"confidence": 3}}),
    (Q_S_SCENARIO, {"scenario": "approve"}),
    (Q_S_SCENARIO, {"scenario": None}),
    (Q_S_SCENARIO, {"scenario": {"decision": "approve", "confidence": 3, "notes": 5}}),
]


@pytest.mark.parametrize(("question", "body"), REFUSED, ids=[f"{i}" for i in range(len(REFUSED))])
def test_malformed_answers_are_refused_before_any_write(
    client: TestClient, gate: GateBackend, question: str, body: Any
) -> None:
    response = put(client, ASSIGN_A, question, body)
    assert response.status_code == 422, response.text
    assert gate.responses() == [] and gate.scenario_rows() == []
    assert gate.assignment(ASSIGN_A)["status"] == "not_started"


@pytest.mark.parametrize(
    ("question", "raw"),
    [
        (Q_A_NUMBER, b'{"answer": NaN}'),
        (Q_A_NUMBER, b'{"answer": Infinity}'),
        (Q_A_NUMBER, b'{"answer": -Infinity}'),
        (Q_A_PERCENT, b'{"answer": NaN}'),
        (Q_A_CURRENCY, b'{"answer": Infinity}'),
        (Q_S_SCENARIO, b'{"scenario": {"decision": "approve", "confidence": NaN}}'),
    ],
    ids=["number-nan", "number-inf", "number-neg-inf", "percent-nan", "currency-inf", "conf-nan"],
)
def test_non_finite_numbers_the_json_parser_admits_are_refused(
    client: TestClient, gate: GateBackend, question: str, raw: bytes
) -> None:
    """Python's JSON parser accepts NaN and Infinity; jsonb does not. They are refused as an
    answer that does not fit, not surfaced as a 502 from the database."""
    response = client.put(
        url(ASSIGN_A, f"/responses/{question}"),
        content=raw,
        headers={**AUTH_A, "content-type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert gate.responses() == []


def test_the_accepted_shape_of_every_type_round_trips(client: TestClient) -> None:
    accepted = [
        (Q_A_TEXT, "short"),
        (Q_A_LONG, ("long " * 10).strip()),
        (Q_A_MULTI, ["a", "c"]),
        (Q_A_NUMBER, 12.5),
        (Q_A_PERCENT, 100),
        (Q_A_SCALE, 0),
        # A ranking of the options that apply; the rest are left out, as the bank's ranking
        # questions ask.
        (Q_A_RANK, ["c", "a"]),
        (Q_A_CURRENCY, -250000),
    ]
    for question, answer in accepted:
        result = put(client, ASSIGN_A, question, {"answer": answer})
        assert result.status_code == 200, result.text
        assert result.json()["response"]["answer"] == answer
    view = client.get(url(ASSIGN_A, "/sections/gate-a-only"), headers=AUTH_A).json()
    assert {q["id"]: q["response"]["answer"] for q in view["questions"]} == dict(accepted)


# ------------------------------------------------------- scenario atomicity --
def test_a_failed_generic_write_stores_nothing(client: TestClient, gate: GateBackend) -> None:
    """The whole typed save is one database call; if it does not answer, nothing was written."""
    gate.failures[("RPC", "save_discovery_typed_response")] = [500]
    assert put(client, ASSIGN_A, Q_S_SCENARIO, SCENARIO).status_code == 502
    assert gate.responses() == [] and gate.scenario_rows() == []


def test_a_failed_typed_update_leaves_the_previous_pair_standing(
    client: TestClient, gate: GateBackend
) -> None:
    """A refused typed write rolls the generic update back with it (E7, fixed in phase 3.5)."""
    assert put(client, ASSIGN_A, Q_S_SCENARIO, SCENARIO).status_code == 200
    gate.failures[("TYPED", "discovery_scenario_responses")] = [1]
    changed = {"scenario": {**SCENARIO["scenario"], "decision": "deny", "confidence": 5}}
    assert put(client, ASSIGN_A, Q_S_SCENARIO, changed).status_code == 502
    assert gate.scenario_rows()[0]["decision"] == "approve"
    assert gate.responses()[0]["status"] == "answered"
    assert gate.tables["discovery_scenario_response_history"] == []
    assert gate.tables["discovery_response_history"] == []
    view = client.get(url(ASSIGN_A, "/sections/gate-shared"), headers=AUTH_A).json()
    assert view["questions"][2]["response"]["scenario"]["decision"] == "approve"


def test_an_orphaned_generic_row_is_invisible_and_repaired(
    client: TestClient, gate: GateBackend
) -> None:
    """The write function makes an orphan unreachable through the API, but the read path stays
    honest regardless: a generic row planted without its typed half is reported by no route,
    progress does not move, and the next save repairs it in place rather than colliding."""
    gate.tables["discovery_responses"].append(
        gate._defaults(
            "discovery_responses",
            {
                "assignment_id": ASSIGN_A,
                "user_id": USER_A,
                "template_id": TEMPLATE_A,
                "section_id": S_SHARED,
                "question_id": Q_S_SCENARIO,
                "question_version": 1,
                "question_type": "scenario_decision",
                "status": "answered",
                "answer": None,
                "note": None,
            },
        )
    )
    assert len(gate.responses()) == 1 and gate.scenario_rows() == []
    view = client.get(url(ASSIGN_A, "/sections/gate-shared"), headers=AUTH_A).json()
    assert view["questions"][2]["response"] is None
    assert view["progress"]["answered"] == 0
    assert client.get(url(ASSIGN_A, "/responses"), headers=AUTH_A).json() == []
    assert (
        client.get("/api/discovery/me", headers=AUTH_A).json()["assignment"]["progress"]["answered"]
        == 0
    )
    assert put(client, ASSIGN_A, Q_S_SCENARIO, SCENARIO).status_code == 200
    assert len(gate.responses()) == 1 and len(gate.scenario_rows()) == 1


# -------------------------------------------------------- retired question --
def test_a_retired_question_keeps_its_answer_and_leaves_the_section(
    client: TestClient, gate: GateBackend
) -> None:
    assert put(client, ASSIGN_A, Q_A_TEXT, {"answer": "kept"}).status_code == 200
    gate.question(Q_A_TEXT)["retired_at"] = now()
    view = client.get(url(ASSIGN_A, "/sections/gate-a-only"), headers=AUTH_A).json()
    assert Q_A_TEXT not in [q["id"] for q in view["questions"]]
    assert view["progress"]["total"] == 7 and view["progress"]["core_total"] == 0
    assert put(client, ASSIGN_A, Q_A_TEXT, {"answer": "again"}).status_code == 404
    listed = client.get(url(ASSIGN_A, "/responses"), headers=AUTH_A).json()
    assert [r["answer"] for r in listed if r["question_id"] == Q_A_TEXT] == ["kept"]
    assert (
        client.get(url(ASSIGN_A, f"/responses/{Q_A_TEXT}/history"), headers=AUTH_A).status_code
        == 200
    )
    overview = client.get(url(ASSIGN_A), headers=AUTH_A).json()
    assert overview["progress"]["core_total"] == 3  # the shared section's three, none from A-only


# ---------------------------------------------------------- deployment gates --
SWITCHES = [
    ("discovery-off", {"DISCOVERY_ENABLED": "false"}),
    ("public-demo", {"DISCOVERY_ENABLED": "true", "PUBLIC_DEMO_ONLY": "true"}),
    ("features-off", {"DISCOVERY_ENABLED": "true", "FEATURES_ENABLED": "false"}),
]


@pytest.mark.parametrize(("switch", "env"), SWITCHES, ids=[s for s, _ in SWITCHES])
@pytest.mark.parametrize(("method", "path"), OPERATIONS, ids=[f"{m} {p}" for m, p in OPERATIONS])
def test_every_operation_under_every_switch(
    gate: GateBackend,
    monkeypatch: pytest.MonkeyPatch,
    switch: str,
    env: dict[str, str],
    method: str,
    path: str,
) -> None:
    """Stakeholder routes are absent under all three switches. The admin session route follows
    the approved design: absent when the portal is off or the deployment is public-demo; under
    FEATURES_ENABLED=false it still answers its boolean, as the existing admin router does,
    because operator routes are not product features."""
    configure(monkeypatch, **env)
    body = {"answer": "gate"} if method == "PUT" else None
    with TestClient(app) as client:
        for identity, headers in (
            ("stakeholder_owner", AUTH_A),
            ("stakeholder_other", AUTH_C),
            ("admin", AUTH_ADMIN),
        ):
            # Operator routes stay up under features-off and keep their own role guard.
            operator = path.startswith(ADMIN_PREFIX) and switch == "features-off"
            wanted = expected_status(path, identity) if operator else 404
            response = client.request(method, concrete(path), json=body, headers=headers)
            assert response.status_code == wanted, (switch, headers, response.text)


def test_the_rest_of_cashlens_is_unaffected_by_the_portal_switch(
    gate: GateBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(monkeypatch, DISCOVERY_ENABLED="false")
    with TestClient(app) as client:
        me = client.get("/api/me", headers=AUTH_A).json()
        assert me["role"] == "member" and me["discovery"] is None
        assert me["business"] is not None
        assert client.get("/api/private/workspace", headers=AUTH_A).status_code == 200
        assert client.get("/api/admin/session", headers=AUTH_A).status_code == 200
    configure(monkeypatch, DISCOVERY_ENABLED="true")
    with TestClient(app) as client:
        assert client.get("/api/private/workspace", headers=AUTH_A).status_code == 200


# ------------------------------------------------------------------ logging --
def test_no_answer_content_reaches_a_log_record(
    client: TestClient, gate: GateBackend, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client.post(url(ASSIGN_A, "/sessions"), headers=AUTH_A)
    gate.failures[("PATCH", "discovery_sessions")] = [500]
    assert put(client, ASSIGN_A, Q_A_TEXT, {"answer": "LOG_ANSWER_SENTINEL"}).status_code == 200
    gate.failures[("RPC", "save_discovery_typed_response")] = [500]
    assert (
        put(
            client,
            ASSIGN_A,
            Q_S_SCENARIO,
            {"scenario": {**SCENARIO["scenario"], "notes": "LOG_NOTE_SENTINEL"}},
        ).status_code
        == 502
    )
    records = [r for r in caplog.records]
    assert any("session_count_failed" in r.getMessage() for r in records)
    assert any("upstream.failed" in r.getMessage() for r in records)
    for record in records:
        blob = record.getMessage() + json.dumps({k: str(v) for k, v in record.__dict__.items()})
        assert "SENTINEL" not in blob, record


# ------------------------------------------------------------- admin boundary --
def test_the_admin_prefix_holds_exactly_the_one_role_unprotected_operation(
    client: TestClient,
) -> None:
    """`/session` answers any signed-in caller; every other admin route refuses every role but
    admin, driven directly, whatever query parameters a stakeholder appends."""
    admin_ops = [(m, p) for m, p in OPERATIONS if p.startswith(ADMIN_PREFIX)]
    assert ("GET", "/api/admin/discovery/session") in admin_ops
    assert client.get("/api/admin/discovery/session", headers=AUTH_C).json() == {
        "is_discovery_admin": False
    }
    assert client.get("/api/admin/discovery/session", headers=AUTH_ADMIN).json() == {
        "is_discovery_admin": True
    }
    smuggled = f"?user_id={USER_A}&assignment_id={ASSIGN_A}&respondent=lead&role=admin"
    for _, path in admin_ops:
        if path.endswith("/session"):
            continue
        for headers in (AUTH_A, AUTH_B, AUTH_C, AUTH_NOBODY):
            response = client.get(concrete(path) + smuggled, headers=headers)
            assert response.status_code == 403, (path, response.text)
            assert A_SECRET not in response.text and C_SECRET not in response.text
        assert client.get(concrete(path) + smuggled, headers=AUTH_ADMIN).status_code == (
            expected_status(path, "admin")
        )


# ------------------------------------------------------------ cleanup order --
def load_account_script():
    path = Path(__file__).resolve().parents[3] / "scripts" / "discovery_test_account.py"
    spec = importlib.util.spec_from_file_location("discovery_test_account", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingAdmin:
    def __init__(self, auth_status: int = 200) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.auth_status = auth_status

    def auth(self, path: str, method: str = "GET", body: Any = None):
        self.calls.append(("auth", method, path.split("?")[0]))
        return self.auth_status, None

    def rest(self, path: str, method: str = "GET", body: Any = None, prefer: str | None = None):
        self.calls.append(("rest", method, path.split("?")[0]))
        return 200, []


def test_account_removal_deletes_the_user_before_it_sweeps_history() -> None:
    """The phase 2 regression: sweeping history first leaves the records the user's own cascade
    then writes. The shared routine deletes the user first and the sweep last, and stops before
    any sweep if the user could not be deleted."""
    module = load_account_script()
    admin = RecordingAdmin()
    module.remove_account(admin, "u1")
    assert admin.calls == [
        ("auth", "DELETE", "admin/users/u1"),
        ("rest", "DELETE", "discovery_response_history"),
        ("rest", "DELETE", "discovery_scenario_response_history"),
        ("rest", "DELETE", "discovery_transaction_calibration_response_history"),
    ]
    # Every history table the schema has is swept: a fourth typed history table added later
    # without joining this list would leave rows behind, which is exactly what phase 3.5 found.
    assert set(module.HISTORY_TABLES) == {
        "discovery_response_history",
        "discovery_scenario_response_history",
        "discovery_transaction_calibration_response_history",
    }
    refusing = RecordingAdmin(auth_status=500)
    with pytest.raises(RuntimeError):
        module.remove_account(refusing, "u1")
    assert refusing.calls == [("auth", "DELETE", "admin/users/u1")]
    assert set(module.ACCOUNT_TABLES) >= {
        "profiles",
        "discovery_assignments",
        "discovery_responses",
        "discovery_scenario_responses",
        "discovery_sessions",
        "discovery_response_history",
        "discovery_scenario_response_history",
    }
