"""Generating one brief: reserve, record, call, check, and only then show anything.

The order matters and is the whole point of the file:

1. build the analysis and the fact sheet from one read, so the explanation and the figures
   describe the same moment;
2. refuse before spending if there is not enough data — no reservation, no call;
3. hold the budget in the database, which is the only place a limit survives two requests
   arriving at once;
4. record the run before dispatching, so a call is never in flight untracked;
5. call once, with at most one repair attempt, both counted;
6. check the answer through four layers before anyone sees a word of it;
7. read the inputs again: if they changed while the model was writing, the result is marked
   superseded rather than presented as current.

Failure never changes a figure. Every outcome below leaves the deterministic dashboard exactly
as it was, which is why the product can ship with the explanation layer switched off.
"""

import hashlib
import logging
from dataclasses import dataclass, replace
from typing import Any

from app.ai import prices, prompts, runs, signals
from app.ai.provider import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderTimeoutError,
)
from app.ai.validate import ValidatedBrief, Validation, validate
from app.analysis.routes import ANALYSIS_VERSION, build_bundle
from app.config import Settings
from app.supabase import SupabaseClient
from app.workspaces import WorkspaceScope

logger = logging.getLogger(__name__)

CAPABILITY = "brief"
# A failure that certainly never reached the provider, so the budget goes straight back.
NEVER_DISPATCHED = frozenset({"model_not_allowed", "recording_missing"})


class UsageRefusedError(Exception):
    """A cap or an in-flight run stopped this before any paid work. No run row is written."""

    def __init__(self, reason: str, identity_spend_micro: int, global_spend_micro: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.identity_spend_micro = identity_spend_micro
        self.global_spend_micro = global_spend_micro


class UnpricedModelError(Exception):
    """No recorded price for the configured model, so no live call can be bounded.

    Reserving against an assumed price would hold a number the provider never agreed to, and
    reporting `cost_source: unknown` after the fact is a description of a call already made,
    not a ceiling on it. The deployment is misconfigured and says so instead of spending.
    """

    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.model = model


@dataclass
class BriefResult:
    status: str
    run_id: str | None
    brief: ValidatedBrief | None
    validation: Validation | None
    message: str | None
    retryable: bool
    reasons: list[str]
    input_fingerprint: str
    as_of: str


def confirmed_imports(supabase: SupabaseClient, scope: WorkspaceScope) -> list[dict[str, Any]]:
    """The statements already folded into these figures, so an update can name what arrived."""
    return supabase.rest(
        "GET",
        "document_imports",
        params={
            "business_id": f"eq.{scope.business_id}",
            "status": "eq.confirmed",
            "select": "id,file_label,confirmed_at",
            "order": "confirmed_at",
        },
    )


def _sum_tokens(first: ModelResponse, second: ModelResponse) -> tuple[int | None, int | None]:
    """Both attempts are billed, so both are counted."""

    def add(a: int | None, b: int | None) -> int | None:
        return None if a is None and b is None else (a or 0) + (b or 0)

    return add(first.prompt_tokens, second.prompt_tokens), add(
        first.completion_tokens, second.completion_tokens
    )


def generate(
    supabase: SupabaseClient,
    scope: WorkspaceScope,
    provider: ModelProvider,
    settings: Settings,
    as_of: str | None = None,
    model: str | None = None,
) -> BriefResult:
    bundle = build_bundle(supabase, scope, as_of)
    analysis = bundle.analysis
    # Anything not answered by a model just now is recorded as `replay`, whatever adapter
    # produced it, so a canned answer can never be read as live model performance.
    mode = "live" if provider.live else "replay"
    # The route decides the model; the deployment's own setting is the fallback for the direct
    # path. Passing it in is what lets two routes be compared without either being reconfigured.
    model = model or settings.ai_model or provider.name

    def record(status: str, **fields: Any) -> str:
        return runs.start_run(
            supabase,
            scope,
            capability=CAPABILITY,
            mode=mode,
            provider=provider.name,
            model_requested=model,
            prompt_version=prompts.BRIEF_PROMPT_VERSION,
            analysis_version=ANALYSIS_VERSION,
            rules_version=analysis.category_rules_version,
            analysis_date=analysis.as_of,
            input_fingerprint=analysis.input_fingerprint,
            status=status,
            **fields,
        )

    # 2. Not enough to explain. Decided before any call, so nothing is reserved and nothing spent.
    if analysis.coverage.posted_count == 0 or analysis.coverage.complete_months < 1:
        run_id = record(
            "insufficient_data", safe_input=None, input_summary=None, reservation_id=None
        )
        runs.log_run(scope, run_id, capability=CAPABILITY, status="insufficient_data", mode=mode)
        return BriefResult(
            status="insufficient_data",
            run_id=run_id,
            brief=None,
            validation=None,
            message="A brief needs at least one complete month of statements.",
            retryable=False,
            reasons=list(analysis.coverage.reasons),
            input_fingerprint=analysis.input_fingerprint,
            as_of=analysis.as_of,
        )

    previous = runs.latest_brief(supabase, scope, analysis.as_of)
    sheet = signals.build(
        analysis,
        bundle.transactions,
        bundle.overrides,
        industry=scope.industry,
        imports=confirmed_imports(supabase, scope),
        previous_summary=(previous or {}).get("input_summary"),
    )
    payload = sheet.payload()
    user_message = prompts.brief_user_message(payload)

    # 3. Hold the budget first, for the whole bounded operation: both calls this run may make,
    #    each at its full output allowance. An estimate that covered only the first call would
    #    leave the repair unbudgeted, which is the same as not holding for it at all.
    if provider.live:
        estimate = prices.estimate_micro(
            model,
            prices.estimate_tokens(prompts.BRIEF_SYSTEM + user_message),
            settings.ai_max_output_tokens,
        )
        # No recorded price means no bounded call. Refused here, before a reservation exists
        # and before anything is dispatched, rather than described afterwards as unknown.
        if estimate is None:
            raise UnpricedModelError(model)
    else:
        # A recorded or scripted answer is not billable, so nothing is held for it. The
        # in-flight limit still applies, which is what keeps the simulated path honest.
        estimate = 0
    reservation = runs.reserve(supabase, runs.identity_of(scope), CAPABILITY, estimate, settings)
    if isinstance(reservation, runs.Refusal):
        raise UsageRefusedError(
            reservation.reason, reservation.identity_spend_micro, reservation.global_spend_micro
        )

    # 4. Recorded before the call, so there is no untracked window.
    run_id = record(
        "running",
        safe_input=payload,
        input_summary=sheet.input_summary,
        reservation_id=reservation.id,
    )

    request = ModelRequest(
        capability=CAPABILITY,
        prompt_version=prompts.BRIEF_PROMPT_VERSION,
        model=model,
        system=prompts.BRIEF_SYSTEM,
        user=user_message,
        json_schema=prompts.BRIEF_SCHEMA,
        max_output_tokens=settings.ai_max_output_tokens,
        timeout_seconds=settings.ai_timeout_seconds,
    )

    attempts = 1
    try:
        response = provider.complete(request)
        validation, brief = validate(response.text, sheet)
        # 5. One repair, never two. A schema or evidence failure gets the validator's own
        #    messages back; anything still wrong after that is not shown at all.
        if validation.rejected:
            repair = replace(
                request, user=prompts.repair_user_message(payload, validation.problems)
            )
            second = provider.complete(repair)
            attempts = 2
            prompt_tokens, completion_tokens = _sum_tokens(response, second)
            cost = None
            if response.cost_micro is not None and second.cost_micro is not None:
                cost = response.cost_micro + second.cost_micro
            response = ModelResponse(
                text=second.text,
                model_used=second.model_used,
                provider_request_id=second.provider_request_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_micro=cost,
                cost_source=second.cost_source,
                latency_ms=response.latency_ms + second.latency_ms,
                finish_reason=second.finish_reason,
                # Carried from the attempt that actually answered. Without it a repair silently
                # drops how the call was routed, and the run then records a routed provider with
                # no routing at all — exactly the thing the routing record exists to make knowable.
                extra=second.extra,
            )
            validation, brief = validate(response.text, sheet)
    except ProviderTimeoutError as exc:
        # The provider may have billed for work whose usage never came back, so the held
        # estimate stands rather than being returned.
        runs.settle(supabase, reservation.id, None)
        return _failed(
            supabase, scope, run_id, mode, "timeout", exc.code, attempts, reservation, analysis
        )
    except ProviderError as exc:
        if exc.code in NEVER_DISPATCHED:
            runs.release(supabase, reservation.id)
        else:
            runs.settle(supabase, reservation.id, None)
        return _failed(
            supabase,
            scope,
            run_id,
            mode,
            "provider_error",
            exc.code,
            attempts,
            reservation,
            analysis,
        )

    cost_micro = response.cost_micro
    runs.settle(supabase, reservation.id, cost_micro)

    validation_record = validation.record()
    if validation.rejected:
        # Raw model text is never stored. The validator's reasons and a hash are enough to
        # investigate a bad answer without keeping the answer itself.
        validation_record["output_sha256"] = hashlib.sha256(response.text.encode()).hexdigest()
        runs.finish_run(
            supabase,
            scope,
            run_id,
            status="invalid_output",
            validation=validation_record,
            attempts=attempts,
            latency_ms=response.latency_ms,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            cost_micro=cost_micro,
            cost_source=response.cost_source,
            routing=response.extra or None,
            model_used=response.model_used,
            provider_request_id=response.provider_request_id,
            error_code="invalid_output",
        )
        runs.log_run(
            scope,
            run_id,
            capability=CAPABILITY,
            status="invalid_output",
            mode=mode,
            attempts=attempts,
            latency_ms=response.latency_ms,
        )
        return BriefResult(
            status="invalid_output",
            run_id=run_id,
            brief=None,
            validation=validation,
            message=(
                "The model's answer did not check out against your figures, so it is not shown."
            ),
            retryable=True,
            reasons=[],
            input_fingerprint=analysis.input_fingerprint,
            as_of=analysis.as_of,
        )

    # 7. The inputs are read again. A statement confirmed while this was being written means
    #    the brief describes figures that are no longer the current ones.
    current = build_bundle(supabase, scope, as_of).analysis
    status = (
        "superseded" if current.input_fingerprint != analysis.input_fingerprint else "succeeded"
    )

    runs.finish_run(
        supabase,
        scope,
        run_id,
        status=status,
        validation=validation_record,
        attempts=attempts,
        latency_ms=response.latency_ms,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        cost_micro=cost_micro,
        cost_source=response.cost_source,
        routing=response.extra or None,
        model_used=response.model_used,
        provider_request_id=response.provider_request_id,
        output=brief.model_dump() if brief else None,
    )
    runs.log_run(
        scope,
        run_id,
        capability=CAPABILITY,
        status=status,
        mode=mode,
        attempts=attempts,
        latency_ms=response.latency_ms,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        cost_micro=cost_micro,
        cost_source=response.cost_source,
        claims_withheld=validation.claims_withheld,
        coverage_gaps=len(validation.coverage_gaps),
    )
    return BriefResult(
        status=status,
        run_id=run_id,
        brief=brief,
        validation=validation,
        message=(
            "Your data changed while this was being written. Ask for it again."
            if status == "superseded"
            else None
        ),
        retryable=status == "superseded",
        reasons=[],
        input_fingerprint=analysis.input_fingerprint,
        as_of=analysis.as_of,
    )


def _failed(
    supabase: SupabaseClient,
    scope: WorkspaceScope,
    run_id: str,
    mode: str,
    status: str,
    error_code: str,
    attempts: int,
    reservation: runs.Reservation,
    analysis: Any,
) -> BriefResult:
    """One line for the owner, the figures untouched, and the attempt on the record."""
    runs.finish_run(
        supabase,
        scope,
        run_id,
        status=status,
        attempts=attempts,
        error_code=error_code,
        cost_micro=reservation.estimated_cost_micro,
        cost_source=prices.SOURCE_ESTIMATE,
    )
    runs.log_run(
        scope, run_id, capability=CAPABILITY, status=status, mode=mode, error_code=error_code
    )
    return BriefResult(
        status=status,
        run_id=run_id,
        brief=None,
        validation=None,
        message="Explanation unavailable right now. Your figures below are complete.",
        retryable=True,
        reasons=[],
        input_fingerprint=analysis.input_fingerprint,
        as_of=analysis.as_of,
    )
