"""Incremental sync: stage every page from the committed cursor, then apply atomically.

The caller passes a provider (anything with `fetch(cursor) -> ProviderPage`)
and a connection id. All pages are fetched first, bounded, restarting from the
committed cursor if the provider reports a pagination mutation. They are then
staged as one sync batch with raw events (payload as received plus the
canonical reading) and applied in one database transaction by
`apply_sync_batch`, which also commits the next cursor. A failed apply marks
the batch failed and leaves the cursor and transactions untouched.
"""

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from app.ingestion.contract import BalanceObservation, PaginationMutationError, ProviderPage
from app.supabase import SupabaseClient, SupabaseError

logger = logging.getLogger(__name__)
MAX_PAGES = 200
MAX_RESTARTS = 3


class Provider(Protocol):
    def fetch(self, cursor: str | None) -> ProviderPage: ...


@dataclass(frozen=True)
class SyncResult:
    batch_id: str | None
    pages: int
    events: int
    added: int
    modified: int
    removed: int
    unchanged: int
    end_cursor: str | None
    restarts: int


class SyncError(Exception):
    """The apply step failed; the batch is marked failed and the cursor is unchanged."""


def _fetch_all(provider: Provider, start_cursor: str | None) -> tuple[list[ProviderPage], int]:
    restarts = 0
    while True:
        pages: list[ProviderPage] = []
        cursor = start_cursor
        try:
            while True:
                page = provider.fetch(cursor)
                pages.append(page)
                if not page.has_more:
                    return pages, restarts
                cursor = page.next_cursor
                if len(pages) >= MAX_PAGES:
                    raise SyncError("too many pages in one sync")
        except PaginationMutationError:
            restarts += 1
            if restarts > MAX_RESTARTS:
                raise SyncError("provider kept mutating during pagination") from None


def sync_connection(supabase: SupabaseClient, connection_id: str, provider: Provider) -> SyncResult:
    connection = supabase.rest(
        "GET",
        "connections",
        params={"id": f"eq.{connection_id}", "select": "id,business_id,sync_cursor,status"},
    )
    if not connection:
        raise SyncError("unknown connection")
    start_cursor = connection[0].get("sync_cursor")
    pages, restarts = _fetch_all(provider, start_cursor)
    events = [event for page in pages for event in page.events]
    end_cursor = pages[-1].next_cursor
    if not events and end_cursor == start_cursor:
        return SyncResult(None, len(pages), 0, 0, 0, 0, 0, start_cursor, restarts)
    batch = supabase.rest(
        "POST",
        "sync_batches",
        params={"select": "id"},
        json_body={
            "connection_id": connection_id,
            "start_cursor": start_cursor,
            "end_cursor": end_cursor,
            "status": "staged",
        },
        prefer="return=representation",
    )[0]
    payload: list[dict[str, Any]] = [
        {
            "sync_batch_id": batch["id"],
            "sequence": sequence,
            "operation": event.operation,
            "external_transaction_id": event.external_id,
            "payload": event.payload,
            "canonical": event.canonical.as_json() if event.canonical else None,
        }
        for sequence, event in enumerate(events)
    ]
    for start in range(0, len(payload), 500):
        supabase.rest(
            "POST", "raw_events", json_body=payload[start : start + 500], prefer="return=minimal"
        )
    try:
        applied = supabase.rpc("apply_sync_batch", {"p_batch_id": batch["id"]})[0]
    except SupabaseError as exc:
        supabase.rest(
            "PATCH",
            "sync_batches",
            params={"id": f"eq.{batch['id']}"},
            json_body={"status": "failed"},
            prefer="return=minimal",
        )
        logger.warning(
            "sync.apply_failed", extra={"connection_id": connection_id, "batch_id": batch["id"]}
        )
        raise SyncError(str(exc)[:200]) from exc
    return SyncResult(
        batch_id=batch["id"],
        pages=len(pages),
        events=len(events),
        added=int(applied["added_count"]),
        modified=int(applied["modified_count"]),
        removed=int(applied["removed_count"]),
        unchanged=int(applied["unchanged_count"]),
        end_cursor=applied["end_cursor"],
        restarts=restarts,
    )


def publish_balances(
    supabase: SupabaseClient,
    business_id: str,
    connection_id: str,
    observations: list[BalanceObservation],
) -> int:
    """Upsert bank-observed balances for the connection's accounts; returns the count written."""
    accounts = supabase.rest(
        "GET",
        "accounts",
        params={
            "connection_id": f"eq.{connection_id}",
            "business_id": f"eq.{business_id}",
            "select": "id,external_account_id",
        },
    )
    by_external = {a["external_account_id"]: a["id"] for a in accounts}
    rows = [
        {
            "business_id": business_id,
            "account_id": by_external[o.external_account_id],
            "kind": o.kind,
            "provenance": "bank_observed",
            "source_type": "synthetic_provider",
            "amount_minor": o.amount_minor,
            "as_of": o.as_of,
        }
        for o in observations
        if o.external_account_id in by_external
    ]
    if rows:
        supabase.rest(
            "POST",
            "balance_snapshots",
            params={"on_conflict": "account_id,kind,provenance,as_of"},
            json_body=rows,
            prefer="resolution=merge-duplicates,return=minimal",
        )
    return len(rows)
