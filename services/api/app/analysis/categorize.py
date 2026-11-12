"""Deterministic categorisation of bank descriptions. No model calls, no learning.

Every rule is a keyword match on the description, applied in order, and every rule is
published to the interface so an owner can see exactly why a transaction landed where it did.
A description that matches nothing stays `uncategorized` rather than being guessed into a
category: this milestone reports cash movement, and it does not claim to be bookkeeping.

Three activity groups sit above the categories:

* `operating` — the ordinary running of the business, and the default for anything unknown;
* `financing` — loans, credit lines and owner draws, which move cash without being trade;
* `internal_transfer` — money moved between the business's own accounts, which is not income
  or spending at all and is netted out of consolidated money in and money out.

Splitting financing and transfers out of operating is a judgement about cash, not an
accounting classification, and the interface says so.

`rules-2` adds a counterparty tier. A description whose words are all instrument words and
digits — `DEPOSIT 8842`, `ACH CREDIT 0192`, `CHECK DEPOSIT`, `WIRE IN 000348` — names no one,
so an instrument keyword is not allowed to place it and it is left uncategorised with the
reason `generic_instrument`. `ACH CREDIT HARBOR POINT HOA` names a counterparty and stays a
customer receipt. The tier is decided by whether a counterparty survives the instrument words,
not by the word itself, because the same word is generic in one row and specific in the next.
"""

import re
from dataclasses import dataclass

CATEGORY_VERSION = "rules-2"

OPERATING = "operating"
FINANCING = "financing"
TRANSFER = "internal_transfer"

# How a placement was decided, published with the rules so the dashboard can say why.
REASON_KEYWORD = "keyword"
REASON_GENERIC_INSTRUMENT = "generic_instrument"
REASON_NO_MATCH = "no_match"

REASON_CODES: dict[str, str] = {
    REASON_KEYWORD: "A published keyword appeared in the description.",
    REASON_GENERIC_INSTRUMENT: (
        "The description names only how the money moved, with no counterparty left once the "
        "instrument words and digits are removed, so it is left alone rather than guessed at."
    ),
    REASON_NO_MATCH: "No published keyword appeared in the description.",
}

# Words that describe how money moved rather than who it moved to or from. Longest first, so
# 'check deposit' is removed as one phrase before 'check' or 'deposit' can match separately.
INSTRUMENT_WORDS: tuple[str, ...] = (
    "zelle payment from",
    "zelle payment to",
    "mobile deposit",
    "check deposit",
    "venmo payment",
    "ach credit",
    "ach debit",
    "wire in",
    "wire out",
    "deposit",
    "check",
    "cash",
)


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    group: str
    # Plain words an owner can read back, matched case-insensitively on whole words.
    keywords: tuple[str, ...]
    direction: str = "either"  # inflow | outflow | either


@dataclass(frozen=True)
class Placement:
    """Where a description landed and why, so the reason can be shown and counted."""

    category: "Category"
    reason: str


# Order matters: the first match wins, so the most specific rules come first.
CATEGORIES: tuple[Category, ...] = (
    Category(
        "internal_transfer",
        "Transfers between your accounts",
        TRANSFER,
        ("transfer to savings", "transfer from checking", "online transfer", "internal transfer"),
    ),
    Category(
        "owner_draw",
        "Owner draws",
        FINANCING,
        ("owner draw", "owner distribution", "transfer to personal", "personal savings"),
    ),
    # Before customer receipts on purpose: an owner putting their own money in is not trade,
    # and it gives an owner answering "that deposit was mine" somewhere to put it.
    Category(
        "owner_contributions",
        "Owner contributions",
        FINANCING,
        ("owner contribution", "capital contribution", "owner deposit"),
        direction="inflow",
    ),
    Category(
        "financing",
        "Loans and credit",
        FINANCING,
        (
            "loan",
            "line of credit",
            "working capital",
            "finance disbursement",
            "finance payment",
            "sba",
            "advance",
            "repayment",
        ),
    ),
    Category(
        "customer_receipts",
        "Customer receipts",
        OPERATING,
        (
            "card processor settlement",
            "card settlement",
            "settlement",
            "deposit",
            "ach credit",
            "payout",
            "customer payment",
            "invoice payment",
        ),
        direction="inflow",
    ),
    Category(
        "interest",
        "Interest received",
        OPERATING,
        ("interest payment", "interest earned", "interest credit"),
        direction="inflow",
    ),
    Category(
        "payroll",
        "Payroll",
        OPERATING,
        ("payroll", "gusto", "adp", "wages", "salaries"),
        direction="outflow",
    ),
    Category(
        "contractors",
        "Contractors",
        OPERATING,
        ("contractor", "freelance", "consulting", "subcontract"),
        direction="outflow",
    ),
    # Before equipment: a service call on a machine is maintenance, not a purchase.
    Category(
        "repairs_maintenance",
        "Repairs and maintenance",
        OPERATING,
        ("repair", "maintenance", "plumbing", "hvac", "service call"),
        direction="outflow",
    ),
    # Before equipment: financing a machine on a lease is rent, not a purchase.
    Category(
        "rent_lease",
        "Rent and lease",
        OPERATING,
        ("equipment lease", "lease payment", "rent", "commons lease", "industrial rent"),
        direction="outflow",
    ),
    Category(
        "equipment",
        "Equipment purchases",
        OPERATING,
        ("equipment", "machinery", "fixtures"),
        direction="outflow",
    ),
    Category(
        "suppliers",
        "Suppliers and stock",
        OPERATING,
        (
            "food distribution",
            "distribution",
            "produce",
            "wholesale",
            "supply",
            "supplies",
            "restaurant depot",
            "ferguson",
            "fastenal",
            "parts",
            "sysco",
            "us foods",
            "home depot",
            "b and h photo",
        ),
        direction="outflow",
    ),
    Category(
        "fuel_travel",
        "Fuel and travel",
        OPERATING,
        ("shell oil", "chevron", "fuel", "gasoline", "mileage"),
        direction="outflow",
    ),
    Category(
        "utilities",
        "Utilities",
        OPERATING,
        ("utilities", "utility", "water", "electric", "gas company", "internet"),
        direction="outflow",
    ),
    Category(
        "insurance",
        "Insurance",
        OPERATING,
        ("insurance",),
        direction="outflow",
    ),
    Category(
        "software",
        "Software and subscriptions",
        OPERATING,
        (
            "cloudpos",
            "booking desk",
            "adobe",
            "google workspace",
            "squarespace",
            "zoom video",
            "software",
            "subscription",
            "monthly",
        ),
        direction="outflow",
    ),
    Category(
        "marketing",
        "Marketing",
        OPERATING,
        ("media payment", "local media", "advertis", "marketing"),
        direction="outflow",
    ),
    Category(
        "bank_fees",
        "Bank fees",
        OPERATING,
        ("service fee", "account fee", "bank fee", "overdraft"),
        direction="outflow",
    ),
    Category(
        "tax",
        "Tax payments",
        OPERATING,
        ("irs", "tax payment", "franchise tax", "sales tax"),
        direction="outflow",
    ),
)

UNCATEGORIZED = Category("uncategorized", "Uncategorised", OPERATING, ())

BY_KEY = {c.key: c for c in (*CATEGORIES, UNCATEGORIZED)}

_DIGITS = re.compile(r"\d+")
_NON_LETTERS = re.compile(r"[^a-z ]+")


def _matches(description: str, keyword: str) -> bool:
    """A keyword matches on a word boundary, so 'rent' does not fire inside 'current'."""
    return re.search(rf"(?<![a-z]){re.escape(keyword)}(?![a-z])", description) is not None


def names_a_counterparty(description: str) -> bool:
    """Whether anything but an instrument word and digits is left in the description.

    `ACH CREDIT HARBOR POINT HOA` names Harbor Point; `ACH CREDIT 0192` names nobody. Only the
    second is barred from being placed by a keyword.
    """
    text = _DIGITS.sub(" ", (description or "").lower())
    for phrase in INSTRUMENT_WORDS:
        text = re.sub(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", " ", text)
    text = _NON_LETTERS.sub(" ", text)
    return any(len(word) >= 3 for word in text.split())


def place(description: str, amount_minor: int) -> Placement:
    """The first rule whose keyword appears and whose direction fits, with the reason why."""
    text = (description or "").strip().lower()
    if not text:
        return Placement(UNCATEGORIZED, REASON_NO_MATCH)
    if not names_a_counterparty(text):
        return Placement(UNCATEGORIZED, REASON_GENERIC_INSTRUMENT)
    inflow = amount_minor > 0
    for category in CATEGORIES:
        if category.direction == "inflow" and not inflow:
            continue
        if category.direction == "outflow" and inflow:
            continue
        if any(_matches(text, word) for word in category.keywords):
            return Placement(category, REASON_KEYWORD)
    return Placement(UNCATEGORIZED, REASON_NO_MATCH)


def categorize(description: str, amount_minor: int) -> Category:
    """Where a description lands. `place` carries the reason as well."""
    return place(description, amount_minor).category


def published_rules() -> list[dict[str, object]]:
    """The rule list exactly as the interface shows it, so nothing is hidden from the owner."""
    return [
        {
            "key": c.key,
            "label": c.label,
            "group": c.group,
            "direction": c.direction,
            "keywords": list(c.keywords),
        }
        for c in (*CATEGORIES, UNCATEGORIZED)
    ]
