"""Scoped reads for one workspace: accounts with balances, paginated transactions, evidence.

Authorization. The workspace id in the path is resolved only through the
caller's own identity: a bearer access token (validated with the Auth server)
must own the workspace through a membership, otherwise the visitor cookie must
own it. A bearer token that fails validation is 401 even if a cookie exists.
Missing identity is 401; any workspace, account or transaction the caller
cannot reach is 404, whether or not it exists. Every query below carries the
resolved business id, so filters, cursors and transaction ids cannot widen
access.

Pagination. Keyset ordering by posted_date descending then id descending (ids
are UUIDs, so the tie-break is total). Pending rows carry the provider's
authorization date in posted_date and sort among the others by that date when
they are requested; removed rows are never returned. Pages read live data:
rows added, modified or removed between requests can shift page boundaries,
and no snapshot is held. A cursor is bound to the workspace, the filters and
the ordering it was issued for; reuse with different filters is rejected.

Evidence. The detail route returns an allowlisted view of provenance (event
operation, receipt time, provider transaction id, sync batch cursors), never
a raw provider payload.
"""

import base64
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.auth import bearer_token, require_features, visitor_from_request
from app.config import get_settings
from app.supabase import SupabaseClient, get_supabase

DEFAULT_LIMIT = 50
MAX_LIMIT = 100
CURSOR_VERSION = 1
SEARCH_ALLOWED = re.compile(r"[^A-Za-z0-9 '&-]")
TRANSACTION_FIELDS = (
    "id,account_id,posted_date,description,amount_minor,currency,status,"
    "source_type,revision,pending_transaction_id"
)
Supabase = Annotated[SupabaseClient, Depends(get_supabase)]


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


def _as_uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        return None


@dataclass(frozen=True)
class WorkspaceScope:
    workspace_id: str
    business_id: str
    data_mode: str
    slug: str
    name: str
    industry: str
    identity: Literal["visitor", "user"]
    # The opaque id of whoever is asking, taken from the validated session and never from the
    # request body. Model spend is counted against this, because the person spends and one
    # person can hold several workspaces.
    identity_id: str


def resolve_workspace(workspace_id: str, request: Request, supabase: Supabase) -> WorkspaceScope:
    """The caller's own workspace, or 401 without identity, or 404 for anything else."""
    wid = _as_uuid(workspace_id)
    if wid is None:
        raise _not_found()
    token = bearer_token(request)
    select = "id,business_id,business_data_mode"
    if token:
        user = supabase.auth_user(token)
        if not user or user.get("aud") != "authenticated" or not user.get("id"):
            raise HTTPException(status_code=401, detail="invalid or expired session")
        rows = supabase.rest(
            "GET",
            "workspaces",
            params={"id": f"eq.{wid}", "user_id": f"eq.{user['id']}", "select": select},
        )
        if rows:
            members = supabase.rest(
                "GET",
                "business_memberships",
                params={
                    "business_id": f"eq.{rows[0]['business_id']}",
                    "user_id": f"eq.{user['id']}",
                    "select": "business_id",
                },
            )
            if not members:
                rows = []
        identity: Literal["visitor", "user"] = "user"
        identity_id = str(user["id"])
    else:
        visitor_id = visitor_from_request(request, supabase)
        if visitor_id is None:
            raise HTTPException(status_code=401, detail="session required")
        rows = supabase.rest(
            "GET",
            "workspaces",
            params={"id": f"eq.{wid}", "visitor_id": f"eq.{visitor_id}", "select": select},
        )
        identity = "visitor"
        identity_id = visitor_id
    if not rows:
        raise _not_found()
    businesses = supabase.rest(
        "GET",
        "businesses",
        params={
            "id": f"eq.{rows[0]['business_id']}",
            # A business awaiting deletion is refused here, which is what stops uploads,
            # staging, confirmation and every other scoped read the moment it is requested.
            "deletion_requested_at": "is.null",
            "select": "id,slug,name,industry,data_mode",
        },
    )
    if not businesses:
        raise _not_found()
    business = businesses[0]
    # A synthetic-only deployment resolves nothing but demo businesses. This is the one place
    # every workspace-scoped route passes through, so the restriction holds for the analysis,
    # transaction and AI routes together rather than being repeated at each of them.
    if get_settings().public_demo_only and business["data_mode"] != "demo":
        raise _not_found()
    return WorkspaceScope(
        workspace_id=wid,
        business_id=business["id"],
        data_mode=business["data_mode"],
        slug=business["slug"],
        name=business["name"],
        industry=business["industry"],
        identity=identity,
        identity_id=identity_id,
    )


Scope = Annotated[WorkspaceScope, Depends(resolve_workspace)]
router = APIRouter(
    prefix="/api/workspaces", tags=["workspaces"], dependencies=[Depends(require_features)]
)


class Business(BaseModel):
    slug: str
    name: str
    industry: str


class Balance(BaseModel):
    kind: str
    provenance: str
    amount_minor: int
    as_of: str
    source_type: str


class Account(BaseModel):
    id: str
    name: str
    account_type: str
    mask: str | None
    source_type: str
    balances: list[Balance]


class WorkspaceSummary(BaseModel):
    workspace_id: str
    business: Business
    data_mode: str
    identity: str
    account_count: int
    posted_transaction_count: int


class AccountRef(BaseModel):
    id: str
    name: str
    mask: str | None


class TransactionRow(BaseModel):
    id: str
    posted_date: str
    description: str
    amount_minor: int
    currency: str
    status: str
    source_type: str
    revision: int
    replaces_pending: bool
    account: AccountRef


class TransactionFilters(BaseModel):
    account_id: str | None
    date_from: str | None
    date_to: str | None
    status: str
    q: str | None


class TransactionPage(BaseModel):
    items: list[TransactionRow]
    next_cursor: str | None
    total: int
    limit: int
    read_at: str
    filters: TransactionFilters


class ProviderEvent(BaseModel):
    kind: Literal["provider_event"] = "provider_event"
    operation: str
    received_at: str
    external_transaction_id: str
    batch_start_cursor: str | None
    batch_end_cursor: str | None
    batch_applied_at: str | None


class ImportRowSource(BaseModel):
    kind: Literal["import_row"] = "import_row"
    import_id: str
    file_label: str
    format: str
    row_index: int
    page_number: int | None
    source_locator: str
    imported_at: str | None
    extracted: dict[str, Any]
    reviewed: bool


class PendingLink(BaseModel):
    id: str
    posted_date: str
    amount_minor: int
    status: str


class TransactionDetail(BaseModel):
    transaction: TransactionRow
    external_id: str | None
    pending_transaction: PendingLink | None
    sources: list[ProviderEvent | ImportRowSource]


def _accounts(supabase: SupabaseClient, business_id: str) -> list[dict[str, Any]]:
    return supabase.rest(
        "GET",
        "accounts",
        params={
            "business_id": f"eq.{business_id}",
            "select": "id,name,account_type,mask,source_type",
            "order": "name",
        },
    )


def _latest_balances(supabase: SupabaseClient, business_id: str) -> dict[str, list[Balance]]:
    rows = supabase.rest(
        "GET",
        "balance_snapshots",
        params={
            "business_id": f"eq.{business_id}",
            "select": "account_id,kind,provenance,amount_minor,as_of,source_type",
            "order": "as_of.desc",
        },
    )
    latest: dict[str, dict[tuple[str, str], Balance]] = {}
    for row in rows:
        per_account = latest.setdefault(row["account_id"], {})
        key = (row["kind"], row["provenance"])
        if key not in per_account:  # rows arrive newest first
            per_account[key] = Balance(
                kind=row["kind"],
                provenance=row["provenance"],
                amount_minor=row["amount_minor"],
                as_of=row["as_of"],
                source_type=row["source_type"],
            )
    return {account: list(balances.values()) for account, balances in latest.items()}


@router.get("/{workspace_id}", response_model=WorkspaceSummary)
def workspace_summary(scope: Scope, supabase: Supabase) -> WorkspaceSummary:
    return WorkspaceSummary(
        workspace_id=scope.workspace_id,
        business=Business(slug=scope.slug, name=scope.name, industry=scope.industry),
        data_mode=scope.data_mode,
        identity=scope.identity,
        account_count=supabase.count("accounts", {"business_id": f"eq.{scope.business_id}"}),
        posted_transaction_count=supabase.count(
            "transactions",
            {
                "business_id": f"eq.{scope.business_id}",
                "status": "eq.posted",
                "removed_at": "is.null",
            },
        ),
    )


@router.get("/{workspace_id}/accounts", response_model=list[Account])
def workspace_accounts(scope: Scope, supabase: Supabase) -> list[Account]:
    balances = _latest_balances(supabase, scope.business_id)
    return [
        Account(
            id=a["id"],
            name=a["name"],
            account_type=a["account_type"],
            mask=a.get("mask"),
            source_type=a["source_type"],
            balances=sorted(balances.get(a["id"], []), key=lambda b: (b.kind, b.provenance)),
        )
        for a in _accounts(supabase, scope.business_id)
    ]


def _scope_hash(scope: WorkspaceScope, filters: TransactionFilters) -> str:
    material = json.dumps({"w": scope.workspace_id, **filters.model_dump()}, sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def _encode_cursor(scope: WorkspaceScope, filters: TransactionFilters, last: dict[str, Any]) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "d": last["posted_date"],
        "i": last["id"],
        "h": _scope_hash(scope, filters),
    }
    return (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def _decode_cursor(
    cursor: str, scope: WorkspaceScope, filters: TransactionFilters
) -> tuple[str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
        version, last_date, last_id, digest = payload["v"], payload["d"], payload["i"], payload["h"]
        date.fromisoformat(last_date)
        if version != CURSOR_VERSION or _as_uuid(last_id) is None or not isinstance(digest, str):
            raise ValueError
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    if digest != _scope_hash(scope, filters):
        raise HTTPException(
            status_code=400, detail="cursor does not match this workspace, filters or ordering"
        )
    return last_date, last_id


def _clean_search(q: str | None) -> str | None:
    if q is None:
        return None
    cleaned = SEARCH_ALLOWED.sub("", q).strip()[:80]
    return cleaned or None


@router.get("/{workspace_id}/transactions", response_model=TransactionPage)
def workspace_transactions(
    scope: Scope,
    supabase: Supabase,
    account_id: Annotated[str | None, Query()] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    status: Annotated[Literal["posted", "pending", "all"], Query()] = "posted",
    q: Annotated[str | None, Query(max_length=80)] = None,
    cursor: Annotated[str | None, Query(max_length=400)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> TransactionPage:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="'from' must not be after 'to'")
    accounts = {a["id"]: a for a in _accounts(supabase, scope.business_id)}
    if account_id is not None:
        account_uuid = _as_uuid(account_id)
        if account_uuid is None:
            raise HTTPException(status_code=422, detail="account_id must be a UUID")
        if account_uuid not in accounts:
            raise _not_found()
        account_id = account_uuid
    filters = TransactionFilters(
        account_id=account_id,
        date_from=date_from.isoformat() if date_from else None,
        date_to=date_to.isoformat() if date_to else None,
        status=status,
        q=_clean_search(q),
    )
    params: dict[str, str] = {"business_id": f"eq.{scope.business_id}", "removed_at": "is.null"}
    if status != "all":
        params["status"] = f"eq.{status}"
    if account_id:
        params["account_id"] = f"eq.{account_id}"
    if filters.date_from and filters.date_to:
        params["and"] = f"(posted_date.gte.{filters.date_from},posted_date.lte.{filters.date_to})"
    elif filters.date_from:
        params["posted_date"] = f"gte.{filters.date_from}"
    elif filters.date_to:
        params["posted_date"] = f"lte.{filters.date_to}"
    if filters.q:
        params["description"] = f"ilike.*{filters.q}*"
    total = supabase.count("transactions", params)
    if cursor:
        last_date, last_id = _decode_cursor(cursor, scope, filters)
        params["or"] = (
            f"(posted_date.lt.{last_date},and(posted_date.eq.{last_date},id.lt.{last_id}))"
        )
    rows = supabase.rest(
        "GET",
        "transactions",
        params={
            **params,
            "select": TRANSACTION_FIELDS,
            "order": "posted_date.desc,id.desc",
            "limit": str(limit + 1),
        },
    )
    page, more = rows[:limit], len(rows) > limit
    items = [
        TransactionRow(
            id=r["id"],
            posted_date=r["posted_date"],
            description=r["description"],
            amount_minor=r["amount_minor"],
            currency=r["currency"],
            status=r["status"],
            source_type=r["source_type"],
            revision=r["revision"],
            replaces_pending=r.get("pending_transaction_id") is not None,
            account=AccountRef(
                id=r["account_id"],
                name=accounts[r["account_id"]]["name"],
                mask=accounts[r["account_id"]].get("mask"),
            ),
        )
        for r in page
        if r["account_id"] in accounts
    ]
    return TransactionPage(
        items=items,
        next_cursor=_encode_cursor(scope, filters, page[-1]) if more and page else None,
        total=total,
        limit=limit,
        read_at=datetime.now(UTC).isoformat(timespec="seconds"),
        filters=filters,
    )


def _import_row_source(
    supabase: SupabaseClient, scope: WorkspaceScope, import_row_id: str
) -> ImportRowSource | None:
    """Allowlisted provenance for a row of an owner's confirmed statement import."""
    rows = supabase.rest(
        "GET",
        "import_rows",
        params={
            "id": f"eq.{import_row_id}",
            "select": "import_id,row_index,page_number,source_locator,extracted,reviewed",
        },
    )
    if not rows:
        return None
    row = rows[0]
    imports = supabase.rest(
        "GET",
        "document_imports",
        params={
            "id": f"eq.{row['import_id']}",
            "business_id": f"eq.{scope.business_id}",
            "select": "id,file_label,format,confirmed_at",
        },
    )
    if not imports:
        return None
    extracted = {
        k: v
        for k, v in (row.get("extracted") or {}).items()
        if k in ("posted_date", "amount_minor", "description", "external_ref", "balance_minor")
    }
    return ImportRowSource(
        import_id=imports[0]["id"],
        file_label=imports[0]["file_label"],
        format=imports[0].get("format") or "csv",
        row_index=row["row_index"],
        page_number=row.get("page_number"),
        source_locator=row["source_locator"],
        imported_at=imports[0].get("confirmed_at"),
        extracted=extracted,
        reviewed=bool(row.get("reviewed")),
    )


@router.get("/{workspace_id}/transactions/{transaction_id}", response_model=TransactionDetail)
def transaction_detail(scope: Scope, supabase: Supabase, transaction_id: str) -> TransactionDetail:
    tid = _as_uuid(transaction_id)
    if tid is None:
        raise _not_found()
    rows = supabase.rest(
        "GET",
        "transactions",
        params={
            "id": f"eq.{tid}",
            "business_id": f"eq.{scope.business_id}",
            "removed_at": "is.null",
            "select": TRANSACTION_FIELDS + ",external_id",
        },
    )
    if not rows:
        raise _not_found()
    row = rows[0]
    accounts = {a["id"]: a for a in _accounts(supabase, scope.business_id)}
    if row["account_id"] not in accounts:
        raise _not_found()
    account = accounts[row["account_id"]]
    pending: PendingLink | None = None
    if row.get("pending_transaction_id"):
        linked = supabase.rest(
            "GET",
            "transactions",
            params={
                "id": f"eq.{row['pending_transaction_id']}",
                "business_id": f"eq.{scope.business_id}",
                "select": "id,posted_date,amount_minor,status",
            },
        )
        if linked:
            pending = PendingLink(**linked[0])
    sources: list[ProviderEvent | ImportRowSource] = []
    for source in supabase.rest(
        "GET",
        "transaction_sources",
        params={"transaction_id": f"eq.{tid}", "select": "raw_event_id,import_row_id"},
    ):
        if source.get("import_row_id"):
            imported = _import_row_source(supabase, scope, source["import_row_id"])
            if imported:
                sources.append(imported)
            continue
        if not source.get("raw_event_id"):
            continue
        events = supabase.rest(
            "GET",
            "raw_events",
            params={
                "id": f"eq.{source['raw_event_id']}",
                "select": "operation,received_at,external_transaction_id,sync_batch_id",
            },
        )
        for event in events:
            batches = supabase.rest(
                "GET",
                "sync_batches",
                params={
                    "id": f"eq.{event['sync_batch_id']}",
                    "select": "start_cursor,end_cursor,applied_at",
                },
            )
            batch = batches[0] if batches else {}
            sources.append(
                ProviderEvent(
                    operation=event["operation"],
                    received_at=event["received_at"],
                    external_transaction_id=event["external_transaction_id"],
                    batch_start_cursor=batch.get("start_cursor"),
                    batch_end_cursor=batch.get("end_cursor"),
                    batch_applied_at=batch.get("applied_at"),
                )
            )
    return TransactionDetail(
        transaction=TransactionRow(
            id=row["id"],
            posted_date=row["posted_date"],
            description=row["description"],
            amount_minor=row["amount_minor"],
            currency=row["currency"],
            status=row["status"],
            source_type=row["source_type"],
            revision=row["revision"],
            replaces_pending=row.get("pending_transaction_id") is not None,
            account=AccountRef(id=account["id"], name=account["name"], mask=account.get("mask")),
        ),
        external_id=row.get("external_id"),
        pending_transaction=pending,
        sources=sources,
    )
