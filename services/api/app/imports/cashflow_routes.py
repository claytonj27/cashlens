"""Cash-flow classification over a staged import: run it, read it, correct it, export it.

A separate module from the import routes, sharing their prefix and their authorization, because
this is a distinct question asked of an import rather than another step in staging it. The
workspace is still resolved by `private_scope`, so a visitor, another user, or a public-demo
deployment gets the same 404 here as everywhere else.

The run endpoint is deliberately not a job queue. It classifies for as long as its deadline
allows and returns how far it got; the browser calls it again. Rows carry their own state — a
null `cashflow` is a row still to do — so nothing has to be leased, nothing is left running
after the response, and closing the tab loses only the batches that were in the air.
"""

import csv
import io
import logging
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.ai import cashflow, prompts
from app.ai.factory import AiSettings, Provider
from app.auth import refuse_on_public_demo, require_client_header, require_features
from app.imports.routes import Scope, Supabase, _load_import, _not_found
from app.supabase import SupabaseClient

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/workspaces/{workspace_id}/imports",
    tags=["imports"],
    dependencies=[Depends(require_features), Depends(refuse_on_public_demo)],
)

ROWS_PAGE_LIMIT = 200

# Columns this feature adds to whatever the file already had. Named once, because the review
# screen, the summary and the export all have to agree about them.
EXPORT_COLUMNS = (
    "Cash Flow Direction",
    "Cash Flow Category",
    "Classification Confidence",
    "Classification Reason",
    "Classification Source",
    "Reviewed",
)


# ------------------------------------------------------------------ models --
class Progress(BaseModel):
    total: int
    classified: int
    remaining: int
    done: bool
    batches: int
    cost_micro: int
    # Why this call stopped short, when it did: a spend cap, an unpriced model, or another run
    # already in flight. Null when it simply ran out of its deadline or finished.
    stopped: str | None


class ClassifiedRow(BaseModel):
    row_index: int
    transaction_id: str
    date: str | None
    name: str | None
    merchant: str | None
    amount_minor: int
    direction: str | None
    category: str | None
    confidence: str | None
    reason: str | None
    source: str | None


class RowsPage(BaseModel):
    items: list[ClassifiedRow]
    next_after: int | None
    total: int
    matched: int


class Bucket(BaseModel):
    category: str
    direction: str
    count: int
    total_minor: int


class Summary(BaseModel):
    total_rows: int
    classified: int
    unclassified: int
    inflow_count: int
    inflow_minor: int
    outflow_count: int
    outflow_minor: int
    net_minor: int
    manually_reviewed: int
    by_confidence: dict[str, int]
    buckets: list[Bucket]


class Override(BaseModel):
    category: str = Field(min_length=1, max_length=64)


# ----------------------------------------------------------------- helpers --
def _cashflow_rows(supabase: SupabaseClient, import_id: str) -> list[dict[str, Any]]:
    """Every row of one import, in file order. Paged by the client, never truncated silently."""
    return supabase.rest_all(
        "import_rows",
        {
            "import_id": f"eq.{import_id}",
            "select": "row_index,raw,extracted,reviewed,cashflow",
            "order": "row_index",
        },
    )


def _amount_of(row: dict[str, Any]) -> int:
    reviewed = row.get("reviewed") or {}
    extracted = row.get("extracted") or {}
    value = reviewed.get("amount_minor", extracted.get("amount_minor"))
    return value if isinstance(value, int) else 0


def _raw(row: dict[str, Any], *names: str) -> str | None:
    raw = row.get("raw") or {}
    for name in names:
        value = raw.get(name)
        if value not in (None, ""):
            return str(value)
    return None


def _view(row: dict[str, Any]) -> ClassifiedRow:
    """One row as the review screen needs it, with the classification where there is one."""
    decided = row.get("cashflow") or {}
    extracted = row.get("extracted") or {}
    reviewed = row.get("reviewed") or {}
    amount = _amount_of(row)
    return ClassifiedRow(
        row_index=row["row_index"],
        transaction_id=_raw(row, "Transaction ID", "transaction_id") or f"row-{row['row_index']}",
        date=_raw(row, "Date", "date")
        or reviewed.get("posted_date")
        or extracted.get("posted_date"),
        name=_raw(row, "Transaction Name", "Name", "Description")
        or reviewed.get("description")
        or extracted.get("description"),
        merchant=_raw(row, "Merchant Name", "merchant_name"),
        amount_minor=amount,
        # Direction is shown for every row, classified or not, because it is arithmetic and does
        # not wait on a model.
        direction=decided.get("direction") or cashflow.direction_of(amount),
        category=decided.get("category"),
        confidence=decided.get("confidence"),
        reason=decided.get("reason"),
        source=decided.get("source"),
    )


def _matches(
    view: ClassifiedRow,
    direction: str | None,
    category: str | None,
    confidence: str | None,
    search: str | None,
) -> bool:
    if direction and view.direction != direction:
        return False
    if category and view.category != category:
        return False
    if confidence and view.confidence != confidence:
        return False
    if search:
        needle = search.lower()
        haystack = " ".join(
            filter(None, [view.name, view.merchant, view.transaction_id, view.category])
        ).lower()
        if needle not in haystack:
            return False
    return True


# --------------------------------------------------------------- the routes --
@router.post(
    "/{import_id}/cashflow",
    response_model=Progress,
    dependencies=[Depends(require_client_header)],
)
def run_classification(
    import_id: str,
    scope: Scope,
    supabase: Supabase,
    provider: Provider,
    settings: AiSettings,
) -> Progress:
    """Classify as much of this import as one request's deadline allows, then say where it got to.

    Safe to call repeatedly: only rows with no classification are picked up, so a second call
    continues rather than reclassifies, and a call on a finished import spends nothing.
    """
    record = _load_import(supabase, scope, import_id)
    if record["status"] not in ("staged", "confirmed"):
        raise HTTPException(status_code=409, detail=f"import is {record['status']}")
    progress = cashflow.classify_import(
        supabase,
        scope,
        provider,
        settings,
        import_id=record["id"],
    )
    logger.info(
        "cashflow.run",
        extra={
            "import_id": record["id"],
            "batches": progress.batches,
            "classified": progress.classified,
            "remaining": progress.remaining,
            "stopped": progress.stopped,
        },
    )
    return Progress(**progress.__dict__)


@router.get("/{import_id}/cashflow", response_model=RowsPage)
def classified_rows(
    import_id: str,
    scope: Scope,
    supabase: Supabase,
    after: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=ROWS_PAGE_LIMIT)] = 100,
    direction: Annotated[Literal["inflow", "outflow"] | None, Query()] = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    confidence: Annotated[Literal["high", "medium", "low"] | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
) -> RowsPage:
    """One page of rows, filtered. `total` is the whole import; `matched` is the filtered set."""
    record = _load_import(supabase, scope, import_id)
    rows = _cashflow_rows(supabase, record["id"])
    views = [_view(row) for row in rows]
    kept = [v for v in views if _matches(v, direction, category, confidence, search)]
    # Paged on row_index so the order is the file's own, and so a page boundary survives a row
    # being corrected between two requests.
    start = 0
    if after is not None:
        start = next((i for i, v in enumerate(kept) if v.row_index > after), len(kept))
    page = kept[start : start + limit]
    more = start + limit < len(kept)
    return RowsPage(
        items=page,
        next_after=page[-1].row_index if more and page else None,
        total=len(views),
        matched=len(kept),
    )


@router.get("/{import_id}/cashflow/summary", response_model=Summary)
def summary(import_id: str, scope: Scope, supabase: Supabase) -> Summary:
    """Totals and per-category counts over the whole import, from the reviewed values."""
    record = _load_import(supabase, scope, import_id)
    rows = _cashflow_rows(supabase, record["id"])

    buckets: dict[tuple[str, str], list[int]] = {}
    by_confidence: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    classified = manually_reviewed = 0
    inflow_count = inflow_minor = outflow_count = outflow_minor = 0

    for row in rows:
        decided = row.get("cashflow") or {}
        amount = _amount_of(row)
        direction = decided.get("direction") or cashflow.direction_of(amount)
        if direction == "inflow":
            inflow_count += 1
            inflow_minor += abs(amount)
        else:
            outflow_count += 1
            outflow_minor += abs(amount)
        if not decided:
            continue
        classified += 1
        if decided.get("source") == "manual":
            manually_reviewed += 1
        confidence = decided.get("confidence")
        if confidence in by_confidence:
            by_confidence[confidence] += 1
        key = (str(decided.get("category")), str(direction))
        buckets.setdefault(key, []).append(abs(amount))

    # Reported in the order the buckets are defined in, so the panel reads the same on every
    # import rather than reordering itself by whatever happened to be common in this file.
    ordered = [
        *[(c, "inflow") for c in prompts.CASHFLOW_INFLOW_CATEGORIES],
        *[(c, "outflow") for c in prompts.CASHFLOW_OUTFLOW_CATEGORIES],
    ]
    return Summary(
        total_rows=len(rows),
        classified=classified,
        unclassified=len(rows) - classified,
        inflow_count=inflow_count,
        inflow_minor=inflow_minor,
        outflow_count=outflow_count,
        outflow_minor=outflow_minor,
        net_minor=inflow_minor - outflow_minor,
        manually_reviewed=manually_reviewed,
        by_confidence=by_confidence,
        buckets=[
            Bucket(
                category=category,
                direction=direction,
                count=len(buckets[(category, direction)]),
                total_minor=sum(buckets[(category, direction)]),
            )
            for category, direction in ordered
            if (category, direction) in buckets
        ],
    )


@router.patch(
    "/{import_id}/cashflow/{row_index}",
    response_model=ClassifiedRow,
    dependencies=[Depends(require_client_header)],
)
def correct_row(
    import_id: str, row_index: int, body: Override, scope: Scope, supabase: Supabase
) -> ClassifiedRow:
    """The owner's correction, which the export then uses in place of the model's answer.

    The direction is not theirs to change either. A category that does not belong to this row's
    direction is refused here, not silently coerced, because the caller asked for something the
    data cannot support and should be told so.
    """
    record = _load_import(supabase, scope, import_id)
    rows = supabase.rest(
        "GET",
        "import_rows",
        params={
            "import_id": f"eq.{record['id']}",
            "row_index": f"eq.{row_index}",
            "select": "id,row_index,raw,extracted,reviewed,cashflow",
        },
    )
    if not rows:
        raise _not_found()
    row = rows[0]
    decided = row.get("cashflow") or {}
    direction = decided.get("direction") or cashflow.direction_of(_amount_of(row))
    if body.category not in cashflow.ALLOWED[direction]:
        raise HTTPException(
            status_code=422,
            detail=f"{body.category!r} is not a category for an {direction}",
        )
    patch = {
        **decided,
        "direction": direction,
        "category": body.category,
        "confidence": "high",
        "reason": "Set by the owner during review.",
        "source": "manual",
        "decided_at": datetime.now(UTC).isoformat(),
    }
    supabase.rest(
        "PATCH",
        "import_rows",
        params={"id": f"eq.{row['id']}"},
        json_body={"cashflow": patch},
        prefer="return=minimal",
    )
    return _view({**row, "cashflow": patch})


@router.get("/{import_id}/cashflow/export")
def export_csv(import_id: str, scope: Scope, supabase: Supabase) -> StreamingResponse:
    """The original file back, with the reviewed classification added to every row.

    Every original column survives, in its original order, with its original value: `raw` is the
    file's own row as parsed and is never written to, so what comes back out is what went in.
    The classification columns are appended, and where a column of that name already existed the
    new one is suffixed rather than overwriting it.

    `csv.writer` does the quoting, so a merchant name containing a comma, a quote or a newline
    round-trips rather than shifting every column after it.
    """
    record = _load_import(supabase, scope, import_id)
    rows = _cashflow_rows(supabase, record["id"])

    # Column order comes from the header the parser recorded, so the export matches the upload
    # even for columns no part of this application reads.
    mapping = record.get("mapping") or {}
    header: list[str] = [str(h) for h in (mapping.get("header") or [])]
    if not header:
        seen: dict[str, None] = {}
        for row in rows:
            for key in row.get("raw") or {}:
                seen.setdefault(str(key), None)
        header = list(seen)

    added = [c if c not in header else f"{c} (CashLens)" for c in EXPORT_COLUMNS]

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([*header, *added])
    for row in rows:
        raw = row.get("raw") or {}
        decided = row.get("cashflow") or {}
        amount = _amount_of(row)
        direction = decided.get("direction") or cashflow.direction_of(amount)
        writer.writerow(
            [
                *[raw.get(column, "") for column in header],
                direction,
                decided.get("category", ""),
                decided.get("confidence", ""),
                decided.get("reason", ""),
                {"manual": "Manual", "ai": "AI"}.get(str(decided.get("source")), ""),
                "Yes" if decided.get("source") == "manual" else "No",
            ]
        )

    body = buffer.getvalue()
    logger.info("cashflow.export", extra={"import_id": record["id"], "rows": len(rows)})
    return StreamingResponse(
        iter([body]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{_export_name(record)}"',
            "Cache-Control": "no-store",
        },
    )


def _export_name(record: dict[str, Any]) -> str:
    """`statement.csv` becomes `statement_classified.csv`.

    The label is what the owner named their upload, so it is reduced to characters that cannot
    break out of the header it is quoted into, and bounded in length.
    """
    label = str(record.get("file_label") or "import")
    stem = re.sub(r"\.(csv|pdf)$", "", label, flags=re.IGNORECASE)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")[:80] or "import"
    return f"{safe}_classified.csv"
