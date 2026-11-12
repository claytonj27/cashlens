"""Replay, overlap and conflict detection for staged rows against existing transactions.

Matching never merges anything on its own. It attaches review flags and a
default decision, and it says whether the owner must decide before anything
is confirmed. The policy (ADR 0009):

- Identity evidence: same account and the same trusted source reference on
  both sides. Identical values are `already_imported` and default to linking
  the row to the existing transaction as extra provenance; different values
  are `conflicting_revision` and need an owner decision (keep, link or
  replace).
- Shape evidence only (date, amount, normalized description) is never proof:
  such rows are `duplicate_candidate` rows that need an owner decision. They
  are paired with existing transactions one to one, so k identical-looking
  rows in a file against m identical-looking existing transactions give
  min(k, m) candidates and k - m rows treated as new. Legitimate repeated
  payments therefore survive in both directions.
- Identical rows inside one file are `repeated_in_file`: informational, kept.
- A statement whose rows all match an overlapping period is reported as a
  likely duplicate statement so the owner can link every candidate in one
  action; nothing is linked or dropped until they do.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app.imports.csvparse import NormalizedRow

NEEDS_DECISION = "needs_decision"
WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class ExistingTransaction:
    id: str
    external_id: str | None
    posted_date: str
    amount_minor: int
    description: str


@dataclass
class MatchOutcome:
    flags: list[str] = field(default_factory=list)
    decision: str = "keep"  # keep | exclude | link_existing | replace_existing
    linked_transaction_id: str | None = None
    conflict: dict[str, Any] | None = None  # existing values shown for review

    @property
    def needs_decision(self) -> bool:
        return NEEDS_DECISION in self.flags


def _norm(description: str | None) -> str:
    return WHITESPACE.sub(" ", (description or "").strip().lower())


def _shape(
    date: str | None, amount: int | None, description: str | None
) -> tuple[str | None, int | None, str]:
    return (date, amount, _norm(description))


def match_rows(
    rows: list[NormalizedRow], existing: list[ExistingTransaction]
) -> dict[int, MatchOutcome]:
    """Per row index: flags, default decision and any existing transaction to show."""
    by_ref = {t.external_id: t for t in existing if t.external_id}
    by_shape: dict[tuple[str | None, int | None, str], list[ExistingTransaction]] = {}
    for t in sorted(existing, key=lambda t: t.id):
        by_shape.setdefault(_shape(t.posted_date, t.amount_minor, t.description), []).append(t)
    paired: set[str] = set()
    outcomes: dict[int, MatchOutcome] = {}
    for row in rows:
        outcome = MatchOutcome()
        if row.external_ref and row.external_ref in by_ref:
            target = by_ref[row.external_ref]
            paired.add(target.id)
            same = _shape(target.posted_date, target.amount_minor, target.description) == _shape(
                row.posted_date, row.amount_minor, row.description
            )
            outcome.linked_transaction_id = target.id
            if same:
                outcome.flags = ["already_imported"]
                outcome.decision = "link_existing"
            else:
                outcome.flags = ["conflicting_revision", NEEDS_DECISION]
                outcome.decision = "keep"
                outcome.conflict = _describe(target, kind="conflict")
        elif row.posted_date and row.amount_minor is not None and row.description:
            candidates = [
                t
                for t in by_shape.get(
                    _shape(row.posted_date, row.amount_minor, row.description), []
                )
                if t.id not in paired
            ]
            if candidates:
                target = candidates[0]
                paired.add(target.id)
                outcome.flags = ["duplicate_candidate", NEEDS_DECISION]
                outcome.decision = "keep"
                outcome.linked_transaction_id = target.id
                outcome.conflict = _describe(
                    target, kind="candidate", remaining=len(candidates) - 1
                )
        outcomes[row.row_index] = outcome
    return outcomes


def _describe(target: ExistingTransaction, *, kind: str, remaining: int = 0) -> dict[str, Any]:
    return {
        "kind": kind,
        "transaction_id": target.id,
        "posted_date": target.posted_date,
        "amount_minor": target.amount_minor,
        "description": target.description,
        "external_id": target.external_id,
        "other_candidates": remaining,
    }


def period_overlap(existing: list[ExistingTransaction], start: str | None, end: str | None) -> int:
    """How many existing transactions fall inside the file's period."""
    if not start or not end:
        return 0
    return sum(1 for t in existing if start <= t.posted_date <= end)


def assess_statement(
    rows: list[NormalizedRow],
    outcomes: dict[int, MatchOutcome],
    existing: list[ExistingTransaction],
    period_start: str | None,
    period_end: str | None,
) -> dict[str, Any]:
    """Is this whole statement probably already in the account? Reported, never acted on."""
    usable = [r for r in rows if r.posted_date and r.amount_minor is not None and r.description]
    candidates = sum(
        1
        for r in usable
        if "duplicate_candidate" in outcomes[r.row_index].flags
        or "already_imported" in outcomes[r.row_index].flags
    )
    in_period = period_overlap(existing, period_start, period_end)
    likely = (
        bool(usable) and len(usable) >= 3 and candidates == len(usable) and in_period == len(usable)
    )
    return {
        "rows_with_values": len(usable),
        "matched_rows": candidates,
        "existing_in_period": in_period,
        "undecided_rows": sum(1 for r in rows if outcomes[r.row_index].needs_decision),
        "likely_duplicate_statement": likely,
    }
