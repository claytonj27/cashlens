"""Who may read the operational dashboard, and what it refuses to hand over.

The dashboard is the one place in this application where a person sees across every business
at once, so the only thing worth testing hard is the boundary. Hiding a navigation link is not
a control; these tests drive the endpoints directly, the way anyone who read the network tab
would.
"""

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from tests.test_ai_brief import CLIENT, GOOD_TOKEN, USER_ID, FakeDb

ADMIN = USER_ID
OTHER_USER = "22222222-2222-4222-8222-222222222222"
OTHER_TOKEN = "other-token"

USAGE = "/api/admin/ai/usage"
RUNS = "/api/admin/ai/runs"
SESSION = "/api/admin/session"


def allow(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ADMIN_USER_IDS", value)
    get_settings.cache_clear()


class DashboardDb(FakeDb):
    """The fake, plus the two reads only the dashboard makes."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/auth/v1/user":
            token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if token == GOOD_TOKEN:
                return httpx.Response(200, json={"id": ADMIN, "aud": "authenticated"})
            if token == OTHER_TOKEN:
                return httpx.Response(200, json={"id": OTHER_USER, "aud": "authenticated"})
            return httpx.Response(401, json={})
        if path == "/rest/v1/rpc/ai_milestone_spend":
            return httpx.Response(
                200,
                json=[
                    {
                        "settled_micro": 2_023_031,
                        "held_micro": 0,
                        "total_micro": 2_023_031,
                        "live_runs": 18,
                        "simulated_runs": 1,
                    }
                ],
            )
        if path == "/rest/v1/usage_reservations":
            return httpx.Response(200, json=self.reservations, headers={"content-range": "0-0/0"})
        if path == "/rest/v1/ai_runs" and request.method == "GET":
            return httpx.Response(200, json=self.ai_runs)
        return super().handler(request)


@pytest.fixture
def dash(monkeypatch: pytest.MonkeyPatch):
    from app.supabase import SupabaseClient, get_supabase

    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_MODEL", "test-model")
    get_settings.cache_clear()
    fake = DashboardDb()
    app.dependency_overrides[get_supabase] = lambda: SupabaseClient(
        "https://fake.supabase.local", "sb_secret_test", transport=httpx.MockTransport(fake.handler)
    )
    yield fake
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def as_admin() -> dict[str, str]:
    return {**CLIENT, "authorization": f"Bearer {GOOD_TOKEN}"}


def as_other() -> dict[str, str]:
    return {**CLIENT, "authorization": f"Bearer {OTHER_TOKEN}"}


# ------------------------------------------------------------------ refusal --
@pytest.mark.parametrize("route", [USAGE, RUNS])
def test_an_anonymous_caller_is_refused(
    dash: DashboardDb, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    allow(monkeypatch, ADMIN)
    with TestClient(app) as client:
        response = client.get(route, headers=CLIENT)
    assert response.status_code in (401, 403)


@pytest.mark.parametrize("route", [USAGE, RUNS])
def test_a_signed_in_user_who_is_not_allowlisted_is_refused(
    dash: DashboardDb, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """The reviewer account and every other signed-in account land here."""
    allow(monkeypatch, ADMIN)
    with TestClient(app) as client:
        response = client.get(route, headers=as_other())
    assert response.status_code == 403


@pytest.mark.parametrize("route", [USAGE, RUNS])
def test_no_one_is_admin_when_the_allowlist_is_empty(
    dash: DashboardDb, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """A deployment that has not been configured grants this to nobody, including the owner."""
    allow(monkeypatch, "")
    with TestClient(app) as client:
        response = client.get(route, headers=as_admin())
    assert response.status_code == 403


def test_the_allowlist_is_user_ids_and_an_email_grants_nothing(
    dash: DashboardDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Authorization binds to the immutable id. An email in the list is simply not a match."""
    allow(monkeypatch, "operator@example.com")
    with TestClient(app) as client:
        assert client.get(USAGE, headers=as_admin()).status_code == 403
        assert client.get(SESSION, headers=as_admin()).json() == {"is_admin": False}


def test_the_session_route_answers_without_granting_anything(
    dash: DashboardDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is the question "may I", so it answers both ways rather than refusing."""
    allow(monkeypatch, ADMIN)
    with TestClient(app) as client:
        assert client.get(SESSION, headers=as_admin()).json() == {"is_admin": True}
        assert client.get(SESSION, headers=as_other()).json() == {"is_admin": False}


def test_one_allowlisted_id_among_several_still_matches(
    dash: DashboardDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    allow(monkeypatch, f" {OTHER_USER} , {ADMIN.upper()} ")
    with TestClient(app) as client:
        assert client.get(SESSION, headers=as_admin()).json() == {"is_admin": True}


# -------------------------------------------------------------- what it shows --
def _run(**over: Any) -> dict[str, Any]:
    row = {
        "id": "99999999-9999-4999-8999-999999999999",
        "created_at": "2026-09-14T12:00:00+00:00",
        "capability": "ask",
        "mode": "live",
        "provider": "openai",
        "model_requested": "test-model",
        "model_used": "test-model",
        "status": "succeeded",
        "latency_ms": 1500,
        "cost_micro": 24573,
        "cost_source": "price_table",
        "attempts": 2,
        "validation": {"rounds": 2, "claims_withheld": 1},
        "error_code": None,
        "output": "SECRET ANSWER TEXT",
        "safe_input": "SECRET QUESTION TEXT",
        "input_summary": "SECRET SUMMARY",
    }
    row.update(over)
    return row


def test_totals_come_from_the_ledger_the_cap_is_enforced_against(
    dash: DashboardDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    allow(monkeypatch, ADMIN)
    monkeypatch.setenv("AI_MILESTONE_TOTAL_CAP_MICRO", "25000000")
    get_settings.cache_clear()
    dash.ai_runs = [_run()]
    with TestClient(app) as client:
        body = client.get(USAGE, headers=as_admin()).json()
    budget = body["budget"]
    assert budget["total_micro"] == 2_023_031
    assert budget["remaining_micro"] == 25_000_000 - 2_023_031
    # The rolling window is reported on its own terms, never as the milestone total.
    assert "rolling_24h_micro" in budget
    assert budget["rolling_24h_global_cap_micro"] == 2_000_000


def test_spend_outside_the_ledger_is_added_once_and_labelled(
    dash: DashboardDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A probe the database never saw is not bounded by the cap, so it is shown apart."""
    allow(monkeypatch, ADMIN)
    monkeypatch.setenv("EXTERNAL_PROBE_COST_MICRO", "1252")
    get_settings.cache_clear()
    with TestClient(app) as client:
        budget = client.get(USAGE, headers=as_admin()).json()["budget"]
    assert budget["external_probe_micro"] == 1252
    assert budget["external_probe_in_ledger"] is False
    assert budget["total_micro"] == 2_023_031
    assert budget["overall_total_micro"] == 2_023_031 + 1252


def test_no_question_answer_or_summary_can_reach_the_dashboard(
    dash: DashboardDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The private columns are never selected, so they cannot leak through this page."""
    allow(monkeypatch, ADMIN)
    dash.ai_runs = [_run()]
    with TestClient(app) as client:
        usage = client.get(USAGE, headers=as_admin()).text
        runs = client.get(RUNS, headers=as_admin()).text
    for secret in ("SECRET ANSWER TEXT", "SECRET QUESTION TEXT", "SECRET SUMMARY"):
        assert secret not in usage
        assert secret not in runs
    # Nor any workspace or business identifier.
    for body in (json.loads(usage), json.loads(runs)):
        assert "workspace_id" not in json.dumps(body)
        assert "business_id" not in json.dumps(body)


def test_a_retained_estimate_is_labelled_rather_than_shown_as_measured(
    dash: DashboardDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    allow(monkeypatch, ADMIN)
    dash.ai_runs = [_run(cost_source="reserved_estimate", status="timeout", latency_ms=None)]
    with TestClient(app) as client:
        item = client.get(RUNS, headers=as_admin()).json()["items"][0]
    assert item["cost_is_estimate"] is True
    # Missing timing is null, for the interface to render as "Not recorded" rather than zero.
    assert item["latency_ms"] is None


def test_missing_latency_is_absent_rather_than_counted_as_zero(
    dash: DashboardDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    allow(monkeypatch, ADMIN)
    dash.ai_runs = [_run(latency_ms=None), _run(latency_ms=2000)]
    with TestClient(app) as client:
        body = client.get(USAGE, headers=as_admin()).json()
    # The average is over the runs that actually carry a timing, and says how many that was.
    assert body["average_latency_ms"] == 2000
    assert body["latency_sample"] == 1
    assert body["runs_in_window"] == 2


def test_live_and_replayed_activity_are_distinguished(
    dash: DashboardDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    allow(monkeypatch, ADMIN)
    dash.ai_runs = [_run(), _run(mode="replay", provider="replay")]
    with TestClient(app) as client:
        body = client.get(USAGE, headers=as_admin()).json()
    modes = {row["mode"] for row in body["by_provider"]}
    assert modes == {"live", "replay"}


def test_capabilities_and_statuses_are_counted_and_labelled(
    dash: DashboardDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    allow(monkeypatch, ADMIN)
    dash.ai_runs = [
        _run(capability="ask"),
        _run(capability="brief"),
        _run(capability="classify", status="invalid_output"),
        _run(capability="scenario_explain", status="provider_error"),
    ]
    with TestClient(app) as client:
        body = client.get(USAGE, headers=as_admin()).json()
    caps = {row["key"]: row["label"] for row in body["by_capability"]}
    assert caps == {
        "ask": "Chat",
        "brief": "Brief",
        "classify": "Classification",
        "scenario_explain": "Scenario explanation",
    }
    statuses = {row["key"]: row["count"] for row in body["by_status"]}
    assert statuses == {"succeeded": 2, "invalid_output": 1, "provider_error": 1}


def test_the_runs_page_is_bounded(dash: DashboardDb, monkeypatch: pytest.MonkeyPatch) -> None:
    allow(monkeypatch, ADMIN)
    with TestClient(app) as client:
        assert client.get(f"{RUNS}?limit=500", headers=as_admin()).status_code == 422
        assert client.get(f"{RUNS}?limit=50", headers=as_admin()).status_code == 200
