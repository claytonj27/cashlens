"""Signed-in users: identity, private business provisioning and the private workspace shell.

Every route derives the user from a validated access token and reads only
rows reachable through that user's memberships. Demo data is never attached
to a private business.
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.auth import (
    CurrentUser,
    refuse_on_public_demo,
    require_client_header,
    require_features,
)
from app.config import get_settings
from app.discovery.auth import role_of
from app.supabase import SupabaseClient, SupabaseError, get_supabase
from app.workspaces import _as_uuid, resolve_workspace

router = APIRouter(
    prefix="/api",
    tags=["private"],
    dependencies=[Depends(require_features), Depends(refuse_on_public_demo)],
)
Supabase = Annotated[SupabaseClient, Depends(get_supabase)]

logger = logging.getLogger(__name__)


class PrivateBusiness(BaseModel):
    id: str
    name: str


class WorkspaceRef(BaseModel):
    business: PrivateBusiness
    workspace_id: str | None


class DiscoverySummary(BaseModel):
    """Enough for the login redirect and the header: which assignment, and is the core done.

    `core_complete` reads the assignment's cached `status`, which the API writes after each save;
    the derivation itself lives in the portal's progress code. Until an answer has been saved the
    status is `not_started`, so a fresh assignment reports false rather than guessing.
    """

    assignment_id: str
    template_id: str
    title: str
    status: str
    core_complete: bool


class Me(BaseModel):
    user_id: str
    email: str | None
    # The earliest business stays in these two fields so existing callers are unaffected.
    business: PrivateBusiness | None
    workspace_id: str | None
    # Every private business the caller belongs to, earliest first.
    businesses: list[WorkspaceRef] = []
    # The Discovery portal (ADR 0014), additively. The role is read from `public.profiles` and
    # is `member` for every account outside the engagement; `discovery` is null for anyone with
    # no assignment and for every deployment where the portal is switched off, so a redirect
    # keyed on it can never send a person to a route that does not exist.
    role: str = "member"
    discovery: DiscoverySummary | None = None


class CreateBusinessRequest(BaseModel):
    """An explicit request for an additional business, made idempotent by the caller's key."""

    name: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$")


class ImportLocation(BaseModel):
    """Where an import actually lives, resolved from the record rather than from a link."""

    import_id: str
    status: str
    workspace_id: str
    business: PrivateBusiness


class ProvisionRequest(BaseModel):
    business_name: str | None = Field(default=None, max_length=120)


class ProvisionResult(BaseModel):
    business: PrivateBusiness
    workspace_id: str
    created: bool


class PrivateWorkspace(BaseModel):
    business: PrivateBusiness
    workspace_id: str
    account_count: int
    transaction_count: int
    statements: dict[str, Any]


def _memberships(supabase: SupabaseClient, user_id: str) -> list[dict[str, Any]]:
    """Every private business the caller belongs to, earliest first, with its workspace."""
    rows = supabase.rest(
        "GET",
        "business_memberships",
        params={
            "user_id": f"eq.{user_id}",
            "select": "business_id,role",
            "order": "created_at",
        },
    )
    if not rows:
        return []
    ids = ",".join(r["business_id"] for r in rows)
    businesses = {
        b["id"]: b
        for b in supabase.rest(
            "GET",
            "businesses",
            params={
                "id": f"in.({ids})",
                "data_mode": "eq.private",
                # A business the owner asked to delete is gone as far as every other route
                # is concerned, even while its files are still being removed.
                "deletion_requested_at": "is.null",
                "select": "id,name",
            },
        )
    }
    workspaces = {
        w["business_id"]: w["id"]
        for w in supabase.rest(
            "GET",
            "workspaces",
            params={
                "user_id": f"eq.{user_id}",
                "business_id": f"in.({ids})",
                "select": "id,business_id",
            },
        )
        if w.get("business_id")
    }
    return [
        {"business": businesses[r["business_id"]], "workspace_id": workspaces.get(r["business_id"])}
        for r in rows
        if r["business_id"] in businesses
    ]


def _membership(supabase: SupabaseClient, user_id: str) -> dict[str, Any] | None:
    """The caller's earliest private business, which is what onboarding resumes."""
    memberships = _memberships(supabase, user_id)
    return memberships[0] if memberships else None


def _discovery_summary(supabase: SupabaseClient, user_id: str) -> DiscoverySummary | None:
    """The caller's own assignment, reduced to what the redirect and the header need, or None.

    Filtered on the caller's user id from the validated session, like everything else here. The
    assignment's own `title_override` wins over the template's title when set.
    """
    # The newest assignment, as the portal's own `/api/discovery/me` picks it, so the redirect
    # and the portal agree on which template a person moved between templates is on.
    rows = supabase.rest(
        "GET",
        "discovery_assignments",
        params={
            "user_id": f"eq.{user_id}",
            "select": "id,template_id,title_override,status",
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    if not rows:
        return None
    row = rows[0]
    templates = supabase.rest(
        "GET",
        "discovery_templates",
        params={"id": f"eq.{row['template_id']}", "select": "title"},
    )
    title = row.get("title_override") or (templates[0]["title"] if templates else "Discovery")
    status = str(row.get("status") or "not_started")
    return DiscoverySummary(
        assignment_id=str(row["id"]),
        template_id=str(row["template_id"]),
        title=str(title),
        status=status,
        core_complete=status in ("core_complete", "complete"),
    )


@router.get("/me", response_model=Me)
def me(user: CurrentUser, supabase: Supabase) -> Me:
    memberships = _memberships(supabase, user.id)
    first = memberships[0] if memberships else None
    # The portal's two fields are read only where the portal exists. Elsewhere both are their
    # neutral values and no table is consulted, so a deployment without the portal pays nothing
    # for it on every page load, cannot fail on a table it does not use, and cannot point a
    # person at a route that would answer 404. `member` is also the never-elevate answer.
    if get_settings().discovery_enabled:
        role, discovery = role_of(supabase, user.id), _discovery_summary(supabase, user.id)
    else:
        role, discovery = "member", None
    return Me(
        user_id=user.id,
        email=user.email,
        business=PrivateBusiness(**first["business"]) if first else None,
        workspace_id=first["workspace_id"] if first else None,
        businesses=[
            WorkspaceRef(business=PrivateBusiness(**m["business"]), workspace_id=m["workspace_id"])
            for m in memberships
        ],
        role=role,
        discovery=discovery,
    )


@router.post(
    "/private/provision",
    response_model=ProvisionResult,
    dependencies=[Depends(require_client_header)],
)
def provision(body: ProvisionRequest, user: CurrentUser, supabase: Supabase) -> ProvisionResult:
    """Create or resume the caller's private business. Idempotent; never touches demo data."""
    try:
        rows = supabase.rpc(
            "provision_private_business",
            {"p_user_id": user.id, "p_business_name": body.business_name},
        )
    except SupabaseError as exc:
        if "business name required" in str(exc):
            raise HTTPException(status_code=422, detail="business name required") from exc
        raise
    row = rows[0]
    return ProvisionResult(
        business=PrivateBusiness(id=row["business_id"], name=row["business_name"]),
        workspace_id=row["workspace_id"],
        created=bool(row["created"]),
    )


@router.post(
    "/private/businesses",
    response_model=ProvisionResult,
    dependencies=[Depends(require_client_header)],
)
def create_business(
    body: CreateBusinessRequest, user: CurrentUser, supabase: Supabase
) -> ProvisionResult:
    """Create an additional private business for the caller.

    Ownership comes from the validated token, never from the request body. The
    caller's key makes the request idempotent: a retry or a concurrent duplicate
    returns the business the first call created instead of making another.
    """
    try:
        rows = supabase.rpc(
            "provision_private_business",
            {
                "p_user_id": user.id,
                "p_business_name": body.name,
                "p_idempotency_key": body.idempotency_key,
            },
        )
    except SupabaseError as exc:
        text = str(exc)
        if "business name required" in text or "idempotency key" in text:
            raise HTTPException(status_code=422, detail="business name and key required") from exc
        raise
    row = rows[0]
    return ProvisionResult(
        business=PrivateBusiness(id=row["business_id"], name=row["business_name"]),
        workspace_id=row["workspace_id"],
        created=bool(row["created"]),
    )


class DeleteBusinessRequest(BaseModel):
    """The owner types the business name; the server checks it rather than trusting the page."""

    confirm_name: str = Field(min_length=1, max_length=120)


class DeletionStatus(BaseModel):
    business_id: str
    business_name: str
    status: str  # removing | completed
    requested_at: str | None = None
    data_removed_at: str | None = None
    objects_pending: int = 0
    objects_retrying: int = 0
    transactions_removed: int | None = None
    imports_ended: int | None = None


def _deletion_state(
    supabase: SupabaseClient, business_id: str, name: str, row: dict[str, Any] | None
) -> DeletionStatus:
    """Pending file removals decide the status; a file is never assumed gone."""
    queued = supabase.rest(
        "GET",
        "storage_cleanup",
        params={
            "business_id": f"eq.{business_id}",
            "deleted_at": "is.null",
            "select": "id,attempts",
        },
    )
    pending = len(queued)
    return DeletionStatus(
        business_id=business_id,
        business_name=name,
        status="completed" if pending == 0 else "removing",
        requested_at=(row or {}).get("deletion_requested_at"),
        data_removed_at=(row or {}).get("data_removed_at"),
        objects_pending=pending,
        objects_retrying=sum(1 for q in queued if int(q.get("attempts") or 0) > 0),
    )


def _owned_business(
    supabase: SupabaseClient, business_id: str, user_id: str
) -> dict[str, Any] | None:
    """The caller's business, including one already awaiting deletion, or None."""
    bid = _as_uuid(business_id)
    if bid is None:
        return None
    member = supabase.rest(
        "GET",
        "business_memberships",
        params={
            "business_id": f"eq.{bid}",
            "user_id": f"eq.{user_id}",
            "select": "business_id",
        },
    )
    if not member:
        return None
    rows = supabase.rest(
        "GET",
        "businesses",
        params={
            "id": f"eq.{bid}",
            "data_mode": "eq.private",
            "select": "id,name,created_by,deletion_requested_at,data_removed_at",
        },
    )
    return rows[0] if rows else None


@router.post(
    "/private/businesses/{business_id}/deletion",
    response_model=DeletionStatus,
    dependencies=[Depends(require_client_header)],
)
def delete_business(
    business_id: str, body: DeleteBusinessRequest, user: CurrentUser, supabase: Supabase
) -> DeletionStatus:
    """Remove a private business and everything in it. Idempotent; files finish afterwards."""
    business = _owned_business(supabase, business_id, user.id)
    if business is None:
        raise HTTPException(status_code=404, detail="not found")
    if body.confirm_name.strip() != business["name"]:
        raise HTTPException(
            status_code=422,
            detail="type the business name exactly as it is shown to confirm the deletion",
        )
    try:
        rows = supabase.rpc(
            "request_business_deletion",
            {"p_business_id": business["id"], "p_user_id": user.id},
        )
    except SupabaseError as exc:
        text = str(exc)
        if "not_the_owner" in text:
            raise HTTPException(
                status_code=403,
                detail="only the account that created this business can delete it",
            ) from exc
        if "shared_business" in text:
            raise HTTPException(
                status_code=409,
                detail="this business has other members; it cannot be deleted from here",
            ) from exc
        if "not_found" in text:
            raise HTTPException(status_code=404, detail="not found") from exc
        raise
    outcome = rows[0] if rows else {}
    fresh = _owned_business(supabase, business_id, user.id)
    state = _deletion_state(
        supabase, business["id"], outcome.get("business_name") or business["name"], fresh
    )
    if state.objects_pending == 0:
        # Nothing is left to remove, so finish here rather than leaving a tombstone waiting
        # for a cleanup run that is not scheduled. The owner is told it is done only once
        # it actually is.
        supabase.rpc("finalize_business_deletion", {"p_business_id": business["id"]})
    state.transactions_removed = outcome.get("transactions_removed")
    state.imports_ended = outcome.get("imports_ended")
    logger.info(
        "business.deletion_requested",
        extra={
            "business_id": business["id"],
            "already_requested": bool(outcome.get("already_requested")),
            "objects_queued": outcome.get("objects_queued"),
            "objects_pending": state.objects_pending,
        },
    )
    return state


@router.get("/private/businesses/{business_id}/deletion", response_model=DeletionStatus)
def deletion_status(business_id: str, user: CurrentUser, supabase: Supabase) -> DeletionStatus:
    """How far the removal has got. A missing business means it finished and was cleared."""
    business = _owned_business(supabase, business_id, user.id)
    if business is None:
        raise HTTPException(status_code=404, detail="not found")
    if business.get("deletion_requested_at") is None:
        raise HTTPException(status_code=409, detail="this business is not being deleted")
    return _deletion_state(supabase, business["id"], business["name"], business)


@router.get("/private/imports/{import_id}", response_model=ImportLocation)
def import_location(import_id: str, user: CurrentUser, supabase: Supabase) -> ImportLocation:
    """The workspace an import actually belongs to, for callers holding only its id.

    The answer comes from the import record and the caller's membership, so a link
    cannot move an import to another business or point confirmation elsewhere.
    """
    iid = _as_uuid(import_id)
    if iid is None:
        raise HTTPException(status_code=404, detail="not found")
    records = supabase.rest(
        "GET", "document_imports", params={"id": f"eq.{iid}", "select": "id,business_id,status"}
    )
    if not records:
        raise HTTPException(status_code=404, detail="not found")
    business_id = records[0]["business_id"]
    members = supabase.rest(
        "GET",
        "business_memberships",
        params={
            "business_id": f"eq.{business_id}",
            "user_id": f"eq.{user.id}",
            "select": "business_id",
        },
    )
    if not members:
        raise HTTPException(status_code=404, detail="not found")
    businesses = supabase.rest(
        "GET",
        "businesses",
        params={"id": f"eq.{business_id}", "data_mode": "eq.private", "select": "id,name"},
    )
    workspaces = supabase.rest(
        "GET",
        "workspaces",
        params={"user_id": f"eq.{user.id}", "business_id": f"eq.{business_id}", "select": "id"},
    )
    if not businesses or not workspaces:
        raise HTTPException(status_code=404, detail="not found")
    return ImportLocation(
        import_id=records[0]["id"],
        status=records[0]["status"],
        workspace_id=workspaces[0]["id"],
        business=PrivateBusiness(**businesses[0]),
    )


@router.get("/private/workspace", response_model=PrivateWorkspace)
def private_workspace(
    user: CurrentUser,
    supabase: Supabase,
    request: Request,
    workspace: Annotated[str | None, Query()] = None,
) -> PrivateWorkspace:
    """The caller's earliest business, or the one named by `workspace` when given.

    A named workspace goes through the same resolver the scoped routes use, so a
    workspace the caller does not own, or a malformed id, is 404 and never falls
    back to another business.
    """
    if workspace is not None:
        scope = resolve_workspace(workspace, request, supabase)
        if scope.identity != "user" or scope.data_mode != "private":
            raise HTTPException(status_code=404, detail="not found")
        membership = {
            "business": {"id": scope.business_id, "name": scope.name},
            "workspace_id": scope.workspace_id,
        }
    else:
        membership = _membership(supabase, user.id)
    if membership is None or membership["workspace_id"] is None:
        raise HTTPException(status_code=404, detail="no private business yet")
    business_id = membership["business"]["id"]
    accounts = supabase.count("accounts", {"business_id": f"eq.{business_id}"})
    transactions = supabase.count(
        "transactions", {"business_id": f"eq.{business_id}", "removed_at": "is.null"}
    )
    confirmed = supabase.count(
        "document_imports", {"business_id": f"eq.{business_id}", "status": "eq.confirmed"}
    )
    active = supabase.rest(
        "GET",
        "document_imports",
        params={
            "business_id": f"eq.{business_id}",
            "status": "in.(awaiting_upload,staged)",
            "select": "id,status",
            "limit": "1",
        },
    )
    if confirmed:
        plural = "s" if confirmed != 1 else ""
        message = (
            f"{confirmed} statement{plural} imported. Import another CSV to extend the history."
        )
    else:
        message = (
            "No statements imported yet. Import a CSV export from your bank to add transactions."
        )
    return PrivateWorkspace(
        business=PrivateBusiness(**membership["business"]),
        workspace_id=membership["workspace_id"],
        account_count=accounts,
        transaction_count=transactions,
        statements={
            "count": confirmed,
            "uploads_available": True,
            "message": message,
            "active_import_id": active[0]["id"] if active else None,
            "active_import_status": active[0]["status"] if active else None,
        },
    )
