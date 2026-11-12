"""Text-PDF extraction on the committed fixtures: supported layouts, refusals, provenance."""

import json
from pathlib import Path

import pytest

from app.imports import csvparse, pdfparse
from app.imports.budget import Deadline, ProcessingTimeoutError
from app.imports.csvparse import UnsupportedFileError

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "statements"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())


def pipeline(name: str):
    data = (FIXTURES / name).read_bytes()
    table = pdfparse.build_table(data)
    mapping = csvparse.propose_mapping(table)
    rows = csvparse.normalize(table, mapping)
    return table, mapping, rows, csvparse.summarize(rows, mapping, table.statement)


def test_layout_a_running_balance_with_wrapped_lines_and_repeated_headers() -> None:
    exp = EXPECTED["layout-a-running-balance.pdf"]
    table, mapping, rows, summary = pipeline("layout-a-running-balance.pdf")
    assert table.header == ["date", "description", "amount", "balance"] and table.source == "pdf"
    assert len(rows) == exp["rows"] == 24  # repeated page headers and summary blocks add nothing
    assert (
        summary["credits_minor"] == exp["credits_minor"]
        and summary["debits_minor"] == exp["debits_minor"]
    )
    assert (
        summary["reconciliation"]["status"] == "matched"
        and summary["reconciliation"]["source"] == "statement_totals"
    )
    assert (
        summary["balance"]["opening_minor"] == exp["opening_minor"]
        and summary["balance"]["closing_minor"] == exp["closing_minor"]
    )
    assert (
        table.statement["period_start"] == "2026-02-01"
        and table.statement["account_last4"] == "4410"
    )
    assert table.statement["transaction_count"] == 24 and summary["blocking_rows"] == 0
    wrapped = next(
        r for r in rows if r.description.startswith("NORTHWIND PARTS DISTRIBUTION INVOICE")
    )
    assert wrapped.description == "NORTHWIND PARTS DISTRIBUTION INVOICE 4471 SPRING TIRES AND TUBES"
    assert rows[0].source_locator == "page 1 line 9" and rows[0].page_number == 1
    assert any(r.page_number == 2 for r in rows)
    loans = [
        r
        for r in rows
        if r.description == "EQUIPMENT LOAN PAYMENT" and r.posted_date == "2026-02-10"
    ]
    assert len(loans) == 2 and all("repeated_in_file" in r.flags for r in loans)
    assert mapping.sign_convention == "signed_inflow_positive" and mapping.flags == []


def test_layout_b_debit_credit_with_us_dates_and_footer_balance() -> None:
    exp = EXPECTED["layout-b-debit-credit.pdf"]
    table, mapping, rows, summary = pipeline("layout-b-debit-credit.pdf")
    assert table.header == ["date", "description", "debit", "credit", "balance"]
    assert mapping.sign_convention == "debit_credit" and mapping.date_format == "mdy"
    assert mapping.flags == []  # days above 12 make month/day/year the only reading
    assert len(rows) == exp["rows"]
    assert (
        summary["credits_minor"] == exp["credits_minor"]
        and summary["debits_minor"] == exp["debits_minor"]
    )
    assert (
        table.statement["opening_minor"] == exp["opening_minor"]
        and table.statement["closing_minor"] == exp["closing_minor"]
    )
    assert table.statement["account_last4"] == "7731"
    assert summary["reconciliation"]["status"] == "matched"
    assert "balance_break" not in summary["flag_counts"]


def test_layout_c_without_balances_reports_reconciliation_unavailable() -> None:
    table, _, rows, summary = pipeline("layout-c-no-balance.pdf")
    assert table.header == ["date", "description", "amount"]
    assert len(rows) == EXPECTED["layout-c-no-balance.pdf"]["rows"]
    unreadable = [r for r in rows if "invalid_amount" in r.flags]
    assert (
        len(unreadable) == 1 and unreadable[0].raw["amount"] == "PENDING"
    )  # kept, blocking, never dropped
    assert summary["blocking_rows"] == 1
    assert summary["reconciliation"]["status"] == "unavailable" and summary["balance"] is None


def test_missing_transaction_line_is_detected_by_reconciliation() -> None:
    exp = EXPECTED["layout-a-missing-line.pdf"]
    table, _, rows, summary = pipeline("layout-a-missing-line.pdf")
    assert len(rows) == exp["rows"] == 23
    problems = set(summary["reconciliation"]["problems"])
    assert summary["reconciliation"]["status"] == "mismatch"
    assert {
        "rows_do_not_bridge_statement_balances",
        "row_count_differs_from_statement",
        "running_balance_breaks",
    } <= problems
    assert summary["flag_counts"]["balance_break"] == 1  # the break sits where the line is missing


def test_wrong_printed_closing_balance_is_a_mismatch_not_a_correction() -> None:
    _, _, rows, summary = pipeline("layout-a-balance-mismatch.pdf")
    assert summary["reconciliation"]["status"] == "mismatch"
    assert "rows_do_not_bridge_statement_balances" in summary["reconciliation"]["problems"]
    assert "running_balance_differs_from_statement_closing" in summary["reconciliation"]["problems"]
    assert len(rows) == 24  # nothing invented to make it balance


def test_instruction_like_text_stays_a_description() -> None:
    exp = EXPECTED["layout-a-injection.pdf"]
    _, _, rows, summary = pipeline("layout-a-injection.pdf")
    injected = [r for r in rows if r.description == exp["injected_description"]]
    assert len(injected) == 1 and injected[0].amount_minor == -1200
    assert summary["reconciliation"]["status"] == "matched"


@pytest.mark.parametrize(
    "name",
    ["credit-card.pdf", "too-many-pages.pdf", "image-only.pdf", "encrypted.pdf", "truncated.pdf"],
)
def test_unsupported_pdfs_are_refused_with_the_expected_code(name: str) -> None:
    with pytest.raises(UnsupportedFileError) as excinfo:
        pdfparse.build_table((FIXTURES / name).read_bytes())
    assert excinfo.value.code == EXPECTED[name]["expect"]
    assert str(excinfo.value)  # a message the owner can act on


def test_non_pdf_bytes_and_layoutless_text_are_refused() -> None:
    with pytest.raises(UnsupportedFileError) as excinfo:
        pdfparse.build_table(b"date,amount\n2026-01-01,1.00\n")
    assert excinfo.value.code == "not_pdf"
    from app.synthetic.pdfwriter import Document

    doc = Document()
    page = doc.page()
    for i in range(6):
        page.text(36, 700 - 14 * i, f"Line {i} of ordinary prose with no table at all, just words.")
    with pytest.raises(UnsupportedFileError) as excinfo:
        pdfparse.build_table(doc.render())
    assert excinfo.value.code == "pdf_layout_unsupported" and "First lines read" in str(
        excinfo.value
    )


def test_deadline_stops_extraction_between_pages() -> None:
    data = (FIXTURES / "layout-a-running-balance.pdf").read_bytes()
    with pytest.raises(ProcessingTimeoutError) as excinfo:
        pdfparse.build_table(data, Deadline(seconds=0))
    assert excinfo.value.stage == "page 1"


def test_pack_layout_family_is_covered_by_fixtures_not_by_real_bank_formats() -> None:
    """Coverage is the fixture list; nothing here validates a real bank's export."""
    supported = [n for n, m in EXPECTED.items() if m.get("expect") == "supported"]
    assert len(supported) >= 3 and all((FIXTURES / n).exists() for n in supported)


def _sheet(page, rows, *, top=600.0, header_y=619.0, step=17.0):
    """A minimal transaction table on one page: header line, then date/description/amount rows."""
    for x, label in ((40, "Date"), (101, "Description"), (400, "Amount")):
        page.text(x, header_y, label, 8, bold=True)
    y = top
    for date, description, amount in rows:
        page.text(40, y, date)
        page.text(101, y, description)
        page.text_right(470, y, amount)
        y -= step
    return y + step  # the last row's baseline


def test_page_footer_in_the_description_column_never_joins_a_transaction() -> None:
    exp = EXPECTED["layout-a-page-footers.pdf"]
    table, _, rows, summary = pipeline("layout-a-page-footers.pdf")
    assert len(rows) == exp["rows"] == 24  # the footer adds no rows and removes none
    for line in exp["footer_lines"]:
        assert not any(line[:20] in (r.description or "") for r in rows)
    assert rows[1].description == "CARD PROCESSOR SETTLEMENT"  # last row of page 1, unpolluted
    assert rows[-1].description == "INTEREST PAYMENT"  # last row of page 2, unpolluted
    wrapped = next(r for r in rows if r.description.startswith("NORTHWIND PARTS"))
    assert wrapped.description == "NORTHWIND PARTS DISTRIBUTION INVOICE 4471 SPRING TIRES AND TUBES"
    assert summary["reconciliation"]["status"] == "matched"
    assert summary["credits_minor"] == exp["credits_minor"] and summary["blocking_rows"] == 0
    assert table.statement["page_count"] == 2


def test_a_footer_far_below_the_table_is_not_a_continuation() -> None:
    """One page, so nothing repeats: distance alone has to reject the footer."""
    from app.synthetic.pdfwriter import Document

    doc = Document()
    page = doc.page()
    last_y = _sheet(
        page,
        [
            ("2026-02-02", "SHOP LEASE PAYMENT", "-1,800.00"),
            ("2026-02-03", "CARD PROCESSOR SETTLEMENT", "2,145.50"),
            ("2026-02-05", "UTILITY ELECTRIC", "-214.60"),
        ],
    )
    page.text(101, last_y - 34, "Continued on next page. Member FDIC.")
    page.text(101, last_y - 46, "Questions? Call the number on your card.")
    table = pdfparse.build_table(doc.render())
    assert [r[1] for r in table.rows] == [
        "SHOP LEASE PAYMENT",
        "CARD PROCESSOR SETTLEMENT",
        "UTILITY ELECTRIC",
    ]


def test_a_footer_one_line_below_the_table_is_recognized_by_repeating_on_every_page() -> None:
    """The hard case: the footer sits exactly where a wrapped description would."""
    from app.synthetic.pdfwriter import Document

    doc = Document()
    for dates in (("2026-02-02", "2026-02-03"), ("2026-03-02", "2026-03-03")):
        page = doc.page()
        last_y = _sheet(
            page,
            [(dates[0], "SHOP LEASE PAYMENT", "-1,800.00"), (dates[1], "PAYROLL", "-3,200.00")],
        )
        page.text(101, last_y - 17, "Member FDIC. Continued on the next page.")
    table = pdfparse.build_table(doc.render())
    assert [r[1] for r in table.rows] == [
        "SHOP LEASE PAYMENT",
        "PAYROLL",
        "SHOP LEASE PAYMENT",
        "PAYROLL",
    ]
    assert table.pages == [1, 1, 2, 2]


def test_a_continuation_cannot_attach_to_a_transaction_on_another_page() -> None:
    """Vertical positions are page-local; a line on page two is never below page one's rows."""
    from app.synthetic.pdfwriter import Document

    doc = Document()
    first = doc.page()
    last_y = _sheet(
        first,
        [
            ("2026-02-02", "SHOP LEASE PAYMENT", "-1,800.00"),
            ("2026-02-03", "PAYROLL", "-3,200.00"),
            ("2026-02-05", "UTILITY ELECTRIC", "-214.60"),
        ],
    )
    second = doc.page()
    for x, label in ((40, "Date"), (101, "Description"), (400, "Amount")):
        second.text(x, 619, label, 8, bold=True)
    # One line below page two's header, twelve points under where page one's table ended:
    # a gap that would read as a wrapped description if the page were ignored.
    second.text(101, last_y - 12, "AND SOME MORE TEXT ENTIRELY")
    table = pdfparse.build_table(doc.render())
    assert [r[1] for r in table.rows] == [
        "SHOP LEASE PAYMENT",
        "PAYROLL",
        "UTILITY ELECTRIC",
    ]


def test_a_transaction_shaped_line_that_cannot_be_read_is_kept_for_review() -> None:
    """Date, description and amount all present but unreadable: flagged, never dropped."""
    from app.synthetic.pdfwriter import Document

    doc = Document()
    page = doc.page()
    for x, label in ((40, "Date"), (101, "Description"), (400, "Amount")):
        page.text(x, 619, label, 8, bold=True)
    page.text(40, 600, "2026-02-02")
    page.text(101, 600, "SHOP LEASE PAYMENT")
    page.text_right(470, 600, "-1,800.00")
    page.text(40, 583, "31/02/2026")  # no such day
    page.text(101, 583, "CARD PROCESSOR SETTLEMENT")
    page.text_right(470, 583, "SEE NOTE 4")
    page.text(40, 566, "2026-02-05")
    page.text(101, 566, "UTILITY ELECTRIC")
    page.text_right(470, 566, "-214.60")
    table = pdfparse.build_table(doc.render())
    assert len(table.rows) == 3
    mapping = csvparse.propose_mapping(table)
    rows = csvparse.normalize(table, mapping)
    unreadable = [r for r in rows if "invalid_amount" in r.flags or "invalid_date" in r.flags]
    assert len(unreadable) == 1 and unreadable[0].raw["description"] == "CARD PROCESSOR SETTLEMENT"
    assert csvparse.summarize(rows, mapping, table.statement)["blocking_rows"] == 1


def test_a_page_whose_rows_have_no_column_header_is_refused_not_dropped() -> None:
    from app.synthetic.pdfwriter import Document

    doc = Document()
    _sheet(
        doc.page(),
        [
            ("2026-02-02", "SHOP LEASE PAYMENT", "-1,800.00"),
            ("2026-02-03", "PAYROLL", "-3,200.00"),
        ],
    )
    second = doc.page()
    second.text(40, 600, "2026-03-02")
    second.text(101, 600, "SHOP LEASE PAYMENT")
    second.text_right(470, 600, "-1,800.00")
    with pytest.raises(UnsupportedFileError) as excinfo:
        pdfparse.build_table(doc.render())
    assert excinfo.value.code == "pdf_layout_unsupported" and "Page 2" in str(excinfo.value)
