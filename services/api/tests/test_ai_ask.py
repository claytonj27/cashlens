"""The conversation panel: what the tools will do, and what they refuse.

This is the only capability where the model influences what data it sees, so most of what is
asserted here is a refusal. The model is a function the test writes, so a round that asks for
something it should not get is produced deliberately rather than waited for.

No network anywhere: the data service is the in-memory one and the provider is scripted.
"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ai import prompts
from app.config import get_settings
from app.main import app
from tests.test_ai_brief import (
    CLIENT,
    GOOD_TOKEN,
    SECRET_DESCRIPTION,
    WS_OTHER_VISITOR,
    WS_USER,
    WS_VISITOR,
    BillableScripted,
    FakeDb,
    Scripted,
    price_the_model,
    use,
    visitor,
)


# ------------------------------------------------------------------ helpers --
def tool(name: str, **arguments: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "transactions": None,
        "scenario": None,
        "propose_category": None,
    }
    if name in body and arguments:
        body[name] = arguments
    return body


def use_tools(*tools: dict[str, Any]) -> str:
    return json.dumps({"action": "use_tools", "tools": list(tools), "answer": None})


def answer(
    text: str,
    claims: list[dict[str, Any]] | None = None,
    sources: list[str] | None = None,
    clarify: dict[str, Any] | None = None,
) -> str:
    return json.dumps(
        {
            "action": "answer",
            "tools": [],
            "answer": {
                "answer": text,
                "claims": claims or [],
                "questions": [],
                "sources": sources or [],
                "clarify": clarify,
            },
        }
    )


def results(payload: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """Every result the named tool produced in the turn payload the model was handed."""
    return [
        r["result"]
        for r in payload.get("tool_results", [])
        if r["tool"] == name and r["ok"] and r["result"]
    ]


def a_fact(payload: dict[str, Any], prefix: str) -> dict[str, Any] | None:
    for result in results(payload, "facts") + results(payload, "scenario"):
        for fact in result.get("facts", []):
            if fact["id"].startswith(prefix):
                return fact
    return None


def ask(client: TestClient, text: str, workspace: str = WS_VISITOR, **body: Any):
    return client.post(
        f"/api/workspaces/{workspace}/ai/ask", headers=CLIENT, json={"text": text, **body}
    )


def owner() -> dict[str, str]:
    return {**CLIENT, "authorization": f"Bearer {GOOD_TOKEN}"}


def answer_from_facts(payload: dict[str, Any]) -> str:
    """A well-formed answer built from whatever the facts tool actually returned."""
    balance = a_fact(payload, "bal:")
    return answer(
        f"Your latest statement balance is {balance['rendered']['amount']}.",
        claims=[
            {
                "signal_id": balance["id"],
                "text": f"The balance on the statement is {balance['rendered']['amount']}.",
                "figures": [
                    {
                        "ref": f"{balance['id']}.amount_minor",
                        "amount_minor": balance["values"]["amount_minor"],
                    }
                ],
                "evidence": [],
            }
        ],
        sources=[balance["id"]],
    )


# --------------------------------------------------------------- the good path --
def test_a_question_is_answered_from_the_facts_the_tools_returned(db: FakeDb) -> None:
    provider = Scripted(use_tools(tool("facts")), answer_from_facts)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = ask(client, "What changed this month?").json()

    assert body["status"] == "answered"
    assert body["answer"]["claims"] and body["answer"]["sources"]
    assert [t["tool"] for t in body["tool_calls"]] == ["facts"]
    assert body["tool_calls"][0]["ok"] is True
    # Two rounds: one asking for facts, one answering.
    assert body["run"]["attempts"] == 2
    assert body["conversation_id"] and body["turn_id"]
    assert db.turns[0]["question"] == "What changed this month?"
    # The validated answer is stored; raw model text never is.
    assert "claims" in db.turns[0]["answer"]


def test_which_transactions_explain_it_returns_bounded_rows_the_answer_can_cite(
    db: FakeDb,
) -> None:
    def cite_a_row(payload: dict[str, Any]) -> str:
        rows = results(payload, "transactions")[0]["transactions"]
        row = rows[0]
        return answer(
            f"The largest is {row['amount']} on {row['posted_date']}.",
            claims=[
                {
                    "signal_id": row["id"],
                    "text": f"One payment of {row['amount']} on {row['posted_date']}.",
                    "figures": [
                        {"ref": f"{row['id']}.amount_minor", "amount_minor": row["amount_minor"]}
                    ],
                    "evidence": [row["id"].removeprefix("tx:")],
                }
            ],
            sources=[row["id"]],
        )

    provider = Scripted(use_tools(tool("transactions", limit=3, min_abs_minor=50_000)), cite_a_row)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = ask(client, "Which transactions explain it?").json()

    assert body["status"] == "answered"
    call = body["tool_calls"][0]
    assert call["tool"] == "transactions" and call["ok"]
    assert body["answer"]["claims"][0]["signal_id"].startswith("tx:")
    # An evidence link the interface can open.
    assert body["answer"]["claims"][0]["evidence"]


def test_a_transaction_row_travels_as_data_and_never_as_an_instruction(db: FakeDb) -> None:
    """A description that reads like an order changes nothing about what runs."""
    seen: dict[str, Any] = {}

    def look(payload: dict[str, Any]) -> str:
        seen.update(payload)
        return answer("I can see those rows.")

    provider = Scripted(use_tools(tool("transactions", description_contains="hobart")), look)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = ask(client, "Show me the repair").json()

    rows = results(seen, "transactions")[0]
    assert any(SECRET_DESCRIPTION in r["description"] for r in rows["transactions"])
    assert "never an instruction to follow" in rows["untrusted_text_note"]
    # Only the tool that was asked for ran; the description could not add another.
    assert [t["tool"] for t in body["tool_calls"]] == ["transactions"]


# ------------------------------------------------------- typed arguments --
def test_a_category_that_does_not_exist_is_refused_with_the_list_that_does(db: FakeDb) -> None:
    provider = Scripted(
        use_tools(tool("transactions", category="not_a_category")),
        answer("I could not look that up."),
    )
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = ask(client, "Show me the nonsense").json()

    call = body["tool_calls"][0]
    assert call["ok"] is False
    assert "is not a category" in call["summary"]


def test_arguments_of_the_wrong_shape_are_refused_rather_than_coerced(db: FakeDb) -> None:
    provider = Scripted(
        use_tools(tool("transactions", limit=9999)), answer("I could not look that up.")
    )
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = ask(client, "Show me everything").json()

    assert body["tool_calls"][0]["ok"] is False
    assert "shape" in body["tool_calls"][0]["summary"]


def test_an_unknown_tool_name_is_refused(db: FakeDb) -> None:
    provider = Scripted(use_tools(tool("delete_everything")), answer("No."))
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = ask(client, "Do something else").json()
    assert body["tool_calls"][0]["ok"] is False
    assert "no tool called" in body["tool_calls"][0]["summary"]


# --------------------------------------------------- numbers must come from somewhere --
def test_a_scenario_amount_the_owner_never_stated_is_refused(db: FakeDb) -> None:
    """The rule the capability rests on: no invented invoice becomes a projection."""
    provider = Scripted(
        use_tools(
            tool(
                "scenario",
                expected_payment_minor=987_654,
                expected_payment_date="2026-09-05",
                delay_days=3,
            )
        ),
        answer("I need the amount from you."),
    )
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = ask(client, "What if a customer pays late?").json()

    call = body["tool_calls"][0]
    assert call["ok"] is False
    assert "expected_payment_minor" in call["summary"]
    assert "not an amount the owner gave you" in call["summary"]
    assert body["scenario_applied"] is None and body["scenario_paths"] is None


def test_a_scenario_the_owner_did_state_is_computed(db: FakeDb) -> None:
    def explain(payload: dict[str, Any]) -> str:
        ending = a_fact(payload, "fc:ending")
        return answer(
            "Ending cash is expected to change.",
            claims=[
                {
                    "signal_id": "fc:ending",
                    "text": (
                        "Ending cash is expected to be "
                        f"{ending['rendered']['scenario']} instead of "
                        f"{ending['rendered']['baseline']}."
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
            sources=["fc:ending"],
        )

    provider = Scripted(
        use_tools(
            tool(
                "scenario",
                expected_payment_minor=250_000,
                expected_payment_date="2026-09-05",
                delay_days=3,
            )
        ),
        explain,
    )
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = ask(client, "What if the $2,500.00 due 2026-09-05 arrives three days later?").json()

    assert body["status"] in ("answered", "partial")
    assert body["tool_calls"][0]["ok"] is True
    assert body["scenario_applied"]["delay_days"] == 3
    assert body["scenario_paths"]["baseline"] and body["scenario_paths"]["scenario"]
    # Agreed for the rest of the conversation, stamped with the data it was agreed against.
    assert db.conversations[0]["agreed_assumptions"]["expected_payment_minor"] == 250_000
    assert db.conversations[0]["assumptions_fingerprint"] == body["fingerprint"]


def test_a_follow_up_changes_only_the_delay_and_keeps_the_agreed_figures(db: FakeDb) -> None:
    """ "and if it's a week?" reuses the amount and date the owner already agreed to."""
    first = Scripted(
        use_tools(
            tool(
                "scenario",
                expected_payment_minor=250_000,
                expected_payment_date="2026-09-05",
                delay_days=3,
            )
        ),
        answer("Computed."),
    )
    use(first)
    with TestClient(app) as client:
        visitor(client)
        started = ask(
            client, "What if the $2,500.00 due 2026-09-05 arrives three days later?"
        ).json()

        # The follow-up names only the new delay. The amount and date come from the agreement.
        second = Scripted(
            use_tools(
                tool(
                    "scenario",
                    expected_payment_minor=250_000,
                    expected_payment_date="2026-09-05",
                    delay_days=7,
                )
            ),
            answer("Computed again."),
        )
        use(second)
        body = ask(
            client, "And if it's a week instead?", conversation_id=started["conversation_id"]
        ).json()

    assert body["tool_calls"][0]["ok"] is True
    assert body["scenario_applied"]["delay_days"] == 7
    assert body["scenario_applied"]["expected_payment_minor"] == 250_000
    # The same conversation, not a new one.
    assert body["conversation_id"] == started["conversation_id"]


def test_a_delay_the_owner_never_described_is_refused_even_with_agreed_figures(
    db: FakeDb,
) -> None:
    first = Scripted(
        use_tools(
            tool(
                "scenario",
                expected_payment_minor=250_000,
                expected_payment_date="2026-09-05",
                delay_days=3,
            )
        ),
        answer("Computed."),
    )
    use(first)
    with TestClient(app) as client:
        visitor(client)
        started = ask(
            client, "What if the $2,500.00 due 2026-09-05 arrives three days later?"
        ).json()
        second = Scripted(
            use_tools(
                tool(
                    "scenario",
                    expected_payment_minor=250_000,
                    expected_payment_date="2026-09-05",
                    delay_days=45,
                )
            ),
            answer("I need to know how late."),
        )
        use(second)
        body = ask(
            client, "What about later still?", conversation_id=started["conversation_id"]
        ).json()

    assert body["tool_calls"][0]["ok"] is False
    assert "delay_days of 45" in body["tool_calls"][0]["summary"]


# ------------------------------------------------ clarifying rather than substituting --
def test_a_clarifying_answer_stores_its_options_and_a_choice_agrees_to_them(
    db: FakeDb,
) -> None:
    """An unsupported request is asked about, never translated into a different one."""

    def offer(payload: dict[str, Any]) -> str:
        rec = a_fact(payload, "rec:")
        return answer(
            "I cannot move every receipt at once.",
            clarify={
                "question": "Which payment did you mean?",
                "options": [
                    {
                        "label": "The recurring one",
                        "scenario": {
                            "receipts_change_percent": None,
                            "expected_payment_minor": abs(rec["values"]["typical_amount_minor"]),
                            "expected_payment_date": rec["values"].get("next_expected"),
                            "delay_days": 3,
                            "one_time_expense_minor": None,
                            "one_time_expense_date": None,
                            "one_time_expense_label": None,
                        },
                    }
                ],
            },
        )

    provider = Scripted(use_tools(tool("facts")), offer)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = ask(client, "What if all my receipts arrive three days later?").json()

        assert body["status"] == "clarifying"
        assert body["scenario_applied"] is None
        option = body["clarify"]["options"][0]
        assert option["id"] and option["scenario"]["delay_days"] == 3

        # Choosing one sends an id, never a scenario. The figures come from what the server
        # stored when it offered the choice.
        chosen = Scripted(
            use_tools(tool("scenario", **{k: v for k, v in option["scenario"].items() if v})),
            answer("Computed."),
        )
        use(chosen)
        second = ask(
            client,
            "That one please",
            conversation_id=body["conversation_id"],
            option_id=option["id"],
        ).json()

    assert second["tool_calls"][0]["ok"] is True
    assert second["scenario_applied"]["delay_days"] == 3


# -------------------------------------------------------------------- bounds --
def test_a_model_that_only_ever_asks_for_tools_is_stopped_and_says_so(db: FakeDb) -> None:
    provider = Scripted(use_tools(tool("facts")))
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = ask(client, "Loop forever").json()

    assert body["status"] == "invalid_output"
    assert "figures below are complete" in body["message"]
    # Never more rounds than the bound, whatever the model does.
    assert len(provider.requests) <= prompts.ASK_MAX_ROUNDS


def test_no_more_than_four_tool_calls_run_however_many_are_asked_for(db: FakeDb) -> None:
    many = use_tools(*[tool("facts") for _ in range(9)])
    provider = Scripted(many, answer("Enough."))
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = ask(client, "Everything at once").json()

    assert len(body["tool_calls"]) <= prompts.ASK_MAX_TOOL_CALLS


def test_only_one_scenario_is_computed_per_turn(db: FakeDb) -> None:
    twice = use_tools(
        tool(
            "scenario",
            expected_payment_minor=250_000,
            expected_payment_date="2026-09-05",
            delay_days=3,
        ),
        tool(
            "scenario",
            expected_payment_minor=250_000,
            expected_payment_date="2026-09-05",
            delay_days=3,
        ),
    )
    provider = Scripted(twice, answer("Once was enough."))
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = ask(client, "What if the $2,500.00 due 2026-09-05 arrives three days later?").json()

    computed = [t for t in body["tool_calls"] if t["tool"] == "scenario" and t["ok"]]
    refused = [t for t in body["tool_calls"] if t["tool"] == "scenario" and not t["ok"]]
    assert len(computed) == 1
    assert refused and "already been computed" in refused[0]["summary"]


def test_a_conversation_stops_at_its_turn_limit(db: FakeDb) -> None:
    use(Scripted(answer("Fine.")))
    with TestClient(app) as client:
        visitor(client)
        started = ask(client, "One").json()
        conversation = started["conversation_id"]
        for row in db.conversations:
            row["turn_count"] = 12
        refused = ask(client, "Thirteen", conversation_id=conversation)
    assert refused.status_code == 409
    assert refused.json()["detail"]["reason"] == "conversation_full"


# ------------------------------------------------------------------- the money --
def test_one_reservation_covers_the_whole_turn_and_settles_at_what_it_cost(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    price_the_model(monkeypatch)
    provider = BillableScripted(use_tools(tool("facts")), answer_from_facts)
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = ask(client, "What changed this month?").json()

    assert body["status"] == "answered"
    held = [r for r in db.reservations if r["capability"] == "ask"]
    assert len(held) == 1
    # The hold covered three rounds; two were made, and both are billed.
    assert held[0]["estimated_cost_micro"] > 0
    assert held[0]["cost_micro"] == 1_500 * 2
    assert body["run"]["cost_micro"] == 3_000


def test_a_second_question_while_one_is_running_is_refused(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The in-flight limit is what stops a double-click buying two turns."""
    price_the_model(monkeypatch)
    use(BillableScripted(answer("Fine.")))
    with TestClient(app) as client:
        visitor(client)
        db.reservations.append(
            {
                "id": "00000000-0000-4000-8000-00000000ffff",
                "identity_kind": "visitor",
                "identity_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1",
                "capability": "ask",
                "estimated_cost_micro": 1_000,
                "cost_micro": None,
                "settled_at": None,
                "expires_at": "2099-01-01T00:00:00+00:00",
                "created_at": "2026-09-14T00:00:00+00:00",
            }
        )
        second = ask(client, "And again")
    assert second.status_code == 409
    assert second.json()["detail"]["reason"] == "in_flight"


def test_an_unpriced_model_refuses_before_anything_is_reserved(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    price_the_model(monkeypatch, {"recorded_on": None, "models": {}})
    use(BillableScripted(answer("Fine.")))
    with TestClient(app) as client:
        visitor(client)
        response = ask(client, "What changed?")
    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "unpriced_model"
    assert db.reservations == [] and db.ai_runs == []


# --------------------------------------------------------------- isolation --
def test_another_workspace_is_out_of_reach(db: FakeDb) -> None:
    use(Scripted(answer("Fine.")))
    with TestClient(app) as client:
        visitor(client)
        assert ask(client, "Tell me", workspace=WS_USER).status_code == 404
        assert ask(client, "Tell me", workspace=WS_OTHER_VISITOR).status_code == 404
    assert db.turns == [] and db.conversations == []


def test_a_conversation_from_another_workspace_cannot_be_continued(db: FakeDb) -> None:
    use(Scripted(answer("Fine.")))
    with TestClient(app) as client:
        visitor(client)
        started = ask(client, "Mine").json()
    # The same id, presented for a workspace that does not own it.
    db.conversations[0]["workspace_id"] = WS_OTHER_VISITOR
    with TestClient(app) as client:
        visitor(client)
        refused = ask(client, "Yours", conversation_id=started["conversation_id"])
    assert refused.status_code == 404


def test_a_private_workspace_cannot_reach_openrouter_through_the_panel(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The restriction holds for the capability that sends the most data per turn."""
    monkeypatch.setenv("AI_OPENROUTER_ENABLED", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setenv("OPENROUTER_MODEL", "qwen/qwen3.8-max-0902")
    monkeypatch.setenv("OPENROUTER_ALLOWED_MODELS", "qwen/qwen3.8-max-0902")
    get_settings.cache_clear()
    provider = BillableScripted(answer("Fine."))
    use(provider)
    with TestClient(app) as client:
        response = client.post(
            f"/api/workspaces/{WS_USER}/ai/ask?route=openrouter",
            headers=owner(),
            json={"text": "Tell me about my business"},
        )
    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "private_data_not_routable"
    # Nothing was reserved, recorded, or sent.
    assert db.reservations == [] and db.ai_runs == [] and provider.requests == []


# ------------------------------------------------- a proposal is only a proposal --
def test_a_proposed_category_becomes_a_suggestion_and_moves_no_figure(db: FakeDb) -> None:
    def propose(payload: dict[str, Any]) -> str:
        rows = results(payload, "transactions")[0]["transactions"]
        target = next(r for r in rows if "DEPOSIT" in r["description"])
        return use_tools(
            tool(
                "propose_category",
                transaction_id=target["id"].removeprefix("tx:"),
                category="customer_receipts",
                rationale="It is a deposit into the operating account.",
            )
        )

    provider = Scripted(
        use_tools(tool("transactions", description_contains="deposit")),
        propose,
        answer("I have put that in your review list."),
    )
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        before = client.get(f"/api/workspaces/{WS_VISITOR}/analysis").json()
        body = ask(client, "What is that deposit?").json()
        after = client.get(f"/api/workspaces/{WS_VISITOR}/analysis").json()
        listed = client.get(f"/api/workspaces/{WS_VISITOR}/ai/suggestions").json()

    call = [t for t in body["tool_calls"] if t["tool"] == "propose_category"][0]
    assert call["ok"] is True
    assert body["proposal_id"]
    # A suggestion waiting for a person, and not one figure moved.
    assert any(i["id"] == body["proposal_id"] for i in listed["items"])
    assert [c["outflow_minor"] for c in before["categories"]] == [
        c["outflow_minor"] for c in after["categories"]
    ]
    assert after["overrides_applied"] == before["overrides_applied"]
    assert db.overrides == []


def test_a_proposal_whose_direction_does_not_fit_the_amount_is_refused(db: FakeDb) -> None:
    def propose(payload: dict[str, Any]) -> str:
        rows = results(payload, "transactions")[0]["transactions"]
        outflow = next(r for r in rows if r["amount_minor"] < 0)
        return use_tools(
            tool(
                "propose_category",
                transaction_id=outflow["id"].removeprefix("tx:"),
                category="customer_receipts",
                rationale="Trying to file money going out as receipts.",
            )
        )

    provider = Scripted(
        use_tools(tool("transactions", limit=20)), propose, answer("I could not do that.")
    )
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = ask(client, "Categorise something").json()

    call = [t for t in body["tool_calls"] if t["tool"] == "propose_category"][0]
    assert call["ok"] is False
    assert "money going out" in call["summary"]
    assert body["proposal_id"] is None


# ------------------------------------------------------------------ staleness --
def test_assumptions_are_shown_again_when_the_figures_have_moved(db: FakeDb) -> None:
    use(
        Scripted(
            use_tools(
                tool(
                    "scenario",
                    expected_payment_minor=250_000,
                    expected_payment_date="2026-09-05",
                    delay_days=3,
                )
            ),
            answer("Computed."),
        )
    )
    with TestClient(app) as client:
        visitor(client)
        ask(client, "What if the $2,500.00 due 2026-09-05 arrives three days later?")
        fresh = client.get(f"/api/workspaces/{WS_VISITOR}/ai/ask/conversation").json()
        assert fresh["assumptions_stale"] is False
        assert fresh["agreed_assumptions"]["delay_days"] == 3

        # The data moves underneath the agreement.
        db.conversations[0]["assumptions_fingerprint"] = "0" * 64
        stale = client.get(f"/api/workspaces/{WS_VISITOR}/ai/ask/conversation").json()
    assert stale["assumptions_stale"] is True
    assert stale["turns_left"] < 12


# --------------------------------------------------------- suggested questions --
def test_suggested_questions_come_from_the_data_and_cost_nothing(db: FakeDb) -> None:
    provider = Scripted(answer("unused"))
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = client.get(f"/api/workspaces/{WS_VISITOR}/ai/ask/suggestions").json()

    assert body["items"]
    assert any("changed this month" in i["text"] for i in body["items"])
    # No model was asked and nothing was spent to produce them.
    assert provider.requests == [] and db.reservations == []


def test_a_recurring_payment_is_offered_and_not_computed_until_the_owner_picks_it(
    db: FakeDb,
) -> None:
    """The substitution the design forbids, refused by the server rather than by the prompt.

    Their history shows a settlement has arrived before. It does not show that one is owed, or
    that it is the one "those receipts" meant. Computing it would answer a different question
    with a chart that looks like an answer to the asked one.
    """

    def take_the_pattern(payload: dict[str, Any]) -> str:
        rec = a_fact(payload, "rec:")
        return use_tools(
            tool(
                "scenario",
                expected_payment_minor=abs(rec["values"]["typical_amount_minor"]),
                expected_payment_date=rec["values"].get("next_expected"),
                delay_days=3,
            )
        )

    provider = Scripted(
        use_tools(tool("facts")), take_the_pattern, answer("Which payment did you mean?")
    )
    use(provider)
    with TestClient(app) as client:
        visitor(client)
        body = ask(client, "What if those receipts arrive three days later?").json()

    call = [t for t in body["tool_calls"] if t["tool"] == "scenario"][0]
    assert call["ok"] is False
    assert "has not said they mean this payment" in call["summary"]
    assert "Offer it back as a choice" in call["summary"]
    # Nothing was computed and nothing was agreed.
    assert body["scenario_applied"] is None and body["scenario_paths"] is None
    assert db.conversations[0]["agreed_assumptions"] is None


def test_the_same_recurring_figures_compute_once_they_have_been_agreed(db: FakeDb) -> None:
    """The other half of the rule: confirmation is what turns a pattern into an assumption."""

    def offer(payload: dict[str, Any]) -> str:
        rec = a_fact(payload, "rec:")
        return answer(
            "I cannot move every receipt at once.",
            clarify={
                "question": "Did you mean the repeating one?",
                "options": [
                    {
                        "label": "Yes, that one",
                        "scenario": {
                            "receipts_change_percent": None,
                            "expected_payment_minor": abs(rec["values"]["typical_amount_minor"]),
                            "expected_payment_date": rec["values"].get("next_expected"),
                            "delay_days": 3,
                            "one_time_expense_minor": None,
                            "one_time_expense_date": None,
                            "one_time_expense_label": None,
                        },
                    }
                ],
            },
        )

    use(Scripted(use_tools(tool("facts")), offer))
    with TestClient(app) as client:
        visitor(client)
        first = ask(client, "What if those receipts arrive three days later?").json()
        option = first["clarify"]["options"][0]

        use(
            Scripted(
                use_tools(tool("scenario", **{k: v for k, v in option["scenario"].items() if v})),
                answer("Computed."),
            )
        )
        second = ask(
            client,
            option["label"],
            conversation_id=first["conversation_id"],
            option_id=option["id"],
        ).json()

    computed = [t for t in second["tool_calls"] if t["tool"] == "scenario"][0]
    assert computed["ok"] is True
    assert second["scenario_applied"]["delay_days"] == 3
