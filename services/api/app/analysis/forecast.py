"""A deterministic 30-day cash path, and scenarios laid over it. Nothing here moves money.

The baseline is built from what the bank already shows, and every future figure is an
estimate that the interface labels as such:

* the starting point is a dated balance anchor, preferring a bank-observed current balance
  and falling back to the latest statement closing balance, which is historical and is never
  called live;
* recurring payments found in the history are placed on their next expected dates;
* everything else is spread as a flat daily rate from complete months only, with the
  recurring items removed first so they are not counted twice;
* no transaction or assumption dated after the analysis date is used.

Scenarios change only this projection. They never touch a transaction, a balance or an
import, and they are computed per request for one workspace.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from app.analysis.metrics import Txn, _as_date, coverage, monthly, recurring, within

FORECAST_VERSION = "forecast-1"
HORIZON_DAYS = 30


@dataclass(frozen=True)
class Anchor:
    amount_minor: int
    as_of: str
    kind: str
    provenance: str
    is_live: bool


@dataclass(frozen=True)
class Scenario:
    """The three controls. All optional; absent means baseline."""

    receipts_change_percent: float = 0.0
    # A payment the OWNER says they expect. Bank history is not evidence of an unpaid invoice,
    # so the amount and the original date are supplied, never inferred.
    expected_payment_minor: int | None = None
    expected_payment_date: str | None = None
    delay_days: int = 0
    one_time_expense_minor: int | None = None
    one_time_expense_date: str | None = None
    one_time_expense_label: str | None = None


def choose_anchor(balances: list[dict], as_of: str) -> Anchor | None:
    """The most recent usable balance on or before the analysis date.

    A bank-observed current balance wins. A statement closing balance is used only when there
    is no live one, and it is returned with `is_live` false so nothing can present it as
    today's cash. Available balances are never added to current ones.
    """
    usable = [b for b in balances if b["as_of"][:10] <= as_of]
    live = [b for b in usable if b["kind"] == "current" and b["provenance"] == "bank_observed"]
    statement = [b for b in usable if b["kind"] == "statement_closing"]
    pool = live or statement
    if not pool:
        return None
    latest_day = max(b["as_of"][:10] for b in pool)
    same_day = [b for b in pool if b["as_of"][:10] == latest_day]
    # One row per account on that day, then summed: the business's cash, not a double count.
    per_account: dict[str, dict] = {}
    for b in same_day:
        per_account[b["account_id"]] = b
    total = sum(b["amount_minor"] for b in per_account.values())
    first = next(iter(per_account.values()))
    return Anchor(
        amount_minor=total,
        as_of=latest_day,
        kind=first["kind"],
        provenance=first["provenance"],
        is_live=bool(live),
    )


def _daily_rates(transactions: list[Txn], as_of: str, repeats: list[dict]) -> tuple[int, int]:
    """Flat daily inflow and outflow rates from complete months, recurring items removed first.

    Inflow and outflow are kept apart so a change to customer receipts can scale what actually
    comes in, rather than being applied to a single net figure where it would be meaningless.
    """
    months = [m for m in monthly(transactions, as_of) if m["complete"]]
    if not months:
        return 0, 0
    recent = months[-3:]
    money_in = sum(m["money_in_minor"] for m in recent)
    money_out = sum(m["money_out_minor"] for m in recent)
    days = 0
    for m in recent:
        year, mon = int(m["month"][:4]), int(m["month"][5:7])
        nxt = date(year + (mon == 12), 1 if mon == 12 else mon + 1, 1)
        days += (nxt - date(year, mon, 1)).days
    if days == 0:
        return 0, 0
    # Remove the recurring items so placing them on dates does not count them twice.
    repeat_in = repeat_out = 0
    for r in repeats:
        cadence = max(r["cadence_days"], 1)
        over_window = round(abs(r["typical_amount_minor"]) * days / cadence)
        if r["typical_amount_minor"] > 0:
            repeat_in += over_window
        else:
            repeat_out += over_window
    return (
        max(round((money_in - repeat_in) / days), 0),
        max(round((money_out - repeat_out) / days), 0),
    )


def build(
    transactions: list[Txn],
    balances: list[dict],
    as_of: str,
    scenario: Scenario | None = None,
    horizon_days: int = HORIZON_DAYS,
) -> dict:
    """The baseline path, and the scenario path when one is given."""
    scenario = scenario or Scenario()
    observed = within(transactions, as_of)
    cover = coverage(transactions, as_of)
    anchor = choose_anchor(balances, as_of)
    repeats = recurring(transactions, as_of)
    start = _as_date(as_of)

    if anchor is None or not cover["sufficient_for_forecast"]:
        reasons = list(cover["reasons"])
        if anchor is None:
            reasons.append("no dated balance to start from")
        return {
            "version": FORECAST_VERSION,
            "available": False,
            "as_of": as_of,
            "horizon_days": horizon_days,
            "anchor": _anchor_payload(anchor),
            "reasons": reasons,
            # None, not an empty list: the response model carries a path or nothing at all,
            # and an empty list fails validation rather than reading as "no projection".
            "baseline": None,
            "scenario": None,
            "assumptions": [],
        }

    daily_in, daily_out = _daily_rates(observed, as_of, repeats)
    daily = daily_in - daily_out
    assumptions: list[dict] = [
        {
            "key": "anchor",
            "label": "Starting cash",
            "detail": (
                ("Bank-observed balance" if anchor.is_live else "Latest statement closing")
                + f" of {anchor.amount_minor} minor units as of {anchor.as_of}"
            ),
            "estimated": not anchor.is_live,
        },
        {
            "key": "daily_rate",
            "label": "Everyday cash rate",
            "detail": (
                f"{daily_in} minor units in and {daily_out} out a day, from complete months "
                "only, with the recurring payments below removed so nothing is counted twice"
            ),
            "estimated": True,
        },
    ]

    def events(apply_scenario: bool) -> list[dict]:
        rows: list[dict] = []
        for r in repeats:
            day = _as_date(r["next_expected"])
            cadence = max(r["cadence_days"], 1)
            while day <= start + timedelta(days=horizon_days):
                if day > start:
                    amount = r["typical_amount_minor"]
                    if apply_scenario and amount > 0 and scenario.receipts_change_percent:
                        amount = round(amount * (1 + scenario.receipts_change_percent / 100.0))
                    rows.append(
                        {
                            "date": day.isoformat(),
                            "label": r["label"],
                            "amount_minor": amount,
                            "kind": "recurring_estimate",
                        }
                    )
                day = day + timedelta(days=cadence)
        if apply_scenario and scenario.expected_payment_minor and scenario.expected_payment_date:
            original = _as_date(scenario.expected_payment_date)
            moved = original + timedelta(days=scenario.delay_days)
            if start < moved <= start + timedelta(days=horizon_days):
                rows.append(
                    {
                        "date": moved.isoformat(),
                        "label": "Expected payment you entered",
                        "amount_minor": scenario.expected_payment_minor,
                        "kind": "owner_assumption",
                    }
                )
        elif scenario.expected_payment_minor and scenario.expected_payment_date:
            original = _as_date(scenario.expected_payment_date)
            if start < original <= start + timedelta(days=horizon_days):
                rows.append(
                    {
                        "date": original.isoformat(),
                        "label": "Expected payment you entered",
                        "amount_minor": scenario.expected_payment_minor,
                        "kind": "owner_assumption",
                    }
                )
        if apply_scenario and scenario.one_time_expense_minor and scenario.one_time_expense_date:
            when = _as_date(scenario.one_time_expense_date)
            if start < when <= start + timedelta(days=horizon_days):
                rows.append(
                    {
                        "date": when.isoformat(),
                        "label": scenario.one_time_expense_label or "One-off cost you entered",
                        "amount_minor": -abs(scenario.one_time_expense_minor),
                        "kind": "owner_assumption",
                    }
                )
        return rows

    def path(apply_scenario: bool) -> list[dict]:
        by_day: dict[str, list[dict]] = {}
        for e in events(apply_scenario):
            by_day.setdefault(e["date"], []).append(e)
        rate_in = daily_in
        if apply_scenario and scenario.receipts_change_percent:
            rate_in = round(daily_in * (1 + scenario.receipts_change_percent / 100.0))
        step = rate_in - daily_out
        balance = anchor.amount_minor
        rows = []
        for offset in range(horizon_days + 1):
            day = (start + timedelta(days=offset)).isoformat()
            if offset > 0:
                balance += step
                for e in by_day.get(day, []):
                    balance += e["amount_minor"]
            rows.append(
                {
                    "date": day,
                    "balance_minor": balance,
                    "events": by_day.get(day, []) if offset > 0 else [],
                }
            )
        return rows

    baseline = path(False)
    has_scenario = bool(
        scenario.receipts_change_percent
        or (scenario.expected_payment_minor and scenario.expected_payment_date)
        or (scenario.one_time_expense_minor and scenario.one_time_expense_date)
    )
    scenario_path = path(True) if has_scenario else None

    if scenario.receipts_change_percent:
        assumptions.append(
            {
                "key": "receipts",
                "label": "Customer receipts",
                "detail": f"changed by {scenario.receipts_change_percent:+.1f}% for the window",
                "estimated": True,
            }
        )
    if scenario.expected_payment_minor and scenario.expected_payment_date:
        assumptions.append(
            {
                "key": "expected_payment",
                "label": "Expected payment",
                "detail": (
                    f"{scenario.expected_payment_minor} minor units you expect on "
                    f"{scenario.expected_payment_date}"
                    + (f", delayed by {scenario.delay_days} days" if scenario.delay_days else "")
                    + ". This is your figure; the bank history is not evidence of an unpaid invoice"
                ),
                "estimated": True,
            }
        )
    if scenario.one_time_expense_minor and scenario.one_time_expense_date:
        assumptions.append(
            {
                "key": "one_time_expense",
                "label": scenario.one_time_expense_label or "One-off cost",
                "detail": (
                    f"{abs(scenario.one_time_expense_minor)} minor units on "
                    f"{scenario.one_time_expense_date}"
                ),
                "estimated": True,
            }
        )

    return {
        "version": FORECAST_VERSION,
        "available": True,
        "as_of": as_of,
        "horizon_days": horizon_days,
        "anchor": _anchor_payload(anchor),
        "daily_rate_minor": daily,
        "reasons": [],
        "baseline": _summarize(baseline),
        "scenario": _summarize(scenario_path) if scenario_path else None,
        "difference_minor": (
            scenario_path[-1]["balance_minor"] - baseline[-1]["balance_minor"]
            if scenario_path
            else None
        ),
        # Delaying a payment often leaves the ending cash unchanged because the money still
        # arrives inside the window. What moves is the dip along the way, so it is reported.
        "lowest_difference_minor": (
            min(p["balance_minor"] for p in scenario_path)
            - min(p["balance_minor"] for p in baseline)
            if scenario_path
            else None
        ),
        "assumptions": assumptions,
    }


def _anchor_payload(anchor: Anchor | None) -> dict | None:
    if anchor is None:
        return None
    return {
        "amount_minor": anchor.amount_minor,
        "as_of": anchor.as_of,
        "kind": anchor.kind,
        "provenance": anchor.provenance,
        "is_live": anchor.is_live,
    }


def _summarize(path: list[dict]) -> dict:
    lowest = min(path, key=lambda r: r["balance_minor"])
    return {
        "points": path,
        "ending_minor": path[-1]["balance_minor"],
        "lowest_minor": lowest["balance_minor"],
        "lowest_date": lowest["date"],
    }
