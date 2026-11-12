"""Duplicate policy: identity evidence links by default, shape evidence needs an owner decision."""

from app.imports.csvparse import NormalizedRow
from app.imports.matching import ExistingTransaction, assess_statement, match_rows, period_overlap


def row(index: int, date: str, amount: int, desc: str, ref: str | None = None) -> NormalizedRow:
    return NormalizedRow(index, index + 2, {}, date, desc, amount, None, ref, "USD", [])


def test_identity_evidence_links_and_conflicts_need_a_decision() -> None:
    existing = [
        ExistingTransaction("t1", "r1", "2026-05-01", -350000, "LEASE PAYMENT"),
        ExistingTransaction("t2", "r2", "2026-05-03", 370676, "CARD SETTLEMENT"),
    ]
    rows = [
        row(0, "2026-05-01", -350000, "LEASE PAYMENT", "r1"),  # identical replay
        row(1, "2026-05-03", 370000, "CARD SETTLEMENT", "r2"),  # same reference, different amount
        row(2, "2026-05-04", -100, "NEW", "r3"),
    ]
    out = match_rows(rows, existing)
    assert out[0].flags == ["already_imported"] and out[0].decision == "link_existing"
    assert out[0].linked_transaction_id == "t1" and not out[0].needs_decision
    assert out[1].flags == ["conflicting_revision", "needs_decision"] and out[1].decision == "keep"
    assert out[1].linked_transaction_id == "t2" and out[1].conflict["amount_minor"] == 370676
    assert out[2].flags == [] and out[2].decision == "keep"


def test_shape_matches_need_a_decision_and_preserve_multiplicity() -> None:
    existing = [
        ExistingTransaction("t1", None, "2026-02-10", -8500, "EQUIPMENT LOAN PAYMENT"),
        ExistingTransaction("t2", None, "2026-02-10", -8500, "EQUIPMENT LOAN PAYMENT"),
    ]
    rows = [row(i, "2026-02-10", -8500, "Equipment loan payment") for i in range(3)]
    out = match_rows(rows, existing)
    assert [o.flags for o in out.values()] == [["duplicate_candidate", "needs_decision"]] * 2 + [[]]
    assert {out[0].linked_transaction_id, out[1].linked_transaction_id} == {"t1", "t2"}
    assert out[0].conflict["other_candidates"] == 1 and out[1].conflict["other_candidates"] == 0
    assert out[2].decision == "keep" and out[2].linked_transaction_id is None  # the third is new


def test_reverse_order_a_referenced_row_can_match_an_unreferenced_transaction_by_shape_only() -> (
    None
):
    existing = [ExistingTransaction("t1", None, "2026-05-01", -350000, "LEASE PAYMENT")]
    out = match_rows([row(0, "2026-05-01", -350000, "LEASE PAYMENT", "r1")], existing)
    assert (
        out[0].flags == ["duplicate_candidate", "needs_decision"]
        and out[0].linked_transaction_id == "t1"
    )


def test_statement_assessment_reports_a_likely_duplicate_without_acting() -> None:
    existing = [
        ExistingTransaction(f"t{i}", None, f"2026-03-0{i + 1}", -100 * (i + 1), f"ROW {i}")
        for i in range(4)
    ]
    rows = [row(i, f"2026-03-0{i + 1}", -100 * (i + 1), f"row {i}") for i in range(4)]
    out = match_rows(rows, existing)
    assessment = assess_statement(rows, out, existing, "2026-03-01", "2026-03-31")
    assert assessment == {
        "rows_with_values": 4,
        "matched_rows": 4,
        "existing_in_period": 4,
        "undecided_rows": 4,
        "likely_duplicate_statement": True,
    }
    assert all(o.decision == "keep" for o in out.values())  # reported, not applied
    partial = assess_statement(
        rows[:2], match_rows(rows[:2], existing), existing, "2026-03-01", "2026-03-31"
    )
    assert partial["likely_duplicate_statement"] is False


def test_matching_never_crosses_the_supplied_history() -> None:
    assert match_rows([row(0, "2026-01-01", 1, "A", "r1")], [])[0].flags == []
    existing = [ExistingTransaction("t", None, "2026-03-05", 1, "A")]
    assert period_overlap(existing, "2026-03-01", "2026-03-31") == 1
    assert period_overlap(existing, None, "2026-04-30") == 0
