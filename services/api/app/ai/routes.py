"""The AI routes: ask for a brief, read the current one, inspect how any of it was made.

Generation routes answer 200 with a status for every outcome the server handled deliberately,
carrying the figures' own state with them, so the interface can always show the dashboard plus
one honest line. HTTP errors are kept for the caller's own request, for refusals and for the
data service.

Nothing here regenerates anything on its own. A confirmed statement or a corrected category
makes a brief stale; turning that into a new brief is a button someone presses, because an
unattended call spends money on somebody else's action and is often stale before it is read.
"""

import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.ai import ask as ask_module
from app.ai import brief as brief_module
from app.ai import classify, routing, runs, signals
from app.ai import scenario as scenario_module
from app.ai.factory import AiSettings, Provider
from app.analysis.routes import (
    ScenarioRequest,
    _current_overrides,
    build_bundle,
    scenario_from_request,
)
from app.auth import require_client_header, require_features
from app.workspaces import Scope, Supabase

router = APIRouter(
    prefix="/api/workspaces",
    tags=["ai"],
    dependencies=[Depends(require_features)],
)

CAP_MESSAGES = {
    "identity_cap": "AI explanations are paused until tomorrow (usage limit).",
    "global_cap": "AI explanations are paused until tomorrow (usage limit).",
    # Not "until tomorrow": the milestone's approved budget does not come back by waiting.
    "total_cap": "AI explanations are switched off: this milestone's approved budget is spent.",
}


class RunSummary(BaseModel):
    """Everything the reviewer view shows. No prompt text, no model reasoning, no raw output."""

    id: str
    capability: str
    mode: str
    provider: str
    model_requested: str
    model_used: str | None = None
    prompt_version: str
    analysis_version: str
    rules_version: str
    analysis_date: str
    input_fingerprint: str
    status: str
    attempts: int
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_micro: int | None = None
    cost_source: str | None = None
    error_code: str | None = None
    validation: dict[str, Any] | None = None
    # Null for a direct provider, which is not routed. Present only where the route chose.
    routing: dict[str, Any] | None = None
    created_at: str
    completed_at: str | None = None


class RunDetail(RunSummary):
    """The same run plus exactly what the model was given and what was kept from its answer."""

    safe_input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None


class BriefView(BaseModel):
    # Reading a brief answers with where it stands; writing one answers with how the attempt
    # went. Both use this field, so every outcome the server handled deliberately arrives as a
    # 200 with a status the interface can render rather than as an error it has to guess at.
    status: Literal[
        "none",
        "current",
        "stale",
        "unavailable",
        "insufficient_data",
        "succeeded",
        "superseded",
        "invalid_output",
        "provider_error",
        "timeout",
    ]
    # Whether the brief carried below still matches the figures, reported separately so a
    # failed refresh can show the previous brief without pretending it is up to date.
    stored_state: Literal["none", "current", "stale"] = "none"
    brief: dict[str, Any] | None = None
    run: RunSummary | None = None
    changes_since: list[str] = []
    reasons: list[str] = []
    message: str | None = None
    retryable: bool = False
    as_of: str
    input_fingerprint: str


def _as_of(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="as_of must be YYYY-MM-DD") from exc


def _summary(row: dict[str, Any]) -> RunSummary:
    return RunSummary(**{k: v for k, v in row.items() if k in RunSummary.model_fields})


@router.get("/{workspace_id}/ai/brief", response_model=BriefView)
def read_brief(
    scope: Scope,
    supabase: Supabase,
    settings: AiSettings,
    as_of: Annotated[str | None, Query()] = None,
) -> BriefView:
    """The current brief, or why there is not one. Never generates and never spends."""
    analysis = build_bundle(supabase, scope, _as_of(as_of)).analysis
    stored = runs.latest_brief(supabase, scope, analysis.as_of)
    newest = runs.latest_attempt(supabase, scope, analysis.as_of)

    stored_state: Literal["none", "current", "stale"] = "none"
    changes: list[str] = []
    if stored is not None:
        if stored["input_fingerprint"] == analysis.input_fingerprint:
            stored_state = "current"
        else:
            stored_state = "stale"
            current_summary = signals.summarize_inputs(
                analysis, brief_module.confirmed_imports(supabase, scope)
            )
            changes = signals.describe_changes(stored.get("input_summary"), current_summary)

    enough = analysis.coverage.posted_count > 0 and analysis.coverage.complete_months >= 1
    failed_latest = (
        newest is not None
        and newest["status"] not in ("succeeded", "insufficient_data")
        and (stored is None or newest["created_at"] > stored["created_at"])
    )

    if failed_latest:
        status: str = "unavailable"
        message = "Explanation unavailable right now. Your figures below are complete."
        retryable = True
    elif stored is not None:
        status = stored_state
        message = None
        retryable = False
    elif not enough:
        status = "insufficient_data"
        message = "A brief needs at least one complete month of statements."
        retryable = False
    else:
        status = "none"
        message = None
        retryable = False

    return BriefView(
        status=status,  # type: ignore[arg-type]
        stored_state=stored_state,
        brief=stored.get("output") if stored else None,
        run=_summary(stored) if stored else None,
        changes_since=changes,
        reasons=list(analysis.coverage.reasons) if status == "insufficient_data" else [],
        message=message,
        retryable=retryable,
        as_of=analysis.as_of,
        input_fingerprint=analysis.input_fingerprint,
    )


@router.post(
    "/{workspace_id}/ai/brief",
    response_model=BriefView,
    dependencies=[Depends(require_client_header)],
)
def write_brief(
    scope: Scope,
    supabase: Supabase,
    provider: Provider,
    settings: AiSettings,
    as_of: Annotated[str | None, Query()] = None,
    route: Annotated[str | None, Query()] = None,
) -> BriefView:
    """Write a brief now. One call, one reservation, and nothing shown before it is checked."""
    chosen = routing.resolve(scope, settings, provider, route)
    try:
        result = brief_module.generate(
            supabase, scope, chosen.provider, settings, _as_of(as_of), model=chosen.model
        )
    except brief_module.UnpricedModelError as unpriced:
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "AI explanations are unavailable in this deployment: no price is recorded "
                    "for the configured model, so the cost of a call cannot be bounded."
                ),
                "reason": "unpriced_model",
            },
        ) from unpriced
    except brief_module.UsageRefusedError as refused:
        if refused.reason == "in_flight":
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "A brief is already being written. Wait for it to finish.",
                    "reason": refused.reason,
                },
            ) from refused
        raise HTTPException(
            status_code=429,
            detail={
                "message": CAP_MESSAGES.get(refused.reason, "Usage limit reached."),
                "reason": refused.reason,
                # A daily cap is worth retrying after; an exhausted milestone budget is not.
                "retry_after_seconds": None if refused.reason == "total_cap" else 3600,
            },
        ) from refused

    run = runs.get_run(supabase, scope, result.run_id) if result.run_id else None
    return BriefView(
        status=result.status,  # type: ignore[arg-type]
        stored_state="current" if result.status == "succeeded" else "none",
        brief=result.brief.model_dump() if result.brief else None,
        run=_summary(run) if run else None,
        changes_since=[],
        reasons=result.reasons,
        message=result.message,
        retryable=result.retryable,
        as_of=result.as_of,
        input_fingerprint=result.input_fingerprint,
    )


class RouteOption(BaseModel):
    key: str
    label: str
    model: str | None = None
    default: bool = False
    note: str | None = None


class RouteChoices(BaseModel):
    options: list[RouteOption]
    note: str


@router.get("/{workspace_id}/ai/routes", response_model=RouteChoices)
def list_routes(scope: Scope, settings: AiSettings) -> RouteChoices:
    """Which provider and model this workspace may be asked with.

    A private workspace is offered the direct provider and nothing else, whatever this
    deployment has configured. The list is computed from the session-derived scope, so it
    describes what the server would actually allow rather than what a control might offer.
    """
    return RouteChoices(
        options=[RouteOption(**option) for option in routing.available(scope, settings)],
        note=(
            "Every result records the provider and model that produced it. Asking a different "
            "one generates a new answer rather than reusing the first."
        ),
    )


@router.get("/{workspace_id}/ai/runs", response_model=list[RunSummary])
def list_ai_runs(scope: Scope, supabase: Supabase, settings: AiSettings) -> list[RunSummary]:
    """The last twenty attempts for this workspace, and no other workspace's."""
    return [_summary(row) for row in runs.list_runs(supabase, scope)]


@router.get("/{workspace_id}/ai/runs/{run_id}", response_model=RunDetail)
def read_ai_run(scope: Scope, supabase: Supabase, settings: AiSettings, run_id: str) -> RunDetail:
    """One attempt in full: the facts it was given, and what survived validation."""
    try:
        wanted = str(uuid.UUID(run_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    row = runs.get_run(supabase, scope, wanted)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return RunDetail(**{k: v for k, v in row.items() if k in RunDetail.model_fields})


# ------------------------------------------------------------- classification --
class SuggestionView(BaseModel):
    """A proposal and the row it is about. Nothing here has changed a figure."""

    id: str
    run_id: str
    status: str
    outcome: str
    category: str | None = None
    category_label: str | None = None
    alternatives: list[str] = []
    alternative_labels: list[str] = []
    rationale: str | None = None
    question: str | None = None
    evidence: list[str] = []
    evidence_transaction_ids: list[str] = []
    transaction_id: str
    transaction_revision: int
    description: str
    posted_date: str
    amount_minor: int
    account_name: str | None = None
    current_category: str | None = None
    created_at: str


class SuggestionList(BaseModel):
    items: list[SuggestionView]
    candidates_remaining: int
    allowed_categories: list[dict[str, Any]]
    note: str


class GenerateSuggestions(BaseModel):
    status: str
    run_id: str | None = None
    created: int = 0
    dropped: int = 0
    message: str | None = None
    run: RunSummary | None = None


class Decision(BaseModel):
    action: Literal["accept", "reject", "correct"]
    category: str | None = None


class DecisionResult(BaseModel):
    status: str
    suggestion_id: str
    applied: bool
    category: str | None = None
    previous_category: str | None = None
    message: str | None = None


def _category_label(key: str | None) -> str | None:
    from app.analysis.categorize import BY_KEY

    found = BY_KEY.get(key or "")
    return found.label if found else None


def _suggestion_views(
    supabase: Supabase, scope: Scope, rows: list[dict[str, Any]]
) -> list[SuggestionView]:
    """Each proposal beside the bank row it concerns, so a person can judge it."""
    if not rows:
        return []
    bundle = build_bundle(supabase, scope)
    by_id = {t.id: t for t in bundle.transactions}
    accounts = {
        a["id"]: a.get("name")
        for a in supabase.rest(
            "GET",
            "accounts",
            params={"business_id": f"eq.{scope.business_id}", "select": "id,name"},
        )
    }
    views = []
    for row in rows:
        txn = by_id.get(row["transaction_id"])
        if txn is None:  # removed since the suggestion was made
            continue
        alternatives = list(row.get("alternatives") or [])
        evidence = list(row.get("evidence") or [])
        views.append(
            SuggestionView(
                id=row["id"],
                run_id=row["run_id"],
                status=row["status"],
                outcome=row["outcome"],
                category=row.get("category"),
                category_label=_category_label(row.get("category")),
                alternatives=alternatives,
                alternative_labels=[_category_label(a) or a for a in alternatives],
                rationale=row.get("rationale"),
                question=row.get("question"),
                evidence=evidence,
                evidence_transaction_ids=[
                    e.split(":", 1)[1] for e in evidence if e.startswith(("tx:", "desc:"))
                ],
                transaction_id=txn.id,
                transaction_revision=txn.revision,
                description=txn.description,
                posted_date=txn.posted_date[:10],
                amount_minor=txn.amount_minor,
                account_name=accounts.get(txn.account_id),
                current_category=bundle.overrides.get(txn.id),
                created_at=str(row["created_at"]),
            )
        )
    return views


@router.get("/{workspace_id}/ai/suggestions", response_model=SuggestionList)
def list_suggestions(
    scope: Scope,
    supabase: Supabase,
    settings: AiSettings,
    status: Annotated[str, Query(pattern="^(open|accepted|rejected|superseded|all)$")] = "open",
) -> SuggestionList:
    """What is waiting for a decision. Reading this never generates and never spends."""
    params: dict[str, str] = {
        "workspace_id": f"eq.{scope.workspace_id}",
        "business_id": f"eq.{scope.business_id}",
        "select": "*",
        "order": "created_at.desc",
        "limit": "50",
    }
    if status != "all":
        params["status"] = f"eq.{status}"
    rows = supabase.rest("GET", "classification_suggestions", params=params)
    bundle = build_bundle(supabase, scope)
    remaining = len(
        classify.select_candidates(supabase, scope, bundle, settings.ai_classify_batch_size)
    )
    return SuggestionList(
        items=_suggestion_views(supabase, scope, rows),
        candidates_remaining=remaining,
        allowed_categories=[
            {"key": c.key, "label": c.label, "group": c.group, "direction": c.direction}
            for c in classify.CATEGORIES
        ],
        note=(
            "These are suggestions. Nothing here changes a figure until you decide, and a "
            "category you set is saved for this workspace only."
        ),
    )


@router.post(
    "/{workspace_id}/ai/suggestions",
    response_model=GenerateSuggestions,
    dependencies=[Depends(require_client_header)],
)
def write_suggestions(
    scope: Scope,
    supabase: Supabase,
    provider: Provider,
    settings: AiSettings,
    as_of: Annotated[str | None, Query()] = None,
    route: Annotated[str | None, Query()] = None,
) -> GenerateSuggestions:
    """Ask about one bounded batch of unplaced rows. Never runs on its own."""
    chosen = routing.resolve(scope, settings, provider, route)
    try:
        result = classify.generate(
            supabase, scope, chosen.provider, settings, _as_of(as_of), model=chosen.model
        )
    except classify.NothingToReviewError:
        return GenerateSuggestions(
            status="nothing_to_review",
            message="Every transaction here is either categorised or already waiting for you.",
        )
    except brief_module.UnpricedModelError as unpriced:
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "AI suggestions are unavailable in this deployment: no price is recorded "
                    "for the configured model, so the cost of a call cannot be bounded."
                ),
                "reason": "unpriced_model",
            },
        ) from unpriced
    except brief_module.UsageRefusedError as refused:
        if refused.reason == "in_flight":
            raise HTTPException(
                status_code=409,
                detail={"message": "Something is already running.", "reason": refused.reason},
            ) from refused
        raise HTTPException(
            status_code=429,
            detail={
                "message": CAP_MESSAGES.get(refused.reason, "Usage limit reached."),
                "reason": refused.reason,
                "retry_after_seconds": None if refused.reason == "total_cap" else 3600,
            },
        ) from refused

    run = runs.get_run(supabase, scope, result["run_id"]) if result.get("run_id") else None
    return GenerateSuggestions(
        status=result["status"],
        run_id=result.get("run_id"),
        created=len(result.get("suggestions") or []),
        dropped=result.get("dropped", 0),
        run=_summary(run) if run else None,
        message=(
            None
            if result["status"] == "succeeded"
            else "Suggestions are unavailable right now. Your figures are unaffected."
        ),
    )


@router.post(
    "/{workspace_id}/ai/suggestions/{suggestion_id}/decision",
    response_model=DecisionResult,
    dependencies=[Depends(require_client_header)],
)
def decide_suggestion(
    scope: Scope,
    supabase: Supabase,
    settings: AiSettings,
    suggestion_id: str,
    body: Decision,
) -> DecisionResult:
    """Accept, reject, or choose another category. The only thing here that moves a figure."""
    try:
        wanted = str(uuid.UUID(suggestion_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    bundle = build_bundle(supabase, scope)
    current = _current_overrides(supabase, scope)
    try:
        result = classify.decide(
            supabase, scope, wanted, body.action, body.category, bundle.transactions, current
        )
    except classify.DecisionConflictError as conflict:
        raise HTTPException(
            status_code=409, detail={"message": conflict.message, "reason": "superseded"}
        ) from conflict
    return DecisionResult(
        status=result["status"],
        suggestion_id=result["suggestion_id"],
        applied=result.get("applied", False),
        category=result.get("category"),
        previous_category=result.get("previous_category"),
        message=(
            "Saved for this workspace. Your figures update on the next read and any written "
            "brief is now out of date."
            if result.get("applied")
            else None
        ),
    )


# ------------------------------------------------------- scenario explanation --
class ScenarioExplanation(BaseModel):
    """An explanation of a what-if the owner already ran. The projection stands without it."""

    status: Literal[
        "succeeded",
        # Checked and shown, but it did not cover everything the scenario turns on. A short
        # true answer is not a complete one, and saying "succeeded" for both would hide the
        # difference from the only person who can tell it matters.
        "partial",
        "current",
        "insufficient_data",
        "invalid_output",
        "provider_error",
        "timeout",
    ]
    explanation: dict[str, Any] | None = None
    run: RunSummary | None = None
    message: str | None = None
    fingerprint: str | None = None
    # True when an explanation for exactly this data and these assumptions already existed, so
    # pressing the button again costs nothing.
    reused: bool = False


@router.post(
    "/{workspace_id}/ai/scenario/explain",
    response_model=ScenarioExplanation,
    dependencies=[Depends(require_client_header)],
)
def explain_scenario(
    body: ScenarioRequest,
    scope: Scope,
    supabase: Supabase,
    provider: Provider,
    settings: AiSettings,
    route: Annotated[str | None, Query()] = None,
) -> ScenarioExplanation:
    """Explain a computed scenario. Never runs on its own, and never recomputes a figure."""
    chosen = routing.resolve(scope, settings, provider, route)
    bundle = build_bundle(supabase, scope, _as_of(body.as_of), scenario_from_request(body))
    analysis = bundle.analysis
    inputs = scenario_module.scenario_inputs(body)
    fingerprint = scenario_module.combined_fingerprint(analysis.input_fingerprint, inputs)

    # Same data, same assumptions AND the same provider and model: the explanation that exists
    # is still the right one, so it is returned rather than bought again. Change any of the
    # three and this misses, which is the staleness rule working rather than a cache getting in
    # the way. The configuration belongs in the key because an explanation is one model's
    # answer: handing back another model's under a new route would report a reused result as
    # this route's own.
    stored = scenario_module.latest(
        supabase, scope, analysis.as_of, fingerprint, chosen.provider.name, chosen.model
    )
    if stored is not None:
        output = stored.get("output") or {}
        gaps = list((stored.get("validation") or {}).get("coverage_gaps") or [])
        # The fact sheet is rebuilt from the analysis already in hand, purely so the gap can be
        # named in the owner's words. It computes nothing new and asks nothing of any model.
        covered, shortfall = scenario_module.completeness(
            gaps,
            len(output.get("claims") or []),
            scenario_module.build_fact_sheet(scope, analysis, inputs),
        )
        return ScenarioExplanation(
            # A reused explanation is reported exactly as it was when it was written. A partial
            # one does not become complete by being stored.
            status="current" if covered == "succeeded" else "partial",
            explanation=stored.get("output"),
            run=_summary(stored),
            message=shortfall,
            fingerprint=fingerprint,
            reused=True,
        )

    try:
        result = scenario_module.generate(
            supabase, scope, chosen.provider, settings, analysis, inputs, model=chosen.model
        )
    except brief_module.UnpricedModelError as unpriced:
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "Explanations are unavailable in this deployment: no price is recorded for "
                    "the configured model, so the cost of a call cannot be bounded."
                ),
                "reason": "unpriced_model",
            },
        ) from unpriced
    except brief_module.UsageRefusedError as refused:
        if refused.reason == "in_flight":
            raise HTTPException(
                status_code=409,
                detail={"message": "Something is already running.", "reason": refused.reason},
            ) from refused
        raise HTTPException(
            status_code=429,
            detail={
                "message": CAP_MESSAGES.get(refused.reason, "Usage limit reached."),
                "reason": refused.reason,
                "retry_after_seconds": None if refused.reason == "total_cap" else 3600,
            },
        ) from refused

    run = runs.get_run(supabase, scope, result["run_id"]) if result.get("run_id") else None
    explanation = result.get("explanation")
    return ScenarioExplanation(
        status=result["status"],
        explanation=explanation.model_dump() if explanation else None,
        run=_summary(run) if run else None,
        message=result.get("message"),
        fingerprint=result.get("fingerprint", fingerprint),
    )


# --------------------------------------------------------- the AI CFO panel --
class AskBody(BaseModel):
    """What the client may send. Deliberately tiny.

    No conversation history and no scenario: the server reads the turns it wrote and resolves
    `option_id` against the choices it offered. A client that invented either would be
    supplying its own evidence, which is the one thing this capability must not accept.
    """

    text: str = Field(min_length=1, max_length=500)
    conversation_id: str | None = None
    option_id: str | None = None


class ToolTrace(BaseModel):
    tool: str
    arguments: dict[str, Any] = {}
    ok: bool
    summary: str


class AskView(BaseModel):
    status: Literal[
        "answered",
        # Shown, but shorter than it should be: nothing survived the checks, or the essentials
        # were not covered. Never reported as a complete answer.
        "partial",
        # The question could not be answered as asked, so the assistant asked back rather than
        # computing something adjacent to it.
        "clarifying",
        "invalid_output",
        "provider_error",
        "timeout",
        # Ran out of rounds, tool calls or the sixty seconds. An honest end, not a retry.
        "deadline",
    ]
    conversation_id: str
    turn_id: str | None = None
    answer: dict[str, Any] | None = None
    clarify: dict[str, Any] | None = None
    scenario_applied: dict[str, Any] | None = None
    scenario_paths: dict[str, Any] | None = None
    proposal_id: str | None = None
    tool_calls: list[ToolTrace] = []
    message: str | None = None
    run: RunSummary | None = None
    fingerprint: str


class AskSuggestion(BaseModel):
    text: str
    scenario: dict[str, Any] | None = None


class AskSuggestions(BaseModel):
    items: list[AskSuggestion]
    conversation_id: str | None = None
    note: str


class TurnView(BaseModel):
    id: str
    sequence: int
    question: str
    answer: dict[str, Any] | None = None
    clarify: dict[str, Any] | None = None
    scenario_applied: dict[str, Any] | None = None
    proposal_id: str | None = None
    tool_calls: list[ToolTrace] = []
    run_id: str | None = None
    input_fingerprint: str | None = None
    status: str
    created_at: str


class ConversationView(BaseModel):
    conversation_id: str | None = None
    turns: list[TurnView] = []
    agreed_assumptions: dict[str, Any] | None = None
    # True when the figures have moved since those assumptions were agreed, so the panel asks
    # again instead of quietly reusing them.
    assumptions_stale: bool = False
    turns_left: int = ask_module.MAX_TURNS_PER_CONVERSATION
    fingerprint: str


@router.get("/{workspace_id}/ai/ask/suggestions", response_model=AskSuggestions)
def ask_suggestions(
    scope: Scope,
    supabase: Supabase,
    settings: AiSettings,
    as_of: Annotated[str | None, Query()] = None,
) -> AskSuggestions:
    """Questions this workspace's own data makes worth asking. Computed, never generated."""
    bundle = build_bundle(supabase, scope, _as_of(as_of))
    existing = supabase.rest(
        "GET",
        "ai_conversations",
        params={
            "workspace_id": f"eq.{scope.workspace_id}",
            "business_id": f"eq.{scope.business_id}",
            "select": "id",
            "order": "updated_at.desc",
            "limit": "1",
        },
    )
    return AskSuggestions(
        items=[AskSuggestion(**s) for s in ask_module.suggested(supabase, scope, bundle)],
        conversation_id=existing[0]["id"] if existing else None,
        note=(
            "Answers are checked against your own figures before you see them, and every "
            "figure quoted was computed before the question was asked."
        ),
    )


@router.get("/{workspace_id}/ai/ask/conversation", response_model=ConversationView)
def read_conversation(
    scope: Scope,
    supabase: Supabase,
    settings: AiSettings,
    as_of: Annotated[str | None, Query()] = None,
) -> ConversationView:
    """This workspace's current conversation. Reading never generates and never spends."""
    analysis = build_bundle(supabase, scope, _as_of(as_of)).analysis
    rows = supabase.rest(
        "GET",
        "ai_conversations",
        params={
            "workspace_id": f"eq.{scope.workspace_id}",
            "business_id": f"eq.{scope.business_id}",
            "select": "*",
            "order": "updated_at.desc",
            "limit": "1",
        },
    )
    if not rows:
        return ConversationView(fingerprint=analysis.input_fingerprint)
    conversation = rows[0]
    turns = supabase.rest(
        "GET",
        "ai_turns",
        params={
            "conversation_id": f"eq.{conversation['id']}",
            "business_id": f"eq.{scope.business_id}",
            "workspace_id": f"eq.{scope.workspace_id}",
            "select": (
                "id,sequence,question,answer,clarify,scenario_applied,proposal_id,"
                "tool_calls,run_id,input_fingerprint,status,created_at"
            ),
            "order": "sequence",
            "limit": "40",
        },
    )
    agreed = conversation.get("agreed_assumptions") or None
    return ConversationView(
        conversation_id=conversation["id"],
        turns=[TurnView(**{**t, "created_at": str(t["created_at"])}) for t in turns],
        agreed_assumptions=agreed,
        assumptions_stale=bool(
            agreed and conversation.get("assumptions_fingerprint") != analysis.input_fingerprint
        ),
        turns_left=max(
            0, ask_module.MAX_TURNS_PER_CONVERSATION - int(conversation.get("turn_count") or 0)
        ),
        fingerprint=analysis.input_fingerprint,
    )


@router.post(
    "/{workspace_id}/ai/ask",
    response_model=AskView,
    dependencies=[Depends(require_client_header)],
)
def ask(
    body: AskBody,
    scope: Scope,
    supabase: Supabase,
    provider: Provider,
    settings: AiSettings,
    as_of: Annotated[str | None, Query()] = None,
    route: Annotated[str | None, Query()] = None,
) -> AskView:
    """One question, answered inside every bound, or an honest account of why it was not."""
    chosen = routing.resolve(scope, settings, provider, route)
    try:
        result = ask_module.run_turn(
            supabase,
            scope,
            chosen,
            settings,
            question=body.text,
            conversation_id=body.conversation_id,
            option_id=body.option_id,
            as_of=_as_of(as_of),
        )
    except ask_module.TurnRefusedError as refused:
        raise HTTPException(
            status_code=404 if refused.reason == "not_found" else 409,
            detail={"message": refused.message, "reason": refused.reason},
        ) from refused
    except brief_module.UnpricedModelError as unpriced:
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "The assistant is unavailable in this deployment: no price is recorded for "
                    "the configured model, so the cost of a turn cannot be bounded."
                ),
                "reason": "unpriced_model",
            },
        ) from unpriced
    except brief_module.UsageRefusedError as refused:
        if refused.reason == "in_flight":
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "One question is already being answered. Wait for it to finish.",
                    "reason": refused.reason,
                },
            ) from refused
        raise HTTPException(
            status_code=429,
            detail={
                "message": CAP_MESSAGES.get(refused.reason, "Usage limit reached."),
                "reason": refused.reason,
                "retry_after_seconds": None if refused.reason == "total_cap" else 3600,
            },
        ) from refused

    run = runs.get_run(supabase, scope, result.run_id) if result.run_id else None
    return AskView(
        status=result.status,  # type: ignore[arg-type]
        conversation_id=result.conversation_id,
        turn_id=result.turn_id,
        answer=result.answer,
        clarify=result.clarify,
        scenario_applied=result.scenario_applied,
        scenario_paths=result.scenario_paths,
        proposal_id=result.proposal_id,
        tool_calls=[ToolTrace(**t) for t in result.tool_calls],
        message=result.message,
        run=_summary(run) if run else None,
        fingerprint=result.fingerprint,
    )
