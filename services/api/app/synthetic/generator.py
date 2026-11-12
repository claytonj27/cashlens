"""Deterministic synthetic bank-provider fixtures for the demo businesses.

A fixed seed and a frozen demo date make the output reproducible byte for
byte. Two artifacts are produced per business:

- a provider fixture (Plaid-like pages of added/modified/removed records with
  cursors), which is what the application ingests;
- an evaluation file (categories, case markers and expected figures), which
  stays outside application and model context.

Provider sign convention mirrors Plaid: a positive amount is money leaving
the account. Canonical amounts are signed minor units with inflows positive.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from random import Random
from typing import Any

GENERATOR_VERSION = "1"
SEED = 20260901
DEMO_DATE = date(2026, 8, 31)
HISTORY_START = date(2026, 3, 1)
FIXTURE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://cashlens.invalid/fixtures")


def stable_id(*parts: str) -> str:
    """A UUID that is the same on every run for the same logical key."""
    return str(uuid.uuid5(FIXTURE_NAMESPACE, ":".join(parts)))


def minor_to_provider_amount(amount_minor: int) -> str:
    """Canonical minor units (inflow positive) to the provider decimal (outflow positive)."""
    return f"{-Decimal(amount_minor) / 100:.2f}"


def provider_amount_to_minor(amount: str) -> int:
    """Provider decimal string (outflow positive) to canonical minor units (inflow positive)."""
    return int(-(Decimal(amount) * 100))


@dataclass(frozen=True)
class AccountSpec:
    external_id: str
    name: str
    type: str
    mask: str
    opening_minor: int


@dataclass
class Tx:
    external_id: str
    account: str
    amount_minor: int
    posted_date: date
    description: str
    category: str
    pending: bool = False
    pending_transaction_id: str | None = None
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Cases:
    transfer_date: date
    transfer_minor: int
    ambiguous_date: date
    ambiguous_minor: int
    ambiguous_description: str
    financing_date: date
    financing_minor: int
    financing_description: str
    financing_payment_day: int
    financing_payment_minor: int
    financing_payment_description: str
    unresolvable: tuple[tuple[date, int, str], ...]
    pending_to_posted_description: str
    pending_minor: int
    posted_minor: int
    removed_description: str
    removed_minor: int
    remaining_pending_description: str
    remaining_pending_minor: int
    stale_snapshot_as_of: date
    interest_minor: int


@dataclass(frozen=True)
class Profile:
    slug: str
    name: str
    industry: str
    checking: AccountSpec
    savings: AccountSpec
    cases: Cases
    assumptions: tuple[dict[str, Any], ...]
    history: Callable[[Random, "Profile"], list[Tx]]
    seed_offset: int


def _days(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _cents(rng: Random, low: float, high: float) -> int:
    return rng.randint(int(low * 100), int(high * 100))


def _juniper_history(rng: Random, p: Profile) -> list[Tx]:
    chk = p.checking.external_id
    txs: list[Tx] = []
    payroll_anchor = date(2026, 3, 6)
    for day in _days(HISTORY_START, DEMO_DATE):
        if day.weekday() != 0:  # closed Mondays
            uplift = 1.35 if day.weekday() >= 4 else 1.0
            processor = "SQUARE INC SETTLEMENT" if day.day % 2 else "TOAST PAYOUT"
            txs.append(
                Tx(
                    f"jk-settle-{day}",
                    chk,
                    int(_cents(rng, 1000, 1900) * uplift),
                    day,
                    processor,
                    "operating_receipts",
                )
            )
        if day.weekday() in (0, 3):
            txs.append(
                Tx(
                    f"jk-sysco-{day}",
                    chk,
                    -_cents(rng, 900, 1900),
                    day,
                    "SYSCO CORP ACH DEBIT",
                    "supplier_payments",
                )
            )
        if day.weekday() == 3:
            txs.append(
                Tx(
                    f"jk-usfoods-{day}",
                    chk,
                    -_cents(rng, 600, 1300),
                    day,
                    "US FOODS INC",
                    "supplier_payments",
                )
            )
        if day.weekday() == 4:
            txs.append(
                Tx(
                    f"jk-produce-{day}",
                    chk,
                    -_cents(rng, 250, 600),
                    day,
                    "GREEN VALLEY PRODUCE",
                    "supplier_payments",
                )
            )
        if day.weekday() == 4 and (day - payroll_anchor).days % 14 == 0:
            txs.append(
                Tx(
                    f"jk-payroll-{day}",
                    chk,
                    -_cents(rng, 7800, 9200),
                    day,
                    "GUSTO PAYROLL",
                    "payroll",
                )
            )
        if day.day == 1:
            txs.append(Tx(f"jk-rent-{day}", chk, -420000, day, "RIVERSIDE PROPERTIES RENT", "rent"))
        if day.day == 12:
            txs.append(
                Tx(
                    f"jk-power-{day}",
                    chk,
                    -_cents(rng, 380, 620),
                    day,
                    "PACIFIC GAS AND ELECTRIC",
                    "utilities",
                )
            )
        if day.day == 15:
            txs.append(
                Tx(
                    f"jk-water-{day}",
                    chk,
                    -_cents(rng, 90, 140),
                    day,
                    "CITY WATER UTILITY",
                    "utilities",
                )
            )
        if day.day == 3:
            txs.append(
                Tx(f"jk-pos-{day}", chk, -16500, day, "TOAST POS SUBSCRIPTION", "subscriptions")
            )
        if day.day == 8:
            txs.append(Tx(f"jk-insure-{day}", chk, -41000, day, "HARTFORD INSURANCE", "insurance"))
        if day in (date(2026, 4, 20), date(2026, 7, 20)):
            txs.append(
                Tx(
                    f"jk-salestax-{day}",
                    chk,
                    -890000,
                    day,
                    "STATE DEPT OF REVENUE SALES TAX",
                    "taxes",
                )
            )
    txs.append(
        Tx(
            "jk-repair-2026-06-10",
            chk,
            -640000,
            date(2026, 6, 10),
            "HOBART SERVICE EQUIPMENT REPAIR",
            "repairs",
            flags=("one_time_repair",),
        )
    )
    return txs


def _willow_history(rng: Random, p: Profile) -> list[Tx]:
    chk = p.checking.external_id
    txs: list[Tx] = []
    for day in _days(HISTORY_START, DEMO_DATE):
        if day.weekday() < 6 and rng.random() < 0.42:
            txs.append(
                Tx(
                    f"ws-stripe-{day}",
                    chk,
                    _cents(rng, 180, 1450),
                    day,
                    "STRIPE PAYOUT",
                    "operating_receipts",
                )
            )
        if day.day == 1:
            txs.append(Tx(f"ws-rent-{day}", chk, -165000, day, "MAPLE COMMONS STUDIO RENT", "rent"))
        if day.day == 4:
            txs.append(
                Tx(f"ws-adobe-{day}", chk, -5999, day, "ADOBE CREATIVE CLOUD", "subscriptions")
            )
        if day.day == 6:
            txs.append(Tx(f"ws-sqsp-{day}", chk, -3600, day, "SQUARESPACE INC", "subscriptions"))
        if day.day == 9:
            txs.append(Tx(f"ws-zoom-{day}", chk, -1599, day, "ZOOM VIDEO COMM", "subscriptions"))
        if day.day == 11:
            txs.append(Tx(f"ws-gws-{day}", chk, -1440, day, "GOOGLE WORKSPACE", "subscriptions"))
        if day.day in (5, 20):
            txs.append(
                Tx(
                    f"ws-assist-{day}",
                    chk,
                    -_cents(rng, 350, 480),
                    day,
                    "VENMO PAYMENT ASSISTANT",
                    "contractor_payments",
                )
            )
        if day.day == 25:
            txs.append(
                Tx(
                    f"ws-draw-{day}",
                    chk,
                    -250000,
                    day,
                    "TRANSFER TO PERSONAL SAVINGS",
                    "owner_draw",
                )
            )
        if day.day == 14:
            txs.append(
                Tx(f"ws-insure-{day}", chk, -8900, day, "HISCOX BUSINESS INSURANCE", "insurance")
            )
    txs.append(
        Tx(
            "ws-equipment-2026-07-09",
            chk,
            -384500,
            date(2026, 7, 9),
            "B AND H PHOTO VIDEO",
            "equipment",
            flags=("one_time_equipment",),
        )
    )
    return txs


def _northline_history(rng: Random, p: Profile) -> list[Tx]:
    chk = p.checking.external_id
    txs: list[Tx] = []
    customers = (
        "ACH CREDIT HARBOR POINT HOA",
        "CHECK DEPOSIT",
        "ACH CREDIT CEDAR RIDGE LLC",
        "ACH CREDIT WESTFIELD PROPERTY MGMT",
    )
    next_receipt = HISTORY_START + timedelta(days=rng.randint(3, 7))
    payroll_anchor = date(2026, 3, 13)
    for day in _days(HISTORY_START, DEMO_DATE):
        if day == next_receipt:
            txs.append(
                Tx(
                    f"ns-receipt-{day}",
                    chk,
                    _cents(rng, 6500, 17000),
                    day,
                    rng.choice(customers),
                    "operating_receipts",
                )
            )
            next_receipt = day + timedelta(days=rng.randint(6, 10))
        if day.weekday() in (1, 4):
            txs.append(
                Tx(
                    f"ns-homedepot-{day}",
                    chk,
                    -_cents(rng, 300, 2400),
                    day,
                    "THE HOME DEPOT",
                    "materials",
                )
            )
        if day.day in (7, 23):
            txs.append(
                Tx(
                    f"ns-ferguson-{day}",
                    chk,
                    -_cents(rng, 1500, 4800),
                    day,
                    "FERGUSON ENTERPRISES",
                    "materials",
                )
            )
        if day.weekday() in (0, 2, 4):
            txs.append(
                Tx(
                    f"ns-fuel-{day}",
                    chk,
                    -_cents(rng, 80, 160),
                    day,
                    "SHELL OIL" if day.day % 2 else "CHEVRON",
                    "fuel",
                )
            )
        if day.weekday() == 4 and (day - payroll_anchor).days % 14 == 0:
            txs.append(
                Tx(
                    f"ns-payroll-{day}",
                    chk,
                    -_cents(rng, 11000, 13500),
                    day,
                    "ADP PAYROLL",
                    "payroll",
                )
            )
        if day.day == 1:
            txs.append(Tx(f"ns-rent-{day}", chk, -390000, day, "K STREET INDUSTRIAL RENT", "rent"))
        if day.day == 10:
            txs.append(
                Tx(
                    f"ns-insure-{day}",
                    chk,
                    -124000,
                    day,
                    "TRAVELERS COMMERCIAL INSURANCE",
                    "insurance",
                )
            )
        if day.day == 15:
            txs.append(
                Tx(
                    f"ns-lease-{day}",
                    chk,
                    -118000,
                    day,
                    "CAT FINANCIAL EQUIPMENT LEASE",
                    "equipment_lease",
                )
            )
    return txs


PROFILES: tuple[Profile, ...] = (
    Profile(
        slug="juniper-kitchen",
        name="Juniper Kitchen",
        industry="restaurant",
        checking=AccountSpec("acct-juniper-chk", "Operating checking", "checking", "4021", 1834500),
        savings=AccountSpec("acct-juniper-sav", "Reserve savings", "savings", "4477", 1200000),
        cases=Cases(
            transfer_date=date(2026, 5, 12),
            transfer_minor=200000,
            ambiguous_date=date(2026, 6, 18),
            ambiguous_minor=350000,
            ambiguous_description="DEPOSIT 8842",
            financing_date=date(2026, 4, 21),
            financing_minor=2500000,
            financing_description="SBA EIDL LOAN PROCEEDS",
            financing_payment_day=21,
            financing_payment_minor=-73100,
            financing_payment_description="SBA LOAN PAYMENT",
            unresolvable=(
                (date(2026, 7, 7), 27500, "ACH CREDIT 0192"),
                (date(2026, 5, 28), -64000, "CHECK 1044"),
            ),
            pending_to_posted_description="SYSCO CORP ACH DEBIT",
            pending_minor=-142000,
            posted_minor=-143650,
            removed_description="DOORDASH PAYOUT",
            removed_minor=41000,
            remaining_pending_description="CARD AUTHORIZATION RESTAURANT DEPOT",
            remaining_pending_minor=-8500,
            stale_snapshot_as_of=date(2026, 8, 15),
            interest_minor=1250,
        ),
        assumptions=(
            {
                "kind": "rent",
                "amount_minor": 420000,
                "due_date": "2026-09-01",
                "origin": "owner_entered",
            },
            {
                "kind": "payroll",
                "amount_minor": 690000,
                "due_date": "2026-09-04",
                "origin": "owner_entered",
            },
        ),
        history=_juniper_history,
        seed_offset=1,
    ),
    Profile(
        slug="willow-studio",
        name="Willow Studio",
        industry="photography studio",
        checking=AccountSpec("acct-willow-chk", "Business checking", "checking", "7310", 942000),
        savings=AccountSpec("acct-willow-sav", "Savings", "savings", "7355", 500000),
        cases=Cases(
            transfer_date=date(2026, 6, 2),
            transfer_minor=100000,
            ambiguous_date=date(2026, 4, 27),
            ambiguous_minor=120000,
            ambiguous_description="ZELLE PAYMENT FROM MARTIN R",
            financing_date=date(2026, 5, 5),
            financing_minor=600000,
            financing_description="PAYPAL WORKING CAPITAL ADVANCE",
            financing_payment_day=19,
            financing_payment_minor=-52000,
            financing_payment_description="PAYPAL WORKING CAPITAL REPAYMENT",
            unresolvable=(
                (date(2026, 6, 24), 45000, "ACH CREDIT 3391"),
                (date(2026, 3, 30), -21000, "CHECK 211"),
            ),
            pending_to_posted_description="ADOBE CREATIVE CLOUD",
            pending_minor=-5999,
            posted_minor=-6299,
            removed_description="STRIPE PAYOUT",
            removed_minor=61200,
            remaining_pending_description="CARD AUTHORIZATION AMAZON",
            remaining_pending_minor=-4275,
            stale_snapshot_as_of=date(2026, 8, 12),
            interest_minor=520,
        ),
        assumptions=(
            {
                "kind": "equipment_purchase",
                "amount_minor": 290000,
                "due_date": "2026-09-20",
                "origin": "owner_entered",
            },
            {
                "kind": "rent",
                "amount_minor": 165000,
                "due_date": "2026-09-01",
                "origin": "owner_entered",
            },
        ),
        history=_willow_history,
        seed_offset=2,
    ),
    Profile(
        slug="northline-services",
        name="Northline Services",
        industry="general contracting",
        checking=AccountSpec(
            "acct-northline-chk", "Operating checking", "checking", "9902", 4280000
        ),
        savings=AccountSpec(
            "acct-northline-sav", "Tax reserve savings", "savings", "9911", 1500000
        ),
        cases=Cases(
            transfer_date=date(2026, 4, 8),
            transfer_minor=500000,
            ambiguous_date=date(2026, 7, 22),
            ambiguous_minor=680000,
            ambiguous_description="DEPOSIT 4471",
            financing_date=date(2026, 6, 3),
            financing_minor=2000000,
            financing_description="LINE OF CREDIT DRAW",
            financing_payment_day=3,
            financing_payment_minor=-185000,
            financing_payment_description="LINE OF CREDIT PAYMENT",
            unresolvable=(
                (date(2026, 8, 12), 950000, "WIRE IN 000348"),
                (date(2026, 4, 16), -132000, "CHECK 5067"),
            ),
            pending_to_posted_description="FERGUSON ENTERPRISES",
            pending_minor=-312000,
            posted_minor=-318400,
            removed_description="ACH CREDIT CEDAR RIDGE LLC",
            removed_minor=740000,
            remaining_pending_description="CARD AUTHORIZATION FASTENAL",
            remaining_pending_minor=-19600,
            stale_snapshot_as_of=date(2026, 8, 18),
            interest_minor=1860,
        ),
        assumptions=(
            {
                "kind": "expected_receipt",
                "amount_minor": 1450000,
                "due_date": "2026-09-10",
                "origin": "owner_entered",
            },
            {
                "kind": "rent",
                "amount_minor": 390000,
                "due_date": "2026-09-01",
                "origin": "owner_entered",
            },
        ),
        history=_northline_history,
        seed_offset=3,
    ),
)


def _inject_cases(p: Profile, txs: list[Tx]) -> list[Tx]:
    c, chk, sav = p.cases, p.checking.external_id, p.savings.external_id
    prefix = p.slug
    txs += [
        Tx(
            f"{prefix}-transfer-out",
            chk,
            -c.transfer_minor,
            c.transfer_date,
            f"ONLINE TRANSFER TO SAVINGS ...{p.savings.mask}",
            "internal_transfer",
            flags=("transfer_pair",),
        ),
        Tx(
            f"{prefix}-transfer-in",
            sav,
            c.transfer_minor,
            c.transfer_date,
            f"ONLINE TRANSFER FROM CHECKING ...{p.checking.mask}",
            "internal_transfer",
            flags=("transfer_pair",),
        ),
        Tx(
            f"{prefix}-ambiguous",
            chk,
            c.ambiguous_minor,
            c.ambiguous_date,
            c.ambiguous_description,
            "ambiguous",
            flags=("ambiguous_deposit",),
        ),
        Tx(
            f"{prefix}-financing",
            chk,
            c.financing_minor,
            c.financing_date,
            c.financing_description,
            "financing",
            flags=("financing_inflow",),
        ),
        Tx(
            f"{prefix}-interest-2026-08-31",
            sav,
            c.interest_minor,
            DEMO_DATE,
            "INTEREST PAYMENT",
            "interest",
            flags=("after_stale_snapshot",),
        ),
    ]
    month = c.financing_date.month + 1
    while month <= DEMO_DATE.month:
        day = date(2026, month, c.financing_payment_day)
        txs.append(
            Tx(
                f"{prefix}-financing-pay-{day}",
                chk,
                c.financing_payment_minor,
                day,
                c.financing_payment_description,
                "financing_repayment",
            )
        )
        month += 1
    for when, amount, description in c.unresolvable:
        txs.append(
            Tx(
                f"{prefix}-unresolvable-{when}",
                chk,
                amount,
                when,
                description,
                "unresolvable",
                flags=("unresolvable",),
            )
        )
    pending_id = f"{prefix}-pending-{DEMO_DATE - timedelta(days=2)}"
    txs += [
        Tx(
            pending_id,
            chk,
            c.pending_minor,
            DEMO_DATE - timedelta(days=2),
            c.pending_to_posted_description,
            "supplier_payments",
            pending=True,
            flags=("pending_to_posted_pending",),
        ),
        Tx(
            f"{prefix}-posted-{DEMO_DATE - timedelta(days=1)}",
            chk,
            c.posted_minor,
            DEMO_DATE - timedelta(days=1),
            c.pending_to_posted_description,
            "supplier_payments",
            pending_transaction_id=pending_id,
            flags=("pending_to_posted_posted",),
        ),
        Tx(
            f"{prefix}-removed-{DEMO_DATE - timedelta(days=4)}",
            chk,
            c.removed_minor,
            DEMO_DATE - timedelta(days=4),
            c.removed_description,
            "operating_receipts",
            flags=("removed_by_provider",),
        ),
        Tx(
            f"{prefix}-remaining-pending-{DEMO_DATE}",
            chk,
            c.remaining_pending_minor,
            DEMO_DATE,
            c.remaining_pending_description,
            "supplier_payments",
            pending=True,
            flags=("remaining_pending",),
        ),
    ]
    return txs


def _record(tx: Tx) -> dict[str, Any]:
    return {
        "transaction_id": tx.external_id,
        "account_id": tx.account,
        "amount": minor_to_provider_amount(tx.amount_minor),
        "iso_currency_code": "USD",
        "date": tx.posted_date.isoformat(),
        "name": tx.description,
        "pending": tx.pending,
        "pending_transaction_id": tx.pending_transaction_id,
    }


def _pages(p: Profile, txs: list[Tx]) -> list[dict[str, Any]]:
    """Monthly pages of added records, then one delta page with corrections."""
    by_id = {t.external_id: t for t in txs}
    delta_added = [t for t in txs if "pending_to_posted_posted" in t.flags]
    delta_removed = [
        t for t in txs if "pending_to_posted_pending" in t.flags or "removed_by_provider" in t.flags
    ]
    july = sorted(
        (t for t in txs if t.posted_date.month == 7 and not t.flags),
        key=lambda t: (t.posted_date, t.external_id),
    )
    modified_tx = july[0]
    pages: list[dict[str, Any]] = []
    months = list(range(HISTORY_START.month, DEMO_DATE.month + 1))
    for index, month in enumerate(months):
        added = [t for t in txs if t.posted_date.month == month and t not in delta_added]
        added.sort(key=lambda t: (t.posted_date, t.external_id))
        records = [_record(t) for t in added]
        if (
            index == 3 and pages
        ):  # duplicate delivery: the previous page's last record is sent again unchanged
            records.insert(0, pages[-1]["added"][-1])
        pages.append(
            {
                "cursor": f"cursor-{p.slug}-{index}",
                "next_cursor": f"cursor-{p.slug}-{index + 1}",
                "has_more": True,
                "added": records,
                "modified": [],
                "removed": [],
            }
        )
    enriched = _record(modified_tx)
    enriched["name"] = f"{modified_tx.description} - MERCHANT VERIFIED"
    pages.append(
        {
            "cursor": f"cursor-{p.slug}-{len(months)}",
            "next_cursor": f"cursor-{p.slug}-{len(months) + 1}",
            "has_more": False,
            "added": [_record(t) for t in delta_added],
            "modified": [enriched],
            "removed": [{"transaction_id": t.external_id} for t in delta_removed],
        }
    )
    assert all(r["transaction_id"] in by_id for page in pages for r in page["added"])
    return pages


def final_state(pages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Apply pages in order: the provider's current view of every record, removed ones flagged."""
    state: dict[str, dict[str, Any]] = {}
    for page in pages:
        for record in page["added"]:
            state[record["transaction_id"]] = {**record, "removed": False}
        for record in page["modified"]:
            state[record["transaction_id"]] = {**state[record["transaction_id"]], **record}
        for record in page["removed"]:
            state[record["transaction_id"]]["removed"] = True
    return state


def _balances(p: Profile, state: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    def total(account: str, through: date, *, pending: bool) -> int:
        return sum(
            provider_amount_to_minor(r["amount"])
            for r in state.values()
            if r["account_id"] == account
            and not r["removed"]
            and r["pending"] == pending
            and date.fromisoformat(r["date"]) <= through
        )

    end_of = lambda d: datetime.combine(d, time(23, 59, 59)).isoformat() + "Z"  # noqa: E731
    chk, sav = p.checking, p.savings
    current = chk.opening_minor + total(chk.external_id, DEMO_DATE, pending=False)
    pending_outflows = sum(
        min(0, provider_amount_to_minor(r["amount"]))
        for r in state.values()
        if r["account_id"] == chk.external_id and r["pending"] and not r["removed"]
    )
    stale_as_of = p.cases.stale_snapshot_as_of
    return [
        {
            "account_id": chk.external_id,
            "kind": "current",
            "amount_minor": current,
            "as_of": end_of(DEMO_DATE),
        },
        {
            "account_id": chk.external_id,
            "kind": "available",
            "amount_minor": current + pending_outflows,
            "as_of": end_of(DEMO_DATE),
        },
        {
            "account_id": sav.external_id,
            "kind": "current",
            "amount_minor": sav.opening_minor + total(sav.external_id, stale_as_of, pending=False),
            "as_of": end_of(stale_as_of),
        },
    ]


def opaque_id(slug: str, key: str) -> str:
    """Provider-facing transaction id that reveals nothing about the case behind it."""
    return "tx-" + uuid.uuid5(FIXTURE_NAMESPACE, f"{slug}:txid:{key}").hex[:16]


def build_fixture(p: Profile) -> tuple[dict[str, Any], dict[str, Any]]:
    rng = Random(SEED + p.seed_offset)
    txs = _inject_cases(p, p.history(rng, p))
    txs.sort(key=lambda t: (t.posted_date, t.external_id))
    # Internal keys name their purpose; the provider must only ever see opaque ids.
    ids = {t.external_id: opaque_id(p.slug, t.external_id) for t in txs}
    for t in txs:
        t.external_id = ids[t.external_id]
        if t.pending_transaction_id is not None:
            t.pending_transaction_id = ids[t.pending_transaction_id]
    txs.sort(key=lambda t: (t.posted_date, t.external_id))
    pages = _pages(p, txs)
    state = final_state(pages)
    provider = {
        "provider": "synthetic",
        "generator_version": GENERATOR_VERSION,
        "seed": SEED + p.seed_offset,
        "demo_date": DEMO_DATE.isoformat(),
        "history_start": HISTORY_START.isoformat(),
        "sign_convention": "positive amount = money leaving the account (Plaid style)",
        "business": {"slug": p.slug, "name": p.name, "industry": p.industry},
        "item": {"external_item_id": f"item-{p.slug}-001"},
        "accounts": [
            {
                "external_account_id": a.external_id,
                "name": a.name,
                "type": a.type,
                "mask": a.mask,
                "opening_balance_minor": a.opening_minor,
                "opening_date": HISTORY_START.isoformat(),
            }
            for a in (p.checking, p.savings)
        ],
        "pages": pages,
        "balances": _balances(p, state),
        "assumptions": list(p.assumptions),
    }
    by_id = {t.external_id: t for t in txs}
    posted_final = {k: r for k, r in state.items() if not r["removed"] and not r["pending"]}
    monthly: dict[str, dict[str, int]] = {}
    for key, record in posted_final.items():
        tx = by_id[key]
        bucket = monthly.setdefault(tx.posted_date.strftime("%Y-%m"), {})
        bucket[tx.category] = bucket.get(tx.category, 0) + provider_amount_to_minor(
            record["amount"]
        )
    evaluation = {
        "business": p.slug,
        "generator_version": GENERATOR_VERSION,
        "labels": {t.external_id: {"category": t.category, "flags": list(t.flags)} for t in txs},
        "cases": {
            "transfer_pair": [t.external_id for t in txs if "transfer_pair" in t.flags],
            "ambiguous_deposit": next(t.external_id for t in txs if "ambiguous_deposit" in t.flags),
            "financing_inflow": next(t.external_id for t in txs if "financing_inflow" in t.flags),
            "unresolvable": [t.external_id for t in txs if "unresolvable" in t.flags],
            "duplicate_delivery": pages[3]["added"][0]["transaction_id"],
            "pending_to_posted": {
                "pending": next(
                    t.external_id for t in txs if "pending_to_posted_pending" in t.flags
                ),
                "posted": next(t.external_id for t in txs if "pending_to_posted_posted" in t.flags),
            },
            "removed_by_provider": next(
                t.external_id for t in txs if "removed_by_provider" in t.flags
            ),
            "modified_by_provider": pages[-1]["modified"][0]["transaction_id"],
            "remaining_pending": next(t.external_id for t in txs if "remaining_pending" in t.flags),
            "stale_snapshot_account": p.savings.external_id,
        },
        "expected": {
            "posted_transaction_count": len(posted_final),
            "closing_balances_minor": {
                b["account_id"] + ":" + b["kind"]: b["amount_minor"] for b in provider["balances"]
            },
            "monthly_totals_minor_by_category": dict(sorted(monthly.items())),
        },
    }
    return provider, evaluation


def build_all() -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    return {p.slug: build_fixture(p) for p in PROFILES}
