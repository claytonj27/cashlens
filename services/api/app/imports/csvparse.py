"""Deterministic CSV statement parsing.

Everything here is pure: bytes in, structured rows and flags out, no I/O and
no database. File contents are untrusted data; cells are never interpreted
as anything but text, numbers or dates, and every limit is enforced before
work proportional to the input happens.

Pipeline: `inspect_bytes` (type, size, encoding, delimiter, bounds) ->
`propose_mapping` (columns, date format, sign convention, with ambiguity
flags rather than guesses) -> `normalize` (canonical rows with per-row
flags) -> `summarize` (totals, period, balance observations, warnings).
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

# csv-2 changed three things about how a mapping is proposed, all of which change the rows a
# file produces, so the version moved with them: partial header matches are ranked by how
# specific the matched name is rather than by where the column sits; a balance column that never
# moves is not used to verify the sign; and a balance that moves once a day rather than once a
# transaction can now verify the sign from day-over-day totals.
PARSER_VERSION = "csv-2"
MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLUMNS = 50
MAX_CELL_CHARS = 500
DELIMITERS = ",;\t|"

DATE_FORMATS: dict[str, str] = {
    "iso": "%Y-%m-%d",
    "iso_slash": "%Y/%m/%d",
    "mdy": "%m/%d/%Y",
    "dmy": "%d/%m/%Y",
    "mdy_dash": "%m-%d-%Y",
    "dmy_dash": "%d-%m-%Y",
    "mdy_short": "%m/%d/%y",
    "dmy_short": "%d/%m/%y",
    "dmon_y": "%d %b %Y",
    "mon_d_y": "%b %d, %Y",
    "mon_d_y_nocomma": "%b %d %Y",
    "compact": "%Y%m%d",
}
SignConvention = Literal["signed_inflow_positive", "signed_outflow_positive", "debit_credit"]

DATE_HEADERS = (
    "date",
    "posted",
    "transaction date",
    "post date",
    "posting date",
    "booking date",
    "value date",
)
DESCRIPTION_HEADERS = (
    "description",
    "memo",
    "payee",
    "name",
    "narrative",
    "details",
    "merchant",
    "transaction",
)
AMOUNT_HEADERS = ("amount", "transaction amount", "value", "net")
DEBIT_HEADERS = ("debit", "withdrawal", "withdrawals", "money out", "paid out", "outflow", "charge")
CREDIT_HEADERS = (
    "credit",
    "deposit",
    "deposits",
    "money in",
    "paid in",
    "inflow",
    "payment received",
)
BALANCE_HEADERS = ("balance", "running balance", "closing balance", "ending balance")
REFERENCE_HEADERS = (
    "transaction_id",
    "transaction id",
    "reference",
    "ref",
    "id",
    "fitid",
    "check number",
)
CURRENCY_HEADERS = ("currency", "iso_currency_code")

MONEY_CLEAN = re.compile(r"[,\s$€£]|USD")


class UnsupportedFileError(ValueError):
    """The bytes are not a CSV statement this importer accepts. `code` is safe to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Table:
    header: list[str]
    rows: list[list[str]]
    delimiter: str
    encoding: str
    byte_size: int
    line_numbers: list[int]  # 1-based physical line of each data row
    source: str = "csv"  # csv | pdf
    locators: list[str] | None = None  # per row, e.g. "page 2 line 14"; default "line N"
    pages: list[int] | None = None  # per row page number for paged sources
    statement: dict[str, Any] | None = None  # totals and period read from the document itself

    def locator(self, index: int) -> str:
        if self.locators is not None:
            return self.locators[index]
        return f"line {self.line_numbers[index]}"

    def page(self, index: int) -> int | None:
        return self.pages[index] if self.pages is not None else None


@dataclass
class Mapping:
    date: str
    description: str
    amount: str | None = None
    debit: str | None = None
    credit: str | None = None
    balance: str | None = None
    reference: str | None = None
    currency: str | None = None
    date_format: str = "iso"
    sign_convention: SignConvention = "signed_inflow_positive"
    flags: list[str] = field(default_factory=list)
    candidates: dict[str, list[str]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "columns": {
                "date": self.date,
                "description": self.description,
                "amount": self.amount,
                "debit": self.debit,
                "credit": self.credit,
                "balance": self.balance,
                "reference": self.reference,
                "currency": self.currency,
            },
            "date_format": self.date_format,
            "sign_convention": self.sign_convention,
            "flags": list(self.flags),
            "candidates": {k: list(v) for k, v in self.candidates.items()},
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any], header: list[str]) -> "Mapping":
        columns = payload.get("columns") or {}

        def col(name: str) -> str | None:
            value = columns.get(name)
            if value in (None, ""):
                return None
            if not isinstance(value, str) or value not in header:
                raise ValueError(f"unknown column for {name}")
            return value

        date_col, desc_col = col("date"), col("description")
        if date_col is None or desc_col is None:
            raise ValueError("date and description columns are required")
        date_format = payload.get("date_format", "iso")
        if date_format not in DATE_FORMATS:
            raise ValueError("unknown date format")
        sign = payload.get("sign_convention", "signed_inflow_positive")
        if sign not in ("signed_inflow_positive", "signed_outflow_positive", "debit_credit"):
            raise ValueError("unknown sign convention")
        mapping = cls(
            date=date_col,
            description=desc_col,
            amount=col("amount"),
            debit=col("debit"),
            credit=col("credit"),
            balance=col("balance"),
            reference=col("reference"),
            currency=col("currency"),
            date_format=date_format,
            sign_convention=sign,
        )
        if sign == "debit_credit":
            if mapping.debit is None and mapping.credit is None:
                raise ValueError("debit or credit column required for debit/credit files")
        elif mapping.amount is None:
            raise ValueError("amount column required for signed files")
        return mapping


@dataclass
class NormalizedRow:
    row_index: int
    line_number: int
    raw: dict[str, str]
    posted_date: str | None
    description: str | None
    amount_minor: int | None
    balance_minor: int | None
    external_ref: str | None
    currency: str | None
    flags: list[str]
    source_locator: str = ""
    page_number: int | None = None

    @property
    def blocking(self) -> bool:
        return any(f in BLOCKING_FLAGS for f in self.flags)

    def extracted(self) -> dict[str, Any]:
        return {
            "posted_date": self.posted_date,
            "description": self.description,
            "amount_minor": self.amount_minor,
            "balance_minor": self.balance_minor,
            "external_ref": self.external_ref,
            "currency": self.currency,
        }


BLOCKING_FLAGS = frozenset(
    {"invalid_date", "invalid_amount", "zero_amount", "missing_description", "unsupported_currency"}
)


# ------------------------------------------------------------- inspection --
def inspect_bytes(data: bytes) -> Table:
    """Type, size, encoding and shape checks; the parsed table, or UnsupportedFileError."""
    if not data:
        raise UnsupportedFileError("empty_file", "The file is empty.")
    if len(data) > MAX_BYTES:
        limit_mb = MAX_BYTES // (1024 * 1024)
        raise UnsupportedFileError("too_large", f"The file exceeds {limit_mb} MB.")
    head = data[:8]
    if head.startswith(b"%PDF"):
        raise UnsupportedFileError(
            "pdf_not_supported", "This is a PDF. Only CSV files are supported in this milestone."
        )
    if head.startswith(b"PK\x03\x04") or head.startswith(b"\xd0\xcf\x11\xe0"):
        raise UnsupportedFileError(
            "spreadsheet_not_supported",
            "Spreadsheets are not supported; export the statement as CSV.",
        )
    if b"\x00" in data[:4096]:
        raise UnsupportedFileError("binary_file", "The file is not text.")
    encoding = "utf-8-sig"
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1252")
            encoding = "cp1252"
        except UnicodeDecodeError as exc:
            raise UnsupportedFileError(
                "unknown_encoding", "The file is not UTF-8 or Windows-1252 text."
            ) from exc
    if any(ord(ch) < 32 and ch not in "\r\n\t" for ch in text[:4096]):
        raise UnsupportedFileError(
            "binary_file", "The file contains control characters and is not a CSV."
        )
    sample = text[:8192]
    delimiter = _sniff_delimiter(sample)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header: list[str] | None = None
    rows: list[list[str]] = []
    line_numbers: list[int] = []
    for record in reader:
        line_number = reader.line_num
        if header is None:
            if not any(cell.strip() for cell in record):
                continue
            header = [cell.strip() for cell in record]
            if len(header) > MAX_COLUMNS:
                raise UnsupportedFileError("too_many_columns", f"More than {MAX_COLUMNS} columns.")
            if len(set(h.lower() for h in header if h)) != len([h for h in header if h]):
                raise UnsupportedFileError(
                    "duplicate_headers", "The header row repeats a column name."
                )
            continue
        if not any(cell.strip() for cell in record):
            continue
        if len(rows) >= MAX_ROWS:
            raise UnsupportedFileError("too_many_rows", f"More than {MAX_ROWS} rows.")
        if len(record) > len(header):
            raise UnsupportedFileError(
                "ragged_rows", f"Line {line_number} has more cells than the header."
            )
        cells = [cell.strip()[:MAX_CELL_CHARS] for cell in record]
        cells += [""] * (len(header) - len(record))
        rows.append(cells)
        line_numbers.append(line_number)
    if header is None or len([h for h in header if h]) < 2:
        raise UnsupportedFileError("no_header", "The first row must name at least two columns.")
    if not rows:
        raise UnsupportedFileError("no_rows", "The file has a header but no rows.")
    return Table(header, rows, delimiter, encoding, len(data), line_numbers)


def _sniff_delimiter(sample: str) -> str:
    first_lines = [line for line in sample.splitlines() if line.strip()][:20]
    if not first_lines:
        raise UnsupportedFileError("no_header", "The file has no rows.")
    best, best_score = ",", -1
    for candidate in DELIMITERS:
        counts = [line.count(candidate) for line in first_lines]
        if counts[0] == 0:
            continue
        consistent = sum(1 for c in counts if c == counts[0])
        score = consistent * 100 + counts[0]
        if score > best_score:
            best, best_score = candidate, score
    if best_score < 0:
        raise UnsupportedFileError(
            "no_delimiter", "No column delimiter found (comma, semicolon, tab or pipe)."
        )
    return best


# ---------------------------------------------------------------- mapping --
def _match_header(header: list[str], names: tuple[str, ...]) -> list[str]:
    """Columns that look like one of `names`, best first.

    Partial matches are ranked by which name matched rather than by where the column sits in the
    file, because the caller takes the first result and header position says nothing about what
    a column means. A file carrying both `Transaction ID` and `Transaction Name` matches the
    description name "name" on the second and the vaguer "transaction" on both; ranking by name
    picks the one that is actually a description, whichever came first in the file.
    """
    lowered = {h.lower().strip(): h for h in header}
    exact = [lowered[n] for n in names if n in lowered]
    if exact:
        return exact
    ranked: list[str] = []
    for name in names:
        for column in header:
            if name in column.lower() and column not in ranked:
                ranked.append(column)
    return ranked


def parse_date(value: str, fmt_key: str) -> date | None:
    fmt = DATE_FORMATS[fmt_key]
    text = value.strip()
    if not text:
        return None
    if fmt_key == "iso" and "T" in text:
        text = text.split("T", 1)[0]
    try:
        return datetime.strptime(text, fmt).date()
    except ValueError:
        return None


def date_formats_matching(values: list[str]) -> list[str]:
    """Every configured format that parses all non-empty sample values."""
    samples = [v for v in values if v.strip()][:200]
    if not samples:
        return []
    return [key for key in DATE_FORMATS if all(parse_date(v, key) is not None for v in samples)]


def parse_money(value: str) -> tuple[int | None, str | None]:
    """Decimal text to minor units. Returns (minor, flag). Handles (1.00), -1.00, 1.00-, CR/DR."""
    text = value.strip()
    if not text:
        return None, "invalid_amount"
    negative = False
    upper = text.upper()
    if upper.endswith("CR"):
        text = text[:-2].strip()
    elif upper.endswith("DR"):
        text = text[:-2].strip()
        negative = True
    if text.startswith("(") and text.endswith(")"):
        text, negative = text[1:-1], True
    if text.endswith("-"):
        text, negative = text[:-1], True
    text = MONEY_CLEAN.sub("", text)
    if text.startswith("+"):
        text = text[1:]
    if text.startswith("-"):
        text, negative = text[1:], not negative
    if not re.fullmatch(r"\d+(\.\d+)?|\.\d+", text):
        return None, "invalid_amount"
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None, "invalid_amount"
    flag = None
    exponent = -amount.as_tuple().exponent if amount != amount.to_integral() else 0
    if exponent > 2:
        flag = "amount_precision"
        amount = amount.quantize(Decimal("0.01"))
    minor = int(amount * 100)
    return (-minor if negative else minor), flag


def propose_mapping(table: Table) -> Mapping:
    """A best-effort mapping with ambiguity recorded as flags, never as silent choices."""
    header = table.header
    dates = _match_header(header, DATE_HEADERS)
    descriptions = _match_header(header, DESCRIPTION_HEADERS)
    amounts = _match_header(header, AMOUNT_HEADERS)
    debits = _match_header(header, DEBIT_HEADERS)
    credits = _match_header(header, CREDIT_HEADERS)
    balances = _match_header(header, BALANCE_HEADERS)
    references = _match_header(header, REFERENCE_HEADERS)
    currencies = _match_header(header, CURRENCY_HEADERS)
    flags: list[str] = []
    date_col = dates[0] if dates else header[0]
    if not dates:
        flags.append("date_column_unrecognized")
    if descriptions:
        description_col = descriptions[0]
    else:
        description_col = next((h for h in header if h != date_col), header[-1])
    if not descriptions:
        flags.append("description_column_unrecognized")
    sign: SignConvention = "signed_inflow_positive"
    amount_col = amounts[0] if amounts else None
    debit_col = credit_col = None
    if amount_col is None and (debits or credits):
        sign = "debit_credit"
        debit_col = debits[0] if debits else None
        credit_col = credits[0] if credits else None
    elif amount_col is None:
        flags.append("amount_column_unrecognized")
    column_index = {h: i for i, h in enumerate(header)}
    date_values = [row[column_index[date_col]] for row in table.rows]
    formats = date_formats_matching(date_values)
    if not formats:
        flags.append("date_format_unrecognized")
        date_format = "iso"
    else:
        date_format = formats[0]
        distinct = {DATE_FORMATS[f] for f in formats}
        if len(distinct) > 1 and formats[0] not in ("iso", "iso_slash", "compact"):
            flags.append("ambiguous_date_format")
    balance_col = _usable_balance(table, column_index, balances)
    if sign != "debit_credit" and amount_col is not None:
        if balance_col is not None:
            verdict = _sign_from_running_balance(table, column_index, amount_col, balance_col)
            if verdict == "unknown":
                verdict = _sign_from_daily_balance(
                    table, column_index, amount_col, balance_col, date_col
                )
            if verdict == "inverted":
                sign = "signed_outflow_positive"
            elif verdict == "unknown":
                flags.append("sign_unverified")
        else:
            flags.append("sign_unverified")
    mapping = Mapping(
        date=date_col,
        description=description_col,
        amount=amount_col,
        debit=debit_col,
        credit=credit_col,
        balance=balance_col,
        reference=references[0] if references else None,
        currency=currencies[0] if currencies else None,
        date_format=date_format,
        sign_convention=sign,
        flags=flags,
        candidates={
            "date": dates,
            "description": descriptions,
            "amount": amounts,
            "debit": debits,
            "credit": credits,
            "balance": balances,
            "reference": references,
            "date_formats": formats,
        },
    )
    return mapping


def _usable_balance(
    table: Table, column_index: dict[str, int], candidates: list[str]
) -> str | None:
    """The first balance candidate that holds money and actually changes.

    A column named like a balance is not necessarily one. `Balance Date` holds dates, and an
    account's `Current Balance` is often one figure copied beside every transaction: today's
    balance rather than the balance after each row. Neither can say anything about the sign, and
    taking the first candidate by header position picks them over a real running balance sitting
    further right.

    So the candidates are ranked by how many distinct figures they actually hold. A balance that
    tracks the account moves on nearly every row, or at least on every day; one that repeats a
    handful of account totals does not, and is passed over for the column that does. Ties keep
    header order, so a file with one plain balance column behaves exactly as before.
    """
    best: str | None = None
    best_distinct = 1  # a column holding a single repeated figure is worth nothing here
    for column in candidates:
        values = {
            parsed
            for parsed, _ in (parse_money(row[column_index[column]]) for row in table.rows)
            if parsed is not None
        }
        if len(values) > best_distinct:
            best, best_distinct = column, len(values)
    return best


def _sign_from_running_balance(
    table: Table, column_index: dict[str, int], amount_col: str, balance_col: str
) -> str:
    """Does balance[i] - balance[i-1] equal +amount (as-is) or -amount (inverted)?"""
    as_is = inverted = 0
    previous: int | None = None
    for row in table.rows:
        amount, _ = parse_money(row[column_index[amount_col]])
        balance, _ = parse_money(row[column_index[balance_col]])
        if amount is None or balance is None:
            previous = balance
            continue
        if previous is not None:
            delta = balance - previous
            if delta == amount:
                as_is += 1
            elif delta == -amount:
                inverted += 1
        previous = balance
    if as_is and not inverted:
        return "as_is"
    if inverted and not as_is:
        return "inverted"
    return "unknown"


# How many days must agree before a day-grouped verdict is trusted, and how far a day's figures
# may sit from each other and still count as agreeing. The tolerance is a hundredth of the day's
# own movement, so a large day is not matched by a rounding coincidence and a small one is not
# rejected for a cent.
DAILY_SIGN_MIN_DAYS = 5
DAILY_SIGN_TOLERANCE = 0.01


def _sign_from_daily_balance(
    table: Table,
    column_index: dict[str, int],
    amount_col: str,
    balance_col: str,
    date_col: str,
) -> str:
    """The same question asked of whole days, for a balance that only moves once a day.

    Some exports carry the closing balance for the day against every transaction on that day
    rather than the balance after each one. Row by row the deltas are then all zero, which the
    per-row check correctly calls unknown. Day over day the balance does move, and it should have
    moved by that day's own total, so the sign is still answerable from the file.

    Only a one-sided verdict over enough days counts. A file whose days disagree, or one with a
    handful of days to compare, stays unknown and is flagged rather than guessed at.
    """
    totals: dict[str, int] = {}
    closing: dict[str, int] = {}
    for row in table.rows:
        day = row[column_index[date_col]].strip()
        amount, _ = parse_money(row[column_index[amount_col]])
        balance, _ = parse_money(row[column_index[balance_col]])
        if not day or amount is None or balance is None:
            continue
        totals[day] = totals.get(day, 0) + amount
        closing[day] = balance

    # Sorted as text, which orders the ISO-like dates this check can say anything about and
    # leaves any other format to fall out as disagreement rather than a wrong answer.
    days = sorted(totals)
    as_is = inverted = 0
    for previous, current in zip(days, days[1:], strict=False):
        delta = closing[current] - closing[previous]
        total = totals[current]
        if total == 0:
            continue
        tolerance = max(1, int(abs(total) * DAILY_SIGN_TOLERANCE))
        if abs(delta - total) <= tolerance:
            as_is += 1
        elif abs(delta + total) <= tolerance:
            inverted += 1
    if as_is >= DAILY_SIGN_MIN_DAYS and not inverted:
        return "as_is"
    if inverted >= DAILY_SIGN_MIN_DAYS and not as_is:
        return "inverted"
    return "unknown"


# ------------------------------------------------------------ normalizing --
def normalize(table: Table, mapping: Mapping) -> list[NormalizedRow]:
    index = {h: i for i, h in enumerate(table.header)}
    rows: list[NormalizedRow] = []
    for row_index, cells in enumerate(table.rows):
        raw = {h: cells[index[h]] for h in table.header if h}
        flags: list[str] = []
        parsed_date = parse_date(cells[index[mapping.date]], mapping.date_format)
        if parsed_date is None:
            flags.append("invalid_date")
        description = cells[index[mapping.description]].strip() or None
        if description is None:
            flags.append("missing_description")
        amount: int | None
        if mapping.sign_convention == "debit_credit":
            debit_text = cells[index[mapping.debit]] if mapping.debit else ""
            credit_text = cells[index[mapping.credit]] if mapping.credit else ""
            debit, dflag = parse_money(debit_text) if debit_text.strip() else (None, None)
            credit, cflag = parse_money(credit_text) if credit_text.strip() else (None, None)
            if dflag or cflag or (debit is None and credit is None):
                flags.append("invalid_amount")
                amount = None
            elif debit is not None and credit is not None and debit and credit:
                flags.append("debit_and_credit_both_present")
                amount = credit - abs(debit)
            else:
                amount = (credit or 0) - abs(debit or 0)
        else:
            if mapping.amount:
                amount, aflag = parse_money(cells[index[mapping.amount]])
            else:
                amount, aflag = None, "invalid_amount"
            if aflag == "invalid_amount":
                flags.append("invalid_amount")
            elif aflag:
                flags.append(aflag)
            if amount is not None and mapping.sign_convention == "signed_outflow_positive":
                amount = -amount
        if amount == 0:
            flags.append("zero_amount")
        balance: int | None = None
        if mapping.balance:
            balance_text = cells[index[mapping.balance]]
            if balance_text.strip():
                balance, bflag = parse_money(balance_text)
                if bflag == "invalid_amount":
                    flags.append("invalid_balance")
        reference = cells[index[mapping.reference]].strip()[:120] if mapping.reference else ""
        currency = cells[index[mapping.currency]].strip().upper() if mapping.currency else ""
        if currency and currency != "USD":
            flags.append("unsupported_currency")
        rows.append(
            NormalizedRow(
                row_index=row_index,
                line_number=table.line_numbers[row_index],
                raw=raw,
                posted_date=parsed_date.isoformat() if parsed_date else None,
                description=description,
                amount_minor=amount,
                balance_minor=balance,
                external_ref=reference or None,
                currency=currency or None,
                flags=flags,
                source_locator=table.locator(row_index),
                page_number=table.page(row_index),
            )
        )
    _flag_balance_breaks(rows)
    _flag_repeats(rows)
    return rows


def _flag_balance_breaks(rows: list[NormalizedRow]) -> None:
    previous: int | None = None
    for row in rows:
        if row.balance_minor is None or row.amount_minor is None:
            previous = row.balance_minor if row.balance_minor is not None else previous
            continue
        if previous is not None and row.balance_minor - previous != row.amount_minor:
            row.flags.append("balance_break")
        previous = row.balance_minor


def _flag_repeats(rows: list[NormalizedRow]) -> None:
    """Identical date, amount and description inside one file are legitimate repeats unless the
    references say otherwise; they are flagged for review, never dropped."""
    seen: dict[tuple[str | None, int | None, str | None], list[NormalizedRow]] = {}
    for row in rows:
        seen.setdefault((row.posted_date, row.amount_minor, row.description), []).append(row)
    for group in seen.values():
        if len(group) < 2:
            continue
        refs = {r.external_ref for r in group}
        if None in refs or len(refs) < len(group):
            for r in group:
                r.flags.append("repeated_in_file")


# -------------------------------------------------------------- summary --
def summarize(
    rows: list[NormalizedRow], mapping: Mapping, statement: dict[str, Any] | None = None
) -> dict[str, Any]:
    included = [r for r in rows if not r.blocking]
    credits = sum(r.amount_minor for r in included if r.amount_minor and r.amount_minor > 0)
    debits = sum(-r.amount_minor for r in included if r.amount_minor and r.amount_minor < 0)
    dates = sorted(r.posted_date for r in included if r.posted_date)
    flag_counts: dict[str, int] = {}
    for r in rows:
        for f in r.flags:
            flag_counts[f] = flag_counts.get(f, 0) + 1
    balance: dict[str, Any] | None = None
    if mapping.balance and rows:
        with_balance = [r for r in rows if r.balance_minor is not None]
        if with_balance and dates:
            first, last = with_balance[0], with_balance[-1]
            opening = None
            if first.amount_minor is not None and first.balance_minor is not None:
                opening = first.balance_minor - first.amount_minor
            balance = {
                "opening_minor": opening,
                "closing_minor": last.balance_minor,
                "period_start": dates[0],
                "period_end": dates[-1],
                "breaks": flag_counts.get("balance_break", 0),
                "reconciles": (
                    opening is not None
                    and opening + credits - debits == last.balance_minor
                    and flag_counts.get("balance_break", 0) == 0
                ),
            }
    reconciliation = reconcile(statement, balance, credits, debits, len(rows))
    if (
        statement
        and statement.get("opening_minor") is not None
        and statement.get("closing_minor") is not None
    ):
        # Balances printed by the statement itself are the observations to keep; the running
        # balance column, when present, is checked against them in `reconcile`.
        balance = {
            "opening_minor": statement["opening_minor"],
            "closing_minor": statement["closing_minor"],
            "period_start": statement.get("period_start") or (dates[0] if dates else None),
            "period_end": statement.get("period_end") or (dates[-1] if dates else None),
            "breaks": flag_counts.get("balance_break", 0),
            "reconciles": reconciliation["status"] == "matched",
            "source": "statement_totals",
        }
    return {
        "parser_version": PARSER_VERSION,
        "row_count": len(rows),
        "blocking_rows": sum(1 for r in rows if r.blocking),
        "credits_minor": credits,
        "debits_minor": debits,
        "net_minor": credits - debits,
        "period_start": dates[0] if dates else None,
        "period_end": dates[-1] if dates else None,
        "flag_counts": flag_counts,
        "balance": balance,
        "statement": statement,
        "reconciliation": reconciliation,
        "mapping_flags": list(mapping.flags),
    }


def reconcile(
    statement: dict[str, Any] | None,
    balance: dict[str, Any] | None,
    credits: int,
    debits: int,
    row_count: int,
) -> dict[str, Any]:
    """Compare what the rows add up to with what the document independently states.

    Sources, in order of independence: totals and balances printed by the statement
    (PDF header or footer), then a running balance column. With neither, reconciliation
    is reported as unavailable rather than assumed.
    """
    problems: list[str] = []
    if (
        statement
        and statement.get("opening_minor") is not None
        and statement.get("closing_minor") is not None
    ):
        expected = statement["opening_minor"] + credits - debits
        if expected != statement["closing_minor"]:
            problems.append("rows_do_not_bridge_statement_balances")
        if (
            statement.get("total_credits_minor") is not None
            and statement["total_credits_minor"] != credits
        ):
            problems.append("credits_differ_from_statement_total")
        if (
            statement.get("total_debits_minor") is not None
            and statement["total_debits_minor"] != debits
        ):
            problems.append("debits_differ_from_statement_total")
        if (
            statement.get("transaction_count") is not None
            and statement["transaction_count"] != row_count
        ):
            problems.append("row_count_differs_from_statement")
        if (
            balance
            and balance.get("closing_minor") is not None
            and balance["closing_minor"] != statement["closing_minor"]
        ):
            problems.append("running_balance_differs_from_statement_closing")
        if balance and balance.get("breaks"):
            problems.append("running_balance_breaks")
        return {
            "status": "mismatch" if problems else "matched",
            "source": "statement_totals",
            "expected_closing_minor": statement["closing_minor"],
            "computed_closing_minor": expected,
            "problems": problems,
        }
    if balance and balance.get("closing_minor") is not None:
        if not balance.get("reconciles"):
            problems.append("running_balance_breaks")
        return {
            "status": "mismatch" if problems else "matched",
            "source": "running_balance",
            "expected_closing_minor": balance["closing_minor"],
            "computed_closing_minor": (balance["opening_minor"] + credits - debits)
            if balance.get("opening_minor") is not None
            else None,
            "problems": problems,
        }
    return {
        "status": "unavailable",
        "source": None,
        "expected_closing_minor": None,
        "computed_closing_minor": None,
        "problems": [],
    }
