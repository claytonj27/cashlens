"""The four validity layers, each failure shown on its own.

These are the checks that decide whether an owner sees a sentence, so they are tested against
hand-built facts rather than through a route: every case here is one rule, failing for one
reason, with the consequence the design gives it.
"""

import json
import pathlib

import app.ai as app_ai
from app.ai.signals import FactSheet, Signal
from app.ai.validate import validate

TX = "ffffffff-ffff-4fff-8fff-000000000001"


def sheet(*extra: Signal) -> FactSheet:
    """A small but realistic fact sheet: a statement balance, a change, and a loan."""
    signals = [
        Signal(
            id="bal:statement_total",
            kind="balance",
            label="Statement closing balance, 2026-08-28",
            values={"amount_minor": 615_000, "mixed_dates": False},
            rendered={"amount": "$6,150.00"},
            is_live=False,
            money_fields=("amount_minor",),
        ),
        Signal(
            id="chg:suppliers",
            kind="change",
            label="Suppliers and stock, 2026-08 against 2026-07",
            values={
                "current_minor": -90_000,
                "previous_minor": -66_000,
                "change_minor": -24_000,
                "pct": -36.4,
            },
            rendered={"change": "-$240.00", "current": "-$900.00", "previous": "-$660.00"},
            evidence=[TX],
            evidence_total=4,
            question_allowed=True,
            direction_word="up",
            money_fields=("current_minor", "previous_minor", "change_minor"),
        ),
        Signal(
            id="fin:1",
            kind="financing",
            label="SBA LOAN PROCEEDS",
            values={"amount_minor": 500_000, "posted_date": "2026-08-22", "direction": "inflow"},
            rendered={"amount": "$5,000.00"},
            evidence=[TX],
            evidence_total=1,
            question_allowed=True,
            money_fields=("amount_minor",),
        ),
        *extra,
    ]
    return FactSheet(
        business_name="Juniper Kitchen",
        industry="restaurant",
        as_of="2026-08-28",
        currency="USD",
        signals=signals,
        input_summary={},
    )


def answer(**changes) -> str:
    body = {
        "headline": "Your latest statement balance is $6,150.00.",
        "claims": [
            {
                "signal_id": "chg:suppliers",
                "text": "Spending on suppliers and stock rose by $240.00 last month.",
                "figures": [{"ref": "chg:suppliers.change_minor", "amount_minor": -24_000}],
                "evidence": [TX],
            }
        ],
        "questions": [],
        "caveats": [],
    }
    body.update(changes)
    return json.dumps(body)


def one_claim(**changes) -> str:
    """The same answer with a single claim whose fields the test replaces."""
    claim = {
        "signal_id": "chg:suppliers",
        "text": "Spending on suppliers and stock rose by $240.00 last month.",
        "figures": [],
        "evidence": [],
    }
    claim.update(changes)
    return answer(claims=[claim])


# ------------------------------------------------------------------- layer one --
def test_an_answer_that_is_not_json_is_rejected_before_anything_else() -> None:
    result, brief = validate("this is not json", sheet())
    assert result.rejected and brief is None
    assert not result.schema.ok and result.evidence.ok


def test_an_answer_of_the_wrong_shape_is_rejected_with_its_reasons() -> None:
    result, brief = validate(json.dumps({"headline": "hi"}), sheet())
    assert result.rejected and brief is None
    assert not result.schema.ok and result.schema.messages


def test_a_field_we_never_asked_for_is_rejected() -> None:
    result, _ = validate(answer(confidence=0.9), sheet())
    assert result.rejected and not result.schema.ok


# ------------------------------------------------------------------ layer two --
def test_a_claim_citing_a_fact_that_was_never_given_rejects_the_whole_answer() -> None:
    result, brief = validate(one_claim(signal_id="chg:invented"), sheet())
    assert result.rejected and brief is None
    assert not result.evidence.ok
    # The whole output goes, not just the claim: an answer that invents one reference has not
    # earned trust on the rest.
    assert result.claims_withheld == 0


def test_a_claim_attaching_a_transaction_it_was_not_given_is_rejected() -> None:
    result, _ = validate(one_claim(evidence=["ffffffff-ffff-4fff-8fff-000000000099"]), sheet())
    assert result.rejected and not result.evidence.ok


def test_a_figure_pointing_at_a_field_the_fact_does_not_carry_is_rejected() -> None:
    result, _ = validate(
        one_claim(figures=[{"ref": "chg:suppliers.invented", "amount_minor": 1}]), sheet()
    )
    assert result.rejected and not result.evidence.ok


# ---------------------------------------------------------------- layer three --
def test_a_figure_that_does_not_equal_the_fact_withholds_that_claim() -> None:
    result, brief = validate(
        one_claim(figures=[{"ref": "chg:suppliers.change_minor", "amount_minor": -25_000}]),
        sheet(),
    )
    assert not result.rejected
    assert result.claims_withheld == 1 and not result.numbers.ok
    assert brief is not None and brief.claims == []


def test_an_amount_in_the_sentence_that_is_not_ours_withholds_the_claim() -> None:
    result, brief = validate(
        one_claim(text="Spending on suppliers and stock rose by $9,999.00 last month."), sheet()
    )
    assert result.claims_withheld == 1 and not result.numbers.ok
    assert brief is not None and brief.claims == [] and brief.claims_withheld == 1


def test_the_same_amount_written_without_commas_is_still_ours() -> None:
    result, brief = validate(
        one_claim(text="Spending on suppliers and stock rose by $240 last month."), sheet()
    )
    assert result.claims_withheld == 0
    assert brief is not None and len(brief.claims) == 1


def test_a_percentage_we_did_not_compute_withholds_the_claim() -> None:
    result, _ = validate(
        one_claim(text="Spending on suppliers and stock rose by 80% last month."), sheet()
    )
    assert result.claims_withheld == 1 and not result.numbers.ok


def test_a_headline_quoting_an_amount_we_do_not_have_is_replaced_by_our_own() -> None:
    result, brief = validate(answer(headline="Cash is $12,345.00 and climbing."), sheet())
    assert result.headline_replaced and brief is not None
    assert "$6,150.00" in brief.headline
    # The claims are untouched: only the headline was at fault.
    assert len(brief.claims) == 1


# ----------------------------------------------------------------- layer four --
def test_a_change_described_in_the_wrong_direction_is_withheld() -> None:
    result, _ = validate(
        one_claim(text="Spending on suppliers and stock fell by $240.00 last month."), sheet()
    )
    assert result.claims_withheld == 1 and not result.support.ok


def test_a_reason_the_bank_data_cannot_show_is_withheld() -> None:
    result, _ = validate(
        one_claim(text="Suppliers rose by $240.00 because your supplier raised prices."), sheet()
    )
    assert result.claims_withheld == 1 and not result.support.ok


def test_a_loan_described_as_trading_income_is_withheld() -> None:
    result, _ = validate(
        one_claim(
            signal_id="fin:1",
            text="Sales of $5,000.00 landed on 2026-08-22 and lifted the month.",
            evidence=[TX],
        ),
        sheet(),
    )
    assert result.claims_withheld == 1 and not result.support.ok


def test_a_financing_row_must_be_described_as_borrowing() -> None:
    result, _ = validate(
        one_claim(signal_id="fin:1", text="$5,000.00 arrived on 2026-08-22."), sheet()
    )
    assert result.claims_withheld == 1 and not result.support.ok


def test_a_loan_described_as_a_loan_is_kept() -> None:
    result, brief = validate(
        one_claim(
            signal_id="fin:1",
            text="A loan of $5,000.00 was paid in on 2026-08-22.",
            figures=[{"ref": "fin:1.amount_minor", "amount_minor": 500_000}],
            evidence=[TX],
        ),
        sheet(),
    )
    assert result.claims_withheld == 0
    assert brief is not None and len(brief.claims) == 1


def test_a_statement_balance_called_money_in_the_bank_today_is_withheld() -> None:
    result, _ = validate(
        one_claim(
            signal_id="bal:statement_total",
            text="You have $6,150.00 in the bank now.",
        ),
        sheet(),
    )
    assert result.claims_withheld == 1 and not result.support.ok


def test_an_unfinished_month_compared_with_a_full_one_is_withheld() -> None:
    partial = Signal(
        id="month:2026-08",
        kind="month",
        label="Cash movement in 2026-08",
        values={"money_in_minor": 100_000, "money_out_minor": 50_000, "net_minor": 50_000},
        rendered={"net": "$500.00"},
        complete=False,
        money_fields=("money_in_minor", "money_out_minor", "net_minor"),
    )
    result, _ = validate(
        one_claim(
            signal_id="month:2026-08",
            text="August is down $500.00 compared with July.",
        ),
        sheet(partial),
    )
    assert result.claims_withheld == 1 and not result.support.ok


def test_an_estimate_stated_as_certainty_is_withheld() -> None:
    forecast = Signal(
        id="fc:baseline",
        kind="forecast",
        label="Projected cash over the next 30 days",
        values={"ending_minor": 700_000, "lowest_minor": 200_000},
        rendered={"ending": "$7,000.00"},
        estimated=True,
        money_fields=("ending_minor", "lowest_minor"),
    )
    result, _ = validate(
        one_claim(signal_id="fc:baseline", text="You will have $7,000.00 in 30 days."),
        sheet(forecast),
    )
    assert result.claims_withheld == 1 and not result.support.ok

    kept, brief = validate(
        one_claim(
            signal_id="fc:baseline",
            text="Cash is expected to be around $7,000.00 in 30 days.",
        ),
        sheet(forecast),
    )
    assert kept.claims_withheld == 0 and brief is not None and len(brief.claims) == 1


# -------------------------------------------------------------------- reporting --
def test_the_layers_are_reported_separately_so_shape_is_never_read_as_truth() -> None:
    result, _ = validate(
        one_claim(text="Spending on suppliers and stock rose by $9,999.00 last month."), sheet()
    )
    record = result.record()
    assert record["schema"]["ok"] is True
    assert record["evidence"]["ok"] is True
    assert record["numbers"]["ok"] is False
    assert record["support"]["ok"] is True
    assert record["claims_withheld"] == 1


def test_a_fact_the_brief_had_to_address_is_recorded_as_a_gap_not_a_failure() -> None:
    result, brief = validate(answer(), sheet())
    # The loan was never mentioned. The brief still shows; the omission is on the record.
    assert brief is not None and "fin:1" in result.coverage_gaps


def test_evidence_truncation_travels_with_the_claim() -> None:
    _, brief = validate(one_claim(evidence=[TX]), sheet())
    assert brief is not None
    claim = brief.claims[0]
    assert claim.evidence == [TX]
    assert claim.evidence_total == 4 and claim.evidence_truncated is True


def test_money_going_out_described_as_coming_in_is_withheld() -> None:
    """The vocabulary check is not enough on its own: a repayment is financing too."""
    repayment = Signal(
        id="fin:2",
        kind="financing",
        label="EQUIPMENT FINANCE PAYMENT",
        values={"amount_minor": -250_000, "posted_date": "2026-08-25", "direction": "outflow"},
        rendered={"amount": "-$2,500.00"},
        evidence=[TX],
        evidence_total=1,
        question_allowed=True,
        money_fields=("amount_minor",),
    )
    result, _ = validate(
        one_claim(
            signal_id="fin:2",
            text="A loan of $2,500.00 was paid in on 2026-08-25.",
            evidence=[TX],
        ),
        sheet(repayment),
    )
    assert result.claims_withheld == 1 and not result.support.ok

    kept, brief = validate(
        one_claim(
            signal_id="fin:2",
            text="Borrowing of $2,500.00 was repaid on 2026-08-25.",
            evidence=[TX],
        ),
        sheet(repayment),
    )
    assert kept.claims_withheld == 0 and brief is not None and len(brief.claims) == 1


def test_the_price_table_ships_with_the_code_that_reads_it() -> None:
    """The first live call refused itself because this file had not been deployed.

    Only what lives under services/api reaches the running API, so a price table anywhere
    above that root is absent at run time — and an absent table means every live model is
    unpriced, which fails closed but also means the feature can never work in a deployment.
    """
    from app.ai import prices

    assert prices.PRICES_PATH.exists(), "the price table must sit inside the deployed root"
    assert prices.PRICES_PATH.is_relative_to(pathlib.Path(app_ai.__file__).parent)
    assert prices.recorded_on(), "prices must carry the date they were read"
    for model in ("gpt-5.2-2025-12-11", "gpt-5.2"):
        assert prices.has_price(model), f"{model} must be priced before it can be dispatched"


def test_a_figure_may_name_the_field_by_either_path() -> None:
    """The first live call wrote every reference the long way, through `values`.

    That is the literal path into the object the model is handed, so it is accepted. It is not
    a looser check: it resolves to the same field and the same exact-equality comparison.
    """
    for ref in ("chg:suppliers.change_minor", "chg:suppliers.values.change_minor"):
        result, brief = validate(
            one_claim(figures=[{"ref": ref, "amount_minor": -24_000}], evidence=[TX]), sheet()
        )
        assert not result.rejected, ref
        assert result.claims_withheld == 0, ref
        assert brief is not None and brief.claims[0].figures[0].rendered == "-$240.00"


def test_a_wrong_figure_is_still_caught_through_the_longer_path() -> None:
    result, _ = validate(
        one_claim(
            figures=[{"ref": "chg:suppliers.values.change_minor", "amount_minor": -25_000}],
            evidence=[TX],
        ),
        sheet(),
    )
    assert result.claims_withheld == 1 and not result.numbers.ok


def test_an_unknown_field_says_which_fields_exist() -> None:
    result, _ = validate(
        one_claim(figures=[{"ref": "chg:suppliers.values.invented", "amount_minor": 1}]), sheet()
    )
    assert result.rejected and not result.evidence.ok
    assert any("carries:" in m for m in result.evidence.messages), "the repair needs the list"


def test_a_question_on_a_fact_that_takes_none_is_dropped_not_fatal() -> None:
    """The fact is real and was given: only its flag says a question would not be answerable.

    That is not the same as inventing a reference, which is what rejecting a whole answer is
    reserved for. The first real brief was thrown away twice over exactly this.
    """
    result, brief = validate(
        answer(
            questions=[
                {"signal_id": "chg:suppliers", "text": "What drove this?"},
                {"signal_id": "bal:statement_total", "text": "Is this right?"},
            ]
        ),
        sheet(),
    )
    assert not result.rejected, "a real fact never rejects the whole answer"
    assert brief is not None
    assert [q.signal_id for q in brief.questions] == ["chg:suppliers"]
    assert result.questions_dropped == 1
    assert len(brief.claims) == 1, "the claims are untouched"


def test_a_question_citing_a_fact_never_given_still_rejects() -> None:
    result, _ = validate(
        answer(questions=[{"signal_id": "chg:invented", "text": "What is this?"}]), sheet()
    )
    assert result.rejected and not result.evidence.ok


def test_a_sentence_full_of_machinery_is_withheld() -> None:
    """The first real brief passed every other check and was still unfit to show.

    It wrote the fact id, the raw minor units and the rendered amount in one sentence. Every
    figure was right; none of it was readable. The references belong in the figures array.
    """
    # Only allowed amounts here, so the numbers layer passes and the readability check is
    # unambiguously what withholds the claim.
    leaked = validate(
        one_claim(
            text="Spending on chg:suppliers rose by $240.00 last month.",
            figures=[{"ref": "chg:suppliers.change_minor", "amount_minor": -24_000}],
            evidence=[TX],
        ),
        sheet(),
    )[0]
    assert leaked.numbers.ok, "the amounts themselves were fine"
    assert leaked.claims_withheld == 1 and not leaked.support.ok

    raw = validate(
        one_claim(
            text="Suppliers rose by 24000 last month, or $240.00.",
            figures=[{"ref": "chg:suppliers.change_minor", "amount_minor": -24_000}],
            evidence=[TX],
        ),
        sheet(),
    )[0]
    assert raw.claims_withheld == 1, "the raw minor-unit value must not reach the prose"

    clean, brief = validate(
        one_claim(
            text="Spending on suppliers and stock rose by $240.00 last month.",
            figures=[{"ref": "chg:suppliers.change_minor", "amount_minor": -24_000}],
            evidence=[TX],
        ),
        sheet(),
    )
    assert clean.claims_withheld == 0 and brief is not None and len(brief.claims) == 1


def test_the_prompt_forbids_machinery_in_the_prose() -> None:
    from app.ai import prompts

    lowered = prompts.BRIEF_SYSTEM.lower()
    assert "fact id" in lowered and "figures" in lowered
    assert "plain english" in lowered


def test_a_cause_worded_around_the_phrase_list_is_still_caught() -> None:
    """The first real brief wrote "is a big reason August ended positive" and it passed."""
    for wording in (
        "Suppliers rose by $240.00, a big reason the month ended positive.",
        "Suppliers rose by $240.00, which drove the month's result.",
        "Suppliers rose by $240.00, and that led to a tighter month.",
        "Suppliers rose by $240.00, which explains the shortfall.",
    ):
        result, _ = validate(one_claim(text=wording), sheet())
        assert result.claims_withheld == 1, wording
        assert not result.support.ok, wording


def test_a_change_never_carries_both_a_direction_word_and_a_minus() -> None:
    """ "Spending rose by -$240.00" reads as the opposite of what the figures say."""
    contradictory, _ = validate(
        one_claim(text="Spending on suppliers rose by -$240.00 last month."), sheet()
    )
    assert contradictory.claims_withheld == 1 and not contradictory.support.ok

    plain, brief = validate(
        one_claim(
            text="Spending on suppliers rose by $240.00 last month.",
            figures=[{"ref": "chg:suppliers.change_minor", "amount_minor": -24_000}],
            evidence=[TX],
        ),
        sheet(),
    )
    assert plain.claims_withheld == 0 and brief is not None and len(brief.claims) == 1


def test_the_prompt_asks_for_evidence_and_for_a_reading_not_a_restatement() -> None:
    from app.ai import prompts

    lowered = prompts.BRIEF_SYSTEM.lower()
    assert "evidence list" in lowered and "transaction ids" in lowered
    assert "dashboard" in lowered


def test_questions_and_caveats_are_held_to_the_same_readability_bar() -> None:
    """The judges found all three caveats written in schema terms while claims were checked.

    A caveat's whole job is managing trust, so machinery there costs the most.
    """
    result, brief = validate(
        answer(
            questions=[
                {"signal_id": "chg:suppliers", "text": "Why did chg:suppliers move?"},
                {"signal_id": "chg:suppliers", "text": "Were these orders planned?"},
            ],
            caveats=[
                "bal:statement_total.values.mixed_dates is true, so treat it as approximate.",
                "Your accounts were last read on different days.",
            ],
        ),
        sheet(),
    )
    assert not result.rejected
    assert brief is not None
    assert [q.text for q in brief.questions] == ["Were these orders planned?"]
    assert brief.caveats == ["Your accounts were last read on different days."]
    assert result.questions_dropped == 1 and result.caveats_dropped == 1


def test_a_rejection_names_the_ids_that_do_exist() -> None:
    """A repair can only fix what it is told.

    The first live scenario explanations invented claim labels of their own, and the repair
    attempt failed too because the message said what was wrong without saying what right
    looked like.
    """
    result, _ = validate(one_claim(signal_id="c1"), sheet())
    assert result.rejected and not result.evidence.ok
    message = " ".join(result.evidence.messages)
    assert "chg:suppliers" in message and "fin:1" in message

    questioned, _ = validate(
        answer(questions=[{"signal_id": "q7", "text": "What is this?"}]), sheet()
    )
    assert questioned.rejected
    assert "bal:statement_total" in " ".join(questioned.evidence.messages)


def test_the_scenario_prompt_names_the_field_and_the_limits() -> None:
    from app.ai import prompts
    from app.ai.validate import Claim, Question

    assert "signal_id" in prompts.SCENARIO_SYSTEM
    assert "VERBATIM" in prompts.SCENARIO_SYSTEM
    for value in (
        Claim.model_fields["text"].metadata[-1].max_length,
        Question.model_fields["text"].metadata[-1].max_length,
    ):
        assert str(value) in prompts.SCENARIO_SYSTEM


# ----------------------------------------- quoting a number without leaking machinery --
# The raw-minor-unit check asks whether a sentence is showing internal storage rather than a
# dollar amount. It compared by substring, which is true far more often than the thing it was
# looking for: any figure whose digits appear anywhere in the sentence fired it.
def test_a_figure_of_exactly_zero_no_longer_rejects_the_sentence_that_reports_it() -> None:
    """The case that cost the delayed-payment explanation four of its five claims.

    A change of nothing has an amount_minor of 0, and "0" is inside "$0.00", "30 days" and
    "2026". Every sentence saying the ending balance did not move was thrown out as though it
    had quoted raw storage.
    """
    zero = Signal(
        id="fc:ending",
        kind="forecast",
        label="Cash at the end of 30 days",
        values={"difference_minor": 0, "baseline_minor": 615_000},
        rendered={"difference": "$0.00"},
        money_fields=("difference_minor", "baseline_minor"),
    )
    result, brief = validate(
        one_claim(
            signal_id="fc:ending",
            text="Cash at the end of the 30 days is unchanged at $6,150.00.",
            figures=[{"ref": "fc:ending.difference_minor", "amount_minor": 0}],
        ),
        sheet(zero),
    )
    assert brief is not None and len(brief.claims) == 1
    assert result.claims_withheld == 0


def test_a_small_figure_inside_a_larger_written_amount_is_not_a_leak() -> None:
    """$5.00 is 500, and "500" sits inside "$5,500.00". The sentence is perfectly readable."""
    small = Signal(
        id="fc:fee",
        kind="forecast",
        label="A fee",
        values={"amount_minor": 500, "total_minor": 550_000},
        rendered={"amount": "$5.00"},
        money_fields=("amount_minor", "total_minor"),
    )
    result, brief = validate(
        one_claim(
            signal_id="fc:fee",
            text="A $5.00 fee is included in the $5,500.00 total.",
            figures=[{"ref": "fc:fee.amount_minor", "amount_minor": 500}],
        ),
        sheet(small),
    )
    assert brief is not None and len(brief.claims) == 1
    assert result.claims_withheld == 0


def test_a_genuine_raw_minor_unit_leak_is_still_withheld() -> None:
    """The real thing this check exists for: storage printed beside the readable amount."""
    result, brief = validate(
        one_claim(
            text="Spending on suppliers and stock rose by 24000 ($240.00) last month.",
            figures=[{"ref": "chg:suppliers.change_minor", "amount_minor": -24_000}],
        ),
        sheet(),
    )
    assert result.claims_withheld == 1
    assert "raw internal number" in result.withheld_reasons[0]
    assert brief is not None and brief.claims == []


# ------------------------------------------------------------------- percentages --
def test_a_percentage_may_be_written_without_its_minus_sign() -> None:
    """The fact holds -36.4; prose says "fell by 36.4%". Direction is the direction check's job.

    The percentage pattern never captures a leading minus, so a signed fact could not be quoted
    in any natural sentence at all.
    """
    result, brief = validate(
        one_claim(
            text="Spending on suppliers and stock rose by 36.4% last month.",
            figures=[],
        ),
        sheet(),
    )
    assert result.claims_withheld == 0
    assert brief is not None and len(brief.claims) == 1


def test_a_percentage_that_is_not_one_of_the_figures_is_still_withheld() -> None:
    result, _ = validate(
        one_claim(text="Spending on suppliers and stock rose by 71.2% last month.", figures=[]),
        sheet(),
    )
    assert result.claims_withheld == 1
    assert "percentage" in result.withheld_reasons[0]


def test_a_percentage_is_quotable_only_where_the_fact_declares_one() -> None:
    """A percentage in `values` under any name is quotable once the signal names the field."""
    declared = Signal(
        id="fc:ending",
        kind="forecast",
        label="Cash at the end of 30 days",
        values={"difference_minor": -50_000, "difference_percent": -8.1},
        rendered={"difference": "-$500.00"},
        money_fields=("difference_minor",),
        percent_fields=("difference_percent",),
    )
    undeclared = Signal(
        id="fc:other",
        kind="forecast",
        label="Something else",
        values={"difference_minor": -50_000, "difference_percent": -8.1},
        rendered={"difference": "-$500.00"},
        money_fields=("difference_minor",),
    )
    ok, _ = validate(
        one_claim(signal_id="fc:ending", text="Ending cash is about 8.1% lower.", figures=[]),
        sheet(declared),
    )
    assert ok.claims_withheld == 0

    refused, _ = validate(
        one_claim(signal_id="fc:other", text="Ending cash is about 8.1% lower.", figures=[]),
        sheet(undeclared),
    )
    assert refused.claims_withheld == 1


# ------------------------------------------------------------ stating a cause --
def test_a_cause_may_be_given_only_where_the_owner_typed_it() -> None:
    """Bank history never shows why. The owner's own assumption is the one exception."""
    assumption = Signal(
        id="sc:inputs",
        kind="scenario_inputs",
        label="What you changed",
        values={"changes": ["a payment arriving 21 days late"]},
        rendered={},
        causal_allowed=True,
    )
    projection = Signal(
        id="fc:lowest",
        kind="forecast",
        label="The lowest point on each path",
        values={"difference_minor": -50_000},
        rendered={"difference": "-$500.00"},
        estimated=True,
        money_fields=("difference_minor",),
    )
    allowed, _ = validate(
        one_claim(
            signal_id="sc:inputs",
            text="The late payment is the reason the two paths separate.",
            figures=[],
        ),
        sheet(assumption),
    )
    assert allowed.claims_withheld == 0

    refused, _ = validate(
        one_claim(
            signal_id="fc:lowest",
            text="Slow trade is the reason the projected low point is expected to fall.",
            figures=[],
        ),
        sheet(projection),
    )
    assert refused.claims_withheld == 1
    assert "reason your bank data cannot show" in refused.withheld_reasons[0]


# ------------------------------------------ citing a figure that belongs to another fact --
def test_a_claim_may_cite_a_figure_from_a_different_fact_when_it_names_it_in_full() -> None:
    """Relating two facts is an ordinary sentence, and both numbers are still exact.

    Two live answers were rejected outright for this: a claim about one fact quoting a real,
    correct figure belonging to another. The prose check already accepts any amount from any
    fact, so the sentence was allowed to contain the number while the figure array was not
    allowed to cite it.
    """
    result, brief = validate(
        one_claim(
            signal_id="chg:suppliers",
            text="Spending on suppliers and stock rose by $240.00, against $6,150.00 of cash.",
            figures=[
                {"ref": "chg:suppliers.change_minor", "amount_minor": -24_000},
                {"ref": "bal:statement_total.amount_minor", "amount_minor": 615_000},
            ],
        ),
        sheet(),
    )
    assert result.evidence.ok and result.claims_withheld == 0
    assert brief is not None and len(brief.claims) == 1
    # The interface renders the value that was actually checked, from the fact that holds it.
    assert [f.rendered for f in brief.claims[0].figures] == ["-$240.00", "$6,150.00"]


def test_a_cross_fact_figure_with_the_wrong_value_is_still_withheld() -> None:
    """Widening which fact may be cited does not widen what the number may be."""
    result, _ = validate(
        one_claim(
            signal_id="chg:suppliers",
            text="Spending rose by $240.00, against $6,150.00 of cash.",
            figures=[{"ref": "bal:statement_total.amount_minor", "amount_minor": 999_999}],
        ),
        sheet(),
    )
    assert result.claims_withheld == 1
    assert "did not match" in result.withheld_reasons[0]


def test_a_reference_to_no_fact_at_all_still_rejects_the_whole_answer() -> None:
    result, brief = validate(
        one_claim(
            signal_id="chg:suppliers",
            text="Spending on suppliers and stock rose by $240.00 last month.",
            figures=[{"ref": "bal:invented.amount_minor", "amount_minor": 615_000}],
        ),
        sheet(),
    )
    assert result.rejected and brief is None
    assert not result.evidence.ok
    assert "not a value of any fact" in result.evidence.messages[0]


def test_a_field_that_does_not_exist_on_a_real_fact_is_still_rejected() -> None:
    result, brief = validate(
        one_claim(
            signal_id="chg:suppliers",
            text="Spending on suppliers and stock rose by $240.00 last month.",
            figures=[{"ref": "bal:statement_total.invented_field", "amount_minor": 615_000}],
        ),
        sheet(),
    )
    assert result.rejected and brief is None
    assert not result.evidence.ok


def test_a_day_count_is_not_a_raw_minor_unit_number() -> None:
    """Writing "18 days late" is right: 18 is days, and days have no minor-unit form."""
    assumption = Signal(
        id="sc:inputs",
        kind="scenario_inputs",
        label="What you changed",
        values={"expected_payment_minor": 250_000, "delay_days": 18},
        rendered={},
        causal_allowed=True,
        money_fields=("expected_payment_minor",),
    )
    result, brief = validate(
        one_claim(
            signal_id="sc:inputs",
            text="You asked what happens if the $2,500.00 payment arrives 18 days late.",
            figures=[{"ref": "sc:inputs.delay_days", "amount_minor": 18}],
        ),
        sheet(assumption),
    )
    assert result.claims_withheld == 0
    assert brief is not None and len(brief.claims) == 1


def test_a_money_figure_quoted_raw_is_still_withheld_on_the_same_fact() -> None:
    """Narrowing the check to money fields does not stop it catching money."""
    assumption = Signal(
        id="sc:inputs",
        kind="scenario_inputs",
        label="What you changed",
        values={"expected_payment_minor": 250_000, "delay_days": 18},
        rendered={},
        money_fields=("expected_payment_minor",),
    )
    result, _ = validate(
        one_claim(
            signal_id="sc:inputs",
            text="You asked what happens if the 250000 ($2,500.00) payment is late.",
            figures=[{"ref": "sc:inputs.expected_payment_minor", "amount_minor": 250_000}],
        ),
        sheet(assumption),
    )
    assert result.claims_withheld == 1
    assert "raw internal number" in result.withheld_reasons[0]


def test_a_transaction_may_be_cited_bare_or_with_its_prefix() -> None:
    """The conversation panel shows a transaction as "tx:<uuid>", so that spelling must work.

    A signal's evidence list holds bare ids. Rejecting the spelling the model was shown would
    be the contract disagreeing with itself, exactly as the two forms of a figure reference
    were before `field_of` accepted both.
    """
    for cited in (TX, f"tx:{TX}"):
        result, brief = validate(
            one_claim(
                text="Spending on suppliers and stock rose by $240.00 last month.",
                evidence=[cited],
            ),
            sheet(),
        )
        assert result.evidence.ok, cited
        assert brief is not None and brief.claims[0].evidence == [TX]


def test_a_transaction_that_is_not_that_facts_evidence_is_still_rejected() -> None:
    other = "ffffffff-ffff-4fff-8fff-000000000099"
    result, brief = validate(
        one_claim(text="Spending rose by $240.00 last month.", evidence=[f"tx:{other}"]),
        sheet(),
    )
    assert result.rejected and brief is None
    assert not result.evidence.ok
