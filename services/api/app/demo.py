"""Demo exploration: anonymous visitor sessions over the three synthetic businesses."""

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.auth import CurrentVisitor, ensure_visitor, require_client_header, require_features
from app.supabase import SupabaseClient, SupabaseError, get_supabase

router = APIRouter(prefix="/api/demo", tags=["demo"], dependencies=[Depends(require_features)])
Supabase = Annotated[SupabaseClient, Depends(get_supabase)]


class DemoBusiness(BaseModel):
    slug: str
    name: str
    industry: str


class DemoSession(BaseModel):
    businesses: list[DemoBusiness]


class DemoWorkspace(BaseModel):
    workspace_id: str
    business: DemoBusiness


class AccountSummary(BaseModel):
    name: str
    account_type: str
    mask: str | None
    current_balance_minor: int | None
    balance_as_of: str | None


class DemoWorkspaceSummary(BaseModel):
    workspace_id: str
    business: DemoBusiness
    accounts: list[AccountSummary]
    posted_transaction_count: int
    note: str


def _demo_businesses(supabase: SupabaseClient) -> list[DemoBusiness]:
    rows = supabase.rest(
        "GET",
        "businesses",
        params={"data_mode": "eq.demo", "select": "slug,name,industry", "order": "slug"},
    )
    return [DemoBusiness(**row) for row in rows]


@router.get("/businesses", response_model=list[DemoBusiness])
def list_businesses(supabase: Supabase) -> list[DemoBusiness]:
    return _demo_businesses(supabase)


@router.post("/session", response_model=DemoSession, dependencies=[Depends(require_client_header)])
def start_session(request: Request, response: Response, supabase: Supabase) -> DemoSession:
    ensure_visitor(request, response, supabase)
    return DemoSession(businesses=_demo_businesses(supabase))


@router.post(
    "/workspaces/{slug}",
    response_model=DemoWorkspace,
    dependencies=[Depends(require_client_header)],
)
def open_workspace(
    slug: str, request: Request, response: Response, supabase: Supabase
) -> DemoWorkspace:
    visitor_id = ensure_visitor(request, response, supabase)
    try:
        rows = supabase.rpc("ensure_demo_workspace", {"p_visitor_id": visitor_id, "p_slug": slug})
    except SupabaseError as exc:
        if "unknown demo business" in str(exc):
            raise HTTPException(status_code=404, detail="unknown demo business") from exc
        raise
    row = rows[0]
    return DemoWorkspace(
        workspace_id=row["workspace_id"],
        business=DemoBusiness(slug=row["slug"], name=row["name"], industry=row["industry"]),
    )


def _workspace_summary(
    supabase: SupabaseClient, workspace_id: str, business: dict[str, Any]
) -> DemoWorkspaceSummary:
    business_id = business["id"]
    accounts = supabase.rest(
        "GET",
        "accounts",
        params={
            "business_id": f"eq.{business_id}",
            "select": "id,name,account_type,mask",
            "order": "name",
        },
    )
    balances = supabase.rest(
        "GET",
        "balance_snapshots",
        params={
            "business_id": f"eq.{business_id}",
            "kind": "eq.current",
            "select": "account_id,amount_minor,as_of",
            "order": "as_of.desc",
        },
    )
    latest: dict[str, dict[str, Any]] = {}
    for snapshot in balances:
        latest.setdefault(snapshot["account_id"], snapshot)
    posted = supabase.count(
        "transactions",
        {"business_id": f"eq.{business_id}", "status": "eq.posted", "removed_at": "is.null"},
    )
    return DemoWorkspaceSummary(
        workspace_id=workspace_id,
        business=DemoBusiness(
            slug=business["slug"], name=business["name"], industry=business["industry"]
        ),
        accounts=[
            AccountSummary(
                name=a["name"],
                account_type=a["account_type"],
                mask=a.get("mask"),
                current_balance_minor=(latest.get(a["id"]) or {}).get("amount_minor"),
                balance_as_of=(latest.get(a["id"]) or {}).get("as_of"),
            )
            for a in accounts
        ],
        posted_transaction_count=posted,
        note=(
            "Synthetic demo data. The figures above are calculated from these "
            "transactions; written insights arrive in a later milestone."
        ),
    )


# The shape the schema enforces on business slugs. A path value that cannot be a slug is
# refused here rather than handed to a filter, so the query only ever carries a slug.
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")


@router.get("/workspaces/{slug}", response_model=DemoWorkspaceSummary)
def get_workspace(
    slug: str, visitor_id: CurrentVisitor, supabase: Supabase
) -> DemoWorkspaceSummary:
    if not SLUG.match(slug):
        raise HTTPException(status_code=404, detail="unknown demo business")
    businesses = supabase.rest(
        "GET",
        "businesses",
        params={"slug": f"eq.{slug}", "data_mode": "eq.demo", "select": "id,slug,name,industry"},
    )
    if not businesses:
        raise HTTPException(status_code=404, detail="unknown demo business")
    business = businesses[0]
    workspaces = supabase.rest(
        "GET",
        "workspaces",
        params={
            "visitor_id": f"eq.{visitor_id}",
            "business_id": f"eq.{business['id']}",
            "select": "id",
        },
    )
    if not workspaces:
        raise HTTPException(status_code=404, detail="open this demo business first")
    return _workspace_summary(supabase, workspaces[0]["id"], business)
