"""Explaining a scenario the owner already ran.

The model never computes here. What is tested is that the facts it is handed keep the three
distinctions that make an explanation trustworthy — ending cash apart from lowest cash, two
troughs apart from one day's movement, and the owner's assumptions apart from anything the bank
observed — and that the same validation that governs a brief governs this too.
"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ai import prices
from app.ai import scenario as scenario_module
from app.ai.provider import ModelRequest, ProviderError
from app.config import get_settings
from app.main import app
from tests.test_ai_brief import (
    CLIENT,
    OTHER_VISITOR,
    WS_OTHER_VISITOR,
    WS_VISITOR,
    BillableScripted,
    FakeDb,
    Scripted,
    price_the_model,
    use,
    visitor,
)

RECEIPTS_DROP = {"receipts_change_percent": -20}
ONE_OFF_COST = {
    "one_time_expense_minor": 250_000,
    "one_time_expense_date": "2026-09-10",
    "one_time_expense_label": "New oven",
}
LATE_PAYMENT = {
    "expected_payment_minor": 400_000,
    "expected_payment_date": "2026-09-05",
    "delay_days": 14,
}


def facts_of(request: ModelRequest) -> dict[str, Any]:
    return json.JSONDecoder().raw_decode(request.user.split("\n\n", 1)[1])[0]


def by_id(facts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["id"]: f for f in facts["facts"]}


def explain(client: TestClient, body: dict[str, Any], workspace: str = WS_VISITOR):
    return client.post(
        f"/api/workspaces/{workspace}/ai/scenario/explain", headers=CLIENT, json=body
    )


def grounded_answer(facts: dict[str, Any]) -> str:
    """An explanation built from whatever the fact sheet actually contains."""
    facts_by_id = by_id(facts)
    claims = []
    # What the owner changed. `sc:inputs` is a required fact, so an explanation that never
    # mentions the assumption is incomplete however true the rest of it is.
    changed = facts_by_id["sc:inputs"]
    claims.append(
        {
            "signal_id": "sc:inputs",
            "text": f"You asked what happens if {changed['values']['changes'][0]}.",
            "figures": [],
            "evidence": [],
        }
    )
    ending = facts_by_id["fc:ending"]
    claims.append(
        {
            "signal_id": "fc:ending",
            "text": (
                f"Cash at the end of the window is expected to be "
                f"{ending['rendered']['scenario']} instead of {ending['rendered']['baseline']}."
            ),
            "figures": [
                {
                    "ref": "fc:ending.scenario_minor",
                    "amount_minor": ending["values"]["scenario_minor"],
                }
            ],
            "evidence": [],
        }
    )
    low = facts_by_id["fc:lowest"]
    claims.append(
        {
            "signal_id": "fc:lowest",
            "text": (
                f"The projected low point moves to {low['rendered']['scenario']} "
                f"from {low['rendered']['baseline']}."
            ),
            "figures": [
                {
                    "ref": "fc:lowest.scenario_minor",
                    "amount_minor": low["values"]["scenario_minor"],
                }
            ],
            "evidence": [],
        }
    )
    if "sc:timing" in facts_by_id:
        timing = facts_by_id["sc:timing"]
        claims.append(
            {
                "signal_id": "sc:timing",
                "text": (f"The paths are expected to separate from {timing['values']['date']}."),
                "figures": [],
                "evidence": [],
            }
        )
    return json.dumps(
        {
            "headline": "What your change is expected to do to cash.",
            "claims": claims,
            "questions": [
                {"signal_id": "fc:ending", "text": "Is the amount you expect still right?"}
            ],
            "caveats": ["Every figure here is a projection, not a promise."],
        }
    )


# ------------------------------------------------------------ what is sent --
@pytest.mark.parametrize(
    "body,expected",
    [
        (RECEIPTS_DROP, "receipts"),
        (ONE_OFF_COST, "New oven"),
        (LATE_PAYMENT, "late"),
    ],
)
def test_the_owners_own_assumptions_travel_with_the_facts(
    db: FakeDb, body: dict[str, Any], expected: str
) -> None:
    provider = Scripted(grounded_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        assert explain(client, body).json()["status"] == "succeeded"
    facts = facts_of(provider.requests[0])
    changes = " ".join(by_id(facts)["sc:inputs"]["values"]["changes"])
    assert expected in changes
    assert by_id(facts)["sc:inputs"]["label"] == "What you changed"


def test_ending_and_lowest_are_separate_facts_with_both_dates(db: FakeDb) -> None:
    """A difference between two troughs is not one day's movement, and the facts say so."""
    provider = Scripted(grounded_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        explain(client, LATE_PAYMENT)
    facts = by_id(facts_of(provider.requests[0]))

    ending, low = facts["fc:ending"], facts["fc:lowest"]
    assert ending["id"] != low["id"], "ending cash and lowest cash are different questions"
    for key in ("baseline_minor", "scenario_minor", "difference_minor"):
        assert key in ending["values"] and key in low["values"]
    # Both troughs carry their own date, and the fact says whether they coincide.
    assert low["values"]["baseline_date"] and low["values"]["scenario_date"]
    assert isinstance(low["values"]["same_day"], bool)
    if not low["values"]["same_day"]:
        assert "not the amount the balance moved on any one day" in low["values"]["note"]


def test_every_projected_figure_is_marked_estimated(db: FakeDb) -> None:
    provider = Scripted(grounded_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        explain(client, RECEIPTS_DROP)
    facts = by_id(facts_of(provider.requests[0]))
    for fact_id in ("fc:ending", "fc:lowest"):
        assert facts[fact_id].get("estimated") is True, f"{fact_id} must be hedged in any claim"


def test_the_first_day_the_paths_differ_is_computed_not_guessed(db: FakeDb) -> None:
    provider = Scripted(grounded_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        explain(client, ONE_OFF_COST)
    facts = by_id(facts_of(provider.requests[0]))
    assert "sc:timing" in facts
    assert facts["sc:timing"]["values"]["date"] >= "2026-09-10", "the cost lands on the 10th"


# ------------------------------------------------------- what comes back --
def test_a_grounded_explanation_survives_the_same_four_layers(db: FakeDb) -> None:
    use(Scripted(grounded_answer))
    with TestClient(app) as client:
        visitor(client)
        body = explain(client, LATE_PAYMENT).json()
    assert body["status"] == "succeeded"
    assert body["explanation"]["claims"]
    layers = body["run"]["validation"]
    assert all(layers[k]["ok"] for k in ("schema", "evidence", "numbers", "support"))
    assert body["run"]["capability"] == "scenario_explain"
    assert body["run"]["prompt_version"] == "scenario-explain-4"


def test_an_unhedged_projection_is_withheld(db: FakeDb) -> None:
    """The forecast facts are estimated, so the validator's own rule forces the hedge."""

    def certain(facts: dict[str, Any]) -> str:
        ending = by_id(facts)["fc:ending"]
        return json.dumps(
            {
                "headline": "What your change does to cash.",
                "claims": [
                    {
                        "signal_id": "fc:ending",
                        "text": f"You will finish with {ending['rendered']['scenario']}.",
                        "figures": [],
                        "evidence": [],
                    }
                ],
                "questions": [],
                "caveats": [],
            }
        )

    use(Scripted(certain))
    with TestClient(app) as client:
        visitor(client)
        body = explain(client, RECEIPTS_DROP).json()
    assert body["explanation"]["claims_withheld"] == 1
    assert body["explanation"]["claims"] == []


def test_an_invented_cause_is_withheld(db: FakeDb) -> None:
    def blames(facts: dict[str, Any]) -> str:
        ending = by_id(facts)["fc:ending"]
        return json.dumps(
            {
                "headline": "What your change is expected to do.",
                "claims": [
                    {
                        "signal_id": "fc:ending",
                        "text": (
                            "Cash is expected to end at "
                            f"{ending['rendered']['scenario']} because your customers pay late."
                        ),
                        "figures": [],
                        "evidence": [],
                    }
                ],
                "questions": [],
                "caveats": [],
            }
        )

    use(Scripted(blames))
    with TestClient(app) as client:
        visitor(client)
        body = explain(client, LATE_PAYMENT).json()
    assert body["explanation"]["claims_withheld"] == 1


def test_a_figure_the_projection_never_produced_is_withheld(db: FakeDb) -> None:
    def invented(facts: dict[str, Any]) -> str:
        return json.dumps(
            {
                "headline": "What your change is expected to do.",
                "claims": [
                    {
                        "signal_id": "fc:ending",
                        "text": "Cash is expected to end at $99,999.00 after the change.",
                        "figures": [],
                        "evidence": [],
                    }
                ],
                "questions": [],
                "caveats": [],
            }
        )

    use(Scripted(invented))
    with TestClient(app) as client:
        visitor(client)
        body = explain(client, RECEIPTS_DROP).json()
    assert body["explanation"]["claims_withheld"] == 1
    assert body["run"]["validation"]["numbers"]["ok"] is False


# ------------------------------------------------------------- staleness --
def test_the_same_data_and_assumptions_are_never_paid_for_twice(db: FakeDb) -> None:
    provider = Scripted(grounded_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        first = explain(client, RECEIPTS_DROP).json()
        again = explain(client, RECEIPTS_DROP).json()
    assert first["status"] == "succeeded" and again["status"] == "current"
    assert again["reused"] is True
    assert again["fingerprint"] == first["fingerprint"]
    assert len(provider.requests) == 1, "the second press asked the model nothing"


def test_changing_an_assumption_makes_the_stored_explanation_miss(db: FakeDb) -> None:
    provider = Scripted(grounded_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        first = explain(client, RECEIPTS_DROP).json()
        other = explain(client, {"receipts_change_percent": -25}).json()
    assert other["fingerprint"] != first["fingerprint"]
    assert other["reused"] is False
    assert len(provider.requests) == 2


def test_changing_the_data_makes_the_stored_explanation_miss(db: FakeDb) -> None:
    from tests.test_ai_brief import _txn

    provider = Scripted(grounded_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        first = explain(client, RECEIPTS_DROP).json()
        db.transactions.append(_txn(980, "2026-08-27", -12_000, "PINE VALLEY FOOD DISTRIBUTION"))
        after = explain(client, RECEIPTS_DROP).json()
    assert after["fingerprint"] != first["fingerprint"]
    assert after["reused"] is False


# --------------------------------------------------- failure and isolation --
def test_the_projection_stands_when_the_explanation_fails(db: FakeDb) -> None:
    use(Scripted(ProviderError("connection reset")))
    with TestClient(app) as client:
        visitor(client)
        body = explain(client, RECEIPTS_DROP).json()
        scenario = client.post(
            f"/api/workspaces/{WS_VISITOR}/analysis/scenario",
            headers=CLIENT,
            json=RECEIPTS_DROP,
        ).json()
    assert body["status"] == "provider_error" and body["explanation"] is None
    assert "projection below is unaffected" in body["message"]
    # The deterministic tool is untouched and still answers.
    assert scenario["forecast"]["scenario"]["ending_minor"] is not None


def test_another_visitor_cannot_read_this_workspaces_explanation(db: FakeDb) -> None:
    use(Scripted(grounded_answer))
    with TestClient(app) as client:
        visitor(client)
        run_id = explain(client, RECEIPTS_DROP).json()["run"]["id"]
    with TestClient(app) as other:
        visitor(other, OTHER_VISITOR)
        assert other.get(f"/api/workspaces/{WS_OTHER_VISITOR}/ai/runs/{run_id}").status_code == 404
        assert other.get(f"/api/workspaces/{WS_VISITOR}/ai/runs").status_code == 404
        # Their own identical scenario is not served my stored explanation.
        mine = explain(other, RECEIPTS_DROP, WS_OTHER_VISITOR).json()
        assert mine["reused"] is False


# ---------------------------------------------------------------- budget --
def test_an_explanation_is_held_against_the_milestone_budget(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    price_the_model(monkeypatch)
    use(BillableScripted(grounded_answer))
    with TestClient(app) as client:
        visitor(client)
        explain(client, RECEIPTS_DROP)
    held = db.reservations[-1]
    assert held["estimated_cost_micro"] > 0 and held["cost_micro"] == 1_500
    assert held["capability"] == "scenario_explain"


def test_an_unpriced_model_never_dispatches_an_explanation(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    price_the_model(monkeypatch, {"recorded_on": None, "models": {}})
    provider = BillableScripted(grounded_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        response = explain(client, RECEIPTS_DROP)
    assert response.status_code == 503
    assert provider.requests == [] and db.reservations == []


def test_explanations_are_off_with_the_master_switch(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ai.factory import get_model_provider

    monkeypatch.setenv("AI_ENABLED", "false")
    get_settings.cache_clear()
    app.dependency_overrides.pop(get_model_provider, None)
    with TestClient(app) as client:
        visitor(client)
        assert explain(client, RECEIPTS_DROP).status_code == 503


def test_the_combined_fingerprint_covers_data_and_assumptions() -> None:
    a = scenario_module.combined_fingerprint("data-1", {"receipts_change_percent": -20})
    assert a == scenario_module.combined_fingerprint("data-1", {"receipts_change_percent": -20})
    assert a != scenario_module.combined_fingerprint("data-2", {"receipts_change_percent": -20})
    assert a != scenario_module.combined_fingerprint("data-1", {"receipts_change_percent": -25})
    assert len(a) == 64


def test_pricing_an_explanation_covers_both_possible_calls() -> None:
    table = {"models": {"m": {"input_usd_per_million": 1.75, "output_usd_per_million": 14.0}}}
    original = prices._table
    prices._table = lambda: table
    try:
        assert prices.estimate_micro("m", 3_000, 1_200) > prices.estimate_micro(
            "m", 3_000, 1_200, attempts=1
        )
    finally:
        prices._table = original


def test_no_scenario_fact_carries_transaction_evidence(db: FakeDb) -> None:
    """These facts come from two computed paths, not from rows, so nothing may be attached.

    All three live explanations attached evidence anyway and were rejected for it, because the
    contract never said the array had to be empty. It says so now.
    """
    from app.ai import prompts

    provider = Scripted(grounded_answer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        explain(client, LATE_PAYMENT)
    for fact in facts_of(provider.requests[0])["facts"]:
        assert not fact.get("evidence"), f"{fact['id']} should carry no transaction evidence"
    assert "MUST BE EMPTY" in prompts.SCENARIO_SYSTEM


def test_attaching_evidence_to_a_computed_fact_is_rejected(db: FakeDb) -> None:
    def attaches(facts: dict[str, Any]) -> str:
        ending = by_id(facts)["fc:ending"]
        return json.dumps(
            {
                "headline": "What your change is expected to do.",
                "claims": [
                    {
                        "signal_id": "fc:ending",
                        "text": f"Cash is expected to end at {ending['rendered']['scenario']}.",
                        "figures": [],
                        "evidence": ["ffffffff-ffff-4fff-8fff-000000000001"],
                    }
                ],
                "questions": [],
                "caveats": [],
            }
        )

    use(Scripted(attaches))
    with TestClient(app) as client:
        visitor(client)
        body = explain(client, RECEIPTS_DROP).json()
    assert body["status"] == "invalid_output"
    assert body["run"]["validation"]["evidence"]["ok"] is False


# ------------------------------------- a short answer is not a successful one --
def one_sentence_answer(facts: dict[str, Any]) -> str:
    """One true sentence about the ending balance, and nothing about the trough."""
    ending = by_id(facts)["fc:ending"]
    return json.dumps(
        {
            "headline": "What your change is expected to do to cash.",
            "claims": [
                {
                    "signal_id": "fc:ending",
                    "text": (
                        "Cash at the end of the window is expected to be "
                        f"{ending['rendered']['scenario']}."
                    ),
                    "figures": [
                        {
                            "ref": "fc:ending.scenario_minor",
                            "amount_minor": ending["values"]["scenario_minor"],
                        }
                    ],
                    "evidence": [],
                }
            ],
            "questions": [],
            "caveats": [],
        }
    )


def test_an_answer_that_misses_the_essentials_is_reported_as_partial(db: FakeDb) -> None:
    """Every layer passes and the sentence is true, but it is not an explanation.

    A delayed payment can leave the ending balance alone and still dig a hole in the middle, so
    an answer that only mentions the ending has skipped the thing the owner asked about.
    Reporting that as "succeeded" states the validator's opinion as the owner's answer.
    """
    use(Scripted(one_sentence_answer))
    with TestClient(app) as client:
        visitor(client)
        body = explain(client, LATE_PAYMENT).json()

    assert body["status"] == "partial"
    assert body["explanation"] is not None and len(body["explanation"]["claims"]) == 1
    assert "does not cover" in body["message"]
    # The run itself succeeded: the call was made and its answer was checked.
    assert body["run"]["status"] == "succeeded"
    assert body["run"]["validation"]["coverage_gaps"]


def test_a_partial_explanation_does_not_become_complete_by_being_reused(db: FakeDb) -> None:
    """Pressing the button again returns the stored answer, still reported as partial."""
    use(Scripted(one_sentence_answer))
    with TestClient(app) as client:
        visitor(client)
        first = explain(client, LATE_PAYMENT).json()
        again = explain(client, LATE_PAYMENT).json()

    assert first["status"] == "partial"
    assert again["status"] == "partial" and again["reused"] is True
    assert "does not cover" in again["message"]
    # Still only one paid attempt.
    assert len([r for r in db.ai_runs if r["capability"] == "scenario_explain"]) == 1
