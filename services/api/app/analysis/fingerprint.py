"""The input fingerprint: one hash that decides whether a stored result is still current.

Everything that can change a figure goes in — the calculation versions, the analysis date,
every counted transaction with its revision, every balance and every applied override — so a
confirmed import, a removed row, a corrected category and a rules change are all the same kind
of event and none of them needs its own trigger. A stored AI result carries the fingerprint it
was written against; a later read recomputes and compares.

It is computed from rows the analysis read has already loaded, so it costs no extra query, and
it is deliberately not stored on the transactions: a fingerprint that lived in a column would
need something to keep it up to date, which is the class of bug this avoids.
"""

import hashlib
import json
from typing import Any

from app.analysis import metrics

FINGERPRINT_VERSION = "fp-1"


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute(
    *,
    versions: dict[str, str],
    as_of: str,
    transactions: list[metrics.Txn],
    balances: list[dict[str, Any]],
    overrides: dict[str, str],
) -> str:
    """A stable hash of every input that can move a number on the dashboard."""
    counted = sorted(
        (
            t.id,
            t.revision,
            t.status,
            t.posted_date[:10],
            t.amount_minor,
            t.account_id,
            t.description,
        )
        for t in metrics.within(transactions, as_of)
    )
    known_balances = sorted(
        (b["account_id"], b["kind"], b["provenance"], int(b["amount_minor"]), b["as_of"][:10])
        for b in balances
        if b["as_of"][:10] <= as_of
    )
    material = {
        "fp": FINGERPRINT_VERSION,
        "versions": dict(sorted(versions.items())),
        "as_of": as_of,
        "transactions": counted,
        "balances": known_balances,
        "overrides": sorted(overrides.items()),
    }
    return hashlib.sha256(_canonical(material).encode()).hexdigest()
