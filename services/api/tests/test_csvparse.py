"""Deterministic CSV parsing: type checks, mapping proposals, money and date handling, flags."""

import pytest

from app.imports.csvparse import (
    MAX_ROWS,
    Mapping,
    UnsupportedFileError,
    date_formats_matching,
    inspect_bytes,
    normalize,
    parse_money,
    propose_mapping,
    summarize,
)

SIGNED = b"""date,description,amount,balance,currency,transaction_id
2026-01-01,LEASE PAYMENT,-3500.00,26500.00,USD,r1
2026-01-03,CARD SETTLEMENT,3307.49,29807.49,USD,r2
2026-01-03,CARD SETTLEMENT,3307.49,33114.98,USD,r3
2026-01-05,"SUPPLIER, INC",-2420.68,30694.30,USD,r4
"""


def pipeline(data: bytes, mapping: Mapping | None = None):
    table = inspect_bytes(data)
    mapping = mapping or propose_mapping(table)
    rows = normalize(table, mapping)
    return table, mapping, rows, summarize(rows, mapping)


def test_signed_file_maps_and_totals_known_answer() -> None:
    table, mapping, rows, summary = pipeline(SIGNED)
    assert mapping.date == "date" and mapping.description == "description"
    assert mapping.amount == "amount" and mapping.balance == "balance"
    assert mapping.reference == "transaction_id" and mapping.date_format == "iso"
    assert mapping.sign_convention == "signed_inflow_positive" and mapping.flags == []
    assert [r.amount_minor for r in rows] == [-350000, 330749, 330749, -242068]
    assert rows[3].description == "SUPPLIER, INC"  # quoted comma survives
    assert summary["credits_minor"] == 661498 and summary["debits_minor"] == 592068
    assert summary["period_start"] == "2026-01-01" and summary["period_end"] == "2026-01-05"
    assert summary["balance"] == {
        "opening_minor": 3000000,
        "closing_minor": 3069430,
        "period_start": "2026-01-01",
        "period_end": "2026-01-05",
        "breaks": 0,
        "reconciles": True,
    }
    # identical date/amount/description with distinct references are not flagged as repeats
    assert "repeated_in_file" not in rows[1].flags and "repeated_in_file" not in rows[2].flags


def test_repeats_without_references_are_flagged_not_dropped() -> None:
    data = (
        b"date,description,amount\n"
        b"2026-02-01,FINANCE PAYMENT,-650.00\n"
        b"2026-02-01,FINANCE PAYMENT,-650.00\n"
    )
    _, mapping, rows, summary = pipeline(data)
    assert len(rows) == 2 and all("repeated_in_file" in r.flags for r in rows)
    assert summary["blocking_rows"] == 0 and summary["debits_minor"] == 130000
    assert "sign_unverified" in mapping.flags  # no balance column to verify the sign against


def test_debit_credit_columns_and_alternate_delimiter() -> None:
    data = (
        b"Posted;Memo;Withdrawal;Deposit\n01/02/2026;RENT;1,200.00;\n03/04/2026;DEPOSIT;;2,000.00\n"
    )
    _, mapping, rows, summary = pipeline(data)
    assert mapping.sign_convention == "debit_credit" and mapping.debit == "Withdrawal"
    assert mapping.credit == "Deposit" and mapping.date_format == "mdy"
    assert "ambiguous_date_format" in mapping.flags  # 01/02 could be DMY as well
    assert [r.amount_minor for r in rows] == [-120000, 200000]
    assert summary["net_minor"] == 80000


def test_ambiguous_dates_are_resolved_only_by_explicit_mapping() -> None:
    data = b"date,description,amount\n03/04/2026,A,-1.00\n05/06/2026,B,2.00\n"
    table = inspect_bytes(data)
    assert set(date_formats_matching(["03/04/2026", "05/06/2026"])) >= {"mdy", "dmy"}
    dmy = Mapping.from_json(
        {
            "columns": {"date": "date", "description": "description", "amount": "amount"},
            "date_format": "dmy",
        },
        table.header,
    )
    assert [r.posted_date for r in normalize(table, dmy)] == ["2026-04-03", "2026-06-05"]


def test_inverted_sign_detected_from_running_balance() -> None:
    data = (
        b"date,description,amount,balance\n"
        b"2026-01-01,OPEN,100.00,900.00\n"
        b"2026-01-02,DEPOSIT,-50.00,950.00\n"
    )
    _, mapping, rows, _ = pipeline(data)
    assert mapping.sign_convention == "signed_outflow_positive"
    assert [r.amount_minor for r in rows] == [-10000, 5000]
    assert all("balance_break" not in r.flags for r in rows)


# ----------------------------------------- an export whose balance moves once a day --
# Plaid-shaped exports carry the day's closing balance beside every transaction on that day, and
# put outflows positive. Row by row the balance never moves, so the per-row check can say
# nothing; day over day it moves by the day's own total, so the sign is still in the file.
DAILY_BALANCE = (
    (
        b"Date,Transaction ID,Transaction Name,Merchant Name,Amount,Categories,"
        b"Current Balance,Calculated Daily Balance\n"
    )
    + b"""\
2026-01-01,t1,CARD PURCHASE,Shop,40.00,"Shops, Food",5000.00,900.00
2026-01-01,t2,ATM DEPOSIT,,-100.00,Transfer,5000.00,900.00
2026-01-02,t3,SUPPLIER ACH,US Foods,30.00,Food,5000.00,870.00
2026-01-03,t4,RENT,Landlord,20.00,Rent,5000.00,850.00
2026-01-04,t5,CARD SETTLEMENT,,-60.00,Transfer,5000.00,910.00
2026-01-05,t6,UTILITIES,Power Co,10.00,Utilities,5000.00,900.00
2026-01-06,t7,PAYROLL,,25.00,Payroll,5000.00,875.00
"""
)


def test_a_daily_balance_verifies_the_sign_the_per_row_check_cannot() -> None:
    _, mapping, rows, _ = pipeline(DAILY_BALANCE)
    # The column that actually moves is chosen over the one repeating an account total.
    assert mapping.balance == "Calculated Daily Balance"
    assert mapping.sign_convention == "signed_outflow_positive"
    assert "sign_unverified" not in mapping.flags
    # Positive in the file is money leaving, so it is negative once normalized.
    assert [r.amount_minor for r in rows] == [-4000, 10000, -3000, -2000, 6000, -1000, -2500]


def test_the_description_is_a_name_not_an_id_whatever_the_column_order() -> None:
    """`Transaction ID` sits left of `Transaction Name` and both partially match a description."""
    _, mapping, rows, _ = pipeline(DAILY_BALANCE)
    assert mapping.description == "Transaction Name"
    assert mapping.reference == "Transaction ID"
    assert rows[0].description == "CARD PURCHASE"


def test_too_few_days_to_agree_stays_unverified_rather_than_guessing() -> None:
    """Two days point the same way, which is not enough days to call it."""
    data = (
        b"Date,Description,Amount,Daily Balance\n"
        b"2026-01-01,A,40.00,900.00\n"
        b"2026-01-01,B,30.00,900.00\n"
        b"2026-01-02,C,20.00,830.00\n"
        b"2026-01-02,D,50.00,830.00\n"
    )
    _, mapping, rows, _ = pipeline(data)
    assert "sign_unverified" in mapping.flags
    assert mapping.sign_convention == "signed_inflow_positive"  # the default, declared as unsure
    assert [r.amount_minor for r in rows] == [4000, 3000, 2000, 5000]


def test_a_balance_that_never_moves_is_not_used_to_verify_anything() -> None:
    data = (
        b"date,description,amount,current balance\n"
        b"2026-01-01,A,-40.00,5000.00\n"
        b"2026-01-02,B,-30.00,5000.00\n"
    )
    _, mapping, _, _ = pipeline(data)
    assert mapping.balance is None and "sign_unverified" in mapping.flags


def test_balance_break_and_blocking_rows_are_flagged() -> None:
    data = (
        b"date,description,amount,balance\n"
        b"2026-01-01,A,-10.00,90.00\n"
        b"2026-01-02,B,5.00,99.00\n"  # 90 + 5 != 99
        b"not a date,C,1.00,100.00\n"
        b"2026-01-04,,1.00,101.00\n"
        b"2026-01-05,E,0.00,101.00\n"
        b"2026-01-06,F,abc,101.00\n"
    )
    _, _, rows, summary = pipeline(data)
    assert "balance_break" in rows[1].flags
    assert "invalid_date" in rows[2].flags and rows[2].blocking
    assert "missing_description" in rows[3].flags and rows[3].blocking
    assert "zero_amount" in rows[4].flags and rows[4].blocking
    assert "invalid_amount" in rows[5].flags and rows[5].blocking
    assert summary["blocking_rows"] == 4 and summary["balance"]["reconciles"] is False


@pytest.mark.parametrize(
    ("text", "minor"),
    [
        ("1,234.56", 123456),
        ("-1,234.56", -123456),
        ("(1,234.56)", -123456),
        ("1234.56-", -123456),
        ("$99.10", 9910),
        ("12.00 CR", 1200),
        ("12.00 DR", -1200),
        ("+7", 700),
        (".5", 50),
    ],
)
def test_money_forms(text: str, minor: int) -> None:
    assert parse_money(text) == (minor, None)


def test_money_precision_and_garbage() -> None:
    assert parse_money("1.005") == (100, "amount_precision")  # quantized half-even, flagged
    assert parse_money("12abc") == (None, "invalid_amount")
    assert parse_money("") == (None, "invalid_amount")


def test_non_usd_currency_blocks_the_row() -> None:
    data = b"date,description,amount,currency\n2026-01-01,A,-1.00,EUR\n2026-01-02,B,2.00,usd\n"
    _, _, rows, _ = pipeline(data)
    assert "unsupported_currency" in rows[0].flags and rows[0].blocking
    assert rows[1].currency == "USD" and not rows[1].blocking


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "empty_file"),
        (b"%PDF-1.7 ...", "pdf_not_supported"),
        (b"PK\x03\x04zipzip", "spreadsheet_not_supported"),
        (b"date,amount\n\x00\x01", "binary_file"),
        (b"just one column\n1\n2\n", "no_delimiter"),
        (b",x\n1,2\n", "no_header"),
        (b"date,amount\n", "no_rows"),
        (b"date,amount,date\n2026-01-01,1,2\n", "duplicate_headers"),
        (b"date,amount\n2026-01-01,1,extra\n", "ragged_rows"),
    ],
)
def test_unsupported_inputs_explain_themselves(data: bytes, code: str) -> None:
    with pytest.raises(UnsupportedFileError) as excinfo:
        inspect_bytes(data)
    assert excinfo.value.code == code


def test_row_bound_is_enforced() -> None:
    body = b"".join(b"2026-01-01,x,1.00\n" for _ in range(MAX_ROWS + 1))
    with pytest.raises(UnsupportedFileError) as excinfo:
        inspect_bytes(b"date,description,amount\n" + body)
    assert excinfo.value.code == "too_many_rows"


def test_bom_and_cp1252_are_accepted() -> None:
    table = inspect_bytes(b"\xef\xbb\xbfdate,description,amount\n2026-01-01,CAF\xc3\x89,1.00\n")
    assert table.header[0] == "date" and table.rows[0][1] == "CAFÉ"
    table = inspect_bytes(b"date,description,amount\n2026-01-01,CAF\xc9,1.00\n")
    assert table.encoding == "cp1252" and table.rows[0][1] == "CAFÉ"


def test_mapping_from_json_validates() -> None:
    header = ["date", "description", "amount"]
    with pytest.raises(ValueError):
        Mapping.from_json(
            {"columns": {"date": "date", "description": "nope", "amount": "amount"}}, header
        )
    with pytest.raises(ValueError):
        Mapping.from_json({"columns": {"date": "date", "description": "description"}}, header)
    with pytest.raises(ValueError):
        Mapping.from_json(
            {
                "columns": {"date": "date", "description": "description", "amount": "amount"},
                "date_format": "x",
            },
            header,
        )
    good = Mapping.from_json(
        {
            "columns": {"date": "date", "description": "description", "amount": "amount"},
            "sign_convention": "signed_outflow_positive",
        },
        header,
    )
    assert good.sign_convention == "signed_outflow_positive"
