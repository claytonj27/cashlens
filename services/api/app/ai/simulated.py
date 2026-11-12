"""A simulated adapter, for showing the interface without spending anything.

There is a gap between "the code is written" and "someone has seen it work". Until a model has
been paid for, the only way to close it is to answer from the fact sheet itself — which is what
this does. It reads the facts it was given and writes claims that quote them exactly, so the
answer passes the same four validity layers as any other and nothing is special-cased for it.

What keeps it honest:

* `live` is false, so every run it produces is recorded and labelled `replay` — the interface
  shows the standing "not a live model call" label, and no evaluation can count it;
* it reports no token usage at all, rather than inventing plausible numbers, and its cost is
  zero from a `simulated` source rather than borrowed from the price table;
* it is selected only by setting `AI_PROVIDER=simulated`, is refused outright in production,
  and the factory never falls back to it.

`AI_SIMULATED_OUTCOME` chooses what it does, so the failure paths can be seen in a browser
without waiting for a real provider to fail: a valid answer, one citing a fact it was never
given, a transport failure, or a timeout.
"""

import json
from typing import Any

from app.ai.provider import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderTimeoutError,
)

OUTCOMES = ("valid", "invalid_evidence", "provider_error", "timeout")

UP_WORD, DOWN_WORD = "rose", "fell"


def _facts(request: ModelRequest) -> dict[str, Any]:
    """The fact sheet back out of the message it was written into."""
    body = request.user.split("\n\n", 1)[-1]
    return json.JSONDecoder().raw_decode(body)[0]


def _first(facts: dict[str, Any], prefix: str) -> dict[str, Any] | None:
    return next((f for f in facts.get("facts", []) if f["id"].startswith(prefix)), None)


def _brief(facts: dict[str, Any], *, invalid_evidence: bool) -> str:
    balance = _first(facts, "bal:")
    loan = _first(facts, "fin:")
    change = next(
        (
            f
            for f in facts.get("facts", [])
            if f["id"].startswith("chg:") and f.get("direction_word") in ("up", "down")
        ),
        None,
    )
    claims: list[dict[str, Any]] = []
    if change is not None:
        word = UP_WORD if change["direction_word"] == "up" else DOWN_WORD
        claims.append(
            {
                "signal_id": "chg:not-a-real-fact" if invalid_evidence else change["id"],
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
        # The direction has to come from the fact, not from the word "loan": a financing row
        # is as often a repayment going out as a drawdown coming in.
        borrowed = loan["values"].get("direction") == "inflow"
        movement = "was drawn down on" if borrowed else "was repaid on"
        claims.append(
            {
                "signal_id": loan["id"],
                "text": (
                    f"Borrowing of {loan['rendered']['amount']} {movement} "
                    f"{loan['values']['posted_date']}; it is financing, not trade."
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
    if not claims:
        months = _first(facts, "month:")
        if months is not None:
            claims.append(
                {
                    "signal_id": months["id"],
                    "text": f"Money in was {months['rendered']['money_in']} that month.",
                    "figures": [
                        {
                            "ref": f"{months['id']}.money_in_minor",
                            "amount_minor": months["values"]["money_in_minor"],
                        }
                    ],
                    "evidence": [],
                }
            )
    headline = (
        f"Your latest balance is {balance['rendered']['amount']}."
        if balance
        else "Your cash position."
    )
    questions = []
    unplaced = _first(facts, "cls")
    if unplaced is not None:
        questions.append(
            {
                "signal_id": "cls",
                "text": "Some entries name no one. Can you say where that money came from?",
            }
        )
    return json.dumps(
        {
            "headline": headline,
            "claims": claims,
            "questions": questions,
            "caveats": ["This explanation was simulated, not written by a model."],
        }
    )


class Simulated:
    name = "simulated"
    live = False

    def __init__(self, outcome: str = "valid") -> None:
        if outcome not in OUTCOMES:
            raise ValueError(f"AI_SIMULATED_OUTCOME must be one of {', '.join(OUTCOMES)}")
        self._outcome = outcome

    def complete(self, request: ModelRequest) -> ModelResponse:
        if self._outcome == "timeout":
            raise ProviderTimeoutError()
        if self._outcome == "provider_error":
            raise ProviderError("simulated transport failure", code="simulated_failure")
        return ModelResponse(
            text=_brief(_facts(request), invalid_evidence=self._outcome == "invalid_evidence"),
            model_used="simulated",
            provider_request_id=None,
            # No usage is reported, because none was incurred. Inventing token counts here
            # would put numbers in the reviewer view that no provider ever returned.
            prompt_tokens=None,
            completion_tokens=None,
            cost_micro=0,
            cost_source="simulated",
            latency_ms=0,
            finish_reason="stop",
        )
