"""Retention cleanup, invoked by an operator or a scheduler. Never runs on its own.

The route answers 404 unless RETENTION_CLEANUP_ENABLED is true and the caller presents
RETENTION_CLEANUP_TOKEN. It has its own switch on purpose: OPS_DIAGNOSTICS_ENABLED gates the
whole diagnostics router, including a database probe and a paid model probe, so enabling
cleanup through that flag would switch those on at the same time.

Retention rules, one per class (ADR 0010):

* an upload that never reached review expires 24 hours after it was created;
* an import awaiting review expires seven days after it first reached review;
* confirmed transactions and the provenance behind them never expire;
* the source file of a confirmed, cancelled or failed import is removed by the lifecycle, and
  a removal that failed is retried here with its attempts and last error recorded.

The database bounds the work: every selection carries its limit into SQL. Two runs cannot
interleave, because the sweep functions take an advisory lock and the second run returns
having done nothing. A file is reported as removed only after Storage confirmed it.
"""

import logging
import secrets
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.imports.budget import Deadline
from app.supabase import SupabaseClient, SupabaseError, get_supabase

logger = logging.getLogger(__name__)

Supabase = Annotated[SupabaseClient, Depends(get_supabase)]

BUCKET = "statements"
# Well inside the platform's 300 s function limit, leaving room to report what was done.
RUN_BUDGET_SECONDS = 120.0


def require_cleanup_operator(request: Request) -> Settings:
    """404 unless cleanup is switched on and the caller holds the token."""
    settings = get_settings()
    token = settings.retention_cleanup_token
    if not settings.retention_cleanup_enabled or token is None:
        raise HTTPException(status_code=404, detail="Not Found")
    presented = request.headers.get("x-cleanup-token", "")
    expected = token.get_secret_value()
    if not presented or not secrets.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="cleanup token required")
    return settings


router = APIRouter(
    prefix="/api/maintenance",
    tags=["retention"],
    dependencies=[Depends(require_cleanup_operator)],
    include_in_schema=False,
)


class CleanupRequest(BaseModel):
    """Batch sizes, and an optional narrowing to resources the caller already knows.

    `business_id` and `visitor_ids` filter on top of the retention conditions, never instead
    of them: a narrowed run can reach fewer rows than an unnarrowed one, never more, and never
    a row that is not yet eligible.
    """

    limit: int = Field(default=200, ge=1, le=1000)
    visitor_limit: int = Field(default=500, ge=0, le=5000)
    business_id: str | None = None
    visitor_ids: list[str] | None = None


class CleanupResult(BaseModel):
    imports_expired: int
    rows_removed: int
    objects_deleted: int
    objects_failed: int
    objects_pending: int
    businesses_finalized: int = 0
    visitors_removed: int
    stopped_early: bool
    duration_ms: int


def _rpc(supabase: SupabaseClient, name: str, body: dict[str, Any]) -> Any:
    return supabase.rpc(name, body)


@router.post("/retention", response_model=CleanupResult)
def run_retention(body: CleanupRequest, supabase: Supabase) -> CleanupResult:
    started = time.monotonic()
    deadline = Deadline(seconds=RUN_BUDGET_SECONDS)
    scope = {"p_business_id": body.business_id} if body.business_id else {"p_business_id": None}

    # 1. Database lifecycle. Ending an import is atomic and invalidates any open lease, so a
    #    parse that returns afterwards is refused rather than staging into an expired import.
    expired = _rpc(supabase, "retention_sweep_imports", {"p_limit": body.limit, **scope}) or []
    rows_removed = sum(int(e.get("rows_removed") or 0) for e in expired)

    # 2. Storage outcomes, tracked separately. A file counts as removed only once Storage said so.
    deleted = failed = 0
    stopped_early = False
    backlog = _rpc(supabase, "retention_storage_backlog", {"p_limit": body.limit, **scope}) or []
    for item in backlog:
        if deadline.remaining <= 5.0:
            stopped_early = True
            break
        try:
            supabase.storage_delete(BUCKET, [item["object_path"]])
        except SupabaseError as exc:
            attempts = _rpc(
                supabase,
                "retention_mark_object_failed",
                {"p_import_id": item["import_id"], "p_error": f"storage {exc.status}"},
            )
            failed += 1
            logger.warning(
                "retention.object_delete_failed",
                extra={
                    "import_id": item["import_id"],
                    "attempts": attempts,
                    "upstream_status": exc.status,
                },
            )
            continue
        if _rpc(supabase, "retention_mark_object_deleted", {"p_import_id": item["import_id"]}):
            deleted += 1

    # 3. Files queued by a business deletion. This inventory has no foreign key, so it
    #    outlives the rows it came from and a failure stays retryable afterwards.
    queued_deleted = queued_failed = 0
    queue = _rpc(supabase, "storage_cleanup_backlog", {"p_limit": body.limit, **scope}) or []
    for item in queue:
        if deadline.remaining <= 5.0:
            stopped_early = True
            break
        try:
            supabase.storage_delete(item["bucket"], [item["object_path"]])
        except SupabaseError as exc:
            attempts = _rpc(
                supabase,
                "storage_cleanup_mark_failed",
                {"p_id": item["id"], "p_error": f"storage {exc.status}"},
            )
            queued_failed += 1
            logger.warning(
                "retention.queued_delete_failed",
                extra={"attempts": attempts, "upstream_status": exc.status},
            )
            continue
        if _rpc(supabase, "storage_cleanup_mark_deleted", {"p_id": item["id"]}):
            queued_deleted += 1

    # 4. A deleted business keeps a tombstone until every file it owned is confirmed gone,
    #    so the owner can watch the removal finish and nothing is reported early.
    businesses_finalized = 0
    for tombstone in _rpc(supabase, "pending_business_deletions", {"p_limit": 50}) or []:
        if tombstone["objects_pending"] > 0:
            continue
        gone = _rpc(
            supabase, "finalize_business_deletion", {"p_business_id": tombstone["business_id"]}
        )
        if gone:
            businesses_finalized += 1

    # 5. Anonymous demo identities. Their workspaces follow by cascade; the shared demo
    #    businesses have no foreign key to a visitor and are never reached from here.
    visitors = 0
    if body.visitor_limit:
        visitors = (
            _rpc(
                supabase,
                "retention_sweep_visitors",
                {"p_limit": body.visitor_limit, "p_visitor_ids": body.visitor_ids},
            )
            or 0
        )

    remaining = _rpc(supabase, "retention_storage_backlog", {"p_limit": 1000, **scope}) or []
    still_queued = _rpc(supabase, "storage_cleanup_backlog", {"p_limit": 1000, **scope}) or []
    result = CleanupResult(
        imports_expired=len(expired),
        rows_removed=rows_removed,
        objects_deleted=deleted + queued_deleted,
        objects_failed=failed + queued_failed,
        objects_pending=len(remaining) + len(still_queued),
        businesses_finalized=businesses_finalized,
        visitors_removed=int(visitors),
        stopped_early=stopped_early,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    # Counts only. No object path, file name, business name or financial value is ever logged.
    logger.info("retention.run", extra=result.model_dump())
    return result
