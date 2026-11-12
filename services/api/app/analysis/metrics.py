"""Cash metrics from posted transactions. Pure arithmetic on integer minor units.

Every figure here is cash movement observed in the bank, not accounting profit, and the
interface says so wherever a total is shown. Rules that matter:

* only `posted` transactions that have not been removed are counted; pending ones are shown
  separately and never added to a period total;
* provenance rows (`transaction_sources`) are evidence, not transactions, so a transaction
  linked to two files is still one transaction;
* an internal transfer matched to its opposite leg is netted out of consolidated money in and
  money out, because the business neither earned nor spent it, while still appearing in total
  cash movement;
* nothing is computed from data dated after the analysis date.
"""

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.analysis.categorize import BY_KEY, TRANSFER, UNCATEGORIZED, Category, categorize

METRICS_VERSION = "cash-2"
TRANSFER_MATCH_DAYS = 3

# A person's category decision for one transaction, applied on read. Nothing is stored back
# into the transaction, so the rules stay the only thing that placed it and the override stays
# visibly the owner's own.
Overrides = Mapping[str, str] | None


@dataclass(frozen=True)
class Txn:
    id: str
    account_id: str
    posted_date: str
    description: str
    amount_minor: int
    status: str
    # Carried so the fingerprint changes when a row is revised, and so an override made
    # against an earlier revision can be held back rather than applied to different data.
    revision: int = 1


@dataclass
class MonthTotals:
    month: str
    money_in_minor: int = 0
    money_out_minor: int = 0
    posted_count: int = 0
    transfer_in_minor: int = 0
    transfer_out_minor: int = 0
    ids: list[str] = field(default_factory=list)

    @property
    def net_minor(self) -> int:
        return self.money_in_minor - self.money_out_minor


def _month(day: str) -> str:
    return day[:7]


def _as_date(day: str) -> date:
    return date.fromisoformat(day[:10])


def within(transactions: list[Txn], as_of: str) -> list[Txn]:
    """Only what was already observed on the analysis date. No future leakage, ever."""
    return [t for t in transactions if t.posted_date[:10] <= as_of]


def category_of(t: Txn, overrides: Overrides = None) -> Category:
    """The owner's decision for this row where there is one, otherwise the published rules."""
    if overrides:
        chosen = overrides.get(t.id)
        if chosen:
            return BY_KEY.get(chosen, UNCATEGORIZED)
    return categorize(t.description, t.amount_minor)


def matched_transfers(transactions: list[Txn], overrides: Overrides = None) -> set[str]:
    """Ids of internal-transfer legs that pair with an opposite leg of the same size.

    A pair is two transfer-categorised rows in different accounts, equal in size, opposite in
    sign, within a few days. Only matched pairs are netted out; a lone transfer row stays in
    the totals, because we cannot show that the money went somewhere the owner still holds.

    An owner who corrects a row to an internal transfer can therefore let a pair form, which is
    the intended effect of the correction and not a special case.
    """
    legs = [t for t in transactions if category_of(t, overrides).group == TRANSFER]
    matched: set[str] = set()
    by_size: dict[int, list[Txn]] = defaultdict(list)
    for leg in legs:
        by_size[abs(leg.amount_minor)].append(leg)
    for group in by_size.values():
        outs = sorted((t for t in group if t.amount_minor < 0), key=lambda t: t.posted_date)
        ins = sorted((t for t in group if t.amount_minor > 0), key=lambda t: t.posted_date)
        used: set[str] = set()
        for out in outs:
            for inflow in ins:
                if inflow.id in used or inflow.account_id == out.account_id:
                    continue
                gap = abs((_as_date(inflow.posted_date) - _as_date(out.posted_date)).days)
                if gap <= TRANSFER_MATCH_DAYS:
                    matched.add(out.id)
                    matched.add(inflow.id)
                    used.add(inflow.id)
                    break
    return matched


def monthly(
    transactions: list[Txn],
    as_of: str,
    consolidate_transfers: bool = True,
    overrides: Overrides = None,
) -> list[dict]:
    """Money in, money out and net per calendar month, oldest first.

    `complete` says whether the month had ended by the analysis date. A month still running is
    labelled incomplete rather than compared as though it were finished.
    """
    posted = [t for t in within(transactions, as_of) if t.status == "posted"]
    netted = matched_transfers(posted, overrides) if consolidate_transfers else set()
    buckets: dict[str, MonthTotals] = {}
    for t in posted:
        bucket = buckets.setdefault(_month(t.posted_date), MonthTotals(month=_month(t.posted_date)))
        bucket.posted_count += 1
        bucket.ids.append(t.id)
        if t.id in netted:
            if t.amount_minor > 0:
                bucket.transfer_in_minor += t.amount_minor
            else:
                bucket.transfer_out_minor += -t.amount_minor
            continue
        if t.amount_minor > 0:
            bucket.money_in_minor += t.amount_minor
        else:
            bucket.money_out_minor += -t.amount_minor
    analysis = _as_date(as_of)
    out = []
    for month in sorted(buckets):
        totals = buckets[month]
        year, mon = int(month[:4]), int(month[5:7])
        last = date(year + (mon == 12), 1 if mon == 12 else mon + 1, 1) - timedelta(days=1)
        out.append(
            {
                "month": month,
                "money_in_minor": totals.money_in_minor,
                "money_out_minor": totals.money_out_minor,
                "net_minor": totals.net_minor,
                "posted_count": totals.posted_count,
                "transfers_netted_minor": totals.transfer_in_minor + totals.transfer_out_minor,
                "complete": last <= analysis,
            }
        )
    return out


def by_category(
    transactions: list[Txn],
    as_of: str,
    month: str | None = None,
    overrides: Overrides = None,
) -> list[dict]:
    """Totals per category, with the ids behind each one so the owner can open the evidence."""
    posted = [t for t in within(transactions, as_of) if t.status == "posted"]
    if month:
        posted = [t for t in posted if _month(t.posted_date) == month]
    netted = matched_transfers(posted, overrides)
    rows: dict[str, dict[str, Any]] = {}
    for t in posted:
        category = category_of(t, overrides)
        row = rows.setdefault(
            category.key,
            {
                "key": category.key,
                "label": category.label,
                "group": category.group,
                "inflow_minor": 0,
                "outflow_minor": 0,
                "count": 0,
                "netted_pair": False,
                "transaction_ids": [],
            },
        )
        row["count"] += 1
        row["transaction_ids"].append(t.id)
        if t.id in netted:
            row["netted_pair"] = True
        if t.amount_minor > 0:
            row["inflow_minor"] += t.amount_minor
        else:
            row["outflow_minor"] += -t.amount_minor
    ordered = sorted(
        rows.values(), key=lambda r: r["outflow_minor"] + r["inflow_minor"], reverse=True
    )
    for row in ordered:
        row["transaction_ids"] = row["transaction_ids"][:200]
    return ordered


def compare_months(
    transactions: list[Txn],
    as_of: str,
    month: str,
    previous: str,
    overrides: Overrides = None,
) -> dict:
    """Category-level change between two months, and the headline totals for each."""
    now = {r["key"]: r for r in by_category(transactions, as_of, month, overrides)}
    before = {r["key"]: r for r in by_category(transactions, as_of, previous, overrides)}
    changes = []
    for key in sorted(set(now) | set(before)):
        a = now.get(key)
        b = before.get(key)
        label = (a or b)["label"]
        group = (a or b)["group"]
        current = (a["inflow_minor"] - a["outflow_minor"]) if a else 0
        prior = (b["inflow_minor"] - b["outflow_minor"]) if b else 0
        if current == prior == 0:
            continue
        changes.append(
            {
                "key": key,
                "label": label,
                "group": group,
                "current_minor": current,
                "previous_minor": prior,
                "change_minor": current - prior,
            }
        )
    changes.sort(key=lambda c: abs(c["change_minor"]), reverse=True)
    months = {m["month"]: m for m in monthly(transactions, as_of, overrides=overrides)}
    return {
        "month": month,
        "previous_month": previous,
        "month_totals": months.get(month),
        "previous_totals": months.get(previous),
        "changes": changes,
    }


def recurring(
    transactions: list[Txn], as_of: str, minimum: int = 3, overrides: Overrides = None
) -> list[dict]:
    """Payments that repeat on a steady cadence, with the next one estimated, not promised.

    A description that has been seen at least `minimum` times with a regular gap is reported
    as likely recurring. This is an observation about the past, and the next date is an
    estimate; the interface labels it as such and never as a scheduled obligation.
    """
    posted = [t for t in within(transactions, as_of) if t.status == "posted"]
    groups: dict[str, list[Txn]] = defaultdict(list)
    for t in posted:
        groups[(t.description or "").strip().upper()].append(t)
    found = []
    for label, rows in groups.items():
        if len(rows) < minimum:
            continue
        rows.sort(key=lambda t: t.posted_date)
        gaps = [
            (_as_date(b.posted_date) - _as_date(a.posted_date)).days
            for a, b in zip(rows, rows[1:], strict=False)
        ]
        gaps = [g for g in gaps if g > 0]
        if not gaps:
            continue
        median_gap = sorted(gaps)[len(gaps) // 2]
        spread = max(abs(g - median_gap) for g in gaps)
        # Two days is the floor: a card settlement stream that lands twice a week is as
        # real a pattern as a monthly lease, and the forecast needs to see it.
        if median_gap < 2 or median_gap > 45 or spread > max(7, median_gap // 3):
            continue
        # The median decides whether the pattern is regular enough to report. The MEAN gap and
        # the MEAN amount decide how much it contributes, because a forecast that multiplies a
        # median amount by a median cadence drifts away from what the months actually show.
        typical_gap = max(round(sum(gaps) / len(gaps)), 1)
        typical_amount = round(sum(t.amount_minor for t in rows) / len(rows))
        last = _as_date(rows[-1].posted_date)
        # The label's category, except that a person's decisions on these rows win. Where they
        # disagree the commonest one is reported, so a half-corrected group is not misread.
        placed = Counter(category_of(t, overrides).key for t in rows).most_common(1)[0][0]
        found.append(
            {
                "label": label,
                "category": placed,
                "typical_amount_minor": typical_amount,
                "cadence_days": typical_gap,
                "occurrences": len(rows),
                "last_seen": rows[-1].posted_date[:10],
                "next_expected": (last + timedelta(days=typical_gap)).isoformat(),
                "direction": "inflow" if typical_amount > 0 else "outflow",
                "transaction_ids": [t.id for t in rows][-20:],
            }
        )
    found.sort(key=lambda r: abs(r["typical_amount_minor"]), reverse=True)
    return found


def coverage(transactions: list[Txn], as_of: str) -> dict:
    """How much usable history there is, and what that does not support."""
    posted = [t for t in within(transactions, as_of) if t.status == "posted"]
    months = monthly(transactions, as_of)
    complete = [m for m in months if m["complete"]]
    latest = max((t.posted_date[:10] for t in posted), default=None)
    stale_days = (_as_date(as_of) - _as_date(latest)).days if latest else None
    reasons = []
    if len(complete) < 2:
        reasons.append("fewer than two complete months of history")
    if stale_days is not None and stale_days > 45:
        reasons.append(f"the newest transaction is {stale_days} days before the analysis date")
    if not posted:
        reasons.append("no posted transactions")
    return {
        "months_available": len(months),
        "complete_months": len(complete),
        "posted_count": len(posted),
        "first_posted": min((t.posted_date[:10] for t in posted), default=None),
        "last_posted": latest,
        "stale_days": stale_days,
        "sufficient_for_forecast": not reasons,
        "reasons": reasons,
    }
