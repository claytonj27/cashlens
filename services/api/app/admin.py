"""The operator's AI usage dashboard: what has been spent, and on what.

Two things this deliberately is not. It is not membership in any business: an allowlisted
operator gains one read-only view of usage accounting and no route here reads a transaction, a
balance, a question, an answer, a filename or a business name. And it is not a general
diagnostics surface: the `ops` diagnostics routes stay behind their own disabled flag.

Authorization is a server-side allowlist of Supabase auth user ids, checked on every route.
The one exception is `/api/admin/session`, which answers a plain boolean to any signed-in
caller so the interface knows whether to draw a link. Hiding a link is never the control.

Every figure comes from the authoritative records that the caps themselves are enforced
against: `ai_milestone_spend()` for the cumulative total, `usage_reservations` for holds, and
`ai_runs` for activity. Nothing is recomputed in a second way that could disagree with the
budget the database actually enforces.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import CurrentUser, refuse_on_public_demo, require_client_header
from app.config import Settings, get_settings
from app.supabase import SupabaseClient, get_supabase

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_client_header), Depends(refuse_on_public_demo)],
)
Supabase = Annotated[SupabaseClient, Depends(get_supabase)]
Config = Annotated[Settings, Depends(get_settings)]

logger = logging.getLogger(__name__)

# Bounded reads. A page that could scan the whole table is not a dashboard, it is an outage
# waiting for a busy week, so every query is capped and says when it hit the cap.
SCAN_LIMIT = 1000
RUNS_PAGE_MAX = 50

# Only scalars and identifiers of the model call itself. `output`, `safe_input` and
# `input_summary` are never selected, so no question, answer or description can reach this page
# even by accident.
RUN_FIELDS = (
    "id,created_at,capability,mode,provider,model_requested,model_used,status,"
    "latency_ms,cost_micro,cost_source,attempts,validation,error_code"
)


def require_admin(user: CurrentUser, settings: Config) -> str:
    """The caller's id, or 403. Bound to the immutable auth user id, never to an email."""
    allowed = settings.admin_ids
    if not allowed or user.id.lower() not in allowed:
        # The same answer either way: a non-admin learns nothing about whether the route exists
        # for anyone else.
        raise HTTPException(status_code=403, detail="not authorised")
    return user.id


AdminUser = Annotated[str, Depends(require_admin)]

WindowName = Literal["today", "7d", "all"]


def _window_start(window: WindowName) -> datetime | None:
    """The inclusive start of an activity window, or None for all time.

    These are calendar-facing windows for browsing activity. They are deliberately not the
    rolling 24-hour window the daily caps use; the two are reported separately and labelled,
    because conflating them is how a budget figure starts lying.
    """
    now = datetime.now(UTC)
    if window == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "7d":
        return now - timedelta(days=7)
    return None


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class BudgetView(BaseModel):
    milestone_cap_micro: int
    settled_micro: int
    held_micro: int
    total_micro: int
    remaining_micro: int
    rolling_24h_micro: int
    rolling_24h_global_cap_micro: int
    rolling_24h_identity_cap_micro: int
    rolling_24h_truncated: bool
    active_reservations: int
    uncertain_holds: int
    external_probe_micro: int
    external_probe_in_ledger: bool
    overall_total_micro: int


class CountRow(BaseModel):
    key: str
    label: str
    count: int


class ProviderRow(BaseModel):
    provider: str
    model: str
    mode: str
    count: int
    cost_micro: int
    average_latency_ms: int | None
    timed_count: int


class UsageView(BaseModel):
    window: WindowName
    window_start: str | None
    generated_at: str
    budget: BudgetView
    by_capability: list[CountRow]
    by_status: list[CountRow]
    by_provider: list[ProviderRow]
    average_latency_ms: int | None
    latency_sample: int
    runs_in_window: int
    runs_truncated: bool
    claims_withheld: int
    claims_withheld_recorded: bool
    live_runs: int
    non_live_runs: int
    last_activity_at: str | None


class RunRow(BaseModel):
    id: str
    created_at: str
    capability: str
    mode: str
    provider: str
    model: str
    status: str
    latency_ms: int | None
    cost_micro: int | None
    cost_is_estimate: bool
    attempts: int | None
    rounds: int | None
    error_code: str | None


class RunsView(BaseModel):
    window: WindowName
    items: list[RunRow]
    next_before: str | None
    truncated: bool


CAPABILITY_LABEL = {
    "ask": "Chat",
    "brief": "Brief",
    "classify": "Classification",
    "scenario_explain": "Scenario explanation",
}

STATUS_LABEL = {
    "succeeded": "Succeeded",
    "invalid_output": "Invalid output",
    "provider_error": "Provider error",
    "timeout": "Timed out",
    "insufficient_data": "Not enough data",
    "reserved": "Reserved",
    "running": "Running",
    "superseded": "Superseded",
}


@router.get("/session")
def admin_session(user: CurrentUser, settings: Config) -> dict[str, bool]:
    """Whether this signed-in caller may read the dashboard. Never 403: it is the question."""
    allowed = settings.admin_ids
    return {"is_admin": bool(allowed) and user.id.lower() in allowed}


def _fetch(supabase: SupabaseClient, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
    rows = supabase.rest("GET", table, params=params)
    return rows if isinstance(rows, list) else []


@router.get("/ai/usage", response_model=UsageView)
def ai_usage(
    supabase: Supabase,
    settings: Config,
    _admin: AdminUser,
    window: Annotated[WindowName, Query()] = "7d",
) -> UsageView:
    now = datetime.now(UTC)
    start = _window_start(window)

    # --- budget, from the same function the cumulative cap is enforced with ---
    spend = supabase.rpc("ai_milestone_spend", {})
    row = (spend or [{}])[0] if isinstance(spend, list) else (spend or {})
    settled = int(row.get("settled_micro") or 0)
    held = int(row.get("held_micro") or 0)
    total = int(row.get("total_micro") or 0)
    live_runs = int(row.get("live_runs") or 0)
    simulated_runs = int(row.get("simulated_runs") or 0)

    # --- rolling 24 hours, counted the way reserve_ai_usage counts it ---
    since = now - timedelta(hours=24)
    holds = _fetch(
        supabase,
        "usage_reservations",
        {
            "created_at": f"gte.{_iso(since)}",
            "select": "cost_micro,estimated_cost_micro,settled_at,expires_at,uncertain",
            "order": "created_at.desc",
            "limit": str(SCAN_LIMIT),
        },
    )
    rolling = 0
    for hold in holds:
        if hold.get("settled_at") is not None:
            rolling += int(hold.get("cost_micro") or 0)
        elif (hold.get("expires_at") or "") > _iso(now):
            rolling += int(hold.get("estimated_cost_micro") or 0)

    active = supabase.count(
        "usage_reservations",
        {"settled_at": "is.null", "expires_at": f"gt.{_iso(now)}"},
    )
    uncertain = supabase.count("usage_reservations", {"uncertain": "is.true"})

    probe = int(settings.external_probe_cost_micro or 0)
    # The probe was spent outside this ledger, so it is not bounded by the database caps and is
    # added once for an overall figure rather than folded into the enforced total.
    probe_in_ledger = False

    budget = BudgetView(
        milestone_cap_micro=settings.ai_milestone_total_cap_micro,
        settled_micro=settled,
        held_micro=held,
        total_micro=total,
        remaining_micro=max(settings.ai_milestone_total_cap_micro - total, 0),
        rolling_24h_micro=rolling,
        rolling_24h_global_cap_micro=settings.ai_global_daily_cost_cap_micro,
        rolling_24h_identity_cap_micro=settings.ai_identity_daily_cost_cap_micro,
        rolling_24h_truncated=len(holds) >= SCAN_LIMIT,
        active_reservations=active,
        uncertain_holds=uncertain,
        external_probe_micro=probe,
        external_probe_in_ledger=probe_in_ledger,
        overall_total_micro=total + (0 if probe_in_ledger else probe),
    )

    # --- activity in the chosen window ---
    params = {
        "select": RUN_FIELDS,
        "order": "created_at.desc",
        "limit": str(SCAN_LIMIT),
    }
    if start is not None:
        params["created_at"] = f"gte.{_iso(start)}"
    runs = _fetch(supabase, "ai_runs", params)

    caps: dict[str, int] = {}
    stats: dict[str, int] = {}
    providers: dict[tuple[str, str, str], dict[str, Any]] = {}
    latencies: list[int] = []
    withheld = 0
    withheld_seen = False
    live_in_window = 0

    for run in runs:
        capability = str(run.get("capability") or "unknown")
        caps[capability] = caps.get(capability, 0) + 1
        status = str(run.get("status") or "unknown")
        stats[status] = stats.get(status, 0) + 1
        mode = str(run.get("mode") or "unknown")
        if mode == "live":
            live_in_window += 1
        key = (
            str(run.get("provider") or "unknown"),
            str(run.get("model_used") or run.get("model_requested") or "not recorded"),
            mode,
        )
        bucket = providers.setdefault(
            key, {"count": 0, "cost_micro": 0, "latency_total": 0, "timed": 0}
        )
        bucket["count"] += 1
        bucket["cost_micro"] += int(run.get("cost_micro") or 0)
        latency = run.get("latency_ms")
        if isinstance(latency, int):
            latencies.append(latency)
            bucket["latency_total"] += latency
            bucket["timed"] += 1
        validation = run.get("validation")
        if isinstance(validation, dict) and "claims_withheld" in validation:
            withheld_seen = True
            withheld += int(validation.get("claims_withheld") or 0)

    return UsageView(
        window=window,
        window_start=_iso(start) if start else None,
        generated_at=_iso(now),
        budget=budget,
        by_capability=[
            CountRow(key=k, label=CAPABILITY_LABEL.get(k, k), count=v)
            for k, v in sorted(caps.items(), key=lambda kv: -kv[1])
        ],
        by_status=[
            CountRow(key=k, label=STATUS_LABEL.get(k, k), count=v)
            for k, v in sorted(stats.items(), key=lambda kv: -kv[1])
        ],
        by_provider=[
            ProviderRow(
                provider=p,
                model=m,
                mode=mode,
                count=b["count"],
                cost_micro=b["cost_micro"],
                average_latency_ms=(round(b["latency_total"] / b["timed"]) if b["timed"] else None),
                timed_count=b["timed"],
            )
            for (p, m, mode), b in sorted(providers.items(), key=lambda kv: -kv[1]["count"])
        ],
        average_latency_ms=(round(sum(latencies) / len(latencies)) if latencies else None),
        latency_sample=len(latencies),
        runs_in_window=len(runs),
        runs_truncated=len(runs) >= SCAN_LIMIT,
        claims_withheld=withheld,
        claims_withheld_recorded=withheld_seen,
        live_runs=live_runs,
        non_live_runs=simulated_runs,
        last_activity_at=(str(runs[0].get("created_at")) if runs else None),
    )


@router.get("/ai/runs", response_model=RunsView)
def ai_runs(
    supabase: Supabase,
    _settings: Config,
    _admin: AdminUser,
    window: Annotated[WindowName, Query()] = "7d",
    limit: Annotated[int, Query(ge=1, le=RUNS_PAGE_MAX)] = 25,
    before: Annotated[str | None, Query(description="ISO timestamp cursor, exclusive")] = None,
) -> RunsView:
    start = _window_start(window)
    params: dict[str, str] = {
        "select": RUN_FIELDS,
        "order": "created_at.desc",
        "limit": str(limit + 1),
    }
    # Both bounds sit on `created_at`, and one query string key cannot carry two filters for the
    # same column, so a pair goes through PostgREST's `and=(...)` form instead.
    bounds: list[str] = []
    if start is not None:
        bounds.append(f"created_at.gte.{_iso(start)}")
    if before:
        bounds.append(f"created_at.lt.{before}")
    if len(bounds) == 1:
        column, _, value = bounds[0].partition(".")
        params[column] = value
    elif bounds:
        params["and"] = f"({','.join(bounds)})"
    rows = _fetch(supabase, "ai_runs", params)
    more = len(rows) > limit
    rows = rows[:limit]

    items: list[RunRow] = []
    for run in rows:
        validation = run.get("validation") if isinstance(run.get("validation"), dict) else {}
        rounds = validation.get("rounds") if isinstance(validation, dict) else None
        items.append(
            RunRow(
                id=str(run.get("id")),
                created_at=str(run.get("created_at")),
                capability=str(run.get("capability") or "unknown"),
                mode=str(run.get("mode") or "unknown"),
                provider=str(run.get("provider") or "unknown"),
                model=str(run.get("model_used") or run.get("model_requested") or ""),
                status=str(run.get("status") or "unknown"),
                latency_ms=run.get("latency_ms")
                if isinstance(run.get("latency_ms"), int)
                else None,
                cost_micro=run.get("cost_micro")
                if isinstance(run.get("cost_micro"), int)
                else None,
                # A retained estimate is money the budget is still holding, and it is labelled
                # as an estimate rather than shown as though it were measured.
                cost_is_estimate=str(run.get("cost_source") or "") == "reserved_estimate",
                attempts=run.get("attempts") if isinstance(run.get("attempts"), int) else None,
                rounds=rounds if isinstance(rounds, int) else None,
                error_code=run.get("error_code") or None,
            )
        )

    return RunsView(
        window=window,
        items=items,
        next_before=(items[-1].created_at if more and items else None),
        truncated=more,
    )
