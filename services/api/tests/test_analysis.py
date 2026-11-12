"""Cash metrics, categorisation and the scenario tool: known answers, by hand.

The figures here are checked against arithmetic written out in the test, not against the
application's own output, so a change in the calculation shows up as a failure rather than a
quietly different number.
"""

from app.analysis import categorize as categorize_module
from app.analysis import fingerprint, forecast, metrics
from app.analysis.categorize import categorize

ACC_A = "11111111-1111-4111-8111-111111111111"
ACC_B = "22222222-2222-4222-8222-222222222222"


def txn(n, day, description, amount, account=ACC_A, status="posted"):
    return metrics.Txn(f"t{n}", account, day, description, amount, status)


# Two complete months of a small shop. Written out so the totals below can be read off it.
LEDGER = [
    txn(1, "2026-01-03", "CARD PROCESSOR SETTLEMENT", 200_000),
    txn(2, "2026-01-10", "CARD PROCESSOR SETTLEMENT", 150_000),
    txn(3, "2026-01-05", "PINE VALLEY FOOD DISTRIBUTION", -60_000),
    txn(4, "2026-01-15", "PAYROLL PROVIDER ACH", -120_000),
    txn(5, "2026-01-20", "CEDAR COMMONS LEASE PAYMENT", -80_000),
    txn(6, "2026-01-25", "MYSTERY VENDOR 4471", -5_000),
    txn(7, "2026-02-03", "CARD PROCESSOR SETTLEMENT", 220_000),
    txn(8, "2026-02-10", "CARD PROCESSOR SETTLEMENT", 160_000),
    txn(9, "2026-02-05", "PINE VALLEY FOOD DISTRIBUTION", -70_000),
    txn(10, "2026-02-15", "PAYROLL PROVIDER ACH", -120_000),
    txn(11, "2026-02-20", "CEDAR COMMONS LEASE PAYMENT", -80_000),
    txn(12, "2026-02-27", "SBA LOAN PROCEEDS", 500_000),
    txn(13, "2026-02-28", "CARD PROCESSOR SETTLEMENT", 90_000, status="pending"),
]

BALANCES = [
    {
        "account_id": ACC_A,
        "kind": "statement_closing",
        "provenance": "bank_observed",
        "amount_minor": 615_000,
        "as_of": "2026-02-28T23:59:59+00:00",
    }
]


def test_monthly_totals_are_exact_and_posted_only() -> None:
    months = metrics.monthly(LEDGER, "2026-02-28")
    jan, feb = months[0], months[1]
    assert jan["month"] == "2026-01"
    assert jan["money_in_minor"] == 200_000 + 150_000 == 350_000
    assert jan["money_out_minor"] == 60_000 + 120_000 + 80_000 + 5_000 == 265_000
    assert jan["net_minor"] == 85_000 and jan["posted_count"] == 6
    # The pending 90,000 is excluded from every February total.
    assert feb["money_in_minor"] == 220_000 + 160_000 + 500_000 == 880_000
    assert feb["money_out_minor"] == 70_000 + 120_000 + 80_000 == 270_000
    assert feb["net_minor"] == 610_000 and feb["posted_count"] == 6
    assert all(m["complete"] for m in months)


def test_an_unfinished_month_is_marked_incomplete() -> None:
    months = metrics.monthly(LEDGER, "2026-02-10")
    assert months[0]["complete"] is True  # January had ended
    assert months[-1]["month"] == "2026-02" and months[-1]["complete"] is False


def test_financing_is_separated_from_operating_but_still_moves_cash() -> None:
    rows = {r["key"]: r for r in metrics.by_category(LEDGER, "2026-02-28", "2026-02")}
    assert rows["financing"]["group"] == "financing"
    assert rows["financing"]["inflow_minor"] == 500_000
    assert rows["customer_receipts"]["group"] == "operating"
    # The loan is not operating income, and it is still in February's money in.
    feb = metrics.monthly(LEDGER, "2026-02-28")[1]
    assert feb["money_in_minor"] == 880_000


def test_an_unrecognised_description_stays_uncategorised() -> None:
    assert categorize("MYSTERY VENDOR 4471", -5_000).key == "uncategorized"
    rows = {r["key"]: r for r in metrics.by_category(LEDGER, "2026-01-31", "2026-01")}
    assert rows["uncategorized"]["outflow_minor"] == 5_000
    assert rows["uncategorized"]["count"] == 1


def test_a_matched_internal_transfer_is_netted_out_of_money_in_and_out() -> None:
    pair = [
        *LEDGER,
        txn(20, "2026-02-14", "ONLINE TRANSFER TO SAVINGS 9911", -100_000, account=ACC_A),
        txn(21, "2026-02-15", "ONLINE TRANSFER FROM CHECKING 4021", 100_000, account=ACC_B),
    ]
    feb = metrics.monthly(pair, "2026-02-28")[1]
    # Neither leg inflates the consolidated figures, but both are still counted and shown.
    assert feb["money_in_minor"] == 880_000 and feb["money_out_minor"] == 270_000
    assert feb["transfers_netted_minor"] == 200_000 and feb["posted_count"] == 8


def test_a_lone_transfer_leg_is_not_netted_out() -> None:
    single = [*LEDGER, txn(22, "2026-02-14", "ONLINE TRANSFER TO SAVINGS 9911", -100_000)]
    feb = metrics.monthly(single, "2026-02-28")[1]
    assert feb["money_out_minor"] == 370_000  # no matching leg, so it counts as spending
    assert feb["transfers_netted_minor"] == 0


def test_an_earlier_analysis_date_never_sees_later_data() -> None:
    months = metrics.monthly(LEDGER, "2026-01-31")
    assert [m["month"] for m in months] == ["2026-01"]
    cover = metrics.coverage(LEDGER, "2026-01-31")
    assert cover["last_posted"] == "2026-01-25" and cover["posted_count"] == 6
    assert "2026-02" not in str(metrics.by_category(LEDGER, "2026-01-31"))


def test_comparison_reports_the_change_between_two_months() -> None:
    compared = metrics.compare_months(LEDGER, "2026-02-28", "2026-02", "2026-01")
    changes = {c["key"]: c for c in compared["changes"]}
    assert changes["customer_receipts"]["previous_minor"] == 350_000
    assert changes["customer_receipts"]["current_minor"] == 380_000
    assert changes["customer_receipts"]["change_minor"] == 30_000
    assert changes["suppliers"]["change_minor"] == -10_000  # 60,000 out became 70,000 out


def test_recurring_payments_are_found_with_an_estimated_next_date() -> None:
    monthlyish = [
        txn(i, f"2026-0{m}-15", "CEDAR COMMONS LEASE PAYMENT", -80_000)
        for i, m in ((1, 1), (2, 2), (3, 3))
    ]
    found = {r["label"]: r for r in metrics.recurring(monthlyish, "2026-03-31")}
    lease = found["CEDAR COMMONS LEASE PAYMENT"]
    assert lease["occurrences"] == 3 and lease["typical_amount_minor"] == -80_000
    assert 28 <= lease["cadence_days"] <= 31
    assert lease["next_expected"] > "2026-03-15" and lease["direction"] == "outflow"


def test_insufficient_history_refuses_to_forecast_and_says_why() -> None:
    thin = [txn(1, "2026-01-03", "CARD PROCESSOR SETTLEMENT", 200_000)]
    built = forecast.build(thin, BALANCES, "2026-01-31")
    assert built["available"] is False
    assert any("complete months" in r for r in built["reasons"])
    # No path at all rather than an empty one. The analysis response carries a projection or
    # nothing, and an empty list is neither: it failed the response model, so a workspace too
    # thin to project answered 500 instead of saying why.
    assert built["baseline"] is None


def test_stale_history_is_refused_rather_than_forecast_confidently() -> None:
    built = forecast.build(LEDGER, BALANCES, "2026-09-30")
    assert built["available"] is False
    assert any("days before the analysis date" in r for r in built["reasons"])


def test_a_statement_balance_anchors_the_forecast_and_is_never_called_live() -> None:
    built = forecast.build(LEDGER, BALANCES, "2026-02-28")
    assert built["available"] is True
    assert built["anchor"]["amount_minor"] == 615_000
    assert built["anchor"]["kind"] == "statement_closing"
    assert built["anchor"]["is_live"] is False
    assert built["baseline"]["points"][0]["balance_minor"] == 615_000
    assert all(a["estimated"] for a in built["assumptions"])


def test_a_live_balance_wins_over_a_statement_balance() -> None:
    live = [
        *BALANCES,
        {
            "account_id": ACC_A,
            "kind": "current",
            "provenance": "bank_observed",
            "amount_minor": 700_000,
            "as_of": "2026-02-28T12:00:00+00:00",
        },
    ]
    anchor = forecast.choose_anchor(live, "2026-02-28")
    assert anchor.amount_minor == 700_000 and anchor.is_live is True


def test_available_balances_are_never_added_to_current_ones() -> None:
    both = [
        {
            "account_id": ACC_A,
            "kind": "current",
            "provenance": "bank_observed",
            "amount_minor": 700_000,
            "as_of": "2026-02-28T12:00:00+00:00",
        },
        {
            "account_id": ACC_A,
            "kind": "available",
            "provenance": "bank_observed",
            "amount_minor": 690_000,
            "as_of": "2026-02-28T12:00:00+00:00",
        },
    ]
    anchor = forecast.choose_anchor(both, "2026-02-28")
    assert anchor.amount_minor == 700_000  # not 1,390,000


def test_fewer_receipts_lower_the_path_and_report_the_difference() -> None:
    base = forecast.build(LEDGER, BALANCES, "2026-02-28")
    cut = forecast.build(
        LEDGER, BALANCES, "2026-02-28", forecast.Scenario(receipts_change_percent=-30)
    )
    assert cut["scenario"]["ending_minor"] < base["baseline"]["ending_minor"]
    assert cut["difference_minor"] == (
        cut["scenario"]["ending_minor"] - cut["baseline"]["ending_minor"]
    )
    assert any(a["key"] == "receipts" for a in cut["assumptions"])


def test_delaying_an_expected_payment_moves_the_dip_not_the_amount() -> None:
    """The owner supplies the amount and date; bank history is not evidence of an invoice."""
    on_time = forecast.build(
        LEDGER,
        BALANCES,
        "2026-02-28",
        forecast.Scenario(expected_payment_minor=400_000, expected_payment_date="2026-03-05"),
    )
    delayed = forecast.build(
        LEDGER,
        BALANCES,
        "2026-02-28",
        forecast.Scenario(
            expected_payment_minor=400_000,
            expected_payment_date="2026-03-05",
            delay_days=14,
        ),
    )
    # The money still arrives inside the window, so ending cash is unchanged...
    assert delayed["scenario"]["ending_minor"] == on_time["scenario"]["ending_minor"]

    # ...but through the middle of the window the business is 400,000 worse off, which is the
    # whole point of asking. Read the same day out of both paths.
    def on(path, day):
        return next(p["balance_minor"] for p in path["points"] if p["date"] == day)

    assert on(on_time["scenario"], "2026-03-10") - on(delayed["scenario"], "2026-03-10") == 400_000
    assert on(on_time["scenario"], "2026-03-20") == on(delayed["scenario"], "2026-03-20")
    detail = next(a for a in delayed["assumptions"] if a["key"] == "expected_payment")
    assert "not evidence of an unpaid invoice" in detail["detail"]


def test_a_one_off_cost_lowers_the_path_by_exactly_its_amount() -> None:
    built = forecast.build(
        LEDGER,
        BALANCES,
        "2026-02-28",
        forecast.Scenario(
            one_time_expense_minor=250_000,
            one_time_expense_date="2026-03-10",
            one_time_expense_label="New oven",
        ),
    )
    assert built["difference_minor"] == -250_000
    assert built["scenario"]["ending_minor"] == built["baseline"]["ending_minor"] - 250_000


def test_a_scenario_changes_nothing_it_was_given() -> None:
    before = [(t.id, t.amount_minor, t.posted_date) for t in LEDGER]
    balances_before = [dict(b) for b in BALANCES]
    forecast.build(
        LEDGER,
        BALANCES,
        "2026-02-28",
        forecast.Scenario(
            receipts_change_percent=50, one_time_expense_minor=1, one_time_expense_date="2026-03-02"
        ),
    )
    assert [(t.id, t.amount_minor, t.posted_date) for t in LEDGER] == before
    assert [dict(b) for b in BALANCES] == balances_before


def test_recurring_items_are_not_counted_twice_in_the_daily_rate() -> None:
    """A payment placed on its own date must not also be inside the flat everyday rate."""
    steady = [
        txn(i, day, "CEDAR COMMONS LEASE PAYMENT", -80_000)
        for i, day in enumerate(("2026-01-15", "2026-02-15", "2026-03-15"), start=100)
    ]
    repeats = metrics.recurring(steady, "2026-03-31")
    assert repeats and repeats[0]["typical_amount_minor"] == -80_000
    with_removal = forecast._daily_rates(steady, "2026-03-31", repeats)
    without_removal = forecast._daily_rates(steady, "2026-03-31", [])
    # Taking the lease out of the flat rate leaves less everyday spending behind, not more.
    assert with_removal[1] < without_removal[1]
    assert with_removal[1] == 0  # the lease was the only outflow, so nothing is left over


# ------------------------------------------------------- rules-2: the counterparty tier --
def test_a_description_that_names_nobody_is_never_placed_by_an_instrument_word() -> None:
    """The rules-1 behaviour these replace: each of these was counted as a customer receipt.

    A bare deposit or wire reference says how money moved, not where it came from. Counting it
    as a receipt is a guess about the business, which is exactly what the methodology promises
    the rules never make.
    """
    for description, amount in (
        ("DEPOSIT 8842", 350_000),
        ("ACH CREDIT 0192", 27_500),
        ("DEPOSIT 4471", 680_000),
        ("CHECK DEPOSIT", 120_000),
        ("WIRE IN 000348", 500_000),
        ("CASH", 5_000),
    ):
        placed = categorize_module.place(description, amount)
        assert placed.category.key == "uncategorized", description
        assert placed.reason == categorize_module.REASON_GENERIC_INSTRUMENT, description


def test_an_instrument_word_with_a_counterparty_still_places_normally() -> None:
    placed = categorize_module.place("ACH CREDIT HARBOR POINT HOA", 120_000)
    assert placed.category.key == "customer_receipts"
    assert placed.reason == categorize_module.REASON_KEYWORD


def test_a_named_payee_the_rules_do_not_know_is_unmatched_rather_than_instrument_only() -> None:
    """The distinction matters: one is a rule gap, the other is a description naming nobody."""
    for description, amount in (
        ("ZELLE PAYMENT FROM MARTIN R", 45_000),
        ("VENMO PAYMENT ASSISTANT", -20_000),
    ):
        placed = categorize_module.place(description, amount)
        assert placed.category.key == "uncategorized", description
        assert placed.reason == categorize_module.REASON_NO_MATCH, description


def test_a_leased_machine_is_rent_and_a_serviced_one_is_maintenance() -> None:
    assert categorize_module.place("CAT FINANCIAL EQUIPMENT LEASE", -87_000).category.key == (
        "rent_lease"
    )
    assert categorize_module.place("HOBART SERVICE EQUIPMENT REPAIR", -45_000).category.key == (
        "repairs_maintenance"
    )
    # The plain word still means a purchase.
    assert categorize_module.place("RESTAURANT EQUIPMENT CO", -200_000).category.key == "equipment"


def test_the_categories_rules_2_adds_are_reachable() -> None:
    assert (
        categorize_module.place("ACME CONTRACTOR SERVICES", -30_000).category.key == "contractors"
    )
    assert categorize_module.place("OWNER CONTRIBUTION", 500_000).category.key == (
        "owner_contributions"
    )
    assert categorize_module.place("OWNER CONTRIBUTION", 500_000).category.group == "financing"


# --------------------------------------------------------- corrections applied on read --
def test_an_owner_correction_moves_one_amount_and_nothing_else() -> None:
    target = LEDGER[5].id  # MYSTERY VENDOR 4471, -5,000, uncategorised by the rules
    before = {r["key"]: r for r in metrics.by_category(LEDGER, "2026-01-31", "2026-01")}
    after = {
        r["key"]: r
        for r in metrics.by_category(LEDGER, "2026-01-31", "2026-01", {target: "marketing"})
    }
    assert before["uncategorized"]["outflow_minor"] == 5_000
    assert "uncategorized" not in after
    assert after["marketing"]["outflow_minor"] == 5_000
    # Every other category is untouched by the correction.
    for key in before:
        if key != "uncategorized":
            assert after[key]["outflow_minor"] == before[key]["outflow_minor"]


def test_a_correction_to_internal_transfer_lets_a_pair_net_out() -> None:
    pair = [
        *LEDGER,
        txn(30, "2026-02-14", "MOVE TO RESERVE 9911", -100_000, account=ACC_A),
        txn(31, "2026-02-15", "MOVE FROM MAIN 4021", 100_000, account=ACC_B),
    ]
    assert metrics.matched_transfers(pair) == set(), "the rules do not know these descriptions"
    corrected = {"t30": "internal_transfer", "t31": "internal_transfer"}
    assert metrics.matched_transfers(pair, corrected) == {"t30", "t31"}
    feb = metrics.monthly(pair, "2026-02-28", overrides=corrected)[1]
    assert feb["transfers_netted_minor"] == 200_000


# ---------------------------------------------------------------------- fingerprint --
VERSIONS = {"analysis": "analysis-2", "metrics": "cash-2", "rules": "rules-2"}


def _fingerprint(transactions=LEDGER, overrides=None, as_of="2026-02-28"):
    return fingerprint.compute(
        versions=VERSIONS,
        as_of=as_of,
        transactions=transactions,
        balances=BALANCES,
        overrides=overrides or {},
    )


def test_the_same_inputs_always_give_the_same_fingerprint() -> None:
    assert _fingerprint() == _fingerprint()
    assert len(_fingerprint()) == 64


def test_every_kind_of_change_moves_the_fingerprint() -> None:
    base = _fingerprint()
    added = _fingerprint([*LEDGER, txn(40, "2026-02-25", "NEW SUPPLIER INVOICE", -1_000)])
    revised = _fingerprint(
        [*LEDGER[:-1], metrics.Txn("t13", ACC_A, "2026-02-28", "CHANGED", 90_000, "posted", 2)]
    )
    corrected = _fingerprint(overrides={"t6": "marketing"})
    earlier = _fingerprint(as_of="2026-01-31")
    assert len({base, added, revised, corrected, earlier}) == 5


def test_a_removed_row_and_a_rules_change_both_move_it() -> None:
    base = _fingerprint()
    without = _fingerprint(LEDGER[:-1])
    other_rules = fingerprint.compute(
        versions={**VERSIONS, "rules": "rules-3"},
        as_of="2026-02-28",
        transactions=LEDGER,
        balances=BALANCES,
        overrides={},
    )
    assert base != without and base != other_rules
