"""Who may do what in the Discovery portal.

Three roles, read from `public.profiles` by Python on every request. There is no SQL caller and
no SECURITY DEFINER helper: the table is consulted here and nowhere else. An absent profile, or
a stored value the enum does not know, is `member`. Never elevate on missing data.

**FastAPI is the authorization boundary.** The API holds the Supabase service role, which
bypasses Row Level Security, so nothing in this module leans on RLS and nothing should be read
as though it did. The discovery tables carry RLS with no policies as dormant insurance against
a future accidental grant; that is the database's second lock, never this module's first.

Two resolvers, deliberately separate and taking different path parameters. A stakeholder route
resolves an assignment by its id, filtered to the caller. An admin reaches somebody else's
assignment by that person's user id, through a resolver that requires the admin role before it
reads anything. A stakeholder route cannot be handed admin scope without changing its URL, and
an admin calling a stakeholder route gets the same 404 as anybody else. Modelled on
`resolve_workspace()` in app.workspaces: identity from the validated session, never from a body
or query parameter; 401 without identity; 404 for anything the caller cannot reach.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, HTTPException

from app.auth import AuthUser, CurrentUser
from app.config import get_settings
from app.supabase import SupabaseClient, get_supabase
from app.workspaces import _as_uuid

Role = Literal["admin", "stakeholder", "member"]
ROLES: frozenset[str] = frozenset({"admin", "stakeholder", "member"})
DEFAULT_ROLE: Role = "member"

Supabase = Annotated[SupabaseClient, Depends(get_supabase)]

# Only the columns a scope needs. Progress, timestamps and the title override are read by the
# routes that show them, not by the resolver that authorises them.
ASSIGNMENT_FIELDS = "id,user_id,template_id"


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


def require_discovery() -> None:
    """The portal exists only where a deployment switched it on; elsewhere 404.

    Composed alongside `require_features` and `refuse_on_public_demo`, so the public synthetic
    demo is refused three times over before a discovery route can answer.
    """
    if not get_settings().discovery_enabled:
        raise HTTPException(status_code=404, detail="Not Found")


def role_of(supabase: SupabaseClient, user_id: str) -> Role:
    """The application role held by an auth user, from profiles. Absent means `member`.

    A value the enum does not know is also `member`: a row that somehow carries something else
    is treated as though it were missing, because the alternative is elevating on bad data.
    """
    rows = supabase.rest("GET", "profiles", params={"user_id": f"eq.{user_id}", "select": "role"})
    if not rows:
        return DEFAULT_ROLE
    value = rows[0].get("role")
    if value in ROLES:
        return value  # type: ignore[return-value]
    return DEFAULT_ROLE


def current_role(user: CurrentUser, supabase: Supabase) -> Role:
    """The caller's application role. 401 without a session; never 403, it is a question."""
    return role_of(supabase, user.id)


CurrentRole = Annotated[Role, Depends(current_role)]


def require_role(*allowed: Role) -> Callable[[AuthUser, SupabaseClient], str]:
    """A dependency factory: the caller's id when their role is one of `allowed`, else 403.

    The same 403 for every disallowed role, so a caller learns nothing about which role the
    route wanted. 401 without a session comes from `current_user` before this runs.
    """
    if not allowed:
        raise ValueError("require_role needs at least one role")
    wanted = frozenset(allowed)

    def dependency(user: CurrentUser, supabase: Supabase) -> str:
        if role_of(supabase, user.id) not in wanted:
            raise HTTPException(status_code=403, detail="not authorised")
        return user.id

    return dependency


# The per-route guard every privileged discovery admin operation takes, by name, in its own
# signature. It is never a router-level dependency: `/session` answers a boolean to any
# signed-in caller and would be refused by a router-level guard before it could.
DiscoveryAdmin = Annotated[str, Depends(require_role("admin"))]


@dataclass(frozen=True)
class AssignmentScope:
    """One person's assignment, resolved and frozen. The scope every stakeholder route carries."""

    assignment_id: str
    user_id: str
    template_id: str
    role: Role


def resolve_own_assignment(
    assignment_id: str, user: CurrentUser, supabase: Supabase
) -> AssignmentScope:
    """The caller's OWN assignment, or 404. The choke point for every stakeholder route.

    Filtered on `user_id = caller` in the query itself, so somebody else's assignment id, a
    guessed id and a malformed id all get the same answer, and an admin gets it too: the admin
    surface is scoped by stakeholder id through `resolve_assignment_for`, not by widening this.
    """
    aid = _as_uuid(assignment_id)
    if aid is None:
        raise _not_found()
    rows = supabase.rest(
        "GET",
        "discovery_assignments",
        params={"id": f"eq.{aid}", "user_id": f"eq.{user.id}", "select": ASSIGNMENT_FIELDS},
    )
    if not rows:
        raise _not_found()
    row = rows[0]
    return AssignmentScope(
        assignment_id=str(row["id"]),
        user_id=str(row["user_id"]),
        template_id=str(row["template_id"]),
        role=role_of(supabase, user.id),
    )


Mine = Annotated[AssignmentScope, Depends(resolve_own_assignment)]


def resolve_assignment_for(
    user_id: str, _admin: DiscoveryAdmin, supabase: Supabase
) -> AssignmentScope:
    """A named stakeholder's assignment, for an admin only. 403 for anyone else, then 404.

    Keyed on the stakeholder's user id, not on an assignment id, because the admin surface is
    organised by person. The role check is the `DiscoveryAdmin` dependency, so it runs before a
    single row is read, and a non-admin never learns whether the person has an assignment.
    """
    uid = _as_uuid(user_id)
    if uid is None:
        raise _not_found()
    rows = supabase.rest(
        "GET",
        "discovery_assignments",
        params={
            "user_id": f"eq.{uid}",
            "select": ASSIGNMENT_FIELDS,
            "order": "created_at",
            "limit": "1",
        },
    )
    if not rows:
        raise _not_found()
    row = rows[0]
    return AssignmentScope(
        assignment_id=str(row["id"]),
        user_id=str(row["user_id"]),
        template_id=str(row["template_id"]),
        role=role_of(supabase, str(row["user_id"])),
    )
