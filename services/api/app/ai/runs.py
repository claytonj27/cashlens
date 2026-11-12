"""Run records and the money that buys them.

Two responsibilities that belong together because they must happen in the right order: hold the
budget before dispatching anything, and record the attempt before it can succeed or fail. A run
row exists from the moment the call is about to be made, so there is no window in which a paid
call is in flight with nothing persisted about it.

Every read here is scoped by workspace AND business. The composite key in the schema means a
guessed run id from another business cannot be selected even if the filter were wrong, but the
filter is not wrong either: authorisation is never left to one mechanism.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.supabase import SupabaseClient
from app.workspaces import WorkspaceScope

logger = logging.getLogger(__name__)

RUN_FIELDS = (
    "id,capability,mode,provider,model_requested,model_used,prompt_version,analysis_version,"
    "rules_version,analysis_date,input_fingerprint,status,validation,attempts,latency_ms,"
    "prompt_tokens,completion_tokens,cost_micro,cost_source,provider_request_id,error_code,"
    "output,input_summary,routing,created_at,completed_at"
)


@dataclass(frozen=True)
class Identity:
    """Who spends. A person, never a workspace: one person can hold several."""

    kind: str  # 'user' or 'visitor'
    id: str


@dataclass(frozen=True)
class Reservation:
    id: str
    estimated_cost_micro: int


@dataclass(frozen=True)
class Refusal:
    reason: str  # 'in_flight' | 'total_cap' | 'identity_cap' | 'global_cap'
    identity_spend_micro: int
    global_spend_micro: int
    total_spend_micro: int = 0


def identity_of(scope: WorkspaceScope) -> Identity:
    return Identity(kind=scope.identity, id=scope.identity_id)


def reserve(
    supabase: SupabaseClient,
    identity: Identity,
    capability: str,
    estimate_micro: int,
    settings: Settings,
) -> Reservation | Refusal:
    """Hold the budget, or refuse. Counting and holding happen in one database transaction."""
    reservation_id = str(uuid.uuid4())
    rows = supabase.rpc(
        "reserve_ai_usage",
        {
            "p_reservation_id": reservation_id,
            "p_identity_kind": identity.kind,
            "p_identity_id": identity.id,
            "p_capability": capability,
            "p_estimated_cost_micro": estimate_micro,
            "p_identity_daily_cap_micro": settings.ai_identity_daily_cost_cap_micro,
            "p_global_daily_cap_micro": settings.ai_global_daily_cost_cap_micro,
            "p_total_cap_micro": settings.ai_milestone_total_cap_micro,
            "p_max_in_flight": settings.ai_max_in_flight_per_identity,
            "p_expires_seconds": settings.ai_reservation_expiry_seconds,
        },
    )
    row = rows[0] if rows else {}
    if not row.get("allowed"):
        return Refusal(
            reason=str(row.get("reason") or "refused"),
            identity_spend_micro=int(row.get("identity_spend_micro") or 0),
            global_spend_micro=int(row.get("global_spend_micro") or 0),
            total_spend_micro=int(row.get("total_spend_micro") or 0),
        )
    return Reservation(id=reservation_id, estimated_cost_micro=estimate_micro)


def settle(supabase: SupabaseClient, reservation_id: str, cost_micro: int | None) -> None:
    """Replace the held estimate with the real cost. A null cost keeps the estimate standing.

    That is the timeout case: the provider may well have billed for work whose usage never came
    back, so the budget stays spent rather than being quietly returned.
    """
    supabase.rpc(
        "settle_ai_usage", {"p_reservation_id": reservation_id, "p_cost_micro": cost_micro}
    )


def release(supabase: SupabaseClient, reservation_id: str) -> None:
    """Give the budget back. Only for a request that never left this process."""
    supabase.rpc("release_ai_usage", {"p_reservation_id": reservation_id})


def start_run(
    supabase: SupabaseClient,
    scope: WorkspaceScope,
    *,
    capability: str,
    mode: str,
    provider: str,
    model_requested: str,
    prompt_version: str,
    analysis_version: str,
    rules_version: str,
    analysis_date: str,
    input_fingerprint: str,
    status: str,
    safe_input: dict[str, Any] | None,
    input_summary: dict[str, Any] | None,
    reservation_id: str | None,
) -> str:
    """Record the attempt before it is made, so nothing is ever in flight untracked."""
    run_id = str(uuid.uuid4())
    supabase.rest(
        "POST",
        "ai_runs",
        json_body={
            "id": run_id,
            "business_id": scope.business_id,
            "workspace_id": scope.workspace_id,
            "capability": capability,
            "mode": mode,
            "provider": provider,
            "model_requested": model_requested,
            "prompt_version": prompt_version,
            "analysis_version": analysis_version,
            "rules_version": rules_version,
            "analysis_date": analysis_date,
            "input_fingerprint": input_fingerprint,
            "status": status,
            "safe_input": safe_input,
            "input_summary": input_summary,
            "reservation_id": reservation_id,
        },
        prefer="return=minimal",
    )
    return run_id


def finish_run(supabase: SupabaseClient, scope: WorkspaceScope, run_id: str, **fields: Any) -> None:
    supabase.rest(
        "PATCH",
        "ai_runs",
        params={"id": f"eq.{run_id}", "business_id": f"eq.{scope.business_id}"},
        json_body={**fields, "completed_at": "now()"},
        prefer="return=minimal",
    )


def latest_brief(
    supabase: SupabaseClient, scope: WorkspaceScope, analysis_date: str
) -> dict[str, Any] | None:
    """The newest succeeded brief for this workspace and analysis date, or nothing."""
    rows = supabase.rest(
        "GET",
        "ai_runs",
        params={
            "workspace_id": f"eq.{scope.workspace_id}",
            "business_id": f"eq.{scope.business_id}",
            "capability": "eq.brief",
            "status": "eq.succeeded",
            "analysis_date": f"eq.{analysis_date}",
            "select": RUN_FIELDS,
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


def latest_attempt(
    supabase: SupabaseClient, scope: WorkspaceScope, analysis_date: str
) -> dict[str, Any] | None:
    """The newest brief attempt whatever its outcome, so a failure can be reported honestly."""
    rows = supabase.rest(
        "GET",
        "ai_runs",
        params={
            "workspace_id": f"eq.{scope.workspace_id}",
            "business_id": f"eq.{scope.business_id}",
            "capability": "eq.brief",
            "analysis_date": f"eq.{analysis_date}",
            "select": RUN_FIELDS,
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


def list_runs(
    supabase: SupabaseClient, scope: WorkspaceScope, limit: int = 20
) -> list[dict[str, Any]]:
    return supabase.rest(
        "GET",
        "ai_runs",
        params={
            "workspace_id": f"eq.{scope.workspace_id}",
            "business_id": f"eq.{scope.business_id}",
            "select": RUN_FIELDS,
            "order": "created_at.desc",
            "limit": str(limit),
        },
    )


def get_run(supabase: SupabaseClient, scope: WorkspaceScope, run_id: str) -> dict[str, Any] | None:
    rows = supabase.rest(
        "GET",
        "ai_runs",
        params={
            "id": f"eq.{run_id}",
            "workspace_id": f"eq.{scope.workspace_id}",
            "business_id": f"eq.{scope.business_id}",
            "select": RUN_FIELDS + ",safe_input",
        },
    )
    return rows[0] if rows else None


def log_run(scope: WorkspaceScope, run_id: str, **fields: Any) -> None:
    """One line per run: ids, counts and durations. Never a description, amount or sentence."""
    logger.info(
        "ai.run",
        extra={
            "run_id": run_id,
            "business_id": scope.business_id,
            "workspace_id": scope.workspace_id,
            **{k: v for k, v in fields.items() if isinstance(v, int | float | str | bool | None)},
        },
    )
