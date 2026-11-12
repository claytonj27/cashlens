"""The admin analysis surface: who may read it, what it computes, and what it never leaks.

A bank shaped like the real one on a small scale: two founders on templates that share the
founder sections, an engineer on his own, a reviewer holding two templates, the author as
admin, and an ordinary member. One question of every plain type, a calibration family with a
controlled variant and a progressive reveal, and a borrower scenario the founders split on.
The comparison rules are tested on literals as well, because they are the claims the
dashboard makes.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.discovery import analysis as an
from app.discovery import taxonomy as tx
from app.discovery.seed import question_id, section_id, template_id
from app.main import app
from tests.test_discovery import AUTH_ADMIN, AUTH_NOBODY, USER_ADMIN, DiscoveryBackend, configure
from tests.test_imports import AUTH_A, AUTH_B, CLIENT, USER_A, USER_B, now

USER_C, TOKEN_C = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa5", "token-c"
USER_D, TOKEN_D = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa6", "token-d"
USER_R, TOKEN_R = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa7", "token-r"
AUTH_C = {"authorization": f"Bearer {TOKEN_C}", **CLIENT}
AUTH_D = {"authorization": f"Bearer {TOKEN_D}", **CLIENT}
AUTH_R = {"authorization": f"Bearer {TOKEN_R}", **CLIENT}

T_LEAD, T_PARTNER, T_ENGINEER = (
    template_id("discovery-lead"),
    template_id("discovery-partner"),
    template_id("discovery-engineer"),
)
S_CORE, S_REC = section_id("founder-core-business"), section_id("founder-rec-underwriting")
S_CAL, S_SCEN = section_id("founder-cal-core"), section_id("founder-scen-core")
S_JO, S_AL = section_id("lead-core-questions"), section_id("partner-core-questions")
S_DAN, S_DS = section_id("engineer-core-ownership"), section_id("engineer-scen-rec")

A_LEAD, A_PARTNER, A_ENGINEER = (
    "99999999-9999-4999-8999-999999999901",
    "99999999-9999-4999-8999-999999999902",
    "99999999-9999-4999-8999-999999999903",
)
A_REV_J, A_REV_A = "99999999-9999-4999-8999-999999999904", "99999999-9999-4999-8999-999999999905"

BANDS = [
    {"value": v, "label": lb}
    for v, lb in (
        ("fewer_than_5", "Fewer than 5"),
        ("5_10", "5–10"),
        ("11_25", "11–25"),
        ("26_50", "26–50"),
        ("prefer_not", "Prefer not to answer"),
    )
]
SEGMENTS = [{"value": v, "label": v} for v in ("trucking", "cleaning", "restaurants", "retail")]
PRIORITIES = [{"value": v, "label": v} for v in ("revenue", "liquidity", "debt", "owner")]
KEEP = [
    {"value": v, "label": v}
    for v in ("core_backend_and_apis", "data_pipeline_and_bank_connections", "frontend")
]
DECISIONS = [
    {"value": v, "label": lb}
    for v, lb in (("approve", "Approve"), ("deny", "Decline"), ("manual_review", "Manual review"))
]
REASONS = [{"value": v, "label": v} for v in ("nsf_recent", "debt_load", "thin_history")]
CATEGORIES = [{"value": v, "label": v} for v in ("Organic revenue", "Loans income", "Unclassified")]

SECRET_LEAD = "LEAD_FREE_TEXT_SENTINEL"
SECRET_PARTNER = "PARTNER_FREE_TEXT_SENTINEL"
SECRET_ENGINEER = "ENGINEER_FREE_TEXT_SENTINEL"


def qrow(
    section: str, key: str, kind: str, order: int, core: bool = True, **extra: Any
) -> dict[str, Any]:
    row = {
        "id": question_id(key),
        "section_id": section,
        "question_key": key,
        "question_version": 1,
        "question_text": f"{key}?",
        "helper_text": None,
        "question_type": kind,
        "options": [],
        "scenario_brief": None,
        "required_core": core,
        "priority": 90 if core else 50,
        "sensitivity": "low",
        "order": order,
        "branch_condition": None,
        "retired_at": None,
    }
    row.update(extra)
    return row


class DashboardBackend(DiscoveryBackend):
    def __init__(self) -> None:
        super().__init__()
        self.tables["profiles"] = [
            {
                "user_id": USER_A,
                "role": "stakeholder",
                "first_name": "Lead",
                "display_name": "Lead T",
            },
            {
                "user_id": USER_C,
                "role": "stakeholder",
                "first_name": "Partner",
                "display_name": "Partner M",
            },
            {
                "user_id": USER_D,
                "role": "stakeholder",
                "first_name": "Engineer",
                "display_name": "Engineer K",
            },
            {
                "user_id": USER_R,
                "role": "stakeholder",
                "first_name": "Reviewer",
                "display_name": None,
            },
            {"user_id": USER_B, "role": "member", "first_name": None},
            {"user_id": USER_ADMIN, "role": "admin", "first_name": "Clayton"},
        ]
        self.tables["discovery_templates"] = [
            {
                "id": T_LEAD,
                "slug": "discovery-lead",
                "title": "Discovery",
                "created_at": now(),
            },
            {
                "id": T_PARTNER,
                "slug": "discovery-partner",
                "title": "Discovery",
                "created_at": now(),
            },
            {
                "id": T_ENGINEER,
                "slug": "discovery-engineer",
                "title": "Discovery — engineering",
                "created_at": now(),
            },
            {
                "id": template_id("phase2-discovery-test"),
                "slug": "phase2-discovery-test",
                "title": "proof",
                "created_at": now(),
            },
        ]
        sections = [
            (S_CORE, "founder-core-business", True),
            (S_REC, "founder-rec-underwriting", False),
            (S_CAL, "founder-cal-core", True),
            (S_SCEN, "founder-scen-core", True),
            (S_JO, "lead-core-questions", True),
            (S_AL, "partner-core-questions", True),
            (S_DAN, "engineer-core-ownership", True),
            (S_DS, "engineer-scen-rec", False),
        ]
        self.tables["discovery_sections"] = [
            {
                "id": sid,
                "slug": slug,
                "title": slug,
                "description": None,
                "is_core": core,
                "estimated_minutes": 5,
            }
            for sid, slug, core in sections
        ]
        links = {
            T_LEAD: [S_CORE, S_REC, S_CAL, S_SCEN, S_JO],
            T_PARTNER: [S_CORE, S_REC, S_CAL, S_SCEN, S_AL],
            T_ENGINEER: [S_DAN, S_DS],
        }
        self.tables["discovery_template_sections"] = [
            {"template_id": t, "section_id": s, "order": i, "created_at": now()}
            for t, ss in links.items()
            for i, s in enumerate(ss)
        ]
        brief = {"title": "Growing every month, and four funders in the account", "ask": "$150,000"}
        self.tables["discovery_questions"] = [
            qrow(S_CORE, "sf.scale.applications-month", "single_select", 0, options=BANDS),
            qrow(S_CORE, "sf.scale.active-definition", "short_text", 1),
            qrow(S_CORE, "sf.cust.segments", "multi_select", 2, options=SEGMENTS),
            qrow(S_CORE, "sf.uw.hard-stop-nsf", "boolean", 3),
            qrow(S_CORE, "sf.uw.max-exposure", "currency", 4),
            qrow(S_CORE, "sf.risk.debt-share", "percentage", 5),
            qrow(S_CORE, "sf.uw.confidence", "scale", 6),
            qrow(S_CORE, "sf.uw.priorities", "ranking", 7, options=PRIORITIES),
            qrow(S_CORE, "sf.scale.approval-rate", "single_select", 8, options=BANDS),
            qrow(S_REC, "sf.uw.manual-review-share", "number", 0, core=False),
            qrow(S_CAL, "tc-01", "transaction_calibration", 0),
            qrow(S_CAL, "tc-02", "transaction_calibration", 1),
            qrow(S_CAL, "tc-06", "transaction_calibration", 2),
            qrow(S_CAL, "tc-06.reveal", "transaction_calibration", 3),
            qrow(S_SCEN, "bs-01", "scenario_decision", 0, options=DECISIONS, scenario_brief=brief),
            qrow(S_JO, "jo.capital.runway", "currency", 0),
            qrow(S_AL, "al.customers.count", "number", 0),
            qrow(S_DAN, "en.own.keep", "multi_select", 0, options=KEEP),
            qrow(S_DAN, "en.own.hand-off", "multi_select", 1, options=KEEP),
            qrow(S_DAN, "en.prod.blocked-by-decision", "multi_select", 2, options=KEEP, core=False),
            qrow(S_DAN, "en.debt.handle-with-care", "long_text", 3),
            qrow(S_DS, "ds-01", "long_text", 0, core=False),
        ]
        self.tables["discovery_transaction_cases"] = [
            self.case("tc-01", "cf-01-ach-inflow", "baseline", 1850000),
            self.case(
                "tc-02",
                "cf-01-ach-inflow",
                "weekly-repeat",
                1850000,
                changed_feature="frequency_30d",
                baseline_question_id=question_id("tc-01"),
            ),
            self.case("tc-06", "cf-05-factoring-advance", "baseline", 4200000),
            self.case(
                "tc-06.reveal",
                "cf-05-factoring-advance",
                "baseline",
                4200000,
                reveal_of_question_id=question_id("tc-06"),
                reveal={"fact": "A second credit from the same originator follows each advance."},
            ),
        ]
        self.tables["discovery_assignments"] = [
            self.assign(A_LEAD, USER_A, T_LEAD, "2026-09-21T03:56:44Z", "in_progress"),
            self.assign(A_PARTNER, USER_C, T_PARTNER, "2026-09-21T03:56:46Z", "in_progress"),
            self.assign(A_ENGINEER, USER_D, T_ENGINEER, "2026-09-21T03:56:47Z", "not_started"),
            self.assign(A_REV_J, USER_R, T_LEAD, "2026-09-20T01:00:00Z", "in_progress"),
            self.assign(A_REV_A, USER_R, T_PARTNER, "2026-09-20T01:00:01Z", "not_started"),
        ]
        for table in (
            "discovery_responses",
            "discovery_response_history",
            "discovery_scenario_responses",
            "discovery_scenario_response_history",
            "discovery_sessions",
            "discovery_transaction_calibration_responses",
            "discovery_transaction_calibration_response_history",
        ):
            self.tables[table] = []
        self.seed_answers()

    @staticmethod
    def case(key: str, family: str, variant: str, amount: int, **extra: Any) -> dict[str, Any]:
        row = {
            "question_id": question_id(key),
            "direction": "inflow",
            "amount_minor": amount,
            "descriptor": f"ACH CREDIT {key}",
            "business_vertical": "Trucking",
            "business_description": None,
            "account_type": None,
            "merchant": None,
            "counterparty": None,
            "payment_rail": None,
            "posted_date": None,
            "day_of_week": None,
            "time_of_day": None,
            "memo": None,
            "frequency_30d": None,
            "frequency_90d": None,
            "average_amount_minor": None,
            "amount_min_minor": None,
            "amount_max_minor": None,
            "recurrence_pattern": None,
            "related_transactions": [],
            "context": {},
            "case_family": family,
            "variant": variant,
            "changed_feature": None,
            "baseline_question_id": None,
            "authored_changed_feature": None,
            "reveal_of_question_id": None,
            "reveal": None,
        }
        row.update(extra)
        return row

    @staticmethod
    def assign(aid: str, user: str, template: str, created: str, status: str) -> dict[str, Any]:
        return {
            "id": aid,
            "user_id": user,
            "template_id": template,
            "title_override": None,
            "current_section_id": None,
            "status": status,
            "started_at": None,
            "core_completed_at": None,
            "completed_at": None,
            "created_at": created,
        }

    def answer(
        self,
        aid: str,
        user: str,
        template: str,
        section: str,
        key: str,
        kind: str,
        value: Any = None,
        *,
        status: str = "answered",
        note: str | None = None,
        scenario: dict[str, Any] | None = None,
        calibration: dict[str, Any] | None = None,
        at: str = "2026-09-21T10:00:00Z",
    ) -> None:
        rid = f"r-{aid[-2:]}-{key}"
        self.tables["discovery_responses"].append(
            {
                "id": rid,
                "assignment_id": aid,
                "user_id": user,
                "template_id": template,
                "section_id": section,
                "question_id": question_id(key),
                "question_version": 1,
                "question_type": kind,
                "status": status,
                "answer": value,
                "note": note,
                "created_at": at,
                "updated_at": at,
            }
        )
        if scenario:
            self.tables["discovery_scenario_responses"].append(
                {
                    "response_id": rid,
                    "assignment_id": aid,
                    "user_id": user,
                    "question_id": question_id(key),
                    **scenario,
                }
            )
        if calibration:
            self.tables["discovery_transaction_calibration_responses"].append(
                {
                    "response_id": rid,
                    "assignment_id": aid,
                    "user_id": user,
                    "question_id": question_id(key),
                    "direction": "inflow",
                    **calibration,
                }
            )

    def seed_answers(self) -> None:
        j = dict(aid=A_LEAD, user=USER_A, template=T_LEAD)
        a = dict(aid=A_PARTNER, user=USER_C, template=T_PARTNER)
        d = dict(aid=A_ENGINEER, user=USER_D, template=T_ENGINEER)
        # adjacent bands (ROUGH), MATERIAL boolean, DIFFERENT number, multi-select, ranking
        self.answer(
            **j,
            section=S_CORE,
            key="sf.scale.applications-month",
            kind="single_select",
            value="11_25",
        )
        self.answer(
            **a,
            section=S_CORE,
            key="sf.scale.applications-month",
            kind="single_select",
            value="26_50",
        )
        self.answer(
            **j,
            section=S_CORE,
            key="sf.scale.active-definition",
            kind="short_text",
            value=SECRET_LEAD,
            note="a note",
        )
        self.answer(
            **a,
            section=S_CORE,
            key="sf.scale.active-definition",
            kind="short_text",
            value=SECRET_PARTNER,
        )
        self.answer(
            **j,
            section=S_CORE,
            key="sf.cust.segments",
            kind="multi_select",
            value=["trucking", "cleaning"],
        )
        self.answer(
            **a,
            section=S_CORE,
            key="sf.cust.segments",
            kind="multi_select",
            value=["trucking", "retail"],
        )
        self.answer(**j, section=S_CORE, key="sf.uw.hard-stop-nsf", kind="boolean", value=True)
        self.answer(**a, section=S_CORE, key="sf.uw.hard-stop-nsf", kind="boolean", value=False)
        self.answer(**j, section=S_CORE, key="sf.uw.max-exposure", kind="currency", value=5000000)
        self.answer(**a, section=S_CORE, key="sf.uw.max-exposure", kind="currency", value=7500000)
        self.answer(**j, section=S_CORE, key="sf.risk.debt-share", kind="percentage", value=15)
        self.answer(**a, section=S_CORE, key="sf.risk.debt-share", kind="percentage", value=18)
        self.answer(**j, section=S_CORE, key="sf.uw.confidence", kind="scale", value=4)
        self.answer(
            **j,
            section=S_CORE,
            key="sf.uw.priorities",
            kind="ranking",
            value=["revenue", "liquidity", "debt"],
        )
        self.answer(
            **a,
            section=S_CORE,
            key="sf.uw.priorities",
            kind="ranking",
            value=["liquidity", "revenue", "owner"],
        )
        self.answer(
            **a,
            section=S_CORE,
            key="sf.scale.approval-rate",
            kind="single_select",
            value="prefer_not",
        )
        self.answer(
            **j,
            section=S_CORE,
            key="sf.scale.approval-rate",
            kind="single_select",
            status="skipped",
        )
        self.answer(**j, section=S_REC, key="sf.uw.manual-review-share", kind="number", value=40)
        self.answer(**a, section=S_REC, key="sf.uw.manual-review-share", kind="number", value=42)
        # calibration: baseline agreed, variant moved Partner only; reveal moved Lead's class
        cal = dict(
            classification_confidence=3,
            underwriting_materiality="medium",
            underwriting_signal="neutral",
            decision_impact=None,
            reason_codes=[],
            needs_more_context=False,
            context_needed=None,
            reasoning=None,
            handling_recommendation=None,
        )
        self.answer(
            **j,
            section=S_CAL,
            key="tc-01",
            kind="transaction_calibration",
            calibration={**cal, "classification_category": "Unclassified"},
        )
        self.answer(
            **a,
            section=S_CAL,
            key="tc-01",
            kind="transaction_calibration",
            calibration={
                **cal,
                "classification_category": "Unclassified",
                "reasoning": "one row is not enough",
            },
        )
        self.answer(
            **j,
            section=S_CAL,
            key="tc-02",
            kind="transaction_calibration",
            calibration={**cal, "classification_category": "Unclassified"},
        )
        self.answer(
            **a,
            section=S_CAL,
            key="tc-02",
            kind="transaction_calibration",
            calibration={
                **cal,
                "classification_category": "Organic revenue",
                "classification_confidence": 4,
            },
        )
        self.answer(
            **j,
            section=S_CAL,
            key="tc-06",
            kind="transaction_calibration",
            calibration={
                **cal,
                "classification_category": "Unclassified",
                "underwriting_signal": "negative",
                "underwriting_materiality": "high",
            },
        )
        self.answer(
            **a,
            section=S_CAL,
            key="tc-06",
            kind="transaction_calibration",
            calibration={
                **cal,
                "classification_category": "Loans income",
                "underwriting_signal": "positive",
            },
        )
        self.answer(
            **j,
            section=S_CAL,
            key="tc-06.reveal",
            kind="transaction_calibration",
            calibration={
                **cal,
                "classification_category": "Loans income",
                "classification_confidence": 5,
                "underwriting_signal": "negative",
                "underwriting_materiality": "high",
            },
        )
        # scenario: approve against deny
        sc = dict(
            confidence=4,
            reason_codes=["debt_load"],
            maximum_exposure_minor=5000000,
            additional_information_needed=None,
            what_would_change_my_decision=None,
            notes=None,
        )
        self.answer(
            **j,
            section=S_SCEN,
            key="bs-01",
            kind="scenario_decision",
            scenario={**sc, "decision": "approve"},
        )
        self.answer(
            **a,
            section=S_SCEN,
            key="bs-01",
            kind="scenario_decision",
            scenario={
                **sc,
                "decision": "deny",
                "reason_codes": ["debt_load", "nsf_recent"],
                "maximum_exposure_minor": None,
                "notes": "four funders",
            },
        )
        # own banks
        self.answer(**j, section=S_JO, key="jo.capital.runway", kind="currency", value=120000000)
        # Engineer
        self.answer(
            **d,
            section=S_DAN,
            key="en.own.keep",
            kind="multi_select",
            value=["core_backend_and_apis"],
        )
        self.answer(
            **d, section=S_DAN, key="en.own.hand-off", kind="multi_select", value=["frontend"]
        )
        self.answer(
            **d,
            section=S_DAN,
            key="en.debt.handle-with-care",
            kind="long_text",
            value=SECRET_ENGINEER,
        )
        self.tables["discovery_scenario_response_history"].append(
            {
                "id": "h-1",
                "response_id": f"r-{A_LEAD[-2:]}-bs-01",
                "assignment_id": A_LEAD,
                "user_id": USER_A,
                "question_id": question_id("bs-01"),
                "decision": "manual_review",
                "confidence": 2,
                "reason_codes": ["thin_history"],
                "maximum_exposure_minor": None,
                "additional_information_needed": "two more months",
                "what_would_change_my_decision": None,
                "notes": None,
                "change": "updated",
                "valid_from": "2026-09-21T09:31:00Z",
                "valid_to": "2026-09-21T09:42:00Z",
            }
        )
        self.tables["discovery_response_history"].append(
            {
                "id": "h-2",
                "response_id": f"r-{A_LEAD[-2:]}-sf.scale.applications-month",
                "assignment_id": A_LEAD,
                "user_id": USER_A,
                "question_id": question_id("sf.scale.applications-month"),
                "question_version": 1,
                "question_type": "single_select",
                "status": "answered",
                "answer": "5_10",
                "note": None,
                "change": "updated",
                "valid_from": "2026-09-21T09:00:00Z",
                "valid_to": "2026-09-21T09:50:00Z",
            }
        )
        self.tables["discovery_sessions"] = [
            {
                "id": "s-1",
                "assignment_id": A_LEAD,
                "user_id": USER_A,
                "started_at": "2026-09-21T09:00:00Z",
                "last_seen_at": "2026-09-21T11:30:00Z",
                "ended_at": None,
                "saves": 12,
            },
            {
                "id": "s-2",
                "assignment_id": A_LEAD,
                "user_id": USER_A,
                "started_at": "2026-09-20T09:00:00Z",
                "last_seen_at": "2026-09-20T09:30:00Z",
                "ended_at": "2026-09-20T09:30:00Z",
                "saves": 2,
            },
        ]

    def handler(self, request):  # type: ignore[no-untyped-def]
        if request.url.path == "/auth/v1/user":
            token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            extra = {TOKEN_C: USER_C, TOKEN_D: USER_D, TOKEN_R: USER_R}
            if token in extra:
                import httpx

                return httpx.Response(
                    200,
                    json={"id": extra[token], "aud": "authenticated", "email": f"{token}@x.org"},
                )
        return super().handler(request)


@pytest.fixture
def board(monkeypatch: pytest.MonkeyPatch):
    import app.supabase as module
    from app.config import get_settings
    from app.supabase import get_supabase

    monkeypatch.setattr(module, "RETRY_DELAYS", (0.0, 0.0))
    configure(monkeypatch, DISCOVERY_ENABLED="true")
    fake = DashboardBackend()
    app.dependency_overrides[get_supabase] = lambda: fake.client()
    yield fake
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def client(board: DashboardBackend) -> TestClient:
    return TestClient(app)


ROUTES = [
    f"history/{A_LEAD}/{question_id('bs-01')}",
    "respondents",
    "overview",
    "dataset",
    "comparisons",
    "business",
    "underwriting",
    "calibration",
    "scenarios",
    "engineering",
    "engagement",
    "open-questions",
    "export/founder-comparison",
    "export/calibration",
    "export/scenarios",
    "export/progress",
]


# ------------------------------------------------------------------------- boundary --
@pytest.mark.parametrize("route", ROUTES)
def test_every_analysis_route_is_admin_only(client: TestClient, route: str) -> None:
    url = f"/api/admin/discovery/{route}"
    assert client.get(url, headers=CLIENT).status_code == 401
    for who, headers in (
        ("lead", AUTH_A),
        ("partner", AUTH_C),
        ("engineer", AUTH_D),
        ("reviewer", AUTH_R),
        ("member", AUTH_B),
        ("no profile", AUTH_NOBODY),
    ):
        response = client.get(url + f"?user_id={USER_A}&assignment_id={A_LEAD}", headers=headers)
        assert response.status_code == 403, (who, response.text)
        for secret in (SECRET_LEAD, SECRET_PARTNER, SECRET_ENGINEER):
            assert secret not in response.text
    assert client.get(url, headers=AUTH_ADMIN).status_code == 200


def test_the_admin_surface_is_read_only(client: TestClient) -> None:
    for route in ("dataset", "comparisons", "overview"):
        for method in ("post", "put", "patch", "delete"):
            response = client.request(
                method, f"/api/admin/discovery/{route}", json={"answer": "x"}, headers=AUTH_ADMIN
            )
            assert response.status_code == 405, (method, route)


def test_a_stakeholder_cannot_reach_another_answer_through_any_route(client: TestClient) -> None:
    """The stakeholder routes still answer only the caller's own assignment, so the admin surface
    added nothing that a stakeholder could use to read across."""
    for aid, headers in ((A_PARTNER, AUTH_A), (A_LEAD, AUTH_C), (A_LEAD, AUTH_D)):
        assert client.get(f"/api/discovery/assignments/{aid}", headers=headers).status_code == 404
        assert (
            client.get(f"/api/discovery/assignments/{aid}/responses", headers=headers).status_code
            == 404
        )


def test_no_export_carries_an_email_id_or_token(client: TestClient) -> None:
    for name in ("founder-comparison", "calibration", "scenarios", "progress"):
        text = client.get(f"/api/admin/discovery/export/{name}", headers=AUTH_ADMIN).text
        assert "@" not in text and USER_A not in text and "token" not in text.lower()
    dataset = client.get("/api/admin/discovery/dataset", headers=AUTH_ADMIN).text
    assert "@x.org" not in dataset and "token-" not in dataset
    assert client.get("/api/admin/discovery/export/users", headers=AUTH_ADMIN).status_code == 404


# ----------------------------------------------------------------------- respondents --
def test_respondents_are_the_stakeholders_then_the_reviewer_never_the_admin(
    client: TestClient,
) -> None:
    cards = client.get("/api/admin/discovery/respondents", headers=AUTH_ADMIN).json()["respondents"]
    assert [(c["label"], c["kind"]) for c in cards] == [
        ("Lead", "stakeholder"),
        ("Partner", "stakeholder"),
        ("Engineer", "stakeholder"),
        ("Lead", "reviewer"),
        ("Partner", "reviewer"),
    ]
    lead = cards[0]
    # 9 core questions + 4 cases + 1 scenario + 1 own = 15; the skipped approval-rate is unanswered
    assert lead["core_total"] == 15 and lead["core_answered"] == 14
    assert (
        lead["skips"] == 1
        and lead["sessions"] == 2
        and lead["last_activity"] == "2026-09-21T11:30:00Z"
    )
    assert lead["by_group"]["calibration"] == {"available": 4, "answered": 4, "skipped": 0}
    partner = cards[1]
    assert partner["prefer_not_to_answer"] == 1 and partner["skips"] == 0
    engineer = cards[2]
    assert (
        engineer["core_total"] == 3
        and engineer["core_answered"] == 3
        and engineer["by_group"]["scenarios"]["answered"] == 0
    )
    assert USER_ADMIN not in {c["user_id"] for c in cards}


# ------------------------------------------------------------------------ comparison --
def q(kind: str, options: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {"question_type": kind, "options": options or [], "question_key": "x", "id": "x"}


def r(kind: str, value: Any = None, **typed: Any) -> dict[str, Any]:
    return {"status": "answered", "answer": value, "question_type": kind, **typed}


@pytest.mark.parametrize(
    ("question", "a", "b", "status"),
    [
        (q("boolean"), r("boolean", True), r("boolean", True), an.AGREE),
        (q("boolean"), r("boolean", True), r("boolean", False), an.MATERIAL),
        (
            q("single_select", BANDS),
            r("single_select", "5_10"),
            r("single_select", "5_10"),
            an.AGREE,
        ),
        (
            q("single_select", BANDS),
            r("single_select", "5_10"),
            r("single_select", "11_25"),
            an.ROUGH,
        ),
        (
            q("single_select", BANDS),
            r("single_select", "fewer_than_5"),
            r("single_select", "11_25"),
            an.DIFFERENT,
        ),
        (
            q("single_select", BANDS),
            r("single_select", "fewer_than_5"),
            r("single_select", "26_50"),
            an.MATERIAL,
        ),
        (
            q("single_select", BANDS),
            r("single_select", "fewer_than_5"),
            r("single_select", "prefer_not"),
            an.DIFFERENT,
        ),
        (
            q("single_select", DECISIONS),
            r("single_select", "approve"),
            r("single_select", "deny"),
            an.MATERIAL,
        ),
        (
            q("single_select", DECISIONS),
            r("single_select", "approve"),
            r("single_select", "manual_review"),
            an.DIFFERENT,
        ),
        (
            q("single_select", SEGMENTS),
            r("single_select", "trucking"),
            r("single_select", "retail"),
            an.DIFFERENT,
        ),
        (
            q("multi_select", SEGMENTS),
            r("multi_select", ["a", "b"]),
            r("multi_select", ["b", "a"]),
            an.AGREE,
        ),
        (
            q("multi_select", SEGMENTS),
            r("multi_select", ["a", "b", "c"]),
            r("multi_select", ["a", "b"]),
            an.ROUGH,
        ),
        (
            q("multi_select", SEGMENTS),
            r("multi_select", ["a", "b"]),
            r("multi_select", ["a", "c", "d", "e"]),
            an.DIFFERENT,
        ),
        (
            q("multi_select", SEGMENTS),
            r("multi_select", ["a"]),
            r("multi_select", ["b"]),
            an.MATERIAL,
        ),
        (q("number"), r("number", 100), r("number", 100), an.AGREE),
        (q("number"), r("number", 100), r("number", 108), an.ROUGH),
        (q("number"), r("number", 100), r("number", 150), an.DIFFERENT),
        (q("number"), r("number", 100), r("number", 400), an.MATERIAL),
        (q("currency"), r("currency", 5000000), r("currency", 7500000), an.DIFFERENT),
        (q("percentage"), r("percentage", 15), r("percentage", 18), an.ROUGH),
        (q("percentage"), r("percentage", 15), r("percentage", 30), an.DIFFERENT),
        (q("percentage"), r("percentage", 10), r("percentage", 60), an.MATERIAL),
        (q("scale"), r("scale", 4), r("scale", 3), an.ROUGH),
        (q("scale"), r("scale", 1), r("scale", 5), an.MATERIAL),
        (q("ranking", PRIORITIES), r("ranking", ["a", "b"]), r("ranking", ["a", "b"]), an.AGREE),
        (q("ranking", PRIORITIES), r("ranking", ["a", "b"]), r("ranking", ["a", "c"]), an.ROUGH),
        (
            q("ranking", PRIORITIES),
            r("ranking", ["a", "b", "c"]),
            r("ranking", ["b", "a", "c"]),
            an.DIFFERENT,
        ),
        (
            q("ranking", PRIORITIES),
            r("ranking", ["a", "b", "c", "d"]),
            r("ranking", ["d", "e", "f", "a"]),
            an.MATERIAL,
        ),
        (q("short_text"), r("short_text", "x"), r("short_text", "y"), an.NOT_COMPARABLE),
        (q("long_text"), r("long_text", "x"), r("long_text", "x"), an.NOT_COMPARABLE),
    ],
)
def test_deterministic_comparison_by_type(
    question: dict[str, Any], a: dict[str, Any], b: dict[str, Any], status: str
) -> None:
    assert an.compare(question, a, b, ("Lead", "Partner"))["status"] == status


def test_missing_and_skipped_answers_compare_as_absent() -> None:
    question = q("boolean")
    assert an.compare(question, None, None, ("Lead", "Partner"))["status"] == an.NEITHER
    assert (
        an.compare(question, r("boolean", True), None, ("Lead", "Partner"))["status"]
        == "ONLY_LEAD_ANSWERED"
    )
    assert (
        an.compare(question, None, r("boolean", True), ("Lead", "Partner"))["status"]
        == "ONLY_PARTNER_ANSWERED"
    )
    skipped = {"status": "skipped", "answer": None}
    assert (
        an.compare(question, skipped, r("boolean", True), ("Lead", "Partner"))["status"]
        == "ONLY_PARTNER_ANSWERED"
    )
    assert an.compare(question, skipped, skipped, ("Lead", "Partner"))["status"] == an.NEITHER


def test_scenario_and_calibration_comparison() -> None:
    approve = {"decision": "approve", "maximum_exposure_minor": 5000000, "reason_codes": ["a"]}
    deny = {"decision": "deny", "maximum_exposure_minor": None, "reason_codes": ["a", "b"]}
    manual = {"decision": "manual_review", "maximum_exposure_minor": 1000000, "reason_codes": []}
    assert an.compare_scenarios(approve, deny)[0] == an.MATERIAL
    assert an.compare_scenarios(approve, manual)[0] == an.DIFFERENT
    assert (
        an.compare_scenarios(approve, {**approve, "maximum_exposure_minor": 5200000})[0] == an.AGREE
    )
    assert (
        an.compare_scenarios(approve, {**approve, "maximum_exposure_minor": 1000000})[0] == an.ROUGH
    )
    assert an.compare_scenarios(approve, deny)[2]["reason_overlap"] == 0.5
    base = {
        "classification_category": "Loans income",
        "classification_confidence": 3,
        "underwriting_materiality": "medium",
        "underwriting_signal": "neutral",
    }
    assert an.compare_calibrations(base, base)[0] == an.AGREE
    assert (
        an.compare_calibrations(base, {**base, "underwriting_materiality": "high"})[0] == an.ROUGH
    )
    assert (
        an.compare_calibrations(base, {**base, "classification_category": "Organic revenue"})[0]
        == an.DIFFERENT
    )
    assert (
        an.compare_calibrations(base, {**base, "underwriting_materiality": "critical"})[0]
        == an.MATERIAL
    )
    assert (
        an.compare_calibrations(
            {**base, "underwriting_signal": "positive"}, {**base, "underwriting_signal": "negative"}
        )[0]
        == an.MATERIAL
    )
    assert (
        an.compare_calibrations(base, {**base, "classification_confidence": 5})[2][
            "confidence_delta"
        ]
        == -2
    )


def test_founder_comparison_over_the_bank(client: TestClient) -> None:
    body = client.get("/api/admin/discovery/comparisons", headers=AUTH_ADMIN).json()
    assert body["labels"] == ["Lead", "Partner"]
    by_key = {row["question"]["question_key"]: row for row in body["rows"]}
    assert by_key["sf.scale.applications-month"]["status"] == an.ROUGH
    assert by_key["sf.scale.applications-month"]["lead"]["display"] == "11–25"
    assert by_key["sf.scale.active-definition"]["status"] == an.NOT_COMPARABLE
    assert by_key["sf.scale.active-definition"]["lead"]["note"] == "a note"
    assert (
        by_key["sf.cust.segments"]["status"] == an.DIFFERENT
        and by_key["sf.cust.segments"]["detail"] == "1 shared · 1 only first · 1 only second"
    )
    assert (
        by_key["sf.uw.hard-stop-nsf"]["status"] == an.MATERIAL
        and by_key["sf.uw.hard-stop-nsf"]["underwriting"]
    )
    assert by_key["sf.uw.max-exposure"]["lead"]["display"] == "$50,000.00"
    assert by_key["sf.uw.confidence"]["status"] == "ONLY_LEAD_ANSWERED"
    assert by_key["sf.scale.approval-rate"]["status"] == "ONLY_PARTNER_ANSWERED"
    assert (
        by_key["sf.uw.manual-review-share"]["status"] == an.ROUGH
        and by_key["sf.uw.manual-review-share"]["question"]["level"] == "recommended"
    )
    # the metadata reaches the row: the real key carries its authored lines
    assert (
        by_key["sf.scale.active-definition"]["question"]["meta"]["semantic_class"]
        == "UNRESOLVED_DISAGREEMENT"
    )
    assert by_key["sf.scale.active-definition"]["business_fact"] is True
    assert "tc-01" not in by_key and "bs-01" not in by_key and "jo.capital.runway" not in by_key
    assert body["summary"] == {
        "total": 10,
        "both": 8,
        "only_lead": 1,
        "only_partner": 1,
        "neither": 0,
        "agree": 0,
        "roughly": 3,
        "different": 3,
        "material": 1,
        "not_comparable": 1,
    }


def test_business_view_flags_conflicting_facts_without_deciding(client: TestClient) -> None:
    body = client.get("/api/admin/discovery/business", headers=AUTH_ADMIN).json()
    groups = {g["group"]: g["items"] for g in body["groups"]}
    scale = {it["question"]["question_key"]: it for it in groups["Company scale"]}
    assert scale["sf.scale.applications-month"]["needs_reconciliation"] is False
    assert (
        scale["sf.scale.applications-month"]["lead"]["display"] == "11–25"
        and scale["sf.scale.applications-month"]["partner"]["display"] == "26–50"
    )
    assert body["facts_collected"] >= 2


def test_underwriting_view_groups_and_phrases_signals_as_observed(client: TestClient) -> None:
    body = client.get("/api/admin/discovery/underwriting", headers=AUTH_ADMIN).json()
    groups = {
        g["group"]: [it["question"]["question_key"] for it in g["items"]] for g in body["groups"]
    }
    assert "sf.uw.hard-stop-nsf" in groups["Hard stops"]
    assert "sf.uw.max-exposure" in groups["Exposure sizing"]
    texts = [s["text"] for s in body["signals"]]
    assert any(t.startswith("Lead: Yes; Partner: No") for t in texts)
    assert any("Lead would approve, Partner would deny" in t for t in texts)
    assert not any("policy" in t.lower() for t in texts)


# ----------------------------------------------------------------------- calibration --
def test_calibration_rows_pairs_and_reveals(client: TestClient) -> None:
    body = client.get("/api/admin/discovery/calibration", headers=AUTH_ADMIN).json()
    rows = {r["case_key"]: r for r in body["rows"]}
    assert rows["tc-01"]["status"] == an.AGREE and rows["tc-01"]["high_dollar"] is True
    assert (
        rows["tc-02"]["status"] == an.DIFFERENT
        and rows["tc-02"]["is_variant"]
        and rows["tc-02"]["changed_feature"] == "frequency_30d"
    )
    assert (
        rows["tc-06"]["status"] == an.MATERIAL and rows["tc-06"]["detail"] == "opposite risk signal"
    )
    assert rows["tc-06.reveal"]["status"] == "ONLY_LEAD_ANSWERED"
    assert rows["tc-01"]["title"] == "Large ACH credit from an unrecognised originator"
    assert body["summary"] == {
        "cases": 4,
        "both": 3,
        "agreements": 1,
        "material": 1,
        "classification_disagreements": 2,
        "awaiting_both": 0,
        "awaiting_one": 1,
    }
    (pair,) = body["controlled_pairs"]
    assert (
        pair["changed_feature"] == "frequency_30d"
        and pair["baseline"]["case_key"] == "tc-01"
        and pair["variant"]["case_key"] == "tc-02"
    )
    assert pair["lead_changed"] == {
        "classification": False,
        "confidence": False,
        "materiality": False,
        "signal": False,
    }
    assert pair["partner_changed"] == {
        "classification": True,
        "confidence": True,
        "materiality": False,
        "signal": False,
    }
    (reveal,) = body["reveals"]
    assert (
        reveal["case_key"] == "tc-06"
        and reveal["reveal"] == "A second credit from the same originator follows each advance."
    )
    assert reveal["lead_changed"] == {
        "classification": True,
        "confidence": True,
        "materiality": False,
        "signal": False,
    }
    assert reveal["partner_changed"] is None


# -------------------------------------------------------------------------- scenarios --
def test_scenarios_side_by_side(client: TestClient) -> None:
    body = client.get("/api/admin/discovery/scenarios", headers=AUTH_ADMIN).json()
    (row,) = body["rows"]
    assert row["approve_vs_deny"] and row["status"] == an.MATERIAL
    assert (
        row["lead"]["scenario"]["decision"] == "approve"
        and row["partner"]["scenario"]["notes"] == "four funders"
    )
    assert row["extra"] == {
        "decision_match": False,
        "exposure_delta_minor": None,
        "reason_overlap": 0.5,
        "info_overlap": False,
    }
    assert row["question"]["scenario_brief"]["title"].startswith("Growing every month")
    assert body["summary"]["approve_vs_deny"] == 1


# ------------------------------------------------------------------------ engineering --
def test_engineering_view_files_answers_by_explicit_rule(client: TestClient) -> None:
    body = client.get("/api/admin/discovery/engineering", headers=AUTH_ADMIN).json()
    assert body["respondent"] == "Engineer" and body["answered"] == 3 and body["total"] == 5
    buckets = {b["bucket"]: b for b in body["buckets"]}
    assert [
        it["question"]["question_key"]
        for it in buckets["What the engineer wants to keep owning"]["items"]
    ] == ["en.own.keep"]
    assert (
        buckets["What the engineer wants to keep owning"]["items"][0]["response"]["display"]
        == "core_backend_and_apis"
    )
    assert [
        it["question"]["question_key"]
        for it in buckets["What the engineer would hand off"]["items"]
    ] == ["en.own.hand-off"]
    assert [
        it["question"]["question_key"]
        for it in buckets["What is blocked by business decisions"]["items"]
    ] == ["en.prod.blocked-by-decision"]
    assert buckets["What is blocked by business decisions"]["answered"] == 0
    assert "en.debt.handle-with-care" in [
        it["question"]["question_key"] for it in buckets["What is painful"]["items"]
    ]
    categories = {
        c["category"]: [it["question"]["question_key"] for it in c["items"]]
        for c in body["categories"]
    }
    assert categories["Ownership"] == ["en.own.keep", "en.own.hand-off"]
    assert categories["Technical scenarios"] == ["ds-01"]
    # core first within every category
    for c in body["categories"]:
        levels = [it["question"]["required_core"] for it in c["items"]]
        assert levels == sorted(levels, reverse=True), c["category"]


# ------------------------------------------------------------------------- engagement --
def test_engagement_measures_and_never_scores_interest(client: TestClient) -> None:
    body = client.get("/api/admin/discovery/engagement", headers=AUTH_ADMIN).json()
    lead = body["respondents"][0]
    assert lead["label"] == "Lead" and lead["sessions"] == 2 and lead["explanation_rate"] > 0
    topics = {t["topic"]: t for t in lead["topics"]}
    assert topics["Calibration"]["percent"] == 100 and topics["Scenarios"]["percent"] == 100
    assert topics["Business"]["available"] == 5 and topics["Underwriting"]["available"] == 5
    assert "interest" not in str(body).lower()
    assert lead["average_text_length"] == len(SECRET_LEAD)


# ---------------------------------------------------------------------- open questions --
def test_open_questions_lead_with_approve_against_deny(client: TestClient) -> None:
    body = client.get("/api/admin/discovery/open-questions", headers=AUTH_ADMIN).json()
    kinds = [it["kind"] for it in body["items"]]
    assert kinds[0] in ("approve_vs_deny", "founder_material_disagreement")
    assert set(kinds) >= {
        "approve_vs_deny",
        "founder_material_disagreement",
        "founder_disagreement",
        "unanswered_core",
        "calibration_disagreement",
        "unresolved_disagreement_marker",
    }
    marker = next(it for it in body["items"] if it["kind"] == "unresolved_disagreement_marker")
    assert marker["question"]["question_key"] == "sf.scale.active-definition" and marker["why"]
    unanswered = [it for it in body["items"] if it["kind"] == "unanswered_core"]
    assert {it["question"]["question_key"] for it in unanswered} == {
        "sf.uw.confidence",
        "sf.scale.approval-rate",
    }


# --------------------------------------------------------------------------- overview --
def test_overview_summarises_deterministic_results(client: TestClient) -> None:
    body = client.get("/api/admin/discovery/overview", headers=AUTH_ADMIN).json()
    assert [c["label"] for c in body["respondents"]][:3] == ["Lead", "Partner", "Engineer"]
    assert (
        body["founder"]["total"] == 10
        and body["calibration"]["cases"] == 4
        and body["scenarios"]["approve_vs_deny"] == 1
    )
    assert body["recent_activity"][0]["respondent"] in ("Lead", "Partner", "Engineer")
    assert body["open_questions"] > 0 and body["facts_collected"] >= 2
    assert all(SECRET_ENGINEER not in s["text"] for s in body["policy_signals"])


def test_dataset_carries_every_question_and_answer_for_the_drawer(client: TestClient) -> None:
    body = client.get("/api/admin/discovery/dataset", headers=AUTH_ADMIN).json()
    by_key = {q["question_key"]: q for q in body["questions"]}
    assert by_key["tc-02"]["transaction_case"]["case_family"] == "cf-01-ach-inflow"
    assert by_key["bs-01"]["scenario_brief"]["ask"] == "$150,000"
    assert by_key["sf.scale.active-definition"]["meta"]["why"]
    answer = body["answers"][A_LEAD][question_id("sf.scale.active-definition")]
    assert answer["answer"] == SECRET_LEAD and answer["updated_at"] == "2026-09-21T10:00:00Z"
    assert set(body["answers"]) == {A_LEAD, A_PARTNER, A_ENGINEER, A_REV_J, A_REV_A}


def test_exports_are_the_comparison_tables(client: TestClient) -> None:
    founder = client.get("/api/admin/discovery/export/founder-comparison", headers=AUTH_ADMIN)
    assert founder.headers["content-type"].startswith("text/csv")
    lines = founder.text.splitlines()
    assert (
        lines[0] == "question_key,section,level,topic,type,question,lead,partner,status,detail"
        and len(lines) == 11
    )
    calibration = client.get(
        "/api/admin/discovery/export/calibration", headers=AUTH_ADMIN
    ).text.splitlines()
    assert (
        calibration[0].startswith("case,title,family,variant,changed_feature")
        and len(calibration) == 5
    )
    scenarios = client.get(
        "/api/admin/discovery/export/scenarios", headers=AUTH_ADMIN
    ).text.splitlines()
    assert "approve" in scenarios[1] and "deny" in scenarios[1]
    progress = client.get(
        "/api/admin/discovery/export/progress", headers=AUTH_ADMIN
    ).text.splitlines()
    assert progress[0].startswith("respondent,kind,status") and len(progress) == 6


# ------------------------------------------------------------------------ empty state --
def test_an_empty_bank_answers_with_zeros_not_errors(
    client: TestClient, board: DashboardBackend
) -> None:
    for table in (
        "discovery_responses",
        "discovery_scenario_responses",
        "discovery_transaction_calibration_responses",
        "discovery_sessions",
    ):
        board.tables[table] = []
    overview = client.get("/api/admin/discovery/overview", headers=AUTH_ADMIN).json()
    assert overview["founder"]["neither"] == 10 and overview["calibration"]["awaiting_both"] == 4
    assert (
        overview["respondents"][0]["core_answered"] == 0
        and overview["respondents"][0]["last_activity"] is None
    )
    assert overview["recent_activity"] == [] and overview["policy_signals"] == []
    board.tables["discovery_assignments"] = []
    assert (
        client.get("/api/admin/discovery/respondents", headers=AUTH_ADMIN).json()["respondents"]
        == []
    )
    assert (
        client.get("/api/admin/discovery/open-questions", headers=AUTH_ADMIN).json()["count"] > 0
    )  # unanswered core still listed
    board.tables["discovery_templates"] = []
    body = client.get("/api/admin/discovery/overview", headers=AUTH_ADMIN).json()
    assert (
        body["respondents"] == [] and body["founder"]["total"] == 0 and body["open_questions"] == 0
    )


def test_taxonomy_rules_are_lookups() -> None:
    assert (
        tx.group_of("founder-cal-core") == "calibration"
        and tx.level_of("founder-cal-core") == "core"
    )
    assert (
        tx.level_of("engineer-scen-rec") == "recommended"
        and tx.group_of("engineer-scen-rec") == "scenarios"
    )
    assert tx.topic_of("sf.uw.x", "founder-core-underwriting") == "Underwriting"
    assert tx.topic_of("al.manual.x", "partner-rec-operations") == "Operations"
    assert tx.topic_of("jo.hire.x", "lead-deep-organisation") == "Role"
    assert tx.underwriting_group_of("sf.uw.payroll-coverage") == "Payroll"
    assert (
        tx.engineer_category_of("en.data.raw-retention", "engineer-rec-data") == "Plaid / ingestion"
    )
    assert tx.engineering_buckets_of("en.own.keep") == ["What the engineer wants to keep owning"]
    assert tx.is_business_fact("sf.scale.active-definition") and not tx.is_business_fact("nope")


def test_answer_history_is_read_per_respondent_and_scoped_to_the_dataset(
    client: TestClient,
) -> None:
    scenario = client.get(
        f"/api/admin/discovery/history/{A_LEAD}/{question_id('bs-01')}", headers=AUTH_ADMIN
    ).json()
    assert scenario["respondent"] == "Lead"
    assert scenario["current"]["scenario"]["decision"] == "approve"
    (entry,) = scenario["entries"]
    assert entry["display"].startswith("manual review · confidence 2")
    assert (
        entry["valid_to"] == "2026-09-21T09:42:00Z"
        and entry["scenario"]["decision"] == "manual_review"
    )
    plain = client.get(
        f"/api/admin/discovery/history/{A_LEAD}/{question_id('sf.scale.applications-month')}",
        headers=AUTH_ADMIN,
    ).json()
    assert plain["current"]["display"] == "11–25" and plain["entries"][0]["display"] == "5–10"
    # nothing to show is an empty list, not an error
    none = client.get(
        f"/api/admin/discovery/history/{A_PARTNER}/{question_id('bs-01')}", headers=AUTH_ADMIN
    ).json()
    assert none["entries"] == [] and none["current"]["scenario"]["decision"] == "deny"
    # an assignment outside the dataset, an unknown question, or a garbage id: 404, no detail
    for aid, qid in (
        ("88888888-8888-4888-8888-888888888899", question_id("bs-01")),
        (A_LEAD, "not-a-question"),
        ("nope", "nope"),
    ):
        response = client.get(f"/api/admin/discovery/history/{aid}/{qid}", headers=AUTH_ADMIN)
        assert response.status_code == 404 and SECRET_LEAD not in response.text
