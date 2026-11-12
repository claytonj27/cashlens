"""Statement import routes for a private workspace.

Every route resolves the workspace through the caller's own identity (see
app.workspaces.resolve_workspace) and then requires a signed-in owner of a
private business; visitors and other users get 404. The browser uploads the
file straight to the private Storage bucket with a one-path signed token
issued here, so file bytes never travel through the web proxy or this API's
request path. Parsing, staging, review and confirmation all run under leased,
bounded jobs; confirmation itself is one database transaction
(confirm_import), so a failure publishes nothing and a retry is safe.
"""

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.auth import refuse_on_public_demo, require_client_header, require_features
from app.config import get_settings
from app.imports import budget, csvparse, extract, pdfparse
from app.imports.budget import Deadline, ProcessingTimeoutError
from app.imports.matching import (
    NEEDS_DECISION,
    ExistingTransaction,
    assess_statement,
    match_rows,
    period_overlap,
)
from app.supabase import SupabaseClient, SupabaseError, get_supabase
from app.workspaces import AccountRef, WorkspaceScope, _accounts, _as_uuid, resolve_workspace

logger = logging.getLogger(__name__)

BUCKET = "statements"
UPLOAD_TOKEN_SECONDS = 7200  # Supabase signed upload tokens are valid for two hours
LEASE_SECONDS = 180  # covers a slow parse with bounded upstream retries inside the function limit
ROWS_PAGE_LIMIT = 200
ROW_FIELDS = (
    "row_index,page_number,source_locator,raw,extracted,reviewed,flags,decision,decision_by,"
    "linked_transaction_id"
)
Supabase = Annotated[SupabaseClient, Depends(get_supabase)]


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


def private_scope(
    scope: Annotated[WorkspaceScope, Depends(resolve_workspace)],
) -> WorkspaceScope:
    """Only a signed-in owner of a private business can import statements."""
    if scope.identity != "user" or scope.data_mode != "private":
        raise _not_found()
    return scope


Scope = Annotated[WorkspaceScope, Depends(private_scope)]
router = APIRouter(
    prefix="/api/workspaces/{workspace_id}/imports",
    tags=["imports"],
    dependencies=[Depends(require_features), Depends(refuse_on_public_demo)],
)


# ------------------------------------------------------------------ models --
class NewAccount(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    account_type: Literal["checking", "savings"]
    mask: str | None = Field(default=None, pattern=r"^[0-9]{4}$")


class CreateImportRequest(BaseModel):
    account_id: str | None = None
    new_account: NewAccount | None = None
    file_label: str = Field(min_length=1, max_length=80)
    declared_bytes: int = Field(ge=1, le=csvparse.MAX_BYTES)

    @field_validator("file_label")
    @classmethod
    def _safe_label(cls, value: str) -> str:
        cleaned = "".join(ch for ch in value.strip() if ch.isalnum() or ch in " ._()-").strip()
        if not cleaned or not cleaned[0].isalnum():
            cleaned = "statement.csv"
        return cleaned[:80]


class UploadTarget(BaseModel):
    bucket: str
    path: str
    token: str
    expires_in_seconds: int
    max_bytes: int
    content_type: str = "text/csv"


class ImportSummary(BaseModel):
    id: str
    status: str
    format: str
    file_label: str
    account: AccountRef | None
    proposed_account: dict[str, Any] | None
    byte_size: int | None
    parser_version: str | None
    draft_revision: int
    confirmed_revision: int | None
    summary: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    created_at: str
    confirmed_at: str | None
    expires_at: str
    duplicate_of: str | None = None


class ImportDetail(ImportSummary):
    columns: list[str]
    sample: list[dict[str, str]]
    mapping: dict[str, Any] | None


class CreateImportResponse(BaseModel):
    import_: ImportSummary = Field(alias="import")
    upload: UploadTarget

    model_config = {"populate_by_name": True}


class MappingRequest(BaseModel):
    mapping: dict[str, Any]


class ImportRow(BaseModel):
    row_index: int
    page_number: int | None
    source_locator: str
    raw: dict[str, str]
    extracted: dict[str, Any]
    reviewed: dict[str, Any] | None
    effective: dict[str, Any]
    flags: list[str]
    decision: str
    decision_by: str
    linked_transaction_id: str | None
    conflict: dict[str, Any] | None
    blocking: bool
    needs_decision: bool


class RowsPage(BaseModel):
    items: list[ImportRow]
    next_after: int | None
    total: int
    flagged_total: int


class RowPatch(BaseModel):
    posted_date: str | None = None
    amount_minor: int | None = Field(default=None, ge=-10_000_000_000_000, le=10_000_000_000_000)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    decision: Literal["keep", "exclude", "link_existing", "replace_existing"] | None = None
    linked_transaction_id: str | None = None


class DecisionsRequest(BaseModel):
    """One decision for every row that still needs one (or a subset by kind)."""

    decision: Literal["keep", "link_existing", "exclude"]
    scope: Literal["undecided", "candidates", "conflicts"] = "undecided"


class DecisionsResult(BaseModel):
    updated: int
    skipped_without_target: int
    revision: int


class ConfirmRequest(BaseModel):
    revision: int = Field(ge=1)


class ConfirmResult(BaseModel):
    import_: ImportSummary = Field(alias="import")
    created: int
    linked: int
    replaced: int
    excluded: int
    already_confirmed: bool

    model_config = {"populate_by_name": True}


# ------------------------------------------------------------- helpers --
IMPORT_FIELDS = (
    "id,business_id,uploaded_by,account_id,proposed_account,status,format,object_path,"
    "object_deleted_at,"
    "file_label,declared_bytes,byte_size,content_hash,parser_version,mapping,summary,draft_revision,"
    "confirmed_revision,confirmed_at,cancelled_at,error_code,error_message,expires_at,created_at"
)


def _load_import(supabase: SupabaseClient, scope: WorkspaceScope, import_id: str) -> dict[str, Any]:
    iid = _as_uuid(import_id)
    if iid is None:
        raise _not_found()
    rows = supabase.rest(
        "GET",
        "document_imports",
        params={
            "id": f"eq.{iid}",
            "business_id": f"eq.{scope.business_id}",
            "select": IMPORT_FIELDS,
        },
    )
    if not rows:
        raise _not_found()
    return rows[0]


def _update_import(
    supabase: SupabaseClient, record: dict[str, Any], patch: dict[str, Any]
) -> dict[str, Any]:
    rows = supabase.rest(
        "PATCH",
        "document_imports",
        params={
            "id": f"eq.{record['id']}",
            "business_id": f"eq.{record['business_id']}",
            "select": IMPORT_FIELDS,
        },
        json_body=patch,
        prefer="return=representation",
    )
    if not rows:
        raise HTTPException(status_code=409, detail="import changed while processing")
    return rows[0]


def _summary(
    record: dict[str, Any], accounts: dict[str, dict[str, Any]], duplicate_of: str | None = None
) -> ImportSummary:
    account = accounts.get(record.get("account_id") or "")
    return ImportSummary(
        id=record["id"],
        status=record["status"],
        format=record.get("format") or "csv",
        file_label=record["file_label"],
        account=AccountRef(id=account["id"], name=account["name"], mask=account.get("mask"))
        if account
        else None,
        proposed_account=record.get("proposed_account"),
        byte_size=record.get("byte_size"),
        parser_version=record.get("parser_version"),
        draft_revision=record["draft_revision"],
        confirmed_revision=record.get("confirmed_revision"),
        summary=(record.get("summary") or None),
        error_code=record.get("error_code"),
        error_message=record.get("error_message"),
        created_at=record["created_at"],
        confirmed_at=record.get("confirmed_at"),
        expires_at=record["expires_at"],
        duplicate_of=duplicate_of,
    )


def _effective(row: dict[str, Any]) -> dict[str, Any]:
    extracted = dict(row.get("extracted") or {})
    extracted.pop("conflict", None)
    for key, value in (row.get("reviewed") or {}).items():
        if value is not None:
            extracted[key] = value
    return extracted


def _is_blocking(row: dict[str, Any]) -> bool:
    if row.get("decision") == "exclude":
        return False
    if NEEDS_DECISION in (row.get("flags") or []) and row.get("decision_by", "system") != "owner":
        return True
    effective = _effective(row)
    if "unsupported_currency" in (row.get("flags") or []) and not (row.get("reviewed") or {}):
        return True
    try:
        datetime.strptime(str(effective.get("posted_date")), "%Y-%m-%d")
    except (TypeError, ValueError):
        return True
    amount = effective.get("amount_minor")
    if not isinstance(amount, int) or amount == 0:
        return True
    description = effective.get("description")
    return not isinstance(description, str) or not description.strip()


def _row_model(row: dict[str, Any]) -> ImportRow:
    extracted = dict(row.get("extracted") or {})
    conflict = extracted.pop("conflict", None)
    return ImportRow(
        row_index=row["row_index"],
        page_number=row.get("page_number"),
        source_locator=row["source_locator"],
        raw=row.get("raw") or {},
        extracted=extracted,
        reviewed=row.get("reviewed"),
        effective=_effective(row),
        flags=row.get("flags") or [],
        decision=row["decision"],
        decision_by=row.get("decision_by", "system"),
        linked_transaction_id=row.get("linked_transaction_id"),
        conflict=conflict,
        blocking=_is_blocking(row),
        needs_decision=NEEDS_DECISION in (row.get("flags") or []),
    )


def _delete_object(supabase: SupabaseClient, record: dict[str, Any]) -> str | None:
    """Best-effort removal of the uploaded file; returns the deletion time or None."""
    if record.get("object_deleted_at"):
        return record["object_deleted_at"]
    try:
        supabase.storage_delete(BUCKET, [record["object_path"]])
    except SupabaseError:
        logger.warning("import.object_delete_failed", extra={"import_id": record["id"]})
        return None
    return datetime.now(UTC).isoformat()


def _lease(supabase: SupabaseClient, import_id: str, kind: str, key: str) -> dict[str, Any]:
    try:
        rows = supabase.rpc(
            "acquire_import_job",
            {
                "p_import_id": import_id,
                "p_kind": kind,
                "p_idempotency_key": key,
                "p_lease_seconds": LEASE_SECONDS,
            },
        )
    except SupabaseError as exc:
        text = str(exc)
        if "job_in_progress" in text:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "this import is already being processed; wait and try again",
                    "retry_after_seconds": LEASE_SECONDS,
                },
                headers={"Retry-After": str(LEASE_SECONDS)},
            ) from exc
        if "attempts_exhausted" in text:
            raise HTTPException(
                status_code=422, detail="processing attempts exhausted; cancel and upload again"
            ) from exc
        raise
    return rows[0]


def _finish(
    supabase: SupabaseClient, job: dict[str, Any], state: str, safe_error: str | None = None
) -> bool:
    """Close this worker's lease. False: the lease went to another worker and nothing changed."""
    closed = supabase.rpc(
        "finish_import_job",
        {
            "p_job_id": job["job_id"],
            "p_lease_token": job.get("lease_token"),
            "p_state": state,
            "p_safe_error": safe_error,
        },
    )
    if closed is not True:
        logger.warning("import.lease_lost", extra={"job_id": job["job_id"], "state": state})
        return False
    return True


def _existing_transactions(
    supabase: SupabaseClient, scope: WorkspaceScope, account_id: str | None
) -> list[ExistingTransaction]:
    if account_id is None:
        return []
    rows = supabase.rest_all(
        "transactions",
        {
            "business_id": f"eq.{scope.business_id}",
            "account_id": f"eq.{account_id}",
            "removed_at": "is.null",
            "select": "id,external_id,posted_date,amount_minor,description",
            "order": "posted_date,id",
        },
    )
    return [
        ExistingTransaction(
            r["id"], r.get("external_id"), r["posted_date"], r["amount_minor"], r["description"]
        )
        for r in rows
    ]


def _stage(
    supabase: SupabaseClient,
    scope: WorkspaceScope,
    record: dict[str, Any],
    data: bytes,
    mapping_payload: dict[str, Any] | None,
    revision: int,
    job: dict[str, Any],
) -> dict[str, Any]:
    """Parse, map, normalize, match and stage rows under the job lease. Returns the updated import.

    The format is decided from the bytes, never from the name. All writes happen in one
    database call that verifies this worker still holds the lease, so a stale attempt can
    neither overwrite a replacement's rows nor flip the import's state.
    """
    deadline = Deadline(seconds=budget.PROCESSING_BUDGET_SECONDS)
    if data.startswith(b"%PDF"):
        # Extraction runs in its own process with its own wall clock, CPU and memory limits.
        # The parent kills and reaps it on timeout; nothing here is published by the child.
        table = extract.extract_pdf(data)
        fmt, parser_version = "pdf", pdfparse.PARSER_VERSION
    else:
        table = csvparse.inspect_bytes(data)
        fmt, parser_version = "csv", csvparse.PARSER_VERSION
    deadline.check("parsing")
    if mapping_payload is None:
        mapping = csvparse.propose_mapping(table)
    else:
        try:
            mapping = csvparse.Mapping.from_json(mapping_payload, table.header)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    rows = csvparse.normalize(table, mapping)
    summary = csvparse.summarize(rows, mapping, table.statement)
    existing = _existing_transactions(supabase, scope, record.get("account_id"))
    deadline.check("history")
    outcomes = match_rows(rows, existing)
    summary["existing_in_period"] = period_overlap(
        existing, summary["period_start"], summary["period_end"]
    )
    summary["overlap"] = assess_statement(
        rows, outcomes, existing, summary["period_start"], summary["period_end"]
    )
    summary["decision_counts"] = {}
    payload: list[dict[str, Any]] = []
    for row in rows:
        outcome = outcomes[row.row_index]
        extracted = row.extracted()
        if outcome.conflict:
            extracted["conflict"] = outcome.conflict
        flags = list(row.flags) + outcome.flags
        for flag in outcome.flags:
            summary["flag_counts"][flag] = summary["flag_counts"].get(flag, 0) + 1
        summary["decision_counts"][outcome.decision] = (
            summary["decision_counts"].get(outcome.decision, 0) + 1
        )
        payload.append(
            {
                "row_index": row.row_index,
                "page_number": row.page_number,
                "source_locator": row.source_locator,
                "raw": row.raw,
                "extracted": extracted,
                "flags": flags,
                "decision": outcome.decision,
                "linked_transaction_id": outcome.linked_transaction_id,
            }
        )
    summary["blocking_rows"] = sum(
        1 for r in payload if _is_blocking({**r, "decision_by": "system"})
    )
    fields = {
        "format": fmt,
        "byte_size": table.byte_size,
        "content_hash": hashlib.sha256(data).hexdigest(),
        "parser_version": parser_version,
        "mapping": {
            "header": table.header,
            "source": table.source,
            "sample": [dict(zip(table.header, r, strict=False)) for r in table.rows[:5]],
            **mapping.to_json(),
        },
        "summary": summary,
        "draft_revision": revision,
    }
    deadline.check("staging")
    written = supabase.rpc(
        "stage_import",
        {
            "p_import_id": record["id"],
            "p_job_id": job["job_id"],
            "p_lease_token": job.get("lease_token"),
            "p_rows": payload,
            "p_fields": fields,
        },
    )
    if written != len(payload):
        raise SupabaseError(f"staging wrote {written} of {len(payload)} rows", transient=True)
    return _load_import(supabase, scope, record["id"])


def _fail(
    supabase: SupabaseClient, record: dict[str, Any], job: dict[str, Any], code: str, message: str
) -> dict[str, Any]:
    """Mark the import failed under the lease and remove the file; a lost lease changes nothing."""
    supabase.rpc(
        "fail_import",
        {
            "p_import_id": record["id"],
            "p_job_id": job["job_id"],
            "p_lease_token": job.get("lease_token"),
            "p_error_code": code,
            "p_error_message": message[:300],
        },
    )
    deleted = _delete_object(supabase, record)
    if deleted:
        supabase.rest(
            "PATCH",
            "document_imports",
            params={"id": f"eq.{record['id']}", "object_deleted_at": "is.null"},
            json_body={"object_deleted_at": deleted},
            prefer="return=minimal",
        )
    rows = supabase.rest(
        "GET",
        "document_imports",
        params={
            "id": f"eq.{record['id']}",
            "business_id": f"eq.{record['business_id']}",
            "select": IMPORT_FIELDS,
        },
    )
    return rows[0] if rows else record


# Both identity flags mean the account already holds a transaction with this row's reference.
IDENTITY_FLAGS = ("conflicting_revision", "already_imported")
KEEP_REFUSED = (
    "this row carries the same source reference as a transaction already in this account, so "
    "it cannot be added as a second transaction. Link it to that transaction, replace that "
    "transaction's values, or exclude the row."
)


def _refuse_keep(rows: list[dict[str, Any]]) -> None:
    """A reference the account already holds can never become a new transaction.

    The database refuses it at confirmation, which is the invariant; refusing it here tells
    the owner which row is wrong while they are still looking at it.
    """
    blocked = [r for r in rows if set(r.get("flags") or []) & set(IDENTITY_FLAGS)]
    if not blocked:
        return
    indexes = sorted(r["row_index"] for r in blocked if r.get("row_index") is not None)
    raise HTTPException(
        status_code=422,
        detail={
            "message": KEEP_REFUSED,
            "rows": indexes[:50],
            "count": len(blocked),
            "kind": "conflicting_reference",
        },
    )


def _lease_error(exc: SupabaseError) -> HTTPException | None:
    text = str(exc)
    if "lease_lost" in text:
        return HTTPException(
            status_code=409,
            detail="another attempt took over this import; reload to see its current state",
        )
    if "invalid_state" in text:
        return HTTPException(status_code=409, detail="the import is no longer waiting to be read")
    return None


# Retention cleanup used to happen opportunistically here, whenever an owner started another
# import. It now lives in app/retention.py behind its own operator switch, so expiry is a
# deliberate, bounded operation rather than a side effect of someone else's upload.


# ------------------------------------------------------------------ routes --
@router.get("", response_model=list[ImportSummary])
def list_imports(scope: Scope, supabase: Supabase) -> list[ImportSummary]:
    accounts = {a["id"]: a for a in _accounts(supabase, scope.business_id)}
    records = supabase.rest(
        "GET",
        "document_imports",
        params={
            "business_id": f"eq.{scope.business_id}",
            "select": IMPORT_FIELDS,
            "order": "created_at.desc",
            "limit": "50",
        },
    )
    return [_summary(r, accounts) for r in records]


@router.post(
    "",
    response_model=CreateImportResponse,
    status_code=201,
    dependencies=[Depends(require_client_header)],
)
def create_import(
    body: CreateImportRequest, scope: Scope, supabase: Supabase
) -> CreateImportResponse:
    if (body.account_id is None) == (body.new_account is None):
        raise HTTPException(
            status_code=422, detail="choose an existing account or describe a new one"
        )
    accounts = {a["id"]: a for a in _accounts(supabase, scope.business_id)}
    account_id: str | None = None
    if body.account_id is not None:
        account_id = _as_uuid(body.account_id)
        if account_id is None or account_id not in accounts:
            raise _not_found()
        if accounts[account_id]["source_type"] not in ("csv_import", "pdf_import"):
            raise HTTPException(
                status_code=422, detail="statements can only be imported into imported accounts"
            )
    # One import at a time in a business, which is what the workspace shows and what the
    # review page links to. The per-user quotas below are the ceiling across businesses.
    active = supabase.rest(
        "GET",
        "document_imports",
        params={
            "business_id": f"eq.{scope.business_id}",
            "status": "in.(awaiting_upload,staged)",
            "select": "id,status",
            "limit": "1",
        },
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "an import is already in progress; finish or cancel it first",
                "active_import_id": active[0]["id"],
            },
        )
    import_id = str(uuid.uuid4())
    path = f"{scope.business_id}/{import_id}.csv"
    # The caller is the authenticated owner of this workspace, never an id from the request.
    # The quota check and the row that holds the slot are one transaction in the database.
    _reserve_and_create(
        supabase,
        _user_id(scope, supabase),
        scope.business_id,
        {
            "id": import_id,
            "account_id": account_id,
            "proposed_account": body.new_account.model_dump() if body.new_account else None,
            "object_path": path,
            "file_label": body.file_label,
            "declared_bytes": body.declared_bytes,
            "expires_at": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        },
    )
    record = _load_import(supabase, scope, import_id)
    try:
        token = supabase.storage_signed_upload(BUCKET, path)
    except SupabaseError as exc:
        _update_import(
            supabase,
            record,
            {
                "status": "failed",
                "error_code": "upload_unavailable",
                "error_message": "could not prepare the upload",
            },
        )
        raise HTTPException(status_code=502, detail="could not prepare the upload") from exc
    logger.info("import.created", extra={"import_id": import_id, "business_id": scope.business_id})
    return CreateImportResponse(
        import_=_summary(record, accounts),
        upload=UploadTarget(
            bucket=BUCKET,
            path=path,
            token=token,
            expires_in_seconds=UPLOAD_TOKEN_SECONDS,
            max_bytes=csvparse.MAX_BYTES,
        ),
    )


QUOTA_ADVICE = {
    "active_imports": (
        "you already have the most uploads in progress that one account may have at once; "
        "finish or cancel one of them first"
    ),
    "rate_hour": "you have started a lot of uploads in the last hour; try again shortly",
    "rate_day": "you have started a lot of uploads today; try again tomorrow",
    "outstanding_bytes": (
        "the statements you have uploaded and not yet confirmed take up the space one account "
        "may use; confirm or cancel an import to free it"
    ),
}


def _reserve_and_create(
    supabase: SupabaseClient, user_id: str, business_id: str, fields: dict[str, Any]
) -> None:
    """Check every upload quota and create the import row, atomically, or refuse.

    The row is the reservation, so it is written inside the same transaction and the same
    advisory lock as the counts. Two requests therefore cannot both take the last slot.
    Counts span every business the caller belongs to, not just this one.
    """
    settings = get_settings()
    rows = supabase.rpc(
        "reserve_and_create_import",
        {
            "p_user_id": user_id,
            "p_business_id": business_id,
            "p_import_id": fields["id"],
            "p_account_id": fields["account_id"],
            "p_proposed_account": fields["proposed_account"],
            "p_object_path": fields["object_path"],
            "p_file_label": fields["file_label"],
            "p_declared_bytes": fields["declared_bytes"],
            "p_expires_at": fields["expires_at"],
            "p_max_active": settings.quota_active_imports,
            "p_max_per_hour": settings.quota_starts_per_hour,
            "p_max_per_day": settings.quota_starts_per_day,
            "p_max_outstanding_bytes": settings.quota_outstanding_bytes,
        },
    )
    outcome = rows[0] if rows else {}
    if outcome.get("allowed"):
        return
    reason = str(outcome.get("reason") or "active_imports")
    detail: dict[str, Any] = {
        "message": QUOTA_ADVICE.get(reason, "this upload is over a limit on your account"),
        "kind": "quota",
        "limit": reason,
    }
    # Only the caller's own usage, never anything about anyone else.
    if outcome.get("active_import_id"):
        detail["active_import_id"] = outcome["active_import_id"]
    if reason == "outstanding_bytes":
        detail["outstanding_bytes"] = outcome.get("outstanding_bytes")
    logger.info("import.quota_refused", extra={"limit": reason, "business_id": business_id})
    raise HTTPException(status_code=409, detail=detail)


def _user_id(scope: WorkspaceScope, supabase: SupabaseClient) -> str:
    rows = supabase.rest(
        "GET", "workspaces", params={"id": f"eq.{scope.workspace_id}", "select": "user_id"}
    )
    if not rows or not rows[0].get("user_id"):
        raise _not_found()
    return str(rows[0]["user_id"])


@router.post(
    "/{import_id}/uploaded",
    response_model=ImportSummary,
    dependencies=[Depends(require_client_header)],
)
def uploaded(import_id: str, scope: Scope, supabase: Supabase) -> ImportSummary:
    """The browser finished its upload: validate the object, parse, match and stage rows."""
    record = _load_import(supabase, scope, import_id)
    accounts = {a["id"]: a for a in _accounts(supabase, scope.business_id)}
    if record["status"] == "staged":
        return _summary(record, accounts)
    if record["status"] != "awaiting_upload":
        raise HTTPException(status_code=409, detail=f"import is {record['status']}")
    job = _lease(supabase, record["id"], "parse", f"parse:{record['id']}:1")
    if job.get("state") == "succeeded":
        return _summary(_load_import(supabase, scope, import_id), accounts)
    try:
        info = supabase.storage_object_info(BUCKET, record["object_path"])
        if info is None:
            _finish(supabase, job, "failed", "upload not found")
            raise HTTPException(
                status_code=422, detail="no uploaded file found; upload the file first"
            )
        size = int(info.get("size") or 0)
        if size <= 0 or size > csvparse.MAX_BYTES:
            failed = _fail(
                supabase, record, job, "too_large", "The uploaded file is empty or exceeds 10 MB."
            )
            _finish(supabase, job, "failed", "size")
            return _summary(failed, accounts)
        data = supabase.storage_download(BUCKET, record["object_path"], csvparse.MAX_BYTES)
        content_hash = hashlib.sha256(data).hexdigest()
        duplicate = supabase.rest(
            "GET",
            "document_imports",
            params={
                "business_id": f"eq.{scope.business_id}",
                "content_hash": f"eq.{content_hash}",
                "status": "in.(staged,confirmed)",
                "id": f"neq.{record['id']}",
                "select": "id,account_id,proposed_account,status",
            },
        )
        same_account = [
            d
            for d in duplicate
            if (record.get("account_id") and d.get("account_id") == record["account_id"])
            or (
                record.get("proposed_account")
                and d.get("proposed_account") == record["proposed_account"]
            )
            or (
                record.get("account_id")
                and d.get("status") == "confirmed"
                and d.get("account_id") == record["account_id"]
            )
        ]
        if same_account:
            deleted = _delete_object(supabase, record)
            cancelled = _update_import(
                supabase,
                record,
                {
                    "status": "cancelled",
                    "cancelled_at": datetime.now(UTC).isoformat(),
                    "object_deleted_at": deleted,
                    "error_code": "duplicate_file",
                    "error_message": "This exact file was already imported into this account.",
                },
            )
            _finish(supabase, job, "succeeded")
            # The active slot and the bytes are released by the cancellation above. The
            # initiation is NOT withdrawn: refunding it would let a known-duplicate file be
            # re-uploaded in a loop to farm the rate limit back to zero.
            return _summary(cancelled, accounts, duplicate_of=same_account[0]["id"])
        try:
            staged = _stage(supabase, scope, record, data, None, 1, job)
        except csvparse.UnsupportedFileError as exc:
            if exc.code in extract.RETRYABLE_CODES:
                # The file is fine; this run was not. Leave the upload in Storage and the
                # import awaiting_upload so the owner can simply read it again.
                _finish(supabase, job, "failed", exc.code)
                raise HTTPException(
                    status_code=503,
                    detail={"message": str(exc), "retryable": True},
                ) from exc
            failed = _fail(supabase, record, job, exc.code, str(exc))
            _finish(supabase, job, "failed", exc.code)
            return _summary(failed, accounts)
        except ProcessingTimeoutError as exc:
            _finish(supabase, job, "failed", f"timeout:{exc.stage}")
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "reading the file took too long; nothing was changed, try again",
                    "retryable": True,
                },
            ) from exc
    except HTTPException as exc:
        _finish(supabase, job, "failed", str(exc.detail)[:120])
        raise
    except SupabaseError as exc:
        # The file stays in Storage and the import stays awaiting_upload: reading can be retried.
        lease = _lease_error(exc)
        if lease is not None:
            raise lease from exc
        logger.warning(
            "import.parse_failed",
            extra={
                "import_id": record["id"],
                "error_class": type(exc).__name__,
                "upstream_status": exc.status,
            },
        )
        _finish(supabase, job, "failed", f"upstream {exc.status or type(exc).__name__}")
        raise
    except Exception as exc:
        logger.error(
            "import.parse_crashed",
            extra={"import_id": record["id"], "error_class": type(exc).__name__},
        )
        _finish(supabase, job, "failed", type(exc).__name__)
        raise
    _finish(supabase, job, "succeeded")
    logger.info(
        "import.staged",
        extra={"import_id": record["id"], "rows": staged.get("summary", {}).get("row_count")},
    )
    return _summary(staged, accounts)


@router.get("/{import_id}", response_model=ImportDetail)
def import_detail(import_id: str, scope: Scope, supabase: Supabase) -> ImportDetail:
    record = _load_import(supabase, scope, import_id)
    accounts = {a["id"]: a for a in _accounts(supabase, scope.business_id)}
    mapping = dict(record.get("mapping") or {})
    header = mapping.pop("header", [])
    sample = mapping.pop("sample", [])
    return ImportDetail(
        **_summary(record, accounts).model_dump(),
        columns=header,
        sample=sample,
        mapping=mapping or None,
    )


@router.put(
    "/{import_id}/mapping",
    response_model=ImportDetail,
    dependencies=[Depends(require_client_header)],
)
def set_mapping(
    import_id: str, body: MappingRequest, scope: Scope, supabase: Supabase
) -> ImportDetail:
    record = _load_import(supabase, scope, import_id)
    if record["status"] != "staged":
        raise HTTPException(status_code=409, detail=f"import is {record['status']}")
    header = (record.get("mapping") or {}).get("header") or []
    try:
        csvparse.Mapping.from_json(body.mapping, header)  # reject bad input before any lease
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    revision = int(record["draft_revision"]) + 1
    job = _lease(supabase, record["id"], "parse", f"parse:{record['id']}:{revision}")
    try:
        data = supabase.storage_download(BUCKET, record["object_path"], csvparse.MAX_BYTES)
        if hashlib.sha256(data).hexdigest() != record.get("content_hash"):
            _finish(supabase, job, "failed", "content changed")
            raise HTTPException(
                status_code=409, detail="the uploaded file changed; cancel and upload again"
            )
        staged = _stage(supabase, scope, record, data, body.mapping, revision, job)
    except HTTPException as exc:
        _finish(supabase, job, "failed", str(exc.detail)[:120])
        raise
    except csvparse.UnsupportedFileError as exc:
        _finish(supabase, job, "failed", exc.code)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProcessingTimeoutError as exc:
        _finish(supabase, job, "failed", f"timeout:{exc.stage}")
        raise HTTPException(
            status_code=503,
            detail={"message": "re-reading the file took too long; try again", "retryable": True},
        ) from exc
    except SupabaseError as exc:
        lease = _lease_error(exc)
        if lease is not None:
            raise lease from exc
        _finish(supabase, job, "failed", f"upstream {exc.status or type(exc).__name__}")
        raise
    except Exception as exc:
        _finish(supabase, job, "failed", type(exc).__name__)
        raise
    _finish(supabase, job, "succeeded")
    return _detail_from(staged, scope, supabase)


def _detail_from(
    record: dict[str, Any], scope: WorkspaceScope, supabase: SupabaseClient
) -> ImportDetail:
    accounts = {a["id"]: a for a in _accounts(supabase, scope.business_id)}
    mapping = dict(record.get("mapping") or {})
    header = mapping.pop("header", [])
    sample = mapping.pop("sample", [])
    return ImportDetail(
        **_summary(record, accounts).model_dump(),
        columns=header,
        sample=sample,
        mapping=mapping or None,
    )


@router.get("/{import_id}/rows", response_model=RowsPage)
def import_rows(
    import_id: str,
    scope: Scope,
    supabase: Supabase,
    after: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=ROWS_PAGE_LIMIT)] = 100,
    only: Annotated[Literal["all", "flagged"], Query()] = "all",
) -> RowsPage:
    record = _load_import(supabase, scope, import_id)
    params: dict[str, str] = {"import_id": f"eq.{record['id']}"}
    if only == "flagged":
        params["flags"] = "neq.{}"
    total = supabase.count("import_rows", params)
    flagged_total = supabase.count(
        "import_rows", {"import_id": f"eq.{record['id']}", "flags": "neq.{}"}
    )
    if after is not None:
        params["row_index"] = f"gt.{after}"
    rows = supabase.rest(
        "GET",
        "import_rows",
        params={
            **params,
            "select": ROW_FIELDS,
            "order": "row_index",
            "limit": str(limit + 1),
        },
    )
    page, more = rows[:limit], len(rows) > limit
    return RowsPage(
        items=[_row_model(r) for r in page],
        next_after=page[-1]["row_index"] if more and page else None,
        total=total,
        flagged_total=flagged_total,
    )


@router.patch(
    "/{import_id}/rows/{row_index}",
    response_model=ImportRow,
    dependencies=[Depends(require_client_header)],
)
def patch_row(
    import_id: str, row_index: int, body: RowPatch, scope: Scope, supabase: Supabase
) -> ImportRow:
    record = _load_import(supabase, scope, import_id)
    if record["status"] != "staged":
        raise HTTPException(status_code=409, detail=f"import is {record['status']}")
    rows = supabase.rest(
        "GET",
        "import_rows",
        params={
            "import_id": f"eq.{record['id']}",
            "row_index": f"eq.{row_index}",
            "select": "id," + ROW_FIELDS,
        },
    )
    if not rows:
        raise _not_found()
    row = rows[0]
    if body.decision == "keep":
        _refuse_keep([{**row, "row_index": row_index}])
    reviewed = dict(row.get("reviewed") or {})
    if body.posted_date is not None:
        try:
            datetime.strptime(body.posted_date, "%Y-%m-%d")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="posted_date must be YYYY-MM-DD") from exc
        reviewed["posted_date"] = body.posted_date
    if body.amount_minor is not None:
        if body.amount_minor == 0:
            raise HTTPException(status_code=422, detail="amount must not be zero")
        reviewed["amount_minor"] = body.amount_minor
    if body.description is not None:
        reviewed["description"] = body.description.strip()
    patch: dict[str, Any] = {"reviewed": reviewed or None}
    decision = body.decision or row["decision"]
    linked = row.get("linked_transaction_id")
    if body.decision is not None:
        if body.decision in ("link_existing", "replace_existing"):
            target = _as_uuid(body.linked_transaction_id or linked or "")
            if target is None:
                raise HTTPException(status_code=422, detail="a target transaction is required")
            if record.get("account_id") is None:
                raise HTTPException(
                    status_code=422, detail="a new account has no existing transactions to link"
                )
            found = supabase.rest(
                "GET",
                "transactions",
                params={
                    "id": f"eq.{target}",
                    "business_id": f"eq.{scope.business_id}",
                    "account_id": f"eq.{record['account_id']}",
                    "removed_at": "is.null",
                    "select": "id",
                },
            )
            if not found:
                raise _not_found()
            linked = target
        else:
            linked = None
        patch["decision"] = decision
        patch["linked_transaction_id"] = linked
        patch["decision_by"] = "owner"
    updated = supabase.rest(
        "PATCH",
        "import_rows",
        params={
            "id": f"eq.{row['id']}",
            "import_id": f"eq.{record['id']}",
            "select": ROW_FIELDS,
        },
        json_body=patch,
        prefer="return=representation",
    )
    bumped = supabase.rest(
        "PATCH",
        "document_imports",
        params={
            "id": f"eq.{record['id']}",
            "draft_revision": f"eq.{record['draft_revision']}",
            "select": "id",
        },
        json_body={"draft_revision": int(record["draft_revision"]) + 1},
        prefer="return=representation",
    )
    if not bumped:
        raise HTTPException(status_code=409, detail="the import changed; reload and try again")
    return _row_model(updated[0])


@router.post(
    "/{import_id}/confirm",
    response_model=ConfirmResult,
    dependencies=[Depends(require_client_header)],
)
def confirm(
    import_id: str, body: ConfirmRequest, scope: Scope, supabase: Supabase
) -> ConfirmResult:
    record = _load_import(supabase, scope, import_id)
    accounts = {a["id"]: a for a in _accounts(supabase, scope.business_id)}
    if record["status"] == "confirmed" and record.get("confirmed_revision") == body.revision:
        result = (record.get("summary") or {}).get("result") or {}
        return ConfirmResult(
            import_=_summary(record, accounts),
            created=result.get("created", 0),
            linked=result.get("linked", 0),
            replaced=result.get("replaced", 0),
            excluded=result.get("excluded", 0),
            already_confirmed=True,
        )
    if record["status"] != "staged":
        raise HTTPException(status_code=409, detail=f"import is {record['status']}")
    if int(record["draft_revision"]) != body.revision:
        raise HTTPException(
            status_code=409, detail="the review changed; reload and confirm the current revision"
        )
    staged_rows = supabase.rest_all(
        "import_rows",
        {
            "import_id": f"eq.{record['id']}",
            "select": (
                "row_index,extracted,reviewed,flags,decision,decision_by,linked_transaction_id"
            ),
            "order": "row_index",
        },
    )
    undecided = [
        r["row_index"]
        for r in staged_rows
        if NEEDS_DECISION in (r.get("flags") or [])
        and r.get("decision_by", "system") != "owner"
        and r.get("decision") != "exclude"
    ]
    if undecided:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "some rows need your decision before confirming",
                "rows": undecided[:50],
                "count": len(undecided),
                "kind": "undecided",
            },
        )
    blocking = [r["row_index"] for r in staged_rows if _is_blocking(r)]
    if blocking:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "some rows still need attention",
                "rows": blocking[:50],
                "count": len(blocking),
            },
        )
    if not any(r["decision"] != "exclude" for r in staged_rows):
        raise HTTPException(status_code=422, detail="every row is excluded; nothing to import")
    job = _lease(supabase, record["id"], "confirm", f"confirm:{record['id']}:{body.revision}")
    try:
        rows = supabase.rpc(
            "confirm_import",
            {
                "p_import_id": record["id"],
                "p_user_id": _user_id(scope, supabase),
                "p_expected_revision": body.revision,
            },
        )
    except SupabaseError as exc:
        text = str(exc)
        _finish(supabase, job, "failed", text[:120])
        if "not_found" in text:
            raise _not_found() from exc
        if "revision_conflict" in text or "invalid_state" in text:
            raise HTTPException(
                status_code=409, detail="the import changed; reload and try again"
            ) from exc
        if "undecided_rows" in text:
            raise HTTPException(
                status_code=422, detail="some rows need your decision before confirming"
            ) from exc
        if "blocking_rows" in text or "link_target_missing" in text:
            raise HTTPException(status_code=422, detail="some rows still need attention") from exc
        if "transactions_import_reference_identity" in text:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "a source reference in this file was claimed by another confirmation "
                        "while this one was running; reload the review and try again"
                    ),
                    "rows": [],
                },
            ) from exc
        if "duplicate_source_reference" in text:
            index = text.split("duplicate_source_reference:")[1].split('"')[0].split(",")[0]
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "a row's source reference already exists in this account; "
                        "link or exclude it"
                    ),
                    "rows": [int(index)] if index.isdigit() else [],
                },
            ) from exc
        logger.warning(
            "import.confirm_failed",
            extra={
                "import_id": record["id"],
                "error_class": type(exc).__name__,
                "upstream_status": exc.status,
            },
        )
        raise
    except Exception as exc:
        _finish(supabase, job, "failed", type(exc).__name__)
        raise
    _finish(supabase, job, "succeeded")
    outcome = rows[0]
    fresh = _load_import(supabase, scope, import_id)
    if not fresh.get("object_deleted_at"):
        deleted = _delete_object(supabase, fresh)
        if deleted:
            fresh = _update_import(supabase, fresh, {"object_deleted_at": deleted})
    accounts = {a["id"]: a for a in _accounts(supabase, scope.business_id)}
    logger.info(
        "import.confirmed",
        extra={
            "import_id": record["id"],
            "created_count": outcome["created_count"],
            "linked_count": outcome["linked_count"],
            "replaced_count": outcome["replaced_count"],
        },
    )
    return ConfirmResult(
        import_=_summary(fresh, accounts),
        created=outcome["created_count"],
        linked=outcome["linked_count"],
        replaced=outcome["replaced_count"],
        excluded=outcome["excluded_count"],
        already_confirmed=bool(outcome["already_confirmed"]),
    )


@router.post(
    "/{import_id}/decisions",
    response_model=DecisionsResult,
    dependencies=[Depends(require_client_header)],
)
def decide_rows(
    import_id: str, body: DecisionsRequest, scope: Scope, supabase: Supabase
) -> DecisionsResult:
    """One owner decision for every row that still needs one. Linking needs a paired transaction."""
    record = _load_import(supabase, scope, import_id)
    if record["status"] != "staged":
        raise HTTPException(status_code=409, detail=f"import is {record['status']}")
    kinds = {
        "undecided": (NEEDS_DECISION,),
        "candidates": ("duplicate_candidate",),
        "conflicts": ("conflicting_revision",),
    }[body.scope]
    rows = supabase.rest_all(
        "import_rows",
        {
            "import_id": f"eq.{record['id']}",
            "flags": "cs.{needs_decision}",
            "select": "id,row_index,flags,linked_transaction_id",
            "order": "row_index",
        },
    )
    chosen = [r for r in rows if any(k in (r.get("flags") or []) for k in kinds)]
    if body.decision == "keep":
        _refuse_keep(chosen)
    if body.decision == "link_existing":
        skipped = [r for r in chosen if not r.get("linked_transaction_id")]
        chosen = [r for r in chosen if r.get("linked_transaction_id")]
    else:
        skipped = []
    for start in range(0, len(chosen), 200):
        ids = ",".join(r["id"] for r in chosen[start : start + 200])
        patch: dict[str, Any] = {"decision": body.decision, "decision_by": "owner"}
        if body.decision != "link_existing":
            patch["linked_transaction_id"] = None
        supabase.rest(
            "PATCH",
            "import_rows",
            params={"id": f"in.({ids})", "import_id": f"eq.{record['id']}"},
            json_body=patch,
            prefer="return=minimal",
        )
    bumped = supabase.rest(
        "PATCH",
        "document_imports",
        params={
            "id": f"eq.{record['id']}",
            "draft_revision": f"eq.{record['draft_revision']}",
            "select": "draft_revision",
        },
        json_body={"draft_revision": int(record["draft_revision"]) + 1},
        prefer="return=representation",
    )
    if not bumped:
        raise HTTPException(status_code=409, detail="the import changed; reload and try again")
    return DecisionsResult(
        updated=len(chosen),
        skipped_without_target=len(skipped),
        revision=bumped[0]["draft_revision"],
    )


@router.post(
    "/{import_id}/cancel",
    response_model=ImportSummary,
    dependencies=[Depends(require_client_header)],
)
def cancel(import_id: str, scope: Scope, supabase: Supabase) -> ImportSummary:
    record = _load_import(supabase, scope, import_id)
    try:
        outcome = supabase.rpc(
            "cancel_import",
            {"p_import_id": record["id"], "p_user_id": _user_id(scope, supabase)},
        )
    except SupabaseError as exc:
        text = str(exc)
        if "not_found" in text:
            raise _not_found() from exc
        if "invalid_state" in text:
            raise HTTPException(
                status_code=409, detail=f"a {record['status']} import cannot be cancelled"
            ) from exc
        raise
    # The import is already cancelled and its rows are gone. Removing the file is a separate
    # system, so a failure here leaves object_deleted_at null and cancelling again retries it.
    fresh = _load_import(supabase, scope, import_id)
    if not fresh.get("object_deleted_at"):
        deleted = _delete_object(supabase, fresh)
        if deleted:
            fresh = _update_import(supabase, fresh, {"object_deleted_at": deleted})
    result = outcome[0] if outcome else {}
    logger.info(
        "import.cancelled",
        extra={
            "import_id": record["id"],
            "already_cancelled": bool(result.get("already_cancelled")),
            "rows_removed": result.get("rows_removed"),
            "file_removed": bool(fresh.get("object_deleted_at")),
        },
    )
    accounts = {a["id"]: a for a in _accounts(supabase, scope.business_id)}
    return _summary(fresh, accounts)
