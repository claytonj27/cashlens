"""Caller identification for the API.

Two identities exist. A demo visitor is an opaque id in an HttpOnly cookie
that this service issued and that must exist in the visitors table; it grants
access to synthetic demo workspaces only. A signed-in user presents a Supabase
Auth access token in the Authorization header, validated with the Auth server;
private rows are reachable only through that user's memberships. Neither
identity is ever taken from a query parameter or a request body.

State-changing routes also require a custom client header, which browsers do
not attach to cross-site requests, so cookie-authenticated demo routes are
not usable as CSRF targets.

Product routes also depend on the deployment's feature switch: where
`Settings.features_active` is false (production by default) they answer 404
before any identity or database work, so a health-only deployment exposes
nothing but health.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response

from app.config import get_settings
from app.supabase import SupabaseClient, SupabaseError, get_supabase

VISITOR_COOKIE = "cl_visitor"
CLIENT_HEADER = "x-cashlens-client"
CLIENT_HEADER_VALUE = "web"
VISITOR_COOKIE_MAX_AGE = 60 * 60 * 24 * 30

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str | None


def require_client_header(request: Request) -> None:
    if request.headers.get(CLIENT_HEADER) != CLIENT_HEADER_VALUE:
        raise HTTPException(status_code=403, detail="missing client header")


def require_features() -> None:
    """Product routes exist only where features are switched on; elsewhere 404."""
    if not get_settings().features_active:
        raise HTTPException(status_code=404, detail="Not Found")


def refuse_on_public_demo() -> None:
    """Private paths do not exist on a synthetic-only deployment.

    404 rather than 403: on a public deployment the useful answer is that there is nothing here,
    not that there is something you may not have.
    """
    if get_settings().public_demo_only:
        raise HTTPException(status_code=404, detail="Not Found")


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def current_user(
    request: Request, supabase: Annotated[SupabaseClient, Depends(get_supabase)]
) -> AuthUser:
    token = bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="sign in required")
    user = supabase.auth_user(token)
    if not user or user.get("aud") != "authenticated" or not user.get("id"):
        raise HTTPException(status_code=401, detail="invalid or expired session")
    return AuthUser(id=str(user["id"]), email=user.get("email"))


CurrentUser = Annotated[AuthUser, Depends(current_user)]


def visitor_from_request(request: Request, supabase: SupabaseClient) -> str | None:
    """The visitor id from the cookie, only if it is well formed and known."""
    raw = request.cookies.get(VISITOR_COOKIE, "")
    try:
        visitor_id = str(uuid.UUID(raw))
    except ValueError:
        return None
    rows = supabase.rest("GET", "visitors", params={"id": f"eq.{visitor_id}", "select": "id"})
    if not rows:
        return None
    # Demo identities expire on inactivity, so activity has to be recorded or the rule is inert.
    # The database throttles this to at most one write an hour per visitor, and a failure here
    # never denies the request: a missed touch only delays that visitor's expiry.
    try:
        supabase.rpc("touch_visitor", {"p_visitor_id": visitor_id})
    except SupabaseError:
        logger.warning("visitor.touch_failed")
    return visitor_id


def set_visitor_cookie(response: Response, visitor_id: str) -> None:
    response.set_cookie(
        VISITOR_COOKIE,
        visitor_id,
        max_age=VISITOR_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=get_settings().app_env != "development",
        path="/api",
    )


def ensure_visitor(request: Request, response: Response, supabase: SupabaseClient) -> str:
    """Resume the visitor named by the cookie, or create one and set the cookie."""
    existing = visitor_from_request(request, supabase)
    if existing:
        return existing
    visitor_id = str(uuid.uuid4())
    supabase.rest("POST", "visitors", json_body={"id": visitor_id}, prefer="return=minimal")
    set_visitor_cookie(response, visitor_id)
    return visitor_id


def require_visitor(
    request: Request, supabase: Annotated[SupabaseClient, Depends(get_supabase)]
) -> str:
    visitor_id = visitor_from_request(request, supabase)
    if visitor_id is None:
        raise HTTPException(status_code=401, detail="demo session required")
    return visitor_id


CurrentVisitor = Annotated[str, Depends(require_visitor)]
