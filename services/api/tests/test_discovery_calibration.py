"""Phase 3.5: transaction calibration through the real routes, against the fake.

Two stakeholders on the phase 3.5 proof template, which the fake loads from the committed
fixture through the real loader so the cases the tests read are the cases the database would
hold. The fake imitates the one database function that writes a typed answer (E7): it either
applies both rows or restores its tables, and it takes the direction from the case, never from
the caller. What the fake cannot prove (the real function, the real constraints, the real
triggers, the grants) is `db/checks/discovery_rls_check.py` and
`db/checks/discovery_calibration_check.py`.
"""

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.discovery.answers import CATEGORIES_BY_DIRECTION
from app.discovery.seed import load_bank, materialise_cases, question_id, section_id, template_id
from app.main import app
from tests.test_discovery import AUTH_ADMIN, AUTH_NOBODY, configure
from tests.test_discovery_portal import PortalBackend
from tests.test_imports import AUTH_A, AUTH_B, CLIENT, USER_A, now

USER_C, TOKEN_C = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa5", "token-c"
AUTH_C = {"authorization": f"Bearer {TOKEN_C}", **CLIENT}

TEMPLATE = template_id("phase35-calibration-test")
SECTION = section_id("phase35-calibration-proof")
Q_UBER = question_id("calib.uber.restaurant.baseline")
Q_SAFEWAY = question_id("calib.safeway.restaurant")
Q_ZELLE = question_id("calib.zelle.restaurant.recurring")
Q_REVERSAL = question_id("calib.reversal.matched")
Q_ACH = question_id("calib.ach.unknown.high")
Q_TUESDAY = question_id("calib.uber.restaurant.tuesday")
ASSIGN_A = "88888888-8888-4888-8888-888888888885"
ASSIGN_C = "88888888-8888-4888-8888-888888888886"
PREFIX_A = f"/api/discovery/assignments/{ASSIGN_A}"
PREFIX_C = f"/api/discovery/assignments/{ASSIGN_C}"
SECTION_URL = f"{PREFIX_A}/sections/phase35-calibration-proof"

OUTFLOW = list(CATEGORIES_BY_DIRECTION["outflow"])
INFLOW = list(CATEGORIES_BY_DIRECTION["inflow"])

MINIMAL = {
    "classification_category": "Personal expense",
    "classification_confidence": 4,
    "underwriting_materiality": "low",
    "underwriting_signal": "negative",
}
FULL = {
    **MINIMAL,
    "decision_impact": "contributes_negative",
    "reason_codes": ["merchant_ambiguous", "timing_atypical"],
    "needs_more_context": True,
    "context_needed": "who was in the car",
    "reasoning": "A Saturday-night ride for a restaurant reads as personal.",
    "handling_recommendation": "ai_review",
}


class CalibrationBackend(PortalBackend):
    """The phase 2 fake with the phase 3.5 template loaded from the committed fixture, and a
    second stakeholder on it."""

    def __init__(self) -> None:
        super().__init__()
        self.tables["profiles"].append(
            {"user_id": USER_C, "role": "stakeholder", "first_name": "Second"}
        )
        bank = load_bank()
        template = next(t for t in bank.templates if t.slug == "phase35-calibration-test")
        section = bank.sections["phase35-calibration-proof"]
        cases, problems = materialise_cases(bank)
        assert problems == []
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
                "scenario_brief": None,
                "required_core": x.required_core,
                "priority": x.priority,
                "sensitivity": x.sensitivity,
                "order": x.order,
                "branch_condition": None,
                "retired_at": None,
            }
            for x in section.questions
        ]
        self.tables["discovery_transaction_cases"] = [
            {"question_id": question_id(key), **case} for key, case in cases.items()
        ]
        self.tables["discovery_assignments"] = [
            {
                "id": ASSIGN_A,
                "user_id": USER_A,
                "template_id": TEMPLATE,
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
                "template_id": TEMPLATE,
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

    def calibrations(self) -> list[dict[str, Any]]:
        return self.tables["discovery_transaction_calibration_responses"]

    def calibration_history(self) -> list[dict[str, Any]]:
        return self.tables["discovery_transaction_calibration_response_history"]


@pytest.fixture
def calib(monkeypatch: pytest.MonkeyPatch):
    import app.supabase as module
    from app.supabase import get_supabase

    monkeypatch.setattr(module, "RETRY_DELAYS", (0.0, 0.0))
    configure(monkeypatch, DISCOVERY_ENABLED="true")
    fake = CalibrationBackend()
    app.dependency_overrides[get_supabase] = lambda: fake.client()
    yield fake
    app.dependency_overrides.clear()


@pytest.fixture
def client(calib: CalibrationBackend):
    with TestClient(app) as c:
        yield c


def put(client: TestClient, question: str, body: Any, headers=AUTH_A, prefix=PREFIX_A):
    return client.put(f"{prefix}/responses/{question}", json=body, headers=headers)


def save(
    client: TestClient, question: str, calibration: dict[str, Any], headers=AUTH_A, prefix=PREFIX_A
):
    return put(client, question, {"calibration": calibration}, headers=headers, prefix=prefix)


# -------------------------------------------------------------- the payload --
def test_the_section_serves_each_case_without_family_metadata_or_a_prediction(
    client: TestClient,
) -> None:
    response = client.get(SECTION_URL, headers=AUTH_A)
    assert response.status_code == 200, response.text
    section = response.json()
    assert [q["question_type"] for q in section["questions"]] == ["transaction_calibration"] * 6
    text = json.dumps(section)
    for forbidden in (
        "case_family",
        "variant",
        "changed_feature",
        "prediction",
        "cashflow",
        'synthetic":',
    ):
        assert forbidden not in text, forbidden
    by_id = {q["id"]: q for q in section["questions"]}
    uber = by_id[Q_UBER]["transaction_case"]
    assert uber["direction"] == "outflow" and uber["amount_minor"] == -1840
    assert uber["descriptor"] == "UBER *TRIP 8N2K4" and uber["merchant"] == "Uber"
    assert uber["categories"] == OUTFLOW
    assert (uber["day_of_week"], uber["frequency_30d"], uber["recurrence_pattern"]) == (
        6,
        11,
        "irregular",
    )
    reversal = by_id[Q_REVERSAL]["transaction_case"]
    assert reversal["direction"] == "inflow" and reversal["categories"] == INFLOW
    assert len(reversal["related_transactions"]) == 1
    assert by_id[Q_ZELLE]["transaction_case"]["context"]["pattern"].startswith("The same amount")
    # The variant is a complete row of its own, and reads like any other case.
    tuesday = by_id[Q_TUESDAY]["transaction_case"]
    assert tuesday["merchant"] == "Uber" and tuesday["day_of_week"] == 2
    assert all(q["scenario_brief"] is None for q in section["questions"])
    assert all(len(q["options"]) == 7 for q in section["questions"])


def test_a_stakeholder_never_sees_another_answer_or_any_prediction(
    client: TestClient, calib: CalibrationBackend
) -> None:
    assert (
        save(client, Q_UBER, {**FULL, "reasoning": "A_PRIVATE_REASONING_SENTINEL"}).status_code
        == 200
    )
    assert (
        save(
            client,
            Q_UBER,
            {**MINIMAL, "classification_category": "Operating expense"},
            headers=AUTH_C,
            prefix=PREFIX_C,
        ).status_code
        == 200
    )
    for path in (
        f"{PREFIX_C}/sections/phase35-calibration-proof",
        PREFIX_C,
        f"{PREFIX_C}/responses",
        "/api/discovery/me",
    ):
        text = client.get(path, headers=AUTH_C).text
        assert "A_PRIVATE_REASONING_SENTINEL" not in text
        assert (
            "Personal expense" not in text
            or path == f"{PREFIX_C}/sections/phase35-calibration-proof"
        )
        assert "prediction" not in text and "cashflow" not in text
    c_view = client.get(f"{PREFIX_C}/sections/phase35-calibration-proof", headers=AUTH_C).json()
    mine = next(q for q in c_view["questions"] if q["id"] == Q_UBER)["response"]["calibration"]
    assert mine["classification_category"] == "Operating expense"


# ------------------------------------------------------------------ saving --
def test_a_minimal_calibration_answer_is_stored_with_the_cases_direction(
    client: TestClient, calib: CalibrationBackend
) -> None:
    response = save(client, Q_UBER, MINIMAL)
    assert response.status_code == 200, response.text
    view = response.json()["response"]
    assert view["status"] == "answered" and view["answer"] is None and view["scenario"] is None
    assert view["calibration"] == {
        "direction": "outflow",
        **MINIMAL,
        "decision_impact": None,
        "reason_codes": [],
        "needs_more_context": False,
        "context_needed": None,
        "reasoning": None,
        "handling_recommendation": None,
    }
    assert response.json()["section_progress"]["answered"] == 1
    assert response.json()["status"] == "in_progress"
    generic = calib.response_for(Q_UBER)
    assert generic["question_type"] == "transaction_calibration" and generic["answer"] is None
    typed = calib.calibrations()
    assert len(typed) == 1 and typed[0]["response_id"] == generic["id"]
    assert typed[0]["direction"] == "outflow" and typed[0]["user_id"] == USER_A


def test_every_optional_field_round_trips(client: TestClient) -> None:
    response = save(client, Q_UBER, FULL)
    assert response.status_code == 200, response.text
    stored = response.json()["response"]["calibration"]
    assert stored == {"direction": "outflow", **FULL}
    section = client.get(SECTION_URL, headers=AUTH_A).json()
    hydrated = next(q for q in section["questions"] if q["id"] == Q_UBER)["response"]
    assert hydrated["calibration"] == stored
    listed = client.get(f"{PREFIX_A}/responses", headers=AUTH_A).json()
    assert listed[0]["calibration"]["handling_recommendation"] == "ai_review"


def test_an_inflow_case_takes_inflow_categories(client: TestClient) -> None:
    ok = save(client, Q_REVERSAL, {**MINIMAL, "classification_category": "Overdraft/Reversals"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["response"]["calibration"]["direction"] == "inflow"
    for category in OUTFLOW:
        if category == "Unclassified":
            continue
        assert (
            save(client, Q_REVERSAL, {**MINIMAL, "classification_category": category}).status_code
            == 422
        )
    assert (
        save(client, Q_REVERSAL, {**MINIMAL, "classification_category": "Unclassified"}).status_code
        == 200
    )


def test_an_outflow_case_refuses_inflow_categories(
    client: TestClient, calib: CalibrationBackend
) -> None:
    for category in INFLOW:
        if category == "Unclassified":
            continue
        refused = save(client, Q_UBER, {**MINIMAL, "classification_category": category})
        assert refused.status_code == 422, category
        assert "outflow buckets" in refused.json()["detail"]
    assert calib.responses() == [] and calib.calibrations() == []
    for category in OUTFLOW:
        assert (
            save(client, Q_UBER, {**MINIMAL, "classification_category": category}).status_code
            == 200
        )


def test_the_database_side_direction_check_is_the_second_lock(
    client: TestClient, calib: CalibrationBackend
) -> None:
    """The fake imitates the row-level constraint: a category from the other direction, sent
    straight to the write function, is refused and nothing is written, even though the API's own
    validation was bypassed."""
    body = {
        "p_assignment_id": ASSIGN_A,
        "p_user_id": USER_A,
        "p_template_id": TEMPLATE,
        "p_section_id": SECTION,
        "p_question_id": Q_UBER,
        "p_question_version": 1,
        "p_question_type": "transaction_calibration",
        "p_status": "answered",
        "p_note": None,
        "p_typed": {**MINIMAL, "classification_category": "Organic revenue"},
    }
    response = calib.rpc("save_discovery_typed_response", body)
    assert response.status_code == 400
    assert calib.responses() == [] and calib.calibrations() == []


def test_a_plain_answer_to_a_calibration_question_is_refused(client: TestClient) -> None:
    assert put(client, Q_UBER, {"answer": "Personal expense"}).status_code == 422
    assert (
        put(client, Q_UBER, {"scenario": {"decision": "approve", "confidence": 3}}).status_code
        == 422
    )


REFUSED: list[dict[str, Any] | Any] = [
    {k: v for k, v in MINIMAL.items() if k != "classification_category"},
    {k: v for k, v in MINIMAL.items() if k != "classification_confidence"},
    {k: v for k, v in MINIMAL.items() if k != "underwriting_materiality"},
    {k: v for k, v in MINIMAL.items() if k != "underwriting_signal"},
    {**MINIMAL, "classification_category": "Groceries"},
    {**MINIMAL, "classification_category": 4},
    {**MINIMAL, "classification_confidence": 0},
    {**MINIMAL, "classification_confidence": 6},
    {**MINIMAL, "classification_confidence": 2.5},
    {**MINIMAL, "classification_confidence": "4"},
    {**MINIMAL, "classification_confidence": True},
    {**MINIMAL, "underwriting_materiality": "severe"},
    {**MINIMAL, "underwriting_materiality": 3},
    {**MINIMAL, "underwriting_signal": "bullish"},
    {**MINIMAL, "underwriting_signal": None},
    {**MINIMAL, "decision_impact": "decline"},
    {**MINIMAL, "handling_recommendation": "ignore"},
    {**MINIMAL, "reason_codes": ["not_offered"]},
    {**MINIMAL, "reason_codes": ["merchant_ambiguous", "merchant_ambiguous"]},
    {**MINIMAL, "reason_codes": ["merchant_ambiguous"] * 21},
    {**MINIMAL, "reason_codes": "merchant_ambiguous"},
    {**MINIMAL, "needs_more_context": "yes"},
    {**MINIMAL, "context_needed": "the surname"},  # named without the flag
    {**MINIMAL, "context_needed": "x" * 2001, "needs_more_context": True},
    {**MINIMAL, "reasoning": "x" * 2001},
    {**MINIMAL, "reasoning": 7},
    {**MINIMAL, "direction": "inflow"},
    {**MINIMAL, "user_id": USER_C},
    {**MINIMAL, "amount_minor": 12.5},
    {**MINIMAL, "unexpected": True},
    "Personal expense",
    ["Personal expense"],
    None,
    {},
]


@pytest.mark.parametrize("calibration", REFUSED, ids=[str(i) for i in range(len(REFUSED))])
def test_a_malformed_calibration_is_refused_before_any_write(
    client: TestClient, calib: CalibrationBackend, calibration: Any
) -> None:
    response = save(client, Q_UBER, calibration)
    assert response.status_code == 422, response.text
    assert calib.responses() == [] and calib.calibrations() == []
    assert calib.assignment(ASSIGN_A)["status"] == "not_started"


# ------------------------------------------------------------- atomicity --
def test_a_typed_failure_leaves_no_generic_row(
    client: TestClient, calib: CalibrationBackend
) -> None:
    calib.failures[("TYPED", "discovery_transaction_calibration_responses")] = [1]
    assert save(client, Q_UBER, MINIMAL).status_code == 502
    assert calib.responses() == [] and calib.calibrations() == []
    assert calib.tables["discovery_response_history"] == []
    assert save(client, Q_UBER, MINIMAL).status_code == 200
    assert len(calib.responses()) == 1 and len(calib.calibrations()) == 1


def test_a_typed_failure_on_an_update_leaves_the_previous_pair_standing(
    client: TestClient, calib: CalibrationBackend
) -> None:
    assert save(client, Q_UBER, MINIMAL).status_code == 200
    calib.failures[("TYPED", "discovery_transaction_calibration_responses")] = [1]
    assert (
        save(client, Q_UBER, {**MINIMAL, "classification_category": "Payroll"}).status_code == 502
    )
    assert calib.calibrations()[0]["classification_category"] == "Personal expense"
    assert calib.response_for(Q_UBER)["status"] == "answered"
    assert calib.calibration_history() == [] and calib.tables["discovery_response_history"] == []


def test_a_write_function_failure_stores_nothing(
    client: TestClient, calib: CalibrationBackend
) -> None:
    calib.failures[("RPC", "save_discovery_typed_response")] = [500]
    assert save(client, Q_UBER, MINIMAL).status_code == 502
    assert calib.responses() == [] and calib.calibrations() == []


# ------------------------------------------------------- shared question --
def test_two_stakeholders_answer_one_question_independently(
    client: TestClient, calib: CalibrationBackend
) -> None:
    a = save(client, Q_UBER, FULL)
    c = save(
        client,
        Q_UBER,
        {
            **MINIMAL,
            "classification_category": "Operating expense",
            "classification_confidence": 2,
            "underwriting_materiality": "high",
            "underwriting_signal": "positive",
        },
        headers=AUTH_C,
        prefix=PREFIX_C,
    )
    assert a.status_code == 200 and c.status_code == 200
    generic = [r for r in calib.responses() if r["question_id"] == Q_UBER]
    typed = calib.calibrations()
    assert len(generic) == 2 and len({r["id"] for r in generic}) == 2
    assert {r["user_id"] for r in generic} == {USER_A, USER_C}
    assert len(typed) == 2 and len({t["response_id"] for t in typed}) == 2
    assert {t["user_id"] for t in typed} == {USER_A, USER_C}
    assert {
        (
            t["classification_category"],
            t["classification_confidence"],
            t["underwriting_materiality"],
            t["underwriting_signal"],
        )
        for t in typed
    } == {
        ("Personal expense", 4, "low", "negative"),
        ("Operating expense", 2, "high", "positive"),
    }
    a_view = client.get(SECTION_URL, headers=AUTH_A).json()
    c_view = client.get(f"{PREFIX_C}/sections/phase35-calibration-proof", headers=AUTH_C).json()
    assert (
        next(q for q in a_view["questions"] if q["id"] == Q_UBER)["response"]["calibration"][
            "classification_category"
        ]
        == "Personal expense"
    )
    assert (
        next(q for q in c_view["questions"] if q["id"] == Q_UBER)["response"]["calibration"][
            "classification_category"
        ]
        == "Operating expense"
    )
    a_over = client.get(PREFIX_A, headers=AUTH_A).json()
    c_over = client.get(PREFIX_C, headers=AUTH_C).json()
    assert a_over["progress"]["answered"] == 1 and c_over["progress"]["answered"] == 1


def test_neither_stakeholder_can_read_or_write_the_others_calibration(
    client: TestClient, calib: CalibrationBackend
) -> None:
    assert save(client, Q_UBER, FULL).status_code == 200
    before = json.dumps(calib.calibrations(), sort_keys=True)
    for method, path, body in (
        ("GET", PREFIX_A, None),
        ("GET", SECTION_URL, None),
        ("GET", f"{PREFIX_A}/responses", None),
        ("GET", f"{PREFIX_A}/responses/{Q_UBER}/history", None),
        ("PUT", f"{PREFIX_A}/responses/{Q_UBER}", {"calibration": MINIMAL}),
        ("POST", f"{PREFIX_A}/responses/{Q_TUESDAY}/skip", None),
    ):
        for headers in (AUTH_C, AUTH_B, AUTH_ADMIN, AUTH_NOBODY):
            response = client.request(method, path, json=body, headers=headers)
            assert response.status_code == 404, (method, path, headers)
            assert "who was in the car" not in response.text
        assert client.request(method, path, json=body, headers=CLIENT).status_code == 401
    assert json.dumps(calib.calibrations(), sort_keys=True) == before


# ----------------------------------------------------------------- history --
FIELD_CHANGES = [
    ("classification_category", "Operating expense"),
    ("classification_confidence", 2),
    ("underwriting_materiality", "critical"),
    ("underwriting_signal", "positive"),
    ("decision_impact", "manual_review"),
    ("reason_codes", ["vertical_decides"]),
    ("needs_more_context", False),
    ("context_needed", "the driver"),
    ("reasoning", "on reflection"),
    ("handling_recommendation", "human_review"),
]


@pytest.mark.parametrize(
    ("field_name", "new_value"), FIELD_CHANGES, ids=[f for f, _ in FIELD_CHANGES]
)
def test_changing_one_field_preserves_the_whole_previous_answer(
    client: TestClient, calib: CalibrationBackend, field_name: str, new_value: Any
) -> None:
    assert save(client, Q_UBER, FULL).status_code == 200
    changed = {**FULL, field_name: new_value}
    if field_name == "needs_more_context":
        changed["context_needed"] = None
    assert save(client, Q_UBER, changed).status_code == 200
    history = client.get(f"{PREFIX_A}/responses/{Q_UBER}/history", headers=AUTH_A).json()
    entries = [e for e in history["entries"] if e["calibration"]]
    assert len(entries) == 1 and entries[0]["change"] == "updated"
    assert entries[0]["calibration"] == {"direction": "outflow", **FULL}
    assert len(calib.calibrations()) == 1
    assert calib.calibrations()[0][field_name] == new_value


def test_deleting_the_answer_records_both_halves(
    client: TestClient, calib: CalibrationBackend
) -> None:
    assert save(client, Q_UBER, FULL).status_code == 200
    generic = calib.response_for(Q_UBER)
    calib.client().rest("DELETE", "discovery_responses", params={"id": f"eq.{generic['id']}"})
    assert calib.calibrations() == []
    history = client.get(f"{PREFIX_A}/responses/{Q_UBER}/history", headers=AUTH_A).json()
    deleted = [e for e in history["entries"] if e["change"] == "deleted"]
    assert len(deleted) == 2
    assert any(
        e["calibration"] and e["calibration"]["classification_category"] == "Personal expense"
        for e in deleted
    )
    assert any(e["calibration"] is None and e["status"] == "answered" for e in deleted)


def test_skipping_the_optional_variant_removes_the_typed_row_and_records_it(
    client: TestClient, calib: CalibrationBackend
) -> None:
    assert save(client, Q_TUESDAY, MINIMAL).status_code == 200
    skipped = client.post(f"{PREFIX_A}/responses/{Q_TUESDAY}/skip", headers=AUTH_A)
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["response"]["status"] == "skipped"
    assert skipped.json()["response"]["calibration"] is None
    assert calib.calibrations() == []
    assert calib.response_for(Q_TUESDAY)["status"] == "skipped"
    history = client.get(f"{PREFIX_A}/responses/{Q_TUESDAY}/history", headers=AUTH_A).json()
    assert [(e["change"], bool(e["calibration"])) for e in history["entries"]] == [
        ("deleted", True),
        ("updated", False),
    ]
    assert client.post(f"{PREFIX_A}/responses/{Q_UBER}/skip", headers=AUTH_A).status_code == 422


def test_saving_the_same_answer_twice_is_one_row(
    client: TestClient, calib: CalibrationBackend
) -> None:
    assert save(client, Q_UBER, MINIMAL).status_code == 200
    assert save(client, Q_UBER, MINIMAL).status_code == 200
    assert len(calib.responses()) == 1 and len(calib.calibrations()) == 1


# ------------------------------------------------------------- progress --
def test_the_five_core_cases_complete_the_core_and_the_variant_completes_the_set(
    client: TestClient, calib: CalibrationBackend
) -> None:
    for question, category in (
        (Q_UBER, "Personal expense"),
        (Q_SAFEWAY, "Operating expense"),
        (Q_ZELLE, "Payroll"),
        (Q_ACH, "Unclassified"),
    ):
        assert (
            save(client, question, {**MINIMAL, "classification_category": category}).status_code
            == 200
        )
    assert client.get(PREFIX_A, headers=AUTH_A).json()["progress"]["core_complete"] is False
    assert (
        save(
            client, Q_REVERSAL, {**MINIMAL, "classification_category": "Overdraft/Reversals"}
        ).status_code
        == 200
    )
    overview = client.get(PREFIX_A, headers=AUTH_A).json()
    assert overview["progress"]["core_complete"] is True and overview["status"] == "core_complete"
    me = client.get("/api/me", headers=AUTH_A).json()
    assert me["discovery"]["core_complete"] is True
    assert save(client, Q_TUESDAY, MINIMAL).status_code == 200
    assert client.get(PREFIX_A, headers=AUTH_A).json()["status"] == "complete"
