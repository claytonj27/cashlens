"""Generating a brief end to end, against an in-memory data service and a scripted model.

No network and no provider: the model is a function the test writes, so every outcome the
design promises — a good answer, an invented reference, a wrong number, a timeout, a cap, a
data change mid-run — is produced deliberately rather than waited for.

The scripted provider reads the fact sheet out of the request it is given and answers from it,
so these tests assert the system's behaviour rather than a hard-coded copy of today's numbers.
"""

import json
import logging
import re
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ai import prices
from app.ai.factory import get_model_provider
from app.ai.provider import ModelRequest, ModelResponse, ProviderError, ProviderTimeoutError
from app.config import get_settings
from app.main import app

CLIENT = {"x-cashlens-client": "web"}
GOOD_TOKEN, USER_ID = "good-token", "11111111-1111-4111-8111-111111111111"
BIZ, PRIVATE_BIZ = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"
ACC = "cccccccc-cccc-4ccc-8ccc-ccccccccccc1"
PRIVATE_ACC = "cccccccc-cccc-4ccc-8ccc-ccccccccccc2"
WS_VISITOR = "dddddddd-dddd-4ddd-8ddd-ddddddddddd1"
WS_OTHER_VISITOR = "dddddddd-dddd-4ddd-8ddd-ddddddddddd3"
WS_USER = "dddddddd-dddd-4ddd-8ddd-ddddddddddd2"
VISITOR, OTHER_VISITOR = (
    "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1",
    "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee2",
)

# Planted so a test can prove they never reach a log line.
SECRET_DESCRIPTION = "HOBART SERVICE EQUIPMENT REPAIR"
SECRET_AMOUNT = 45_000


def _txn(n: int, posted: str, amount: int, description: str, business: str = BIZ) -> dict[str, Any]:
    return {
        "id": f"ffffffff-ffff-4fff-8fff-{n:012d}",
        "business_id": business,
        "account_id": ACC if business == BIZ else PRIVATE_ACC,
        "posted_date": posted,
        "description": description,
        "amount_minor": amount,
        "currency": "USD",
        "status": "posted",
        "source_type": "synthetic_provider",
        "revision": 1,
        "removed_at": None,
    }


def _ledger() -> list[dict[str, Any]]:
    """Three months to 28 August: two complete, one still running.

    Weekly card settlements give a recurring pattern, suppliers rise in the last month, a loan
    arrives, one repair stands out as a one-off, and a bare `DEPOSIT` names nobody so the rules
    leave it alone.
    """
    rows: list[dict[str, Any]] = []
    n = 1
    day = date(2026, 6, 5)
    while day <= date(2026, 8, 28):
        rows.append(_txn(n, day.isoformat(), 150_000, "CARD PROCESSOR SETTLEMENT"))
        n += 1
        day += timedelta(days=7)
    for month, supplier in ((6, 60_000), (7, 66_000), (8, 90_000)):
        rows.append(_txn(n, f"2026-{month:02d}-08", -supplier, "PINE VALLEY FOOD DISTRIBUTION"))
        n += 1
        rows.append(_txn(n, f"2026-{month:02d}-15", -120_000, "PAYROLL PROVIDER ACH"))
        n += 1
        rows.append(_txn(n, f"2026-{month:02d}-20", -80_000, "CEDAR COMMONS LEASE PAYMENT"))
        n += 1
    rows.append(_txn(n, "2026-08-22", 500_000, "SBA LOAN PROCEEDS"))
    n += 1
    rows.append(_txn(n, "2026-08-25", -SECRET_AMOUNT, SECRET_DESCRIPTION))
    n += 1
    rows.append(_txn(n, "2026-08-26", 350_000, "DEPOSIT 8842"))
    return rows


class FakeDb:
    """Enough of PostgREST to answer the queries this feature actually makes."""

    def __init__(self) -> None:
        self.transactions = _ledger()
        self.ai_runs: list[dict[str, Any]] = []
        self.overrides: list[dict[str, Any]] = []
        self.reservations: list[dict[str, Any]] = []
        self.imports: list[dict[str, Any]] = []
        self.suggestions: list[dict[str, Any]] = []
        self.conversations: list[dict[str, Any]] = []
        self.turns: list[dict[str, Any]] = []
        self.override_seq = 0
        self.suggestion_seq = 0
        self.clock = 0
        # Set by a test to run just before the model answers, so a data change can land
        # in the middle of a generation.
        self.on_model_call: Any = None

    def tick(self) -> str:
        self.clock += 1
        return f"2026-09-01T00:00:{self.clock:02d}+00:00"

    # ----------------------------------------------------------------- helpers --
    @staticmethod
    def _match(row: dict[str, Any], key: str, spec: str) -> bool:
        op, _, value = spec.partition(".")
        current = row.get(key)
        if op == "eq":
            return str(current) == value
        if op == "is" and value == "null":
            return current is None
        if op == "not" and value == "is.null":
            return current is not None
        if op in ("gte", "lte", "lt", "gt"):
            if current is None:
                return False
            return {
                "gte": current >= value,
                "lte": current <= value,
                "lt": current < value,
                "gt": current > value,
            }[op]
        raise AssertionError(f"unsupported filter {key}={spec}")

    def _select(self, rows: list[dict[str, Any]], params: dict[str, str]) -> list[dict[str, Any]]:
        skip = {"select", "order", "limit", "offset"}
        out = [
            r for r in rows if all(self._match(r, k, v) for k, v in params.items() if k not in skip)
        ]
        order = params.get("order")
        if order:
            field = order.split(",")[0]
            key, _, direction = field.partition(".")
            out = sorted(
                out, key=lambda r: (r.get(key) is None, r.get(key)), reverse=direction == "desc"
            )
        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", len(out) or 1))
        return out[offset : offset + limit]

    # ------------------------------------------------------------------- rpc --
    def _reserve(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        """The application-level shape of reserve_ai_usage.

        The real guarantee is the advisory lock in migration 0018, which only a database can
        provide; what is checked here is that the caller refuses correctly on what it is told.
        """
        identity = (body["p_identity_kind"], body["p_identity_id"])
        live = [
            r
            for r in self.reservations
            if (r["identity_kind"], r["identity_id"]) == identity and r["settled_at"] is None
        ]
        spent = sum(
            r["cost_micro"] if r["settled_at"] else r["estimated_cost_micro"]
            for r in self.reservations
            if (r["identity_kind"], r["identity_id"]) == identity
        )
        overall = sum(
            r["cost_micro"] if r["settled_at"] else r["estimated_cost_micro"]
            for r in self.reservations
        )
        # The milestone total has no time window, so here it is the same sum as the daily one.
        # The distinction that matters is tested against Postgres, where the window is real.
        total = overall
        estimate = body["p_estimated_cost_micro"]
        refusal = None
        if len(live) >= body["p_max_in_flight"]:
            refusal = "in_flight"
        elif total + estimate > body["p_total_cap_micro"]:
            refusal = "total_cap"
        elif spent + estimate > body["p_identity_daily_cap_micro"]:
            refusal = "identity_cap"
        elif overall + estimate > body["p_global_daily_cap_micro"]:
            refusal = "global_cap"
        if refusal:
            return [
                {
                    "allowed": False,
                    "reason": refusal,
                    "identity_spend_micro": spent,
                    "global_spend_micro": overall,
                    "total_spend_micro": total,
                    "in_flight": len(live),
                }
            ]
        self.reservations.append(
            {
                "id": body["p_reservation_id"],
                "identity_kind": identity[0],
                "identity_id": identity[1],
                "capability": body["p_capability"],
                "estimated_cost_micro": estimate,
                "cost_micro": None,
                "uncertain": False,
                "settled_at": None,
            }
        )
        return [
            {
                "allowed": True,
                "reason": "ok",
                "identity_spend_micro": spent,
                "global_spend_micro": overall,
                "total_spend_micro": total,
                "in_flight": len(live),
            }
        ]

    def _settle(self, body: dict[str, Any], *, zero: bool = False) -> Any:
        for row in self.reservations:
            if row["id"] == body["p_reservation_id"] and row["settled_at"] is None:
                cost = body.get("p_cost_micro")
                row["cost_micro"] = (
                    0 if zero else (cost if cost is not None else row["estimated_cost_micro"])
                )
                row["uncertain"] = not zero and cost is None
                row["settled_at"] = self.tick()
                return row["cost_micro"]
        return None

    # --------------------------------------------------------------- handler --
    def handler(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        body = json.loads(request.content) if request.content else None

        if path == "/auth/v1/user":
            token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            return (
                httpx.Response(200, json={"id": USER_ID, "aud": "authenticated"})
                if token == GOOD_TOKEN
                else httpx.Response(401, json={})
            )
        if path == "/rest/v1/visitors":
            wanted = params.get("id", "").removeprefix("eq.")
            known = wanted in (VISITOR, OTHER_VISITOR)
            return httpx.Response(200, json=[{"id": wanted}] if known else [])
        if path == "/rest/v1/rpc/touch_visitor":
            return httpx.Response(200, json=None)
        if path == "/rest/v1/rpc/reserve_ai_usage":
            return httpx.Response(200, json=self._reserve(body))
        if path == "/rest/v1/rpc/settle_ai_usage":
            return httpx.Response(200, json=self._settle(body))
        if path == "/rest/v1/rpc/release_ai_usage":
            return httpx.Response(200, json=self._settle(body, zero=True))
        if path == "/rest/v1/workspaces":
            rows = [
                {"id": WS_VISITOR, "business_id": BIZ, "visitor_id": VISITOR, "user_id": None},
                {
                    "id": WS_OTHER_VISITOR,
                    "business_id": BIZ,
                    "visitor_id": OTHER_VISITOR,
                    "user_id": None,
                },
                {
                    "id": WS_USER,
                    "business_id": PRIVATE_BIZ,
                    "visitor_id": None,
                    "user_id": USER_ID,
                },
            ]
            return httpx.Response(200, json=self._select(rows, params))
        if path == "/rest/v1/business_memberships":
            ok = (
                params.get("business_id") == f"eq.{PRIVATE_BIZ}"
                and params.get("user_id") == f"eq.{USER_ID}"
            )
            return httpx.Response(200, json=[{"business_id": PRIVATE_BIZ}] if ok else [])
        if path == "/rest/v1/businesses":
            known = {
                BIZ: {
                    "id": BIZ,
                    "slug": "juniper-kitchen",
                    "name": "Juniper Kitchen",
                    "industry": "restaurant",
                    "data_mode": "demo",
                },
                PRIVATE_BIZ: {
                    "id": PRIVATE_BIZ,
                    "slug": "private-x",
                    "name": "Owner Co",
                    "industry": "other",
                    "data_mode": "private",
                },
            }
            wanted = params.get("id", "").removeprefix("eq.")
            return httpx.Response(200, json=[known[wanted]] if wanted in known else [])
        if path == "/rest/v1/accounts":
            biz = params.get("business_id", "").removeprefix("eq.")
            rows = [
                {
                    "id": ACC if biz == BIZ else PRIVATE_ACC,
                    "business_id": biz,
                    "name": "Checking",
                    "account_type": "checking",
                    "mask": "4021",
                    "source_type": "synthetic_provider",
                }
            ]
            return httpx.Response(200, json=self._select(rows, params))
        if path == "/rest/v1/transactions":
            return httpx.Response(200, json=self._select(self.transactions, params))
        if path in ("/rest/v1/transaction_sources", "/rest/v1/raw_events", "/rest/v1/sync_batches"):
            return httpx.Response(200, json=[])
        if path == "/rest/v1/balance_snapshots":
            biz = params.get("business_id", "").removeprefix("eq.")
            rows = [
                {
                    "account_id": ACC if biz == BIZ else PRIVATE_ACC,
                    "business_id": biz,
                    "kind": "statement_closing",
                    "provenance": "bank_observed",
                    "amount_minor": 1_250_000,
                    "as_of": "2026-08-28T23:59:59+00:00",
                    "source_type": "synthetic_provider",
                }
            ]
            return httpx.Response(200, json=self._select(rows, params))
        if path == "/rest/v1/category_overrides":
            if request.method == "POST":
                rows = body if isinstance(body, list) else [body]
                for row in rows:
                    self.override_seq += 1
                    self.overrides.append(
                        {
                            "id": f"00000000-0000-4000-8000-{self.override_seq:012d}",
                            "created_at": self.tick(),
                            **row,
                        }
                    )
                return httpx.Response(201, json=[])
            return httpx.Response(200, json=self._select(self.overrides, params))
        if path == "/rest/v1/classification_suggestions":
            if request.method == "POST":
                made = []
                rows = body if isinstance(body, list) else [body]
                for row in rows:
                    self.suggestion_seq += 1
                    stored = {
                        "id": f"11111111-0000-4000-8000-{self.suggestion_seq:012d}",
                        "created_at": self.tick(),
                        "decided_at": None,
                        "decided_by_kind": None,
                        "decided_by_id": None,
                        **row,
                    }
                    self.suggestions.append(stored)
                    made.append(stored)
                return httpx.Response(201, json=made)
            if request.method == "PATCH":
                for row in self._select(self.suggestions, params):
                    row.update(body)
                return httpx.Response(204, content=b"")
            return httpx.Response(200, json=self._select(self.suggestions, params))
        if path == "/rest/v1/ai_conversations":
            if request.method == "POST":
                row = {
                    "agreed_assumptions": None,
                    "assumptions_fingerprint": None,
                    "turn_count": 0,
                    "created_at": self.tick(),
                    "updated_at": self.tick(),
                    **body,
                }
                self.conversations.append(row)
                return httpx.Response(201, json=[row])
            if request.method == "PATCH":
                for row in self._select(self.conversations, params):
                    row.update({k: v for k, v in body.items() if v != "now()"})
                    row["updated_at"] = self.tick()
                return httpx.Response(204, content=b"")
            return httpx.Response(200, json=self._select(self.conversations, params))
        if path == "/rest/v1/ai_turns":
            if request.method == "POST":
                row = {"created_at": self.tick(), **body}
                self.turns.append(row)
                return httpx.Response(201, json=[row])
            return httpx.Response(200, json=self._select(self.turns, params))
        if path == "/rest/v1/document_imports":
            return httpx.Response(200, json=self._select(self.imports, params))
        if path == "/rest/v1/ai_runs":
            if request.method == "POST":
                row = {**body, "created_at": self.tick(), "completed_at": None}
                row.setdefault("model_used", None)
                row.setdefault("attempts", 0)
                self.ai_runs.append(row)
                return httpx.Response(201, json=[])
            if request.method == "PATCH":
                for row in self._select(self.ai_runs, params):
                    row.update(body)
                return httpx.Response(204, content=b"")
            return httpx.Response(200, json=self._select(self.ai_runs, params))
        return httpx.Response(404, json={"message": f"unhandled {path}"})


class Scripted:
    """A model the test writes. Never live, so a run made with it is recorded as replay."""

    name = "scripted"
    live = False

    def __init__(self, *answers: Any, db: FakeDb | None = None) -> None:
        self.answers = list(answers)
        self.requests: list[ModelRequest] = []
        self.db = db

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.db is not None and self.db.on_model_call is not None:
            self.db.on_model_call()
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        text = answer(facts_of(request)) if callable(answer) else answer
        return ModelResponse(
            text=text,
            model_used="scripted-1",
            provider_request_id="req_test",
            prompt_tokens=1_000,
            completion_tokens=200,
            cost_micro=1_500,
            cost_source="price_table",
            latency_ms=12,
            finish_reason="stop",
        )


class BillableScripted(Scripted):
    """A scripted model that accounts as if it were billable, so the budget can be tested.

    It still never touches a network — the answers are the test's own — but it reports itself
    as live, which is what makes the reservation arithmetic run. It exists only here and is
    never reachable from the provider factory, so no deployment can select it.
    """

    name = "openai"
    live = True


TEST_PRICES = {
    "recorded_on": "2026-09-14",
    "models": {"test-model": {"input_usd_per_million": 2.5, "output_usd_per_million": 10.0}},
}


def price_the_model(monkeypatch: pytest.MonkeyPatch, table: dict[str, Any] | None = None) -> None:
    """Give the deployment a price table. Without one, no live call may be dispatched."""
    monkeypatch.setattr(prices, "_table", lambda: table if table is not None else TEST_PRICES)


def facts_of(request: ModelRequest) -> dict[str, Any]:
    """The fact sheet as the model received it, ignoring any repair notes after it."""
    body = request.user.split("\n\n", 1)[1]
    return json.JSONDecoder().raw_decode(body)[0]


def fact(facts: dict[str, Any], prefix: str) -> dict[str, Any] | None:
    for item in facts["facts"]:
        if item["id"].startswith(prefix):
            return item
    return None


def good_answer(facts: dict[str, Any]) -> str:
    """A brief built from whatever the fact sheet actually contains."""
    balance = fact(facts, "bal:")
    loan = fact(facts, "fin:")
    change = next(
        (
            f
            for f in facts["facts"]
            if f["id"].startswith("chg:") and f.get("direction_word") in ("up", "down")
        ),
        None,
    )
    claims = []
    if change is not None:
        word = "rose" if change["direction_word"] == "up" else "fell"
        claims.append(
            {
                "signal_id": change["id"],
                "text": f"{change['label']} {word} by {change['rendered']['change']}.",
                "figures": [
                    {
                        "ref": f"{change['id']}.change_minor",
                        "amount_minor": change["values"]["change_minor"],
                    }
                ],
                "evidence": change.get("evidence", [])[:2],
            }
        )
    if loan is not None:
        claims.append(
            {
                "signal_id": loan["id"],
                "text": (
                    f"A loan of {loan['rendered']['amount']} was paid in on "
                    f"{loan['values']['posted_date']}."
                ),
                "figures": [
                    {
                        "ref": f"{loan['id']}.amount_minor",
                        "amount_minor": loan["values"]["amount_minor"],
                    }
                ],
                "evidence": loan.get("evidence", []),
            }
        )
    return json.dumps(
        {
            "headline": f"Your latest statement balance is {balance['rendered']['amount']}.",
            "claims": claims,
            "questions": [],
            "caveats": [],
        }
    )


def answer_with(claim: dict[str, Any], facts: dict[str, Any]) -> str:
    balance = fact(facts, "bal:")
    return json.dumps(
        {
            "headline": f"Your latest statement balance is {balance['rendered']['amount']}.",
            "claims": [claim],
            "questions": [],
            "caveats": [],
        }
    )


def use(provider: Any) -> None:
    app.dependency_overrides[get_model_provider] = lambda: provider


def visitor(client: TestClient, who: str = VISITOR) -> TestClient:
    client.cookies.set("cl_visitor", who)
    return client


def post_brief(client: TestClient, workspace: str = WS_VISITOR) -> httpx.Response:
    return client.post(f"/api/workspaces/{workspace}/ai/brief", headers=CLIENT)


# ------------------------------------------------------------- the master switch --
def test_every_ai_route_is_off_until_a_deployment_switches_it_on(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(app) as client:
        visitor(client)
        assert client.get(f"/api/workspaces/{WS_VISITOR}/ai/brief").status_code == 503
        assert post_brief(client).status_code == 503
        assert client.get(f"/api/workspaces/{WS_VISITOR}/ai/runs").status_code == 503
    assert db.ai_runs == [] and db.reservations == []


# ------------------------------------------------------------------- the good path --
def test_a_checked_brief_is_returned_stored_and_marked_as_not_live(db: FakeDb) -> None:
    provider = Scripted(good_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        response = post_brief(client)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["brief"]["claims"], "a valid answer should survive validation"
    assert body["run"]["mode"] == "replay", "a scripted answer is never recorded as live"
    assert body["run"]["provider"] == "scripted"
    assert body["run"]["attempts"] == 1
    validation = body["run"]["validation"]
    assert all(validation[layer]["ok"] for layer in ("schema", "evidence", "numbers", "support"))
    assert db.reservations[0]["settled_at"] is not None
    assert db.reservations[0]["cost_micro"] == 1_500


def test_every_evidence_id_opens_a_real_transaction_of_this_workspace(db: FakeDb) -> None:
    use(Scripted(good_answer))
    with TestClient(app) as client:
        visitor(client)
        body = post_brief(client).json()
        known = {t["id"] for t in db.transactions if t["business_id"] == BIZ}
        found = 0
        for claim in body["brief"]["claims"]:
            for evidence in claim["evidence"]:
                assert evidence in known
                detail = client.get(f"/api/workspaces/{WS_VISITOR}/transactions/{evidence}")
                assert detail.status_code == 200
                found += 1
    assert found > 0, "the brief should have cited at least one transaction"


def test_the_figures_a_brief_quotes_are_the_dashboards_own(db: FakeDb) -> None:
    use(Scripted(good_answer))
    with TestClient(app) as client:
        visitor(client)
        body = post_brief(client).json()
        analysis = client.get(f"/api/workspaces/{WS_VISITOR}/analysis").json()
    assert body["input_fingerprint"] == analysis["input_fingerprint"]
    loan_claims = [c for c in body["brief"]["claims"] if c["signal_id"].startswith("fin:")]
    assert loan_claims and loan_claims[0]["figures"][0]["amount_minor"] == 500_000


# ---------------------------------------------------------------- bad answers --
def test_an_invented_reference_is_rejected_after_exactly_one_repair(db: FakeDb) -> None:
    bad = lambda facts: answer_with(  # noqa: E731
        {"signal_id": "chg:invented", "text": "Something happened.", "figures": [], "evidence": []},
        facts,
    )
    provider = Scripted(bad)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = post_brief(client).json()
    assert body["status"] == "invalid_output"
    assert body["brief"] is None
    assert len(provider.requests) == 2, "one repair attempt, and no more"
    assert "rejected for these reasons" in provider.requests[1].user
    assert db.ai_runs[0]["status"] == "invalid_output"
    assert db.ai_runs[0]["attempts"] == 2
    assert db.ai_runs[0].get("output") is None


def test_raw_model_text_is_never_stored_only_a_hash_of_it(db: FakeDb) -> None:
    use(Scripted("not json at all"))
    with TestClient(app) as client:
        visitor(client)
        post_brief(client)
    run = db.ai_runs[0]
    assert run["status"] == "invalid_output"
    assert "not json at all" not in json.dumps(run)
    assert re.fullmatch(r"[0-9a-f]{64}", run["validation"]["output_sha256"])


def test_a_repaired_answer_is_accepted_and_both_attempts_are_paid_for(db: FakeDb) -> None:
    bad = lambda facts: answer_with(  # noqa: E731
        {"signal_id": "chg:invented", "text": "Something happened.", "figures": [], "evidence": []},
        facts,
    )
    use(Scripted(bad, good_answer))
    with TestClient(app) as client:
        visitor(client)
        body = post_brief(client).json()
    assert body["status"] == "succeeded"
    assert body["run"]["attempts"] == 2
    assert body["run"]["prompt_tokens"] == 2_000, "both attempts counted"
    assert db.reservations[0]["cost_micro"] == 3_000


def test_an_unsupported_amount_withholds_one_claim_and_says_so(db: FakeDb) -> None:
    def mixed(facts: dict[str, Any]) -> str:
        loan = fact(facts, "fin:")
        good = json.loads(good_answer(facts))
        good["claims"].append(
            {
                "signal_id": loan["id"],
                "text": "A loan of $99,999.00 was also paid in.",
                "figures": [],
                "evidence": [],
            }
        )
        return json.dumps(good)

    use(Scripted(mixed))
    with TestClient(app) as client:
        visitor(client)
        body = post_brief(client).json()
    assert body["status"] == "succeeded"
    assert body["brief"]["claims_withheld"] == 1
    assert body["brief"]["withheld_reasons"]
    assert body["run"]["validation"]["numbers"]["ok"] is False
    assert body["run"]["validation"]["evidence"]["ok"] is True, "layers stay separate"
    assert all("99,999" not in c["text"] for c in body["brief"]["claims"])


# ------------------------------------------------------------------- failures --
def test_a_timeout_keeps_the_budget_spent_because_the_call_may_have_been_billed(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The usage never came back, so the hold stands. It is never returned as though free."""
    price_the_model(monkeypatch)
    use(BillableScripted(ProviderTimeoutError()))
    with TestClient(app) as client:
        visitor(client)
        body = post_brief(client).json()
    assert body["status"] == "timeout" and body["retryable"] is True
    assert body["brief"] is None
    assert "figures below are complete" in body["message"]
    reservation = db.reservations[0]
    assert reservation["settled_at"] is not None
    assert reservation["cost_micro"] > 0, "a timeout is not free"
    assert reservation["cost_micro"] == reservation["estimated_cost_micro"]
    assert reservation["uncertain"] is True, "usage never came back, so it is not claimed as exact"
    assert db.ai_runs[0]["status"] == "timeout"


def test_a_timeout_still_counts_against_the_next_request(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The spend a timeout held has to keep counting, or a caller could loop past the cap."""
    price_the_model(monkeypatch)
    first = BillableScripted(ProviderTimeoutError())
    use(first)
    with TestClient(app) as client:
        visitor(client)
        assert post_brief(client).json()["status"] == "timeout"

    # The cap is set from what the timed-out run actually held, so the second request needs
    # only a little more than is left. Held plus a fresh hold must exceed it.
    held = db.reservations[0]["cost_micro"]
    assert held > 0
    monkeypatch.setenv("AI_IDENTITY_DAILY_COST_CAP_MICRO", str(int(held * 1.5)))
    get_settings.cache_clear()
    second = BillableScripted(good_answer)
    use(second)
    with TestClient(app) as client:
        visitor(client)
        response = post_brief(client)
    assert response.status_code == 429, "the timed-out spend still counts"
    assert second.requests == []


def test_a_provider_failure_records_the_attempt_and_leaves_the_figures_alone(db: FakeDb) -> None:
    use(Scripted(ProviderError("connection reset")))
    with TestClient(app) as client:
        visitor(client)
        body = post_brief(client).json()
        analysis = client.get(f"/api/workspaces/{WS_VISITOR}/analysis")
    assert body["status"] == "provider_error" and body["brief"] is None
    assert db.ai_runs[0]["status"] == "provider_error"
    assert analysis.status_code == 200 and analysis.json()["coverage"]["posted_count"] > 0


def test_a_request_that_never_left_gives_the_budget_back(db: FakeDb) -> None:
    use(Scripted(ProviderError("no recording", code="recording_missing")))
    with TestClient(app) as client:
        visitor(client)
        assert post_brief(client).json()["status"] == "provider_error"
    assert db.reservations[0]["cost_micro"] == 0, "nothing can have been billed"


# --------------------------------------------------------------------- money --
def test_a_model_with_no_recorded_price_is_never_dispatched(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of failing closed: an unpriced call cannot be bounded, so it is refused.

    Reporting `cost_source: unknown` afterwards describes a call that already happened. It is
    not a ceiling, so it is not allowed to stand in for one.
    """
    price_the_model(monkeypatch, {"recorded_on": None, "models": {}})
    provider = BillableScripted(good_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        response = post_brief(client)
    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "unpriced_model"
    assert provider.requests == [], "nothing may be dispatched without a price"
    assert db.reservations == [], "and nothing is held"
    assert db.ai_runs == [], "a refusal writes no run"


def test_a_simulated_answer_needs_no_price_because_it_costs_nothing(db: FakeDb) -> None:
    """The preview path. Not billable, so nothing is held, and the run says it was not live."""
    provider = Scripted(good_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = post_brief(client).json()
    assert body["status"] == "succeeded"
    assert body["run"]["mode"] == "replay"
    assert db.reservations[0]["estimated_cost_micro"] == 0


def test_a_cap_refuses_before_any_call_is_made(db: FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    price_the_model(monkeypatch)
    monkeypatch.setenv("AI_IDENTITY_DAILY_COST_CAP_MICRO", "0")
    get_settings.cache_clear()
    provider = BillableScripted(good_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        response = post_brief(client)
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["reason"] == "identity_cap"
    assert provider.requests == [], "the model was never called"
    assert db.ai_runs == [], "a refusal writes no run"


def test_a_budget_too_small_for_the_whole_operation_refuses_up_front(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enough left for one call is not enough: the repair is part of the same operation."""
    price_the_model(monkeypatch)
    provider = BillableScripted(good_answer)
    use(provider)
    one_attempt = prices.estimate_micro("test-model", 4_000, 1_200, attempts=1)
    both_attempts = prices.estimate_micro("test-model", 4_000, 1_200)
    assert both_attempts > one_attempt * 1.9, "two calls must cost about twice one"
    monkeypatch.setenv("AI_IDENTITY_DAILY_COST_CAP_MICRO", str(one_attempt))
    get_settings.cache_clear()
    with TestClient(app) as client:
        visitor(client)
        response = post_brief(client)
    assert response.status_code == 429
    assert provider.requests == []


def test_the_hold_covers_the_repair_call_as_well_as_the_first(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    price_the_model(monkeypatch)
    bad = lambda facts: answer_with(  # noqa: E731
        {"signal_id": "chg:invented", "text": "Something happened.", "figures": [], "evidence": []},
        facts,
    )
    use(BillableScripted(bad, good_answer))
    with TestClient(app) as client:
        visitor(client)
        body = post_brief(client).json()
    assert body["status"] == "succeeded" and body["run"]["attempts"] == 2
    reservation = db.reservations[0]
    # Held before either call, and enough for both at their full output allowance.
    single = prices.estimate_micro(
        "test-model",
        prices.estimate_tokens("x" * 4),
        1_200,
        attempts=1,
    )
    assert reservation["estimated_cost_micro"] > single
    assert reservation["cost_micro"] == 3_000, "both attempts settled"
    assert reservation["uncertain"] is False


def test_a_successful_call_settles_at_what_it_actually_cost(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    price_the_model(monkeypatch)
    use(BillableScripted(good_answer))
    with TestClient(app) as client:
        visitor(client)
        body = post_brief(client).json()
    reservation = db.reservations[0]
    assert body["status"] == "succeeded"
    assert reservation["cost_micro"] == 1_500, "the reported cost replaces the estimate"
    assert reservation["cost_micro"] < reservation["estimated_cost_micro"]
    assert reservation["uncertain"] is False
    assert reservation["settled_at"] is not None


def test_a_second_run_for_the_same_person_is_refused_while_one_is_in_flight(db: FakeDb) -> None:
    db.reservations.append(
        {
            "id": "held",
            "identity_kind": "visitor",
            "identity_id": VISITOR,
            "estimated_cost_micro": 10,
            "cost_micro": None,
            "uncertain": False,
            "settled_at": None,
        }
    )
    provider = Scripted(good_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        response = post_brief(client)
    assert response.status_code == 409
    assert provider.requests == []


def test_not_enough_history_reserves_nothing_and_calls_nothing(db: FakeDb) -> None:
    db.transactions = [_txn(900, "2026-08-27", 1_000, "CARD PROCESSOR SETTLEMENT")]
    provider = Scripted(good_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = post_brief(client).json()
    assert body["status"] == "insufficient_data"
    assert "complete month" in body["message"]
    assert provider.requests == [] and db.reservations == []
    assert db.ai_runs[0]["status"] == "insufficient_data"


# ----------------------------------------------------------------- staleness --
def test_a_brief_goes_stale_when_the_data_changes_and_names_what_changed(db: FakeDb) -> None:
    use(Scripted(good_answer))
    with TestClient(app) as client:
        visitor(client)
        assert post_brief(client).json()["status"] == "succeeded"
        assert client.get(f"/api/workspaces/{WS_VISITOR}/ai/brief").json()["status"] == "current"

        db.transactions.append(_txn(950, "2026-08-27", -30_000, "PINE VALLEY FOOD DISTRIBUTION"))
        after = client.get(f"/api/workspaces/{WS_VISITOR}/ai/brief").json()

    assert after["status"] == "stale"
    assert after["brief"] is not None, "the previous brief is still readable"
    assert any("more transaction" in line for line in after["changes_since"])


def test_a_correction_makes_the_brief_stale_exactly_as_an_import_does(db: FakeDb) -> None:
    use(Scripted(good_answer))
    with TestClient(app) as client:
        visitor(client)
        post_brief(client)
        target = db.transactions[-1]["id"]
        db.overrides.append(
            {
                "workspace_id": WS_VISITOR,
                "business_id": BIZ,
                "transaction_id": target,
                "category": "owner_contributions",
                "transaction_revision": 1,
                "source": "owner_correction",
                "created_at": "2026-09-02T00:00:00+00:00",
            }
        )
        after = client.get(f"/api/workspaces/{WS_VISITOR}/ai/brief").json()
        analysis = client.get(f"/api/workspaces/{WS_VISITOR}/analysis").json()

    assert after["status"] == "stale"
    assert any("corrected by you" in line for line in after["changes_since"])
    assert analysis["overrides_applied"] == 1


def test_data_changing_mid_generation_is_not_presented_as_current(db: FakeDb) -> None:
    def land_a_statement() -> None:
        db.transactions.append(_txn(960, "2026-08-27", 75_000, "CARD PROCESSOR SETTLEMENT"))
        db.on_model_call = None

    db.on_model_call = land_a_statement
    use(Scripted(good_answer, db=db))
    with TestClient(app) as client:
        visitor(client)
        body = post_brief(client).json()
    assert body["status"] == "superseded"
    assert "changed while this was being written" in body["message"]
    assert db.ai_runs[0]["status"] == "superseded"
    with TestClient(app) as client:
        visitor(client)
        assert client.get(f"/api/workspaces/{WS_VISITOR}/ai/brief").json()["status"] != "current"


# ----------------------------------------------------------------- isolation --
def test_one_visitors_runs_are_invisible_to_another(db: FakeDb) -> None:
    use(Scripted(good_answer))
    with TestClient(app) as client:
        visitor(client)
        run_id = post_brief(client).json()["run"]["id"]

    with TestClient(app) as other:
        visitor(other, OTHER_VISITOR)
        assert other.get(f"/api/workspaces/{WS_OTHER_VISITOR}/ai/runs").json() == []
        # A valid id from the same business, guessed from another workspace, is still not theirs.
        assert other.get(f"/api/workspaces/{WS_OTHER_VISITOR}/ai/runs/{run_id}").status_code == 404
        assert other.get(f"/api/workspaces/{WS_VISITOR}/ai/runs").status_code == 404
        assert (
            other.get(f"/api/workspaces/{WS_OTHER_VISITOR}/ai/brief").json()["status"] != "current"
        )


def test_an_owners_runs_are_refused_to_a_visitor_and_to_a_stranger(db: FakeDb) -> None:
    with TestClient(app) as client:
        visitor(client)
        assert client.get(f"/api/workspaces/{WS_USER}/ai/runs").status_code == 404
    with TestClient(app) as client:
        assert client.get(f"/api/workspaces/{WS_USER}/ai/runs").status_code == 401
        assert (
            client.get(
                f"/api/workspaces/{WS_USER}/ai/runs", headers={"authorization": "Bearer wrong"}
            ).status_code
            == 401
        )


# ------------------------------------------------------------- the trace view --
def test_the_run_detail_shows_the_facts_that_were_sent_and_what_survived(db: FakeDb) -> None:
    use(Scripted(good_answer))
    with TestClient(app) as client:
        visitor(client)
        run_id = post_brief(client).json()["run"]["id"]
        detail = client.get(f"/api/workspaces/{WS_VISITOR}/ai/runs/{run_id}").json()
    assert detail["prompt_version"] == "brief-2"
    assert detail["rules_version"] == "rules-2"
    assert detail["analysis_version"] == "analysis-2"
    assert detail["mode"] == "replay"
    assert detail["cost_source"] == "price_table"
    assert detail["safe_input"]["fact_sheet_version"] == "facts-1"
    assert detail["output"]["claims"]
    assert len(detail["input_fingerprint"]) == 64


def test_a_malformed_run_id_is_not_found_rather_than_an_error(db: FakeDb) -> None:
    with TestClient(app) as client:
        visitor(client)
        assert client.get(f"/api/workspaces/{WS_VISITOR}/ai/runs/not-a-uuid").status_code == 404


# ----------------------------------------------------------------------- logs --
def test_a_run_log_carries_ids_and_counts_but_never_content(
    db: FakeDb, caplog: pytest.LogCaptureFixture
) -> None:
    use(Scripted(good_answer))
    with TestClient(app) as client:
        # Startup installs the JSON handler and clears the root logger's handlers, which takes
        # the capturing one with it, so it is put back once the application has started.
        logging.getLogger().addHandler(caplog.handler)
        with caplog.at_level("INFO"):
            visitor(client)
            post_brief(client)
        logging.getLogger().removeHandler(caplog.handler)
    lines = [r for r in caplog.records if r.getMessage() == "ai.run"]
    assert lines, "a run should be logged"
    captured = json.dumps([r.__dict__ for r in lines], default=str)
    assert SECRET_DESCRIPTION not in captured
    assert "450.00" not in captured and str(SECRET_AMOUNT) not in captured
    assert lines[0].__dict__["status"] == "succeeded"
    assert lines[0].__dict__["business_id"] == BIZ


# ------------------------------------------------------------- the simulated path --
def simulate(monkeypatch: pytest.MonkeyPatch, outcome: str = "valid") -> None:
    """Configure the deployment the way a preview would, and use the real factory."""
    monkeypatch.setenv("AI_PROVIDER", "simulated")
    monkeypatch.setenv("AI_SIMULATED_OUTCOME", outcome)
    get_settings.cache_clear()
    app.dependency_overrides.pop(get_model_provider, None)


def test_a_simulated_brief_passes_the_same_checks_and_is_never_called_live(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulate(monkeypatch)
    with TestClient(app) as client:
        visitor(client)
        body = post_brief(client).json()
    assert body["status"] == "succeeded"
    assert body["brief"]["claims"], "a simulated answer still has to survive validation"
    run = body["run"]
    assert run["provider"] == "simulated"
    assert run["mode"] == "replay", "never recorded as a live call"
    assert all(run["validation"][layer]["ok"] for layer in ("schema", "evidence", "support"))


def test_a_simulated_run_invents_no_usage_and_claims_no_price(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero because nothing was spent, not zero borrowed from a price table."""
    simulate(monkeypatch)
    with TestClient(app) as client:
        visitor(client)
        run = post_brief(client).json()["run"]
    assert run["cost_micro"] == 0 and run["cost_source"] == "simulated"
    assert run["prompt_tokens"] is None and run["completion_tokens"] is None
    assert db.reservations[0]["estimated_cost_micro"] == 0, "nothing billable, nothing held"


def test_a_simulated_answer_citing_a_fact_it_was_not_given_is_still_rejected(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulate(monkeypatch, "invalid_evidence")
    with TestClient(app) as client:
        visitor(client)
        body = post_brief(client).json()
    assert body["status"] == "invalid_output"
    assert body["brief"] is None
    assert db.ai_runs[0]["attempts"] == 2, "repaired once, then refused"
    assert db.ai_runs[0]["validation"]["evidence"]["ok"] is False


def test_the_simulated_failure_outcomes_render_the_documented_states(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    for outcome, expected in (("provider_error", "provider_error"), ("timeout", "timeout")):
        simulate(monkeypatch, outcome)
        with TestClient(app) as client:
            visitor(client)
            body = post_brief(client).json()
        assert body["status"] == expected
        assert body["brief"] is None
        assert "figures below are complete" in body["message"]


def test_the_simulated_provider_is_refused_in_production(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulate(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("FEATURES_ENABLED", "true")
    get_settings.cache_clear()
    with TestClient(app) as client:
        visitor(client)
        response = post_brief(client)
    assert response.status_code == 503
    assert "production" in str(response.json()["detail"])


def test_the_master_switch_is_the_reason_given_even_when_nothing_is_configured(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With AI off, the switch answers — not a configuration detail behind it."""
    monkeypatch.setenv("AI_ENABLED", "false")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.delenv("AI_MODEL", raising=False)
    get_settings.cache_clear()
    app.dependency_overrides.pop(get_model_provider, None)
    with TestClient(app) as client:
        visitor(client)
        for response in (
            client.get(f"/api/workspaces/{WS_VISITOR}/ai/brief"),
            post_brief(client),
            client.get(f"/api/workspaces/{WS_VISITOR}/ai/runs"),
        ):
            assert response.status_code == 503
            assert "off in this environment" in str(response.json()["detail"])


# ------------------------------------------------------- the milestone's total budget --
def test_the_milestone_budget_refuses_even_when_today_has_room(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A daily cap bounds a day. Only the cumulative cap bounds the milestone."""
    price_the_model(monkeypatch)
    # Plenty of room today, and none left overall.
    monkeypatch.setenv("AI_IDENTITY_DAILY_COST_CAP_MICRO", "25000000")
    monkeypatch.setenv("AI_GLOBAL_DAILY_COST_CAP_MICRO", "25000000")
    monkeypatch.setenv("AI_MILESTONE_TOTAL_CAP_MICRO", "1000")
    get_settings.cache_clear()
    provider = BillableScripted(good_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        response = post_brief(client)
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["reason"] == "total_cap"
    assert "approved budget is spent" in detail["message"]
    assert detail["retry_after_seconds"] is None, "waiting does not refill an approved budget"
    assert provider.requests == [] and db.ai_runs == []


def test_spend_from_an_earlier_task_still_counts_against_the_milestone(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Budget is never reset between tasks: a settled row from before still occupies it."""
    price_the_model(monkeypatch)
    monkeypatch.setenv("AI_MILESTONE_TOTAL_CAP_MICRO", "60000")
    get_settings.cache_clear()
    db.reservations.append(
        {
            "id": "spent-earlier",
            "identity_kind": "user",
            "identity_id": USER_ID,
            "estimated_cost_micro": 55_000,
            "cost_micro": 55_000,
            "uncertain": False,
            "settled_at": "2026-09-01T00:00:00+00:00",
        }
    )
    provider = BillableScripted(good_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        assert post_brief(client).status_code == 429
    assert provider.requests == []


def test_a_simulated_run_costs_nothing_against_the_milestone(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_MILESTONE_TOTAL_CAP_MICRO", "0")
    get_settings.cache_clear()
    simulate(monkeypatch)
    monkeypatch.setenv("AI_MILESTONE_TOTAL_CAP_MICRO", "0")
    get_settings.cache_clear()
    with TestClient(app) as client:
        visitor(client)
        body = post_brief(client).json()
    assert body["status"] == "succeeded", "nothing billable, so nothing to refuse"
    assert db.reservations[0]["estimated_cost_micro"] == 0
