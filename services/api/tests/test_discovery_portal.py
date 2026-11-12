"""The phase 2 slice of the Discovery portal: the stakeholder routes, end to end through the API.

What a stakeholder can read, what they can save, what is refused, how progress is derived and
where a resume lands. The fake here is the phase 1 fake with every discovery table added, the
phase 2 template loaded, and the two history triggers imitated, so the history route and the
"nothing is overwritten silently" promise can be asserted without a database. What the database
itself enforces (foreign keys, check constraints, the real triggers, the grants) is asserted
against the development database by `db/checks/discovery_rls_check.py` and
`db/checks/discovery_flow_check.py`.

Every assignment-scoped route is also enumerated from the OpenAPI document and driven as a
stranger and as nobody, so a route added later without the scope guard fails here on its own.
"""

import copy
import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.discovery.answers import CATEGORIES_BY_DIRECTION
from app.discovery.seed import load_bank, question_id, section_id, template_id
from app.main import app
from tests.test_discovery import (
    AUTH_ADMIN,
    AUTH_NOBODY,
    TOKEN_ADMIN,
    DiscoveryBackend,
    configure,
)
from tests.test_imports import AUTH_A, AUTH_B, CLIENT, TOKEN_A, USER_A, _RefusedError, now

TYPED_SAVE = "save_discovery_typed_response"
TYPED_TABLE = {
    "scenario_decision": "discovery_scenario_responses",
    "transaction_calibration": "discovery_transaction_calibration_responses",
}

TEMPLATE = template_id("phase2-discovery-test")
SECTION = section_id("phase2-core-test")
Q_TEXT = question_id("phase2.test.short_text")
Q_BOOL = question_id("phase2.test.boolean")
Q_SELECT = question_id("phase2.test.single_select")
Q_CURRENCY = question_id("phase2.test.currency")
Q_SCENARIO = question_id("phase2.test.scenario")
# A sixth question the fixture does not have: hidden until the boolean is answered "yes".
Q_BRANCH = question_id("phase2.test.branch")
ASSIGNMENT = "88888888-8888-4888-8888-888888888882"

PREFIX = f"/api/discovery/assignments/{ASSIGNMENT}"
SECTION_URL = f"{PREFIX}/sections/phase2-core-test"

SCENARIO_OK = {
    "scenario": {
        "decision": "manual_review",
        "confidence": 4,
        "reason_codes": ["nsf_recent", "debt_load"],
        "maximum_exposure_minor": 1500000,
        "additional_information_needed": "Three more months of statements.",
        "what_would_change_my_decision": None,
        "notes": "  synthetic case  ",
    }
}


class PortalBackend(DiscoveryBackend):
    """The phase 1 fake with the whole bank and the two triggers imitated.

    USER_A holds an assignment on the phase 2 template. The section is the committed fixture
    plus one branched question, so visibility has something to hide.
    """

    def __init__(self) -> None:
        super().__init__()
        bank = load_bank()
        template = bank.templates[0]
        section = bank.sections["phase2-core-test"]
        self.tables["discovery_templates"] = [
            {
                "id": TEMPLATE,
                "slug": template.slug,
                "title": template.title,
                "subtitle": template.subtitle,
                "estimated_minutes": template.estimated_minutes,
                "created_at": now(),
            }
        ]
        self.tables["discovery_sections"] = [
            {
                "id": SECTION,
                "slug": section.slug,
                "title": section.title,
                "description": section.description,
                "is_core": section.is_core,
                "estimated_minutes": section.estimated_minutes,
            }
        ]
        self.tables["discovery_template_sections"] = [
            {"template_id": TEMPLATE, "section_id": SECTION, "order": 0, "created_at": now()}
        ]
        self.tables["discovery_questions"] = [
            {
                "id": question_id(x.question_key),
                "section_id": SECTION,
                "question_key": x.question_key,
                "question_version": 1,
                "question_text": x.question_text,
                "helper_text": x.helper_text,
                "question_type": x.question_type,
                "options": x.options,
                "scenario_brief": x.scenario_brief,
                "required_core": x.required_core,
                "priority": x.priority,
                "sensitivity": x.sensitivity,
                "order": x.order,
                "branch_condition": x.branch_condition,
                "retired_at": None,
            }
            for x in section.questions
        ] + [
            {
                "id": Q_BRANCH,
                "section_id": SECTION,
                "question_key": "phase2.test.branch",
                "question_version": 1,
                "question_text": "Which card processor?",
                "helper_text": None,
                "question_type": "short_text",
                "options": [],
                "scenario_brief": None,
                "required_core": False,
                "priority": 50,
                "sensitivity": "low",
                "order": 5,
                "branch_condition": {"question_key": "phase2.test.boolean", "equals": True},
                "retired_at": None,
            }
        ]
        self.tables["discovery_assignments"] = [
            {
                "id": ASSIGNMENT,
                "user_id": USER_A,
                "template_id": TEMPLATE,
                "title_override": None,
                "current_section_id": None,
                "status": "not_started",
                "started_at": None,
                "core_completed_at": None,
                "completed_at": None,
                "created_at": now(),
            }
        ]
        for table in (
            "discovery_responses",
            "discovery_response_history",
            "discovery_scenario_responses",
            "discovery_scenario_response_history",
            "discovery_sessions",
            "discovery_transaction_cases",
            "discovery_transaction_calibration_responses",
            "discovery_transaction_calibration_response_history",
        ):
            self.tables[table] = []

    def _defaults(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        base = super()._defaults(table, row)
        if table in ("discovery_responses", *TYPED_TABLE.values()):
            base.setdefault("updated_at", base["created_at"])
        if table == "discovery_sessions":
            base.setdefault("started_at", base["created_at"])
            base.setdefault("last_seen_at", base["created_at"])
            base.setdefault("ended_at", None)
            base.setdefault("saves", 0)
        return base

    # The two triggers: before update or delete, the old row goes to history.
    def _record(self, table: str, old: dict[str, Any], change: str) -> None:
        stamp = now()
        if table == "discovery_responses":
            self.tables["discovery_response_history"].append(
                {
                    "id": f"h-{len(self.tables['discovery_response_history']) + 1}",
                    "response_id": old["id"],
                    "assignment_id": old["assignment_id"],
                    "user_id": old["user_id"],
                    "question_id": old["question_id"],
                    "question_version": old["question_version"],
                    "question_type": old["question_type"],
                    "status": old["status"],
                    "answer": old.get("answer"),
                    "note": old.get("note"),
                    "change": change,
                    "valid_from": old.get("updated_at") or old.get("created_at"),
                    "valid_to": stamp,
                }
            )
        elif table == "discovery_scenario_responses":
            self.tables["discovery_scenario_response_history"].append(
                {
                    "id": f"s-{len(self.tables['discovery_scenario_response_history']) + 1}",
                    "response_id": old["response_id"],
                    "assignment_id": old["assignment_id"],
                    "user_id": old["user_id"],
                    "question_id": old["question_id"],
                    "decision": old["decision"],
                    "confidence": old["confidence"],
                    "reason_codes": old["reason_codes"],
                    "maximum_exposure_minor": old.get("maximum_exposure_minor"),
                    "additional_information_needed": old.get("additional_information_needed"),
                    "what_would_change_my_decision": old.get("what_would_change_my_decision"),
                    "notes": old.get("notes"),
                    "change": change,
                    "valid_from": old.get("updated_at") or old.get("created_at"),
                    "valid_to": stamp,
                }
            )
        else:
            history = self.tables["discovery_transaction_calibration_response_history"]
            history.append(
                {
                    "id": f"c-{len(history) + 1}",
                    **{
                        k: old.get(k)
                        for k in (
                            "response_id",
                            "assignment_id",
                            "user_id",
                            "question_id",
                            "direction",
                            "classification_category",
                            "classification_confidence",
                            "underwriting_materiality",
                            "underwriting_signal",
                            "decision_impact",
                            "reason_codes",
                            "needs_more_context",
                            "context_needed",
                            "reasoning",
                            "handling_recommendation",
                        )
                    },
                    "change": change,
                    "valid_from": old.get("updated_at") or old.get("created_at"),
                    "valid_to": stamp,
                }
            )

    def rest(self, request: httpx.Request, table: str, params: dict[str, str]) -> httpx.Response:
        history = table in ("discovery_responses", *TYPED_TABLE.values())
        # A queued gateway failure means the statement never ran, so no trigger fired.
        if self.failures.get((request.method, table)):
            history = False
        if history and request.method in ("PATCH", "DELETE"):
            for old in self._select(table, params):
                self._record(
                    table, dict(old), "deleted" if request.method == "DELETE" else "updated"
                )
        if history and request.method == "DELETE" and table == "discovery_responses":
            # on delete cascade to the typed row, whose own trigger fires too
            for old in self._select(table, params):
                for typed_table in TYPED_TABLE.values():
                    for typed in [
                        t for t in self.tables[typed_table] if t["response_id"] == old["id"]
                    ]:
                        self._record(typed_table, dict(typed), "deleted")
                        self.tables[typed_table].remove(typed)
        response = super().rest(request, table, params)
        if history and request.method == "PATCH":
            for row in self._select(table, params):
                row["updated_at"] = now()
        return response

    # ---- the one database function that writes a typed answer (migration 0027, E7) ----
    def rpc(self, name: str, body: dict[str, Any]) -> httpx.Response:
        if name != TYPED_SAVE:
            return super().rpc(name, body)
        queued = self.failures.get(("RPC", name))
        if queued:
            return httpx.Response(queued.pop(0), json={"message": "Gateway Timeout"})
        # The real function is one transaction: on any error nothing it did survives.
        snapshot = copy.deepcopy(self.tables)
        try:
            response_id = self._save_typed(body)
        except _RefusedError as exc:
            self.tables = snapshot
            return httpx.Response(400, json={"message": str(exc)})
        return httpx.Response(200, json=response_id)

    def _save_typed(self, p: dict[str, Any]) -> str:
        kind, status = p["p_question_type"], p["p_status"]
        if kind not in TYPED_TABLE:
            raise _RefusedError("not a typed question type")
        generic = next(
            (
                r
                for r in self.tables["discovery_responses"]
                if r["assignment_id"] == p["p_assignment_id"]
                and r["question_id"] == p["p_question_id"]
            ),
            None,
        )
        note = p["p_note"] if status == "answered" else None
        if generic is None:
            generic = self._defaults(
                "discovery_responses",
                {
                    "assignment_id": p["p_assignment_id"],
                    "user_id": p["p_user_id"],
                    "template_id": p["p_template_id"],
                    "section_id": p["p_section_id"],
                    "question_id": p["p_question_id"],
                    "question_version": p["p_question_version"],
                    "question_type": kind,
                    "status": status,
                    "answer": None,
                    "note": note,
                },
            )
            self.tables["discovery_responses"].append(generic)
        else:
            self._record("discovery_responses", dict(generic), "updated")
            generic.update(
                {
                    "status": status,
                    "answer": None,
                    "note": note,
                    "question_version": p["p_question_version"],
                    "updated_at": now(),
                }
            )
        response_id = generic["id"]
        typed_table = TYPED_TABLE[kind]
        if status == "skipped":
            for typed in [t for t in self.tables[typed_table] if t["response_id"] == response_id]:
                self._record(typed_table, dict(typed), "deleted")
                self.tables[typed_table].remove(typed)
            return response_id
        if self.failures.get(("TYPED", typed_table)):
            self.failures[("TYPED", typed_table)].pop(0)
            raise _RefusedError("the typed write was refused")
        typed = p["p_typed"]
        if not isinstance(typed, dict):
            raise _RefusedError("an answer needs its typed detail")
        row = {
            "response_id": response_id,
            "assignment_id": p["p_assignment_id"],
            "user_id": p["p_user_id"],
            "question_id": p["p_question_id"],
            **typed,
        }
        # The row-level checks the database makes, imitated so a bypass of the API's own
        # validation is still refused here.
        if kind == "transaction_calibration":
            case = next(
                (
                    c
                    for c in self.tables["discovery_transaction_cases"]
                    if c["question_id"] == p["p_question_id"]
                ),
                None,
            )
            if case is None:
                raise _RefusedError("no transaction case")
            row["direction"] = case["direction"]  # never the caller's
            if row["classification_category"] not in CATEGORIES_BY_DIRECTION[case["direction"]]:
                raise _RefusedError("calibration_category_matches_direction")
            if not 1 <= int(row["classification_confidence"]) <= 5:
                raise _RefusedError("confidence out of range")
            if row.get("context_needed") is not None and not row.get("needs_more_context"):
                raise _RefusedError("context_needed_requires_the_flag")
            row.setdefault("reason_codes", [])
            row.setdefault("needs_more_context", False)
        elif not 1 <= int(row["confidence"]) <= 5:
            raise _RefusedError("confidence out of range")
        existing = next(
            (t for t in self.tables[typed_table] if t["response_id"] == response_id), None
        )
        if existing is None:
            self.tables[typed_table].append(self._defaults(typed_table, row))
        else:
            self._record(typed_table, dict(existing), "updated")
            existing.update({**row, "updated_at": now()})
        return response_id

    # ---- shortcuts for assertions ----
    def responses(self) -> list[dict[str, Any]]:
        return self.tables["discovery_responses"]

    def response_for(self, question: str) -> dict[str, Any] | None:
        return next((r for r in self.responses() if r["question_id"] == question), None)

    def scenario_rows(self) -> list[dict[str, Any]]:
        return self.tables["discovery_scenario_responses"]


@pytest.fixture
def portal(monkeypatch: pytest.MonkeyPatch):
    import app.supabase as module
    from app.supabase import get_supabase

    monkeypatch.setattr(module, "RETRY_DELAYS", (0.0, 0.0))
    configure(monkeypatch, DISCOVERY_ENABLED="true")
    fake = PortalBackend()
    app.dependency_overrides[get_supabase] = lambda: fake.client()
    yield fake
    app.dependency_overrides.clear()


@pytest.fixture
def client(portal: PortalBackend):
    with TestClient(app) as c:
        yield c


def put(client: TestClient, question: str, body: dict[str, Any], headers=AUTH_A) -> httpx.Response:
    return client.put(f"{PREFIX}/responses/{question}", json=body, headers=headers)


def answer_core(client: TestClient) -> None:
    """The four core questions, answered. The optional currency is left alone."""
    assert put(client, Q_TEXT, {"answer": "Sells synthetic sandwiches."}).status_code == 200
    assert put(client, Q_BOOL, {"answer": False}).status_code == 200
    assert put(client, Q_SELECT, {"answer": "weekly"}).status_code == 200
    assert put(client, Q_SCENARIO, SCENARIO_OK).status_code == 200


# ---------------------------------------------------------------- /me and overview --
def test_me_for_a_stakeholder_is_the_overview(client: TestClient) -> None:
    me = client.get("/api/discovery/me", headers=AUTH_A).json()
    assert me["role"] == "stakeholder"
    assert me["first_name"] == "Lead"
    assignment = me["assignment"]
    assert assignment["assignment_id"] == ASSIGNMENT
    assert assignment["template_slug"] == "phase2-discovery-test"
    assert assignment["title"] == "Discovery (phase 2 test)"
    assert assignment["status"] == "not_started"
    assert assignment["resume_section_slug"] == "phase2-core-test"
    assert assignment["current_section_slug"] is None
    assert [s["slug"] for s in assignment["sections"]] == ["phase2-core-test"]
    # Five visible: the branched sixth is hidden until the boolean says yes.
    assert assignment["progress"] == {
        "total": 5,
        "answered": 0,
        "core_total": 4,
        "core_answered": 0,
        "core_complete": False,
        "complete": False,
        "percent": 0,
    }


def test_me_for_everyone_else_names_no_template(client: TestClient) -> None:
    for headers, role in ((AUTH_B, "member"), (AUTH_NOBODY, "member"), (AUTH_ADMIN, "admin")):
        me = client.get("/api/discovery/me", headers=headers).json()
        assert me["role"] == role
        assert me["assignment"] is None
    assert client.get("/api/discovery/me", headers=CLIENT).status_code == 401


def test_me_lists_every_assignment_the_caller_holds_newest_first(
    client: TestClient, portal: PortalBackend
) -> None:
    """A reviewer holding several templates chooses between their own; the list is only ever
    the caller's, the newest is what opens, and none of it is reachable by anyone else."""
    second_template = "77777777-7777-4777-8777-777777777771"
    second_assignment = "77777777-7777-4777-8777-777777777772"
    portal.tables["discovery_templates"].append(
        {
            "id": second_template,
            "slug": "discovery-partner",
            "title": "Discovery",
            "subtitle": None,
            "estimated_minutes": 26,
            "created_at": now(),
        }
    )
    portal.tables["discovery_assignments"].append(
        {
            "id": second_assignment,
            "user_id": USER_A,
            "template_id": second_template,
            "title_override": None,
            "current_section_id": None,
            "status": "not_started",
            "started_at": None,
            "core_completed_at": None,
            "completed_at": None,
            "created_at": "2099-01-01T00:00:00+00:00",
        }
    )
    me = client.get("/api/discovery/me", headers=AUTH_A).json()
    assert [a["template_slug"] for a in me["assignments"]] == [
        "discovery-partner",
        "phase2-discovery-test",
    ]
    assert me["assignments"][0]["assignment_id"] == second_assignment
    assert me["assignments"][1]["assignment_id"] == ASSIGNMENT
    assert me["assignments"][0]["title"] == "Discovery"
    # The newest is what opens by default.
    assert me["assignment"]["assignment_id"] == second_assignment
    assert me["assignment"]["template_slug"] == "discovery-partner"
    # Both remain readable through their own scope, by their owner only.
    assert client.get(PREFIX, headers=AUTH_A).status_code == 200
    assert (
        client.get(f"/api/discovery/assignments/{second_assignment}", headers=AUTH_A).status_code
        == 200
    )
    for headers in (AUTH_B, AUTH_ADMIN, AUTH_NOBODY):
        assert (
            client.get(
                f"/api/discovery/assignments/{second_assignment}", headers=headers
            ).status_code
            == 404
        )
    # Nobody else's list carries it: a member holds nothing.
    assert client.get("/api/discovery/me", headers=AUTH_B).json()["assignments"] == []


def test_the_portal_is_absent_when_switched_off(
    portal: PortalBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(monkeypatch, DISCOVERY_ENABLED="false")
    with TestClient(app) as client:
        assert client.get("/api/discovery/me", headers=AUTH_A).status_code == 404
        assert client.get(SECTION_URL, headers=AUTH_A).status_code == 404
        assert put(client, Q_TEXT, {"answer": "x"}).status_code == 404


def test_the_overview_route_is_the_callers_own(client: TestClient) -> None:
    assert client.get(PREFIX, headers=AUTH_A).status_code == 200
    assert client.get(PREFIX, headers=AUTH_B).status_code == 404
    assert client.get(PREFIX, headers=AUTH_ADMIN).status_code == 404
    assert client.get(PREFIX, headers=CLIENT).status_code == 401
    assert client.get("/api/discovery/assignments/not-a-uuid", headers=AUTH_A).status_code == 404


# ------------------------------------------------------------------ the section --
def test_the_section_lists_visible_questions_in_order_with_no_answers_yet(
    client: TestClient,
) -> None:
    section = client.get(SECTION_URL, headers=AUTH_A).json()
    assert section["slug"] == "phase2-core-test"
    assert section["position"] == 1 and section["section_count"] == 1
    assert section["next_section_slug"] is None and section["previous_section_slug"] is None
    ids = [q["id"] for q in section["questions"]]
    assert ids == [Q_TEXT, Q_BOOL, Q_SELECT, Q_CURRENCY, Q_SCENARIO]
    assert all(q["response"] is None for q in section["questions"])
    scenario = section["questions"][-1]
    assert scenario["scenario_brief"]["synthetic"] is True
    assert [o["value"] for o in scenario["options"]][:2] == ["revenue_stable", "nsf_recent"]
    assert section["questions"][3]["required_core"] is False
    assert section["questions"][3]["sensitivity"] == "high"


def test_an_unknown_section_and_a_strangers_section_are_the_same_404(
    client: TestClient,
) -> None:
    assert client.get(f"{PREFIX}/sections/no-such-section", headers=AUTH_A).status_code == 404
    assert client.get(SECTION_URL, headers=AUTH_B).status_code == 404
    assert client.get(SECTION_URL, headers=AUTH_ADMIN).status_code == 404


def test_a_hidden_question_is_absent_and_cannot_be_answered(client: TestClient) -> None:
    """Branch visibility is decided here, not in the browser: the payload has no trace of the
    branched question, and a direct write to it is a 404 like any question outside the bank."""
    before = client.get(SECTION_URL, headers=AUTH_A).json()
    assert Q_BRANCH not in [q["id"] for q in before["questions"]]
    assert "processor" not in json.dumps(before)
    assert put(client, Q_BRANCH, {"answer": "Stripe"}).status_code == 404

    assert put(client, Q_BOOL, {"answer": True}).status_code == 200
    revealed = client.get(SECTION_URL, headers=AUTH_A).json()
    assert [q["id"] for q in revealed["questions"]][-1] == Q_BRANCH
    assert revealed["progress"]["total"] == 6
    assert put(client, Q_BRANCH, {"answer": "Stripe"}).status_code == 200

    # Changing the branching answer hides it again; its stored answer is kept, not counted.
    assert put(client, Q_BOOL, {"answer": False}).status_code == 200
    hidden = client.get(SECTION_URL, headers=AUTH_A).json()
    assert Q_BRANCH not in [q["id"] for q in hidden["questions"]]
    assert hidden["progress"]["total"] == 5
    assert hidden["progress"]["answered"] == 1


# ------------------------------------------------------------------- saving --
def test_saving_one_answer_stores_it_and_moves_the_assignment_along(
    client: TestClient, portal: PortalBackend
) -> None:
    result = put(client, Q_TEXT, {"answer": "  Sells synthetic sandwiches.  "})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["response"]["status"] == "answered"
    assert body["response"]["answer"] == "Sells synthetic sandwiches."
    assert body["response"]["question_version"] == 1
    assert body["section_progress"]["answered"] == 1
    assert body["assignment_progress"]["core_answered"] == 1
    assert body["status"] == "in_progress"

    stored = portal.response_for(Q_TEXT)
    assert stored is not None
    assert stored["user_id"] == USER_A
    assert stored["template_id"] == TEMPLATE
    assert stored["section_id"] == SECTION
    assert stored["question_type"] == "short_text"
    assignment = portal.assignment(ASSIGNMENT)
    assert assignment["status"] == "in_progress"
    assert assignment["current_section_id"] == SECTION
    assert assignment["started_at"] is not None


def test_saving_needs_the_client_header(client: TestClient) -> None:
    response = client.put(
        f"{PREFIX}/responses/{Q_TEXT}",
        json={"answer": "x"},
        headers={"authorization": f"Bearer {TOKEN_A}"},
    )
    assert response.status_code == 403


def test_saving_is_idempotent_and_never_overwrites_silently(
    client: TestClient, portal: PortalBackend
) -> None:
    """One row per question however often it is saved; every superseded value is in history."""
    assert put(client, Q_TEXT, {"answer": "first"}).status_code == 200
    assert put(client, Q_TEXT, {"answer": "first"}).status_code == 200
    assert put(client, Q_TEXT, {"answer": "second"}).status_code == 200
    assert len([r for r in portal.responses() if r["question_id"] == Q_TEXT]) == 1
    assert portal.response_for(Q_TEXT)["answer"] == "second"

    history = client.get(f"{PREFIX}/responses/{Q_TEXT}/history", headers=AUTH_A).json()
    answers = [e["answer"] for e in history["entries"]]
    assert answers == ["first", "first"]  # two updates, each preserving what stood before
    assert all(e["change"] == "updated" for e in history["entries"])
    assert client.get(f"{PREFIX}/responses/{Q_TEXT}/history", headers=AUTH_B).status_code == 404


def test_saving_one_question_leaves_the_others_alone(
    client: TestClient, portal: PortalBackend
) -> None:
    assert put(client, Q_TEXT, {"answer": "kept"}).status_code == 200
    assert put(client, Q_BOOL, {"answer": True}).status_code == 200
    assert put(client, Q_SELECT, {"answer": "daily"}).status_code == 200
    assert portal.response_for(Q_TEXT)["answer"] == "kept"
    assert portal.response_for(Q_BOOL)["answer"] is True
    assert len(portal.tables["discovery_response_history"]) == 0


def test_a_stranger_cannot_save_into_somebody_elses_assignment(
    client: TestClient, portal: PortalBackend
) -> None:
    for headers in (AUTH_B, AUTH_ADMIN, AUTH_NOBODY):
        assert put(client, Q_TEXT, {"answer": "x"}, headers=headers).status_code == 404
    assert put(client, Q_TEXT, {"answer": "x"}, headers=CLIENT).status_code == 401
    assert portal.responses() == []


def test_a_question_outside_the_bank_is_404(client: TestClient, portal: PortalBackend) -> None:
    assert put(client, "not-a-uuid", {"answer": "x"}).status_code == 404
    assert put(client, "99999999-9999-4999-8999-999999999999", {"answer": "x"}).status_code == 404
    portal.tables["discovery_questions"][0]["retired_at"] = now()
    assert put(client, Q_TEXT, {"answer": "x"}).status_code == 404
    assert portal.responses() == []


# --------------------------------------------------------------- validation --
@pytest.mark.parametrize(
    ("question", "body"),
    [
        (Q_TEXT, {"answer": ""}),
        (Q_TEXT, {"answer": "   "}),
        (Q_TEXT, {"answer": 12}),
        (Q_TEXT, {"answer": "x" * 501}),
        (Q_TEXT, {}),
        (Q_BOOL, {"answer": "yes"}),
        (Q_BOOL, {"answer": 1}),
        (Q_SELECT, {"answer": "hourly"}),
        (Q_SELECT, {"answer": ["weekly"]}),
        (Q_CURRENCY, {"answer": 12.5}),
        (Q_CURRENCY, {"answer": "1250"}),
        (Q_CURRENCY, {"answer": True}),
        (Q_CURRENCY, {"answer": 10**14}),
        (Q_SCENARIO, {"answer": "approve"}),
        (Q_SCENARIO, {"scenario": {"decision": "maybe", "confidence": 3}}),
        (Q_SCENARIO, {"scenario": {"decision": "approve", "confidence": 0}}),
        (Q_SCENARIO, {"scenario": {"decision": "approve", "confidence": 6}}),
        (Q_SCENARIO, {"scenario": {"decision": "approve", "confidence": 3.5}}),
        (Q_SCENARIO, {"scenario": {"decision": "deny", "confidence": 3, "reason_codes": ["x"]}}),
        (
            Q_SCENARIO,
            {"scenario": {"decision": "deny", "confidence": 3, "reason_codes": ["other", "other"]}},
        ),
        (
            Q_SCENARIO,
            {"scenario": {"decision": "deny", "confidence": 3, "maximum_exposure_minor": 10.5}},
        ),
        (Q_SCENARIO, {"scenario": {"decision": "deny", "confidence": 3, "notes": "n" * 2001}}),
    ],
    ids=lambda v: v[:8] if isinstance(v, str) else json.dumps(v)[:40],
)
def test_an_answer_that_does_not_fit_the_question_is_refused_before_any_write(
    client: TestClient, portal: PortalBackend, question: str, body: dict[str, Any]
) -> None:
    response = put(client, question, body)
    assert response.status_code == 422, response.text
    assert portal.responses() == []
    assert portal.scenario_rows() == []
    assert portal.assignment(ASSIGNMENT)["status"] == "not_started"


def test_the_accepted_shapes_are_stored_canonically(
    client: TestClient, portal: PortalBackend
) -> None:
    assert put(client, Q_BOOL, {"answer": True}).json()["response"]["answer"] is True
    assert put(client, Q_SELECT, {"answer": "monthly"}).json()["response"]["answer"] == "monthly"
    assert put(client, Q_CURRENCY, {"answer": 250000}).json()["response"]["answer"] == 250000
    assert put(client, Q_CURRENCY, {"answer": -1}).json()["response"]["answer"] == -1
    assert portal.response_for(Q_CURRENCY)["answer"] == -1


# ------------------------------------------------------------------ scenarios --
def test_a_scenario_answer_is_two_rows_read_back_as_one(
    client: TestClient, portal: PortalBackend
) -> None:
    result = put(client, Q_SCENARIO, SCENARIO_OK)
    assert result.status_code == 200, result.text
    view = result.json()["response"]
    assert view["answer"] is None
    assert view["scenario"] == {
        "decision": "manual_review",
        "confidence": 4,
        "reason_codes": ["nsf_recent", "debt_load"],
        "maximum_exposure_minor": 1500000,
        "additional_information_needed": "Three more months of statements.",
        "what_would_change_my_decision": None,
        "notes": "synthetic case",
    }
    generic = portal.response_for(Q_SCENARIO)
    assert generic["question_type"] == "scenario_decision" and generic["answer"] is None
    typed = portal.scenario_rows()
    assert len(typed) == 1 and typed[0]["response_id"] == generic["id"]
    assert typed[0]["user_id"] == USER_A and typed[0]["assignment_id"] == ASSIGNMENT

    section = client.get(SECTION_URL, headers=AUTH_A).json()
    assert section["questions"][-1]["response"]["scenario"]["decision"] == "manual_review"
    listed = client.get(f"{PREFIX}/responses", headers=AUTH_A).json()
    assert listed[0]["scenario"]["confidence"] == 4


def test_a_change_of_mind_on_a_scenario_keeps_the_earlier_decision(
    client: TestClient, portal: PortalBackend
) -> None:
    assert put(client, Q_SCENARIO, SCENARIO_OK).status_code == 200
    changed = {
        **SCENARIO_OK["scenario"],
        "decision": "deny",
        "confidence": 5,
        "reason_codes": ["nsf_recent"],
    }
    assert put(client, Q_SCENARIO, {"scenario": changed}).status_code == 200
    assert len(portal.scenario_rows()) == 1
    assert portal.scenario_rows()[0]["decision"] == "deny"
    history = client.get(f"{PREFIX}/responses/{Q_SCENARIO}/history", headers=AUTH_A).json()
    scenarios = [e["scenario"] for e in history["entries"] if e["scenario"]]
    assert [s["decision"] for s in scenarios] == ["manual_review"]
    assert scenarios[0]["reason_codes"] == ["nsf_recent", "debt_load"]
    assert scenarios[0]["maximum_exposure_minor"] == 1500000


def test_a_scenario_whose_typed_row_cannot_be_written_leaves_nothing_behind(
    client: TestClient, portal: PortalBackend
) -> None:
    """Half a scenario is never an answer. Since phase 3.5 the generic and typed rows are
    written by one database function, so a refused typed write leaves no generic row to remove:
    the person sees the question unanswered and progress does not move."""
    portal.failures[("TYPED", "discovery_scenario_responses")] = [1]
    response = put(client, Q_SCENARIO, SCENARIO_OK)
    assert response.status_code == 502
    assert portal.responses() == []
    assert portal.scenario_rows() == []
    assert portal.tables["discovery_response_history"] == []
    section = client.get(SECTION_URL, headers=AUTH_A).json()
    assert section["questions"][-1]["response"] is None
    assert section["progress"]["answered"] == 0
    # And a retry works.
    assert put(client, Q_SCENARIO, SCENARIO_OK).status_code == 200


def test_a_scenario_row_missing_its_typed_half_is_not_reported_as_answered(
    client: TestClient, portal: PortalBackend
) -> None:
    """If a typed row ever went missing, the read path shows the question again rather than a
    hollow answer, and the next save repairs it in place."""
    assert put(client, Q_SCENARIO, SCENARIO_OK).status_code == 200
    portal.tables["discovery_scenario_responses"].clear()
    section = client.get(SECTION_URL, headers=AUTH_A).json()
    assert section["questions"][-1]["response"] is None
    assert section["progress"]["answered"] == 0
    assert put(client, Q_SCENARIO, SCENARIO_OK).status_code == 200
    assert len(portal.responses()) == 1 and len(portal.scenario_rows()) == 1


# --------------------------------------------------------------------- skipping --
def test_an_optional_question_can_be_skipped_and_a_core_one_cannot(
    client: TestClient, portal: PortalBackend
) -> None:
    refused = client.post(f"{PREFIX}/responses/{Q_TEXT}/skip", headers=AUTH_A)
    assert refused.status_code == 422
    assert "core" in refused.json()["detail"]
    assert portal.responses() == []

    skipped = client.post(f"{PREFIX}/responses/{Q_CURRENCY}/skip", headers=AUTH_A)
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["response"]["status"] == "skipped"
    assert skipped.json()["response"]["answer"] is None
    assert skipped.json()["section_progress"]["answered"] == 1
    assert skipped.json()["section_progress"]["core_answered"] == 0
    assert portal.response_for(Q_CURRENCY)["status"] == "skipped"


def test_skipping_an_answered_optional_question_keeps_the_answer_in_history(
    client: TestClient, portal: PortalBackend
) -> None:
    assert put(client, Q_CURRENCY, {"answer": 250000}).status_code == 200
    assert client.post(f"{PREFIX}/responses/{Q_CURRENCY}/skip", headers=AUTH_A).status_code == 200
    assert put(client, Q_CURRENCY, {"answer": 300000}).status_code == 200
    history = client.get(f"{PREFIX}/responses/{Q_CURRENCY}/history", headers=AUTH_A).json()
    assert [(e["status"], e["answer"]) for e in history["entries"]] == [
        ("skipped", None),
        ("answered", 250000),
    ]


def test_skipping_is_scoped_like_everything_else(client: TestClient) -> None:
    assert client.post(f"{PREFIX}/responses/{Q_CURRENCY}/skip", headers=AUTH_B).status_code == 404
    assert client.post(f"{PREFIX}/responses/{Q_CURRENCY}/skip", headers=CLIENT).status_code == 401
    assert client.post(f"{PREFIX}/responses/{Q_BRANCH}/skip", headers=AUTH_A).status_code == 404


# --------------------------------------------------------- progress and the gate --
def test_core_complete_needs_every_visible_core_question_and_no_more(
    client: TestClient, portal: PortalBackend
) -> None:
    answer_core(client)
    overview = client.get(PREFIX, headers=AUTH_A).json()
    assert overview["progress"]["core_answered"] == 4
    assert overview["progress"]["core_complete"] is True
    assert overview["progress"]["complete"] is False
    assert overview["status"] == "core_complete"
    assignment = portal.assignment(ASSIGNMENT)
    assert assignment["status"] == "core_complete"
    assert assignment["core_completed_at"] is not None
    assert assignment["completed_at"] is None

    me = client.get("/api/me", headers=AUTH_A).json()
    assert me["discovery"]["core_complete"] is True
    assert me["discovery"]["status"] == "core_complete"


def test_a_skip_satisfies_an_optional_question_only(
    client: TestClient, portal: PortalBackend
) -> None:
    answer_core(client)
    assert client.post(f"{PREFIX}/responses/{Q_CURRENCY}/skip", headers=AUTH_A).status_code == 200
    overview = client.get(PREFIX, headers=AUTH_A).json()
    assert overview["progress"]["answered"] == 5
    assert overview["progress"]["complete"] is True
    assert overview["status"] == "complete"
    assert portal.assignment(ASSIGNMENT)["completed_at"] is not None


def test_milestone_timestamps_are_written_once(client: TestClient, portal: PortalBackend) -> None:
    answer_core(client)
    first = portal.assignment(ASSIGNMENT)["core_completed_at"]
    assert put(client, Q_TEXT, {"answer": "revised"}).status_code == 200
    assert portal.assignment(ASSIGNMENT)["core_completed_at"] == first


def test_the_soft_gate_never_blocks_the_workspace(client: TestClient) -> None:
    """A stakeholder with the core unfinished still has a workspace to open. The redirect after
    sign-in is decided in the browser from these two fields; nothing server-side refuses."""
    me = client.get("/api/me", headers=AUTH_A).json()
    assert me["role"] == "stakeholder"
    assert me["discovery"]["core_complete"] is False
    assert me["business"] is not None
    assert client.get("/api/private/workspace", headers=AUTH_A).status_code == 200


def test_the_cached_status_follows_the_arithmetic_not_the_other_way_round(
    client: TestClient, portal: PortalBackend
) -> None:
    """A stale cache is corrected by the next save: the arithmetic is the authority."""
    answer_core(client)
    portal.assignment(ASSIGNMENT)["status"] = "not_started"
    assert put(client, Q_TEXT, {"answer": "again"}).status_code == 200
    assert portal.assignment(ASSIGNMENT)["status"] == "core_complete"


# ------------------------------------------------------------------ resume --
def test_resume_points_at_the_current_section_until_it_is_finished(
    client: TestClient, portal: PortalBackend
) -> None:
    assert put(client, Q_TEXT, {"answer": "one"}).status_code == 200
    me = client.get("/api/discovery/me", headers=AUTH_A).json()["assignment"]
    assert me["current_section_slug"] == "phase2-core-test"
    assert me["resume_section_slug"] == "phase2-core-test"
    assert me["status"] == "in_progress"
    # Every answer comes back on the next read, from the database and nowhere else.
    section = client.get(SECTION_URL, headers=AUTH_A).json()
    hydrated = {q["id"]: q["response"] for q in section["questions"]}
    assert hydrated[Q_TEXT]["answer"] == "one"
    assert hydrated[Q_BOOL] is None
    # Finish the lot: nothing is left to resume.
    answer_core(client)
    assert put(client, Q_CURRENCY, {"answer": 1}).status_code == 200
    done = client.get("/api/discovery/me", headers=AUTH_A).json()["assignment"]
    assert done["progress"]["complete"] is True
    assert done["resume_section_slug"] is None


# ---------------------------------------------------------------- sessions --
def test_a_visit_is_opened_once_and_touched_after(
    client: TestClient, portal: PortalBackend
) -> None:
    first = client.post(f"{PREFIX}/sessions", headers=AUTH_A)
    assert first.status_code == 200, first.text
    second = client.post(f"{PREFIX}/sessions", headers=AUTH_A)
    assert second.json()["session_id"] == first.json()["session_id"]
    assert len(portal.tables["discovery_sessions"]) == 1
    assert put(client, Q_TEXT, {"answer": "x"}).status_code == 200
    assert put(client, Q_BOOL, {"answer": True}).status_code == 200
    assert portal.tables["discovery_sessions"][0]["saves"] == 2
    assert client.post(f"{PREFIX}/sessions", headers=AUTH_B).status_code == 404


def test_a_session_failure_does_not_lose_a_save(client: TestClient, portal: PortalBackend) -> None:
    client.post(f"{PREFIX}/sessions", headers=AUTH_A)
    portal.failures[("PATCH", "discovery_sessions")] = [500]
    result = put(client, Q_TEXT, {"answer": "kept anyway"})
    assert result.status_code == 200
    assert portal.response_for(Q_TEXT)["answer"] == "kept anyway"


# --------------------------------------------------- every stakeholder operation --
def stakeholder_operations() -> list[tuple[str, str]]:
    """Every (method, path) under the stakeholder prefix, from the OpenAPI document."""
    found: list[tuple[str, str]] = []
    for path, methods in app.openapi()["paths"].items():
        if path.startswith("/api/discovery/assignments/"):
            found.extend(
                (m.upper(), path) for m in methods if m in ("get", "post", "put", "delete")
            )
    return sorted(found)


def concrete(path: str) -> str:
    return (
        path.replace("{assignment_id}", ASSIGNMENT)
        .replace("{slug}", "phase2-core-test")
        .replace("{question_id}", Q_TEXT)
    )


def test_every_assignment_route_is_enumerated_and_known() -> None:
    assert len(stakeholder_operations()) == 7
    assert not any(p.startswith("/api/admin/") for _, p in stakeholder_operations())


@pytest.mark.parametrize(("method", "path"), stakeholder_operations())
def test_every_assignment_route_refuses_a_stranger_and_nobody(
    client: TestClient, method: str, path: str
) -> None:
    body = {"answer": "x"} if method == "PUT" else None
    for headers in (AUTH_B, AUTH_ADMIN, AUTH_NOBODY):
        assert client.request(method, concrete(path), json=body, headers=headers).status_code == 404
    anonymous = client.request(method, concrete(path), json=body, headers=CLIENT)
    assert anonymous.status_code == 401
    # No user id of any kind in a body or query changes whose assignment is read.
    smuggled = client.request(
        method,
        concrete(path) + f"?user_id={USER_A}",
        json={"user_id": USER_A, "answer": "x"} if method == "PUT" else None,
        headers=AUTH_B,
    )
    assert smuggled.status_code == 404


def test_the_admin_route_enumeration_still_has_one_exclusion() -> None:
    """Phase 2 adds no admin operation, and the analysis routes added since are all guarded:
    the phase 1 rule, one exclusion, is untouched."""
    from tests.test_discovery import ALL_OPERATIONS, ROLE_UNPROTECTED

    assert {("GET", "/api/admin/discovery/session")} == ROLE_UNPROTECTED
    assert ROLE_UNPROTECTED < ALL_OPERATIONS
    assert TOKEN_ADMIN  # the admin identity is still the one the phase 1 suite uses
