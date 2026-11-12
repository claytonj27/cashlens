"""Cash-flow classification: what the model is allowed to decide, and what it is not.

The three things worth testing hard are the three the feature promises.

Direction is arithmetic. It is computed from the amount, told to the model as settled, and
re-checked afterwards; a category belonging to the other direction is refused whether it came
from the model or from a person. These tests drive that from both ends.

The file's own category column never reaches the model. Not filtered out of the payload, but
never put in it: what is sent is an allowlist, and the tests assert against the bytes that
actually left rather than against the intention.

Nothing is invented and nothing is lost. Fifteen hundred rows in, fifteen hundred rows out, with
a failed batch, a refused batch, an invented id and a missing answer all landing in Unclassified
rather than dropping a transaction on the floor.

The provider here is scripted, so no test in this file makes a paid call.
"""

import csv
import io
import json
import re
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ai import cashflow
from app.ai.factory import get_model_provider
from app.ai.provider import ModelResponse, ProviderError, ProviderTimeoutError
from app.config import get_settings
from app.main import app
from app.supabase import SupabaseClient, get_supabase
from tests.test_ai_brief import price_the_model
from tests.test_imports import (
    ACC_A,
    AUTH_A,
    AUTH_B,
    CLIENT,
    VISITOR,
    WS_A,
    FakeBackend,
    base,
    start_import,
)

# Positive is money leaving, and the daily balance moves by each day's total, which is what lets
# the parser verify that without being told. Five days, so the day-grouped check has enough to
# agree on. The Categories column is here precisely so the tests can prove it goes no further.
PLAID_HEADER = (
    b"Date,Transaction ID,Transaction Name,Merchant Name,Amount,Categories,"
    b"Current Balance,Calculated Daily Balance\n"
)
PLAID_CSV = (
    PLAID_HEADER
    + b"""\
2026-01-01,t1,US FOODS INC ACH,US Foods,1309.92,"Food and Drink, Groceries",5000.00,8690.08
2026-01-02,t2,ATM Deposit US Bank,,-790.00,"Transfer, Deposit",5000.00,9480.08
2026-01-03,t3,Zelle Instant Pmt To Marco Diaz kitchen help,Marco Diaz,300.00,\
"Transfer, Withdrawal",5000.00,9180.08
2026-01-04,t4,SQUARE INC PAYOUT,Square,-2500.00,"Transfer, Deposit",5000.00,11680.08
2026-01-05,t5,CHECK 2041,,125.00,"Payment, Check",5000.00,11555.08
2026-01-06,t6,POWER FUNDING SOLUTIONS ACH,Power Funding,250.00,"Payment, Loan",5000.00,11305.08
"""
)

CATEGORY_FOR = {
    "t1": "Operating expense",
    "t2": "Organic revenue",
    "t3": "Payroll",
    "t4": "Organic revenue",
    "t5": "Unclassified",
    "t6": "Loan payments",
}
DIRECTION_FOR = {
    "t1": "outflow",
    "t2": "inflow",
    "t3": "outflow",
    "t4": "inflow",
    "t5": "outflow",
    "t6": "outflow",
}


class ScriptedProvider:
    """Answers from a script, records every request, and never reaches a network.

    `live` is True so the code under test takes the same path it takes in production: it prices
    the call, reserves against the budget, and settles afterwards. Only the answer is canned.
    """

    name = "scripted"
    live = True

    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.answer = self._echo
        self.raise_with: Exception | None = None

    def _echo(self, batch: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "items": [
                {
                    "transaction_id": item["transaction_id"],
                    "cash_flow_category": CATEGORY_FOR.get(item["transaction_id"], "Unclassified"),
                    "confidence": "high",
                    "reason": "Scripted answer.",
                }
                for item in batch
            ]
        }

    def complete(self, request: Any) -> ModelResponse:
        self.requests.append(request)
        if self.raise_with is not None:
            raise self.raise_with
        batch = json.loads(request.user[request.user.index("[") :])
        return ModelResponse(
            text=json.dumps(self.answer(batch)),
            model_used="test-model",
            provider_request_id="req-1",
            prompt_tokens=100,
            completion_tokens=200,
            cost_micro=1234,
            cost_source="price_table",
            latency_ms=42,
            finish_reason="stop",
        )

    @property
    def sent(self) -> list[dict[str, Any]]:
        """Every transaction payload that actually left, parsed back out of the request body."""
        out: list[dict[str, Any]] = []
        for request in self.requests:
            out.extend(json.loads(request.user[request.user.index("[") :]))
        return out


# The check constraints the real `ai_runs` table carries, restated where the fake writes to it.
#
# A stand-in that accepts anything cannot catch a row the real table would reject, and that is
# not hypothetical: the first live run of this capability failed on both of these, an
# unadmitted capability and a fingerprint that was readable rather than sixty-four characters,
# after a green test suite. Enforcing them here is what makes the suite able to say anything
# about whether a run row can actually be written.
AI_RUN_CAPABILITIES = frozenset(
    {"brief", "classify", "scenario_interpret", "scenario_explain", "ask", "cashflow"}
)
AI_RUN_STATUSES = frozenset(
    {
        "reserved",
        "running",
        "succeeded",
        "invalid_output",
        "provider_error",
        "timeout",
        "insufficient_data",
        "superseded",
    }
)
AI_RUN_MODES = frozenset({"live", "cached", "replay"})
AI_RUN_PROVIDERS = frozenset({"openai", "openrouter", "replay", "scripted", "simulated"})


def enforce_ai_run_constraints(row: dict[str, Any]) -> None:
    digest = str(row.get("input_fingerprint", ""))
    assert len(digest) == 64, f"input_fingerprint must be 64 characters, got {len(digest)}"
    for field, allowed in (
        ("capability", AI_RUN_CAPABILITIES),
        ("status", AI_RUN_STATUSES),
        ("mode", AI_RUN_MODES),
        ("provider", AI_RUN_PROVIDERS),
    ):
        assert row.get(field) in allowed, f"{field} {row.get(field)!r} is not admitted"
    assert 1 <= len(str(row.get("prompt_version", ""))) <= 40, "prompt_version is 1 to 40 chars"
    assert 1 <= len(str(row.get("model_requested", ""))) <= 120, "model_requested is 1 to 120"
    assert row.get("business_id") and row.get("workspace_id"), "a run row is always scoped"


def enforce_ai_run_update(patch: dict[str, Any]) -> None:
    code = patch.get("error_code")
    if code is not None:
        assert re.fullmatch(r"[a-z_]{1,60}", str(code)), f"error_code {code!r} is not snake_case"
    if patch.get("status") is not None:
        assert patch["status"] in AI_RUN_STATUSES, f"status {patch['status']!r} is not admitted"
    for field in ("cost_micro", "latency_ms", "prompt_tokens", "completion_tokens", "attempts"):
        if isinstance(patch.get(field), int):
            assert patch[field] >= 0, f"{field} must not be negative"


class CashflowBackend(FakeBackend):
    """The import fake, plus the reservation ledger and run rows this capability writes."""

    def __init__(self) -> None:
        super().__init__()
        self.tables["ai_runs"] = []
        self.tables["usage_reservations"] = []
        self.refuse: str | None = None
        self.settled: list[int | None] = []

    @staticmethod
    def _match(row: dict[str, Any], key: str, spec: str) -> bool:
        # PostgREST negates a filter by prefixing `not.`, which is how "is not null" is spelled.
        if spec.startswith("not."):
            return not FakeBackend._match(row, key, spec[4:])
        return FakeBackend._match(row, key, spec)

    def _defaults(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        if table == "import_rows":
            return {**super()._defaults(table, row), "cashflow": row.get("cashflow")}
        if table == "ai_runs":
            enforce_ai_run_constraints(row)
            return {"id": str(uuid.uuid4()), **row}
        return super()._defaults(table, row)

    def rest(self, request: httpx.Request, table: str, params: dict[str, str]) -> httpx.Response:
        if table == "ai_runs" and request.method == "PATCH":
            enforce_ai_run_update(json.loads(request.content))
        return super().rest(request, table, params)

    def rpc(self, name: str, body: dict[str, Any]) -> httpx.Response:
        if name == "reserve_ai_usage":
            if self.refuse:
                return httpx.Response(
                    200,
                    json=[
                        {
                            "allowed": False,
                            "reason": self.refuse,
                            "identity_spend_micro": 1,
                            "global_spend_micro": 2,
                            "total_spend_micro": 3,
                        }
                    ],
                )
            self.tables["usage_reservations"].append(
                {"id": body["p_reservation_id"], "cost_micro": body["p_estimated_cost_micro"]}
            )
            return httpx.Response(200, json=[{"allowed": True}])
        if name in ("settle_ai_usage", "release_ai_usage"):
            self.settled.append(body.get("p_cost_micro"))
            return httpx.Response(200, json=[])
        return super().rpc(name, body)


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch):
    import app.supabase as module

    monkeypatch.setattr(module, "RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_MODEL", "test-model")
    # One batch at a time, so a test that scripts two answers gets them in a known order.
    monkeypatch.setenv("AI_CASHFLOW_CONCURRENCY", "1")
    get_settings.cache_clear()
    # Without a price there is nothing to reserve against, so no live call is dispatched at all.
    price_the_model(monkeypatch)
    fake = CashflowBackend()
    provider = ScriptedProvider()
    app.dependency_overrides[get_supabase] = lambda: SupabaseClient(
        "https://fake.supabase.local", "sb_secret_test", transport=httpx.MockTransport(fake.handler)
    )
    app.dependency_overrides[get_model_provider] = lambda: provider
    fake.provider = provider  # type: ignore[attr-defined]
    yield fake
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def staged(client: TestClient, backend: CashflowBackend, data: bytes = PLAID_CSV) -> str:
    record = start_import(client, backend, data, account_id=ACC_A, label="plaid.csv")
    return record["id"]


def classify(client: TestClient, import_id: str) -> dict[str, Any]:
    response = client.post(f"{base()}/{import_id}/cashflow", headers=AUTH_A)
    assert response.status_code == 200, response.text
    return response.json()


def rows_of(client: TestClient, import_id: str, **query: Any) -> list[dict[str, Any]]:
    response = client.get(f"{base()}/{import_id}/cashflow", headers=AUTH_A, params=query)
    assert response.status_code == 200, response.text
    return response.json()["items"]


# --------------------------------------------------- direction is arithmetic --
@pytest.mark.parametrize(
    "amount,expected",
    [(1, "inflow"), (250_000, "inflow"), (-1, "outflow"), (-250_000, "outflow"), (0, "outflow")],
)
def test_direction_comes_from_the_amount_alone(amount: int, expected: str) -> None:
    assert cashflow.direction_of(amount) == expected


def test_the_file_sign_convention_survives_into_the_direction(backend: CashflowBackend) -> None:
    """Positive in this file means money leaving, and the stored direction says so."""
    with TestClient(app) as client:
        import_id = staged(client, backend)
        classify(client, import_id)
        by_id = {r["transaction_id"]: r for r in rows_of(client, import_id, limit=200)}
    assert {tid: row["direction"] for tid, row in by_id.items()} == DIRECTION_FOR
    # The Zelle payment out is an outflow even though the file wrote it as +300.
    assert by_id["t3"]["amount_minor"] == -30000 and by_id["t3"]["direction"] == "outflow"
    assert by_id["t2"]["amount_minor"] == 79000 and by_id["t2"]["direction"] == "inflow"


def test_the_model_cannot_move_a_row_to_the_other_direction() -> None:
    """An outflow answered as Organic revenue is refused, not displayed."""
    batch = [
        cashflow.Candidate("r1", 0, "t1", -5000, "outflow", {"Name": "SUPPLIER"}),
        cashflow.Candidate("r2", 1, "t2", 5000, "inflow", {"Name": "DEPOSIT"}),
    ]
    verdicts, problems = cashflow.validate_items(
        {
            "items": [
                {
                    "transaction_id": "t1",
                    "cash_flow_category": "Organic revenue",
                    "confidence": "high",
                    "reason": "x",
                },
                {
                    "transaction_id": "t2",
                    "cash_flow_category": "Payroll",
                    "confidence": "high",
                    "reason": "x",
                },
            ]
        },
        batch,
    )
    assert [v.category for v in verdicts] == ["Unclassified", "Unclassified"]
    assert [v.direction for v in verdicts] == ["outflow", "inflow"]
    assert len(problems) == 2 and all("is not a" in p for p in problems)


def test_a_person_cannot_move_a_row_to_the_other_direction_either(
    backend: CashflowBackend,
) -> None:
    with TestClient(app) as client:
        import_id = staged(client, backend)
        classify(client, import_id)
        # t1 is an outflow; Organic revenue is an inflow bucket.
        refused = client.patch(
            f"{base()}/{import_id}/cashflow/0",
            headers=AUTH_A,
            json={"category": "Organic revenue"},
        )
        accepted = client.patch(
            f"{base()}/{import_id}/cashflow/0", headers=AUTH_A, json={"category": "Payroll"}
        )
    assert refused.status_code == 422 and "outflow" in refused.json()["detail"]
    assert accepted.status_code == 200
    assert accepted.json()["category"] == "Payroll" and accepted.json()["source"] == "manual"


def test_an_invented_category_is_refused(backend: CashflowBackend) -> None:
    with TestClient(app) as client:
        import_id = staged(client, backend)
        classify(client, import_id)
        response = client.patch(
            f"{base()}/{import_id}/cashflow/0", headers=AUTH_A, json={"category": "Tax deduction"}
        )
    assert response.status_code == 422


# ------------------------------------------- the file's own categories stay here --
def test_the_source_category_column_never_reaches_the_model(backend: CashflowBackend) -> None:
    """Asserted against the bytes that actually left, not against the intention to exclude."""
    with TestClient(app) as client:
        import_id = staged(client, backend)
        classify(client, import_id)
    sent = backend.provider.sent  # type: ignore[attr-defined]
    assert len(sent) == 6
    body = json.dumps(sent)
    for leaked in ("Categories", "Food and Drink", "Groceries", "Transfer, Withdrawal", "Payment"):
        assert leaked not in body, f"{leaked!r} reached the model"
    # What it does get is the transaction and the direction already decided.
    assert {item["transaction_id"] for item in sent} == set(CATEGORY_FOR)
    assert all(item["direction"] in ("inflow", "outflow") for item in sent)
    assert all("Transaction Name" in item for item in sent)


def test_a_category_field_in_the_payload_is_a_hard_failure() -> None:
    """The backstop, for an edit that widens the allowlist without noticing what it let in."""
    with pytest.raises(ValueError, match="pre-existing category"):
        cashflow.assert_nothing_forbidden([{"transaction_id": "t1", "Categories": "Food"}])
    with pytest.raises(ValueError, match="pre-existing category"):
        cashflow.assert_nothing_forbidden(
            [{"transaction_id": "t1", "personal_finance_category": "x"}]
        )
    # The keys the module adds itself are not confused for one.
    cashflow.assert_nothing_forbidden([{"transaction_id": "t1", "allowed_categories": ["Payroll"]}])


def test_the_candidate_carries_no_column_outside_the_allowlist() -> None:
    candidate = cashflow.candidate_from_row(
        {
            "id": "r1",
            "row_index": 0,
            "extracted": {"amount_minor": -1000},
            "raw": {
                "Transaction Name": "US FOODS",
                "Categories": "Food and Drink",
                "User ID": "u-1",
                "Account Last 4": "6812",
                "Current Balance": "5000.00",
            },
        }
    )
    assert candidate is not None
    assert set(candidate.fields) == {"Transaction Name"}


# ------------------------------------------ nothing invented, nothing lost --
def test_every_row_gets_exactly_one_verdict_in_order() -> None:
    batch = [
        cashflow.Candidate(f"r{i}", i, f"t{i}", -100, "outflow", {"Name": "X"}) for i in range(5)
    ]
    verdicts, _ = cashflow.validate_items({"items": []}, batch)
    assert [v.transaction_id for v in verdicts] == [f"t{i}" for i in range(5)]
    assert all(v.category == "Unclassified" and v.confidence == "low" for v in verdicts)


def test_an_id_nobody_asked_about_is_discarded() -> None:
    batch = [cashflow.Candidate("r0", 0, "t0", -100, "outflow", {"Name": "X"})]
    verdicts, problems = cashflow.validate_items(
        {
            "items": [
                {
                    "transaction_id": "made-up",
                    "cash_flow_category": "Payroll",
                    "confidence": "high",
                    "reason": "x",
                }
            ]
        },
        batch,
    )
    assert len(verdicts) == 1 and verdicts[0].transaction_id == "t0"
    assert verdicts[0].category == "Unclassified"
    assert any("unknown transaction_id" in p for p in problems)


def test_the_same_id_answered_twice_is_taken_once() -> None:
    batch = [cashflow.Candidate("r0", 0, "t0", -100, "outflow", {"Name": "X"})]
    answer = {
        "transaction_id": "t0",
        "cash_flow_category": "Payroll",
        "confidence": "high",
        "reason": "first",
    }
    verdicts, problems = cashflow.validate_items(
        {"items": [answer, {**answer, "reason": "second"}]}, batch
    )
    assert len(verdicts) == 1 and verdicts[0].reason == "first"
    assert any("duplicate" in p for p in problems)


def test_an_unreadable_confidence_becomes_low_rather_than_being_believed() -> None:
    batch = [cashflow.Candidate("r0", 0, "t0", -100, "outflow", {"Name": "X"})]
    verdicts, _ = cashflow.validate_items(
        {
            "items": [
                {
                    "transaction_id": "t0",
                    "cash_flow_category": "Payroll",
                    "confidence": "certain",
                    "reason": "x",
                }
            ]
        },
        batch,
    )
    assert verdicts[0].confidence == "low" and verdicts[0].category == "Payroll"


@pytest.mark.parametrize(
    "failure", [ProviderTimeoutError(), ProviderError("upstream refused", code="provider_error")]
)
def test_a_failed_batch_leaves_unclassified_rows_rather_than_losing_them(
    backend: CashflowBackend, failure: Exception
) -> None:
    with TestClient(app) as client:
        import_id = staged(client, backend)
        backend.provider.raise_with = failure  # type: ignore[attr-defined]
        progress = classify(client, import_id)
        rows = rows_of(client, import_id, limit=200)
    assert progress["done"] is True and progress["classified"] == 6
    assert len(rows) == 6
    assert all(r["category"] == "Unclassified" for r in rows)
    # Direction is still right, because it never depended on the model.
    assert {r["transaction_id"]: r["direction"] for r in rows} == DIRECTION_FOR


def test_a_refused_batch_stops_the_run_and_leaves_the_rows_to_retry(
    backend: CashflowBackend,
) -> None:
    """A cap refusal must not write Unclassified over rows that were never asked about."""
    with TestClient(app) as client:
        import_id = staged(client, backend)
        backend.refuse = "identity_cap"
        first = classify(client, import_id)
        assert first == {
            "total": 6,
            "classified": 0,
            "remaining": 6,
            "done": False,
            "batches": 0,
            "cost_micro": 0,
            "stopped": "identity_cap",
        }
        # The budget frees up and the same call picks up exactly where it stopped.
        backend.refuse = None
        second = classify(client, import_id)
        rows = rows_of(client, import_id, limit=200)
    assert second["done"] is True and second["classified"] == 6
    assert {r["transaction_id"]: r["category"] for r in rows} == CATEGORY_FOR


def test_running_twice_does_not_reclassify_or_spend_again(backend: CashflowBackend) -> None:
    with TestClient(app) as client:
        import_id = staged(client, backend)
        classify(client, import_id)
        calls = len(backend.provider.requests)  # type: ignore[attr-defined]
        again = classify(client, import_id)
    assert again["batches"] == 0 and again["done"] is True
    assert len(backend.provider.requests) == calls  # type: ignore[attr-defined]


def test_a_correction_survives_a_later_run(backend: CashflowBackend) -> None:
    with TestClient(app) as client:
        import_id = staged(client, backend)
        classify(client, import_id)
        client.patch(
            f"{base()}/{import_id}/cashflow/0",
            headers=AUTH_A,
            json={"category": "Personal expense"},
        )
        classify(client, import_id)
        rows = rows_of(client, import_id, limit=200)
    row = next(r for r in rows if r["row_index"] == 0)
    assert row["category"] == "Personal expense" and row["source"] == "manual"


def test_every_batch_is_reserved_before_it_is_sent_and_settled_after(
    backend: CashflowBackend,
) -> None:
    with TestClient(app) as client:
        import_id = staged(client, backend)
        classify(client, import_id)
    assert len(backend.tables["usage_reservations"]) == 1
    assert backend.settled == [1234]  # the measured cost, not the estimate
    run = backend.tables["ai_runs"][0]
    assert run["capability"] == "cashflow" and run["prompt_version"] == "cashflow-1"
    assert run["status"] == "succeeded" and run["cost_micro"] == 1234


# ------------------------------------------------------ reading and filtering --
def test_filters_narrow_the_set_and_report_both_counts(backend: CashflowBackend) -> None:
    with TestClient(app) as client:
        import_id = staged(client, backend)
        classify(client, import_id)
        outflows = client.get(
            f"{base()}/{import_id}/cashflow", headers=AUTH_A, params={"direction": "outflow"}
        ).json()
        payroll = client.get(
            f"{base()}/{import_id}/cashflow", headers=AUTH_A, params={"category": "Payroll"}
        ).json()
        searched = client.get(
            f"{base()}/{import_id}/cashflow", headers=AUTH_A, params={"search": "marco"}
        ).json()
        low = client.get(
            f"{base()}/{import_id}/cashflow", headers=AUTH_A, params={"confidence": "low"}
        ).json()
    assert outflows["matched"] == 4 and outflows["total"] == 6
    assert payroll["matched"] == 1 and payroll["items"][0]["transaction_id"] == "t3"
    assert searched["matched"] == 1 and searched["items"][0]["merchant"] == "Marco Diaz"
    assert low["matched"] == 0  # the scripted provider answered high throughout


def test_paging_walks_the_whole_file_in_its_own_order(backend: CashflowBackend) -> None:
    with TestClient(app) as client:
        import_id = staged(client, backend)
        classify(client, import_id)
        seen: list[int] = []
        after: int | None = None
        while True:
            params: dict[str, Any] = {"limit": 2}
            if after is not None:
                params["after"] = after
            page = client.get(
                f"{base()}/{import_id}/cashflow", headers=AUTH_A, params=params
            ).json()
            seen.extend(r["row_index"] for r in page["items"])
            after = page["next_after"]
            if after is None:
                break
    assert seen == [0, 1, 2, 3, 4, 5]


def test_the_summary_totals_both_directions_and_counts_every_bucket(
    backend: CashflowBackend,
) -> None:
    with TestClient(app) as client:
        import_id = staged(client, backend)
        classify(client, import_id)
        client.patch(
            f"{base()}/{import_id}/cashflow/4", headers=AUTH_A, json={"category": "Transfer out"}
        )
        body = client.get(f"{base()}/{import_id}/cashflow/summary", headers=AUTH_A).json()
    assert body["total_rows"] == 6 and body["classified"] == 6 and body["unclassified"] == 0
    assert body["inflow_count"] == 2 and body["inflow_minor"] == 79000 + 250000
    assert body["outflow_count"] == 4
    assert body["outflow_minor"] == 130992 + 30000 + 12500 + 25000
    assert body["net_minor"] == body["inflow_minor"] - body["outflow_minor"]
    assert body["manually_reviewed"] == 1
    buckets = {(b["category"], b["direction"]): b for b in body["buckets"]}
    assert buckets[("Organic revenue", "inflow")]["count"] == 2
    assert buckets[("Operating expense", "outflow")]["total_minor"] == 130992
    assert buckets[("Transfer out", "outflow")]["count"] == 1  # the corrected row moved bucket


# ------------------------------------------------------------------- export --
def read_export(client: TestClient, import_id: str) -> httpx.Response:
    return client.get(f"{base()}/{import_id}/cashflow/export", headers=AUTH_A)


def test_the_export_keeps_every_original_column_and_adds_the_classification(
    backend: CashflowBackend,
) -> None:
    with TestClient(app) as client:
        import_id = staged(client, backend)
        classify(client, import_id)
        response = read_export(client, import_id)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    expected_name = 'attachment; filename="plaid_classified.csv"'
    assert response.headers["content-disposition"] == expected_name
    exported = list(csv.DictReader(io.StringIO(response.text)))
    original = list(csv.DictReader(io.StringIO(PLAID_CSV.decode())))
    assert len(exported) == len(original) == 6
    for before, after in zip(original, exported, strict=True):
        for column, value in before.items():
            assert after[column] == value, f"{column} changed in the export"
    order = [f"t{i}" for i in range(1, 7)]
    assert [r["Cash Flow Category"] for r in exported] == [CATEGORY_FOR[t] for t in order]
    assert [r["Cash Flow Direction"] for r in exported] == [DIRECTION_FOR[t] for t in order]
    assert all(r["Classification Source"] == "AI" for r in exported)
    assert all(r["Classification Confidence"] == "high" for r in exported)
    assert all(r["Reviewed"] == "No" for r in exported)


def test_the_export_uses_the_corrected_value_not_the_model_answer(
    backend: CashflowBackend,
) -> None:
    with TestClient(app) as client:
        import_id = staged(client, backend)
        classify(client, import_id)
        client.patch(
            f"{base()}/{import_id}/cashflow/2",
            headers=AUTH_A,
            json={"category": "Personal expense"},
        )
        exported = list(csv.DictReader(io.StringIO(read_export(client, import_id).text)))
    row = exported[2]
    assert row["Cash Flow Category"] == "Personal expense"
    assert row["Classification Source"] == "Manual" and row["Reviewed"] == "Yes"
    assert row["Transaction Name"] == "Zelle Instant Pmt To Marco Diaz kitchen help"


def test_a_comma_or_a_quote_in_a_cell_survives_the_round_trip(
    backend: CashflowBackend,
) -> None:
    """The Categories column holds a comma in every row, so it proves the quoting on its own."""
    with TestClient(app) as client:
        import_id = staged(client, backend)
        classify(client, import_id)
        exported = list(csv.DictReader(io.StringIO(read_export(client, import_id).text)))
    assert exported[0]["Categories"] == "Food and Drink, Groceries"
    assert exported[2]["Categories"] == "Transfer, Withdrawal"


def test_an_unclassified_row_still_appears_in_the_export(backend: CashflowBackend) -> None:
    with TestClient(app) as client:
        import_id = staged(client, backend)
        backend.provider.raise_with = ProviderTimeoutError()  # type: ignore[attr-defined]
        classify(client, import_id)
        exported = list(csv.DictReader(io.StringIO(read_export(client, import_id).text)))
    assert len(exported) == 6
    assert all(r["Cash Flow Category"] == "Unclassified" for r in exported)
    order = [f"t{i}" for i in range(1, 7)]
    assert [r["Cash Flow Direction"] for r in exported] == [DIRECTION_FOR[t] for t in order]


def test_the_export_name_cannot_carry_anything_out_of_the_header(
    backend: CashflowBackend,
) -> None:
    with TestClient(app) as client:
        import_id = staged(client, backend, PLAID_CSV)
        backend.tables["document_imports"][0]["file_label"] = 'a"; drop\nx.csv'
        disposition = read_export(client, import_id).headers["content-disposition"]
    assert disposition == 'attachment; filename="a_drop_x_classified.csv"'


# --------------------------------------------------------------- who may ask --
@pytest.mark.parametrize("path", ["", "/summary", "/export"])
def test_another_owner_and_a_visitor_are_refused(backend: CashflowBackend, path: str) -> None:
    with TestClient(app) as client:
        import_id = staged(client, backend)
        classify(client, import_id)
        url = f"{base()}/{import_id}/cashflow{path}"
        assert client.get(url, headers=AUTH_B).status_code == 404  # B does not own this workspace
        assert client.get(url, headers=CLIENT).status_code == 401  # nobody is signed in
        client.cookies.set("cl_visitor", VISITOR)
        assert client.get(url, headers=CLIENT).status_code == 404  # a visitor never owns an import


def test_another_owner_cannot_run_or_correct(backend: CashflowBackend) -> None:
    with TestClient(app) as client:
        import_id = staged(client, backend)
        classify(client, import_id)
        assert client.post(f"{base()}/{import_id}/cashflow", headers=AUTH_B).status_code == 404
        assert (
            client.patch(
                f"{base()}/{import_id}/cashflow/0", headers=AUTH_B, json={"category": "Payroll"}
            ).status_code
            == 404
        )


def test_an_import_belonging_to_another_workspace_is_not_found(backend: CashflowBackend) -> None:
    """A real import id guessed from elsewhere grants nothing through the other workspace."""
    with TestClient(app) as client:
        import_id = staged(client, backend)
        other = f"/api/workspaces/{WS_A}/imports/{uuid.uuid4()}/cashflow"
        assert client.get(other, headers=AUTH_A).status_code == 404
        assert client.get(f"{base()}/{import_id}/cashflow", headers=AUTH_A).status_code == 200


# ------------------------------------------- the batch must fit in what may be written --
@pytest.mark.parametrize(
    "ceiling,configured,expected",
    [
        (1600, 25, 19),  # the measured local ceiling: nineteen items fit with room to spare
        (1200, 25, 14),  # the default ceiling
        (8000, 25, 25),  # plenty of room, so the configured size is what stands
        (64, 25, 1),  # never zero, however little the model is allowed to write
    ],
)
def test_the_batch_never_asks_for_more_than_the_model_may_write(
    monkeypatch: pytest.MonkeyPatch, ceiling: int, configured: int, expected: int
) -> None:
    """A truncated answer is unparseable, and takes every row in its batch down with it."""
    monkeypatch.setenv("AI_MAX_OUTPUT_TOKENS", str(ceiling))
    monkeypatch.setenv("AI_CASHFLOW_BATCH_SIZE", str(configured))
    get_settings.cache_clear()
    assert cashflow.batch_size_for(get_settings()) == expected
    get_settings.cache_clear()


def test_a_measured_batch_fits_with_room_left(monkeypatch: pytest.MonkeyPatch) -> None:
    """Twenty-five real transactions came back at 53.3 output tokens each; the budget is 75."""
    monkeypatch.setenv("AI_MAX_OUTPUT_TOKENS", "1600")
    get_settings.cache_clear()
    settings = get_settings()
    size = cashflow.batch_size_for(settings)
    assert size * 53.3 + cashflow.OUTPUT_TOKENS_OVERHEAD < settings.ai_max_output_tokens
    get_settings.cache_clear()


def test_the_fingerprint_is_a_digest_and_is_stable(backend: CashflowBackend) -> None:
    """Sixty-four characters, because the column requires it of every capability alike."""
    payload = [{"transaction_id": "t1", "direction": "outflow"}]
    once = cashflow.fingerprint("import-1", payload)
    assert len(once) == 64 and once == cashflow.fingerprint("import-1", payload)
    assert once != cashflow.fingerprint("import-2", payload)
