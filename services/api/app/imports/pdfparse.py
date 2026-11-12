"""Deterministic text extraction for single-account bank statement PDFs.

Bytes in, the same `Table` the CSV path uses out, plus what the statement
itself says (period, balances, totals, count) for independent reconciliation.
No OCR, no model calls. Text positions come from pypdf's text visitor; lines
are rebuilt from y positions, columns from the table header's x positions.
Everything the document says is data: a description is never an instruction.
"""

import io
import re
from dataclasses import dataclass
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.imports.budget import Deadline
from app.imports.csvparse import DATE_FORMATS, Table, UnsupportedFileError, parse_date, parse_money

PARSER_VERSION = "pdf-1"
MAX_PAGES = 20
MAX_TEXT_CHARS = 500_000
MIN_TEXT_CHARS_PER_PAGE = 20
LINE_TOLERANCE = 2.5
# A wrapped description sits one line below its transaction. The allowance is a multiple of
# the spacing measured in this document, never a fixed distance, because font size and
# leading differ between statements; 1.5 accepts the next line and rejects the next block.
CONTINUATION_SPACING_RATIO = 1.5
DATE_LIKE_RE = re.compile(
    r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{1,2}\s+[A-Za-z]{3}|[A-Za-z]{3}\s+\d{1,2}"
)

HEADER_WORDS = {
    "date": ("date", "posted", "transaction date", "post date", "posting date"),
    "description": ("description", "details", "memo", "payee", "transaction", "narrative"),
    "amount": ("amount",),
    "debit": ("debit", "debits", "withdrawal", "withdrawals", "money out", "paid out"),
    "credit": ("credit", "credits", "deposit", "deposits", "money in", "paid in"),
    "balance": ("balance", "running balance"),
    "reference": ("reference", "ref", "check", "check no", "transaction id"),
}
SUMMARY_LABELS = {
    "opening_minor": (
        "opening balance",
        "beginning balance",
        "previous balance",
        "balance forward",
    ),
    "closing_minor": ("closing balance", "ending balance", "new balance", "balance at end"),
    "total_credits_minor": (
        "total credits",
        "total deposits",
        "deposits and credits",
        "total money in",
    ),
    "total_debits_minor": (
        "total debits",
        "total withdrawals",
        "withdrawals and debits",
        "total money out",
    ),
}
UNSUPPORTED_KINDS = (
    ("credit card statement", ("minimum payment due", "credit limit", "purchases and adjustments")),
    ("invoice", ("invoice number", "invoice #", "amount due", "bill to")),
)
PERIOD_RE = re.compile(
    r"(?:period|statement period|from)\s*:?\s*"
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\s*(?:through|to|-|–|until)\s*"
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})",
    re.I,
)
LAST4_RE = re.compile(
    r"(?:ending|ending in|account\s*(?:no\.?|number)?\s*[:#]?\s*[x*]+|x{2,}|\*{2,})\s*(\d{4})\b",
    re.I,
)
COUNT_RE = re.compile(r"\b(\d{1,5})\s+(?:posted\s+)?transactions?\b", re.I)
MONEY_RE = re.compile(r"^\(?-?\$?\s?[\d,]+\.\d{2}\)?-?$")


@dataclass
class Chunk:
    page: int
    x: float
    y: float
    text: str


@dataclass
class Line:
    page: int
    y: float
    chunks: list[Chunk]

    @property
    def text(self) -> str:
        return " ".join(c.text for c in sorted(self.chunks, key=lambda c: c.x))


def _read(data: bytes) -> PdfReader:
    if not data.startswith(b"%PDF"):
        raise UnsupportedFileError("not_pdf", "The file is not a PDF.")
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except (PdfReadError, ValueError, OSError, KeyError, TypeError) as exc:
        raise UnsupportedFileError(
            "pdf_malformed", "The PDF could not be read; it may be damaged."
        ) from exc
    if reader.is_encrypted:
        try:
            opened = reader.decrypt("")
        except Exception:  # noqa: BLE001 - any failure to open means the same to the owner
            opened = 0
        if not opened:
            raise UnsupportedFileError(
                "pdf_encrypted",
                "The PDF is password-protected. Remove the password in your PDF viewer and "
                "upload it again; passwords are never accepted here.",
            )
    return reader


def extract_chunks(data: bytes, deadline: Deadline | None = None) -> tuple[list[Chunk], int]:
    """Positioned text chunks for every page, with page count, within the limits."""
    reader = _read(data)
    try:
        page_count = len(reader.pages)
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise UnsupportedFileError(
            "pdf_malformed", "The PDF could not be read; it may be damaged."
        ) from exc
    if page_count == 0:
        raise UnsupportedFileError("pdf_malformed", "The PDF has no pages.")
    if page_count > MAX_PAGES:
        raise UnsupportedFileError(
            "too_many_pages", f"The PDF has {page_count} pages; the limit is {MAX_PAGES}."
        )
    chunks: list[Chunk] = []
    total_chars = 0
    for index, page in enumerate(reader.pages, start=1):
        if deadline:
            deadline.check(f"page {index}")
        page_chunks: list[Chunk] = []

        def visitor(
            text: str,
            cm: Any,
            tm: Any,
            font_dict: Any,
            font_size: Any,
            page_no: int = index,
            sink: list[Chunk] = page_chunks,
        ) -> None:
            cleaned = text.replace("\r", "").strip("\n")
            if cleaned.strip():
                sink.append(Chunk(page_no, float(tm[4]), float(tm[5]), cleaned.strip()))

        try:
            page.extract_text(visitor_text=visitor)
        except (PdfReadError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
            raise UnsupportedFileError("pdf_malformed", f"Page {index} could not be read.") from exc
        total_chars += sum(len(c.text) for c in page_chunks)
        if total_chars > MAX_TEXT_CHARS:
            raise UnsupportedFileError(
                "extraction_text_too_large", "The PDF contains more text than the importer accepts."
            )
        chunks.extend(page_chunks)
    if total_chars < MIN_TEXT_CHARS_PER_PAGE * page_count:
        raise UnsupportedFileError(
            "pdf_no_text_layer",
            "The PDF has no usable text layer, so it is probably a scan or a photo. Only "
            "text-based statements are supported; scanned images are not read.",
        )
    return chunks, page_count


def _lines(chunks: list[Chunk]) -> list[Line]:
    lines: list[Line] = []
    for chunk in sorted(chunks, key=lambda c: (c.page, -c.y, c.x)):
        if lines and lines[-1].page == chunk.page and abs(lines[-1].y - chunk.y) <= LINE_TOLERANCE:
            lines[-1].chunks.append(chunk)
        else:
            lines.append(Line(chunk.page, chunk.y, [chunk]))
    return lines


def _header_role(text: str) -> str | None:
    lowered = text.strip().lower().rstrip(":")
    for role, names in HEADER_WORDS.items():
        if lowered in names:
            return role
    return None


def _detect_header(line: Line) -> dict[str, float] | None:
    roles: dict[str, float] = {}
    for chunk in line.chunks:
        role = _header_role(chunk.text)
        if role and role not in roles:
            roles[role] = chunk.x
    has_amount = "amount" in roles or "debit" in roles or "credit" in roles
    if "date" in roles and has_amount and len(roles) >= 3:
        return roles
    return None


def _document_kind(lines: list[Line]) -> str | None:
    text = "\n".join(line.text.lower() for line in lines)
    for kind, markers in UNSUPPORTED_KINDS:
        if sum(1 for m in markers if m in text) >= 2:
            return kind
    return None


def _statement_metadata(lines: list[Line]) -> dict[str, Any]:
    """Period, balances, totals, count and account digits as printed on the first page with them."""
    found: dict[str, Any] = {}
    by_page: dict[int, list[Line]] = {}
    for line in lines:
        by_page.setdefault(line.page, []).append(line)
    for page in sorted(by_page):
        page_lines = by_page[page]
        for i, line in enumerate(page_lines):
            text = line.text
            m = PERIOD_RE.search(text)
            if m and "period_start" not in found:
                start, end = _flexible_date(m.group(1)), _flexible_date(m.group(2))
                if start and end:
                    found["period_start"], found["period_end"] = start, end
            m = LAST4_RE.search(text)
            if m and "account_last4" not in found:
                found["account_last4"] = m.group(1)
            m = COUNT_RE.search(text)
            if m and "transaction_count" not in found:
                found["transaction_count"] = int(m.group(1))
            for chunk in line.chunks:
                label = chunk.text.strip().lower().rstrip(":")
                for key, names in SUMMARY_LABELS.items():
                    if key in found or label not in names:
                        continue
                    value = _value_after_label(line, chunk, page_lines[i + 1 : i + 3])
                    if value is not None:
                        found[key] = value
    return found


def _flexible_date(text: str) -> str | None:
    for key in ("iso", "mdy", "mdy_short", "dmy", "dmy_short"):
        parsed = parse_date(text, key)
        if parsed:
            return parsed.isoformat()
    return None


def _value_after_label(line: Line, label: Chunk, following: list[Line]) -> int | None:
    """A money value on the same line right after the label, or on the next line under it."""
    same_line = sorted((c for c in line.chunks if c.x > label.x), key=lambda c: c.x)
    for chunk in same_line[:2]:
        if MONEY_RE.match(chunk.text.strip()):
            minor, flag = parse_money(chunk.text)
            return minor if flag != "invalid_amount" else None
    for below in following:
        for chunk in below.chunks:
            if abs(chunk.x - label.x) <= 30 and MONEY_RE.match(chunk.text.strip()):
                minor, flag = parse_money(chunk.text)
                return minor if flag != "invalid_amount" else None
    return None


def _is_summary_line(text: str) -> bool:
    lowered = text.strip().lower()
    labels = [n for names in SUMMARY_LABELS.values() for n in names]
    return any(lowered.startswith(n) for n in labels) or lowered.startswith(
        ("total", "subtotal", "page ")
    )


def _line_spacing(lines: list[Line], header_y_by_page: dict[int, float]) -> float:
    """Typical distance between adjacent lines of the transaction table, measured here.

    The lower quartile of the gaps, not the mean: a statement's last line is followed
    by a large jump to the page footer, and that outlier must not widen the allowance.
    """
    gaps: list[float] = []
    previous: Line | None = None
    for line in lines:
        header_y = header_y_by_page.get(line.page)
        if header_y is None or line.y >= header_y:
            previous = None
            continue
        if previous is not None and previous.page == line.page and previous.y > line.y:
            gaps.append(previous.y - line.y)
        previous = line
    if not gaps:
        return 0.0
    gaps.sort()
    return gaps[max(0, (len(gaps) - 1) // 4)]


def _furniture_keys(lines: list[Line], page_count: int) -> set[tuple[str, int]]:
    """Lines that repeat in the same place on more than one page: banners, footers, notices.

    Page furniture is printed from a template, so it lands at the same text and the same
    height on every page; a transaction never does. Lines shaped like a posting are excluded
    from the test, so two genuinely identical rows can never be mistaken for a banner.
    """
    if page_count < 2:
        return set()
    seen: dict[tuple[str, int], set[int]] = {}
    for line in lines:
        if _looks_like_a_transaction(line):
            continue
        seen.setdefault(_furniture_key(line), set()).add(line.page)
    return {key for key, on_pages in seen.items() if len(on_pages) > 1}


def _furniture_key(line: Line) -> tuple[str, int]:
    return (line.text.strip().lower(), round(line.y))


def _looks_like_a_transaction(line: Line) -> bool:
    """A date-shaped token and a money-shaped token on one line: the shape of a posting."""
    texts = [c.text.strip() for c in line.chunks]
    return any(DATE_LIKE_RE.search(t) for t in texts) and any(MONEY_RE.match(t) for t in texts)


def build_table(data: bytes, deadline: Deadline | None = None) -> Table:
    """The statement's transaction lines as a Table with page and line provenance."""
    chunks, page_count = extract_chunks(data, deadline)
    lines = _lines(chunks)
    kind = _document_kind(lines)
    if kind:
        raise UnsupportedFileError(
            "unsupported_document_type",
            f"This looks like a {kind}, not a bank account statement. "
            "Only checking and savings statements are supported.",
        )
    statement = _statement_metadata(lines)
    columns: dict[str, float] | None = None
    header_y_by_page: dict[int, float] = {}
    for line in lines:
        roles = _detect_header(line)
        if roles and line.page not in header_y_by_page:
            header_y_by_page[line.page] = line.y
            if columns is None:
                columns = roles
    if columns is None:
        sample = [line.text[:80] for line in lines[:6]]
        raise UnsupportedFileError(
            "pdf_layout_unsupported",
            "No transaction table with date and amount columns was found. First lines read: "
            + " | ".join(sample),
        )
    ordered = sorted(columns.items(), key=lambda kv: kv[1])
    boundaries: list[tuple[str, float, float]] = []
    for i, (role, x) in enumerate(ordered):
        left = -1e9 if i == 0 else (ordered[i - 1][1] + x) / 2
        right = 1e9 if i == len(ordered) - 1 else (x + ordered[i + 1][1]) / 2
        boundaries.append((role, left, right))
    header = [role for role, _, _ in boundaries]
    if "reference" not in header:
        pass

    def column_of(x: float) -> str:
        for role, left, right in boundaries:
            if left <= x < right:
                return role
        return boundaries[-1][0]

    by_page: dict[int, list[Line]] = {}
    for line in lines:
        by_page.setdefault(line.page, []).append(line)
    for page_no in sorted(by_page):
        if page_no in header_y_by_page:
            continue
        if any(_looks_like_a_transaction(line) for line in by_page[page_no]):
            raise UnsupportedFileError(
                "pdf_layout_unsupported",
                f"Page {page_no} carries transaction-like lines but no column header this "
                "importer can read, so its rows would be lost. Every page of the table needs "
                "its header line.",
            )

    furniture = _furniture_keys(lines, page_count)
    spacing = _line_spacing(lines, header_y_by_page)
    rows: list[list[str]] = []
    locators: list[str] = []
    pages: list[int] = []
    line_numbers: list[int] = []
    per_page_line = 0
    current_page = 0
    # Page and vertical position of the last line that belongs to the row being built, so a
    # continuation joins only the line it actually sits under.
    anchor: tuple[int, float] | None = None
    for line in lines:
        if line.page != current_page:
            current_page, per_page_line = line.page, 0
        per_page_line += 1
        header_y = header_y_by_page.get(line.page)
        if header_y is None or line.y >= header_y:
            continue  # header block, page banner or the table header itself
        if _is_summary_line(line.text):
            continue  # totals repeated per page are never transactions
        if _furniture_key(line) in furniture:
            continue  # the same text at the same height on another page: a banner or a footer
        cells: dict[str, list[str]] = {role: [] for role in header}
        for chunk in sorted(line.chunks, key=lambda c: c.x):
            cells[column_of(chunk.x)].append(chunk.text)
        date_text = " ".join(cells.get("date", [])).strip()
        has_date = any(parse_date(date_text, key) for key in DATE_FORMATS) if date_text else False
        amount_cells = [
            " ".join(cells.get(role, [])).strip()
            for role in ("amount", "debit", "credit")
            if role in cells
        ]
        has_amount = any(MONEY_RE.match(c) for c in amount_cells if c)
        description = " ".join(cells.get("description", [])).strip()
        if not has_date and not has_amount:
            continuation = (
                bool(rows)
                and bool(description)
                and not date_text
                and anchor is not None
                and anchor[0] == line.page
                and 0 < anchor[1] - line.y <= spacing * CONTINUATION_SPACING_RATIO
            )
            if continuation:
                rows[-1][header.index("description")] = (
                    rows[-1][header.index("description")] + " " + description
                ).strip()
                anchor = (line.page, line.y)
                continue
            if not (date_text and description and any(amount_cells)):
                continue  # a page footer, a banner or a stray label: not part of any row
            # Date, description and amount columns are all filled but neither the date nor the
            # amount could be read. It is transaction-shaped, so it is kept and flagged for
            # review rather than dropped where nobody would see it go.
        rows.append([" ".join(cells.get(role, [])).strip() for role in header])
        locators.append(f"page {line.page} line {per_page_line}")
        pages.append(line.page)
        line_numbers.append(len(line_numbers) + 1)
        anchor = (line.page, line.y)
    if not rows:
        raise UnsupportedFileError(
            "pdf_layout_unsupported",
            "A transaction table header was found but no transaction lines could be read under it.",
        )
    return Table(
        header=header,
        rows=rows,
        delimiter="pdf",
        encoding="pdf",
        byte_size=len(data),
        line_numbers=line_numbers,
        source="pdf",
        locators=locators,
        pages=pages,
        statement={**statement, "page_count": page_count, "parser_version": PARSER_VERSION}
        if statement
        else {"page_count": page_count, "parser_version": PARSER_VERSION},
    )
