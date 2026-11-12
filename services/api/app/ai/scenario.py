"""Explaining a scenario the owner already ran, without recomputing any part of it.

M9 computes both paths in Python. This module explains the difference between them. Every
number it can talk about was produced by `forecast.build` before the model was called, and the
validator's universe of quotable figures is exactly the facts assembled here — so the
explanation can be wrong about emphasis, but not about arithmetic.

Three distinctions this file is careful to keep, because collapsing any of them produces a
sentence that reads true and is not:

* **Ending cash and lowest cash are different questions.** A delayed payment can leave the
  ending balance untouched and still dig a hole in the middle, which is the case the whole
  feature exists for.
* **Two minima are not one day's change.** The baseline's low point and the scenario's low
  point may fall on different dates. Their difference is a difference between two troughs, not
  the amount the balance moved on any particular day, and both dates travel with it so nobody
  can imply otherwise.
* **The assumptions are the owner's, not observations.** An expected payment is a figure the
  owner typed. Bank history does not record an unpaid invoice, and nothing here may present one
  as though it did.

Everything the projection produces is marked estimated, so the validator's own rule forces
every claim bound to it to be hedged. A scenario cannot be described as what will happen.
"""

import hashlib
import json
import logging
from dataclasses import replace
from typing import Any

from app.ai import prices, prompts, runs, signals
from app.ai.provider import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderTimeoutError,
)
from app.ai.signals import FactSheet, Signal, render_money
from app.ai.validate import ValidatedBrief, Validation, validate
from app.analysis import forecast as forecast_module
from app.analysis.categorize import CATEGORY_VERSION
from app.analysis.routes import ANALYSIS_VERSION, CashAnalysis
from app.config import Settings
from app.supabase import SupabaseClient
from app.workspaces import WorkspaceScope

logger = logging.getLogger(__name__)

CAPABILITY = "scenario_explain"

# Below this, an explanation is a remark rather than an explanation. Two is not a quality bar:
# it is the point at which a scenario has said something about both what changed and what it
# did, which is the least this feature claims to do.
MIN_USEFUL_CLAIMS = 2


def completeness(
    coverage_gaps: list[str], kept_claims: int, sheet: FactSheet | None
) -> tuple[str, str | None]:
    """Whether what survived validation actually covers the scenario, in the owner's words.

    A validated answer is not the same as a complete one. An explanation can pass all four
    layers, lose most of its sentences to the numbers check, and arrive as one true remark that
    never mentions the trough — which is exactly what a delayed payment needs explained. Calling
    that "succeeded" reports the validator's opinion as the owner's answer.

    So coverage is reported separately and honestly. The run still succeeded, because the call
    did; the explanation is partial, because it is.
    """
    if not coverage_gaps and kept_claims >= MIN_USEFUL_CLAIMS:
        return "succeeded", None
    labels = []
    if sheet is not None:
        known = sheet.by_id()
        labels = [known[gap].label.lower() for gap in coverage_gaps if gap in known]
    if labels:
        missing = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]
        return "partial", (
            f"This explanation does not cover {missing}. Those figures are on the chart."
        )
    return (
        "partial",
        "This explanation is shorter than it should be. The projection below is complete.",
    )


def scenario_inputs(body: Any) -> dict[str, Any]:
    """The owner's assumptions, exactly as they were submitted."""
    return {
        "receipts_change_percent": body.receipts_change_percent,
        "expected_payment_minor": body.expected_payment_minor,
        "expected_payment_date": body.expected_payment_date,
        "delay_days": body.delay_days,
        "one_time_expense_minor": body.one_time_expense_minor,
        "one_time_expense_date": body.one_time_expense_date,
        "one_time_expense_label": body.one_time_expense_label,
    }


def combined_fingerprint(data_fingerprint: str, inputs: dict[str, Any]) -> str:
    """One hash over the data AND the assumptions.

    An explanation is only about this scenario on this data. Change a figure the owner typed
    or confirm a statement, and the stored explanation is stale by the same single mechanism
    that already covers the brief — no second staleness rule to keep in step.
    """
    material = json.dumps(
        {"data": data_fingerprint, "scenario": inputs}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _percent_of(part: int, whole: int) -> float | None:
    """A precomputed share, to one decimal place, or None when there is nothing to divide by.

    Computed here so an explanation that needs a proportion quotes one that was worked out in
    Python, like every other figure. A percentage the model arrives at itself is a number no
    layer of this validator ever checked.
    """
    if not whole:
        return None
    return round(part / abs(whole) * 100, 1)


def _divergence(baseline: Any, scenario: Any) -> dict[str, Any]:
    """The first day the two paths stop agreeing, computed here rather than guessed at."""
    pairs = list(zip(baseline.points, scenario.points, strict=False))
    for point, other in pairs:
        if point.balance_minor != other.balance_minor:
            return {"date": other.date, "gap_minor": other.balance_minor - point.balance_minor}
    return {"date": None, "gap_minor": 0}


def build_fact_sheet(
    scope: WorkspaceScope, analysis: CashAnalysis, inputs: dict[str, Any]
) -> FactSheet | None:
    """Both paths, their two endings, their two troughs, and the owner's own assumptions."""
    f = analysis.forecast
    if not f.available or f.baseline is None or f.scenario is None:
        return None
    base, alt = f.baseline, f.scenario
    signal_list: list[Signal] = []

    # What the owner changed. Not a computed fact: it is what they typed, and it is labelled
    # as theirs so nothing can present it as something the bank showed.
    changes: list[str] = []
    if inputs.get("receipts_change_percent"):
        changes.append(f"customer receipts changed by {inputs['receipts_change_percent']}%")
    if inputs.get("expected_payment_minor"):
        changes.append(
            f"a payment of {render_money(inputs['expected_payment_minor'])} you expect on "
            f"{inputs['expected_payment_date']}, arriving {inputs.get('delay_days', 0)} days late"
        )
    if inputs.get("one_time_expense_minor"):
        label = inputs.get("one_time_expense_label") or "a one-off cost"
        changes.append(
            f"{label} of {render_money(inputs['one_time_expense_minor'])} on "
            f"{inputs['one_time_expense_date']}"
        )
    signal_list.append(
        Signal(
            id="sc:inputs",
            kind="scenario_inputs",
            label="What you changed",
            values={
                "changes": changes,
                **{k: v for k, v in inputs.items() if v not in (None, 0, 0.0)},
            },
            rendered={},
            required=True,
            causal_allowed=True,
            money_fields=("expected_payment_minor", "one_time_expense_minor"),
            percent_fields=("receipts_change_percent",),
        )
    )

    signal_list.append(
        Signal(
            id="fc:ending",
            kind="forecast",
            label=f"Cash at the end of {f.horizon_days} days, before and after your change",
            values={
                "baseline_minor": base.ending_minor,
                "scenario_minor": alt.ending_minor,
                "difference_minor": alt.ending_minor - base.ending_minor,
                "difference_percent": _percent_of(
                    alt.ending_minor - base.ending_minor, base.ending_minor
                ),
                "unchanged": alt.ending_minor == base.ending_minor,
                "horizon_days": f.horizon_days,
            },
            rendered={
                "baseline": render_money(base.ending_minor),
                "scenario": render_money(alt.ending_minor),
                "difference": render_money(alt.ending_minor - base.ending_minor),
            },
            estimated=True,
            required=True,
            question_allowed=True,
            money_fields=("baseline_minor", "scenario_minor", "difference_minor"),
            percent_fields=("difference_percent",),
        )
    )

    # Deliberately one fact carrying BOTH troughs and BOTH dates. Splitting the difference out
    # on its own invites a sentence that treats it as one day's movement.
    low_difference = alt.lowest_minor - base.lowest_minor
    signal_list.append(
        Signal(
            id="fc:lowest",
            kind="forecast",
            label="The lowest point on each path",
            values={
                "baseline_minor": base.lowest_minor,
                "baseline_date": base.lowest_date,
                "scenario_minor": alt.lowest_minor,
                "scenario_date": alt.lowest_date,
                "difference_minor": low_difference,
                "difference_percent": _percent_of(low_difference, base.lowest_minor),
                "unchanged": low_difference == 0,
                "same_day": base.lowest_date == alt.lowest_date,
                "note": (
                    "These two low points fall on "
                    + (
                        "the same day."
                        if base.lowest_date == alt.lowest_date
                        else "different days, so the difference between them is not the amount "
                        "the balance moved on any one day."
                    )
                ),
            },
            rendered={
                "baseline": render_money(base.lowest_minor),
                "scenario": render_money(alt.lowest_minor),
                "difference": render_money(low_difference),
            },
            estimated=True,
            question_allowed=True,
            required=low_difference != 0,
            money_fields=("baseline_minor", "scenario_minor", "difference_minor"),
            percent_fields=("difference_percent",),
        )
    )

    gap = _divergence(base, alt)
    if gap["date"]:
        signal_list.append(
            Signal(
                id="sc:timing",
                kind="scenario_timing",
                label="When the two paths first differ",
                values={"date": gap["date"], "gap_minor": gap["gap_minor"]},
                rendered={"gap": render_money(gap["gap_minor"])},
                estimated=True,
                required=True,
                causal_allowed=True,
                money_fields=("gap_minor",),
            )
        )

    for index, assumption in enumerate(f.assumptions, start=1):
        signal_list.append(
            Signal(
                id=f"asm:{index}",
                kind="assumption",
                label=assumption.label,
                values={"detail": assumption.detail, "estimated": assumption.estimated},
                rendered={},
                estimated=assumption.estimated,
                question_allowed=True,
            )
        )

    anchor = f.anchor or {}
    signal_list.append(
        Signal(
            id="fc:anchor",
            kind="balance",
            label="Where both paths start",
            values={
                "amount_minor": anchor.get("amount_minor"),
                "as_of": anchor.get("as_of"),
                "is_live": anchor.get("is_live"),
            },
            rendered={"amount": render_money(anchor.get("amount_minor") or 0)},
            is_live=bool(anchor.get("is_live")),
            money_fields=("amount_minor",),
        )
    )

    return FactSheet(
        business_name=scope.name,
        industry=scope.industry,
        as_of=analysis.as_of,
        currency=analysis.currency,
        signals=signal_list,
        input_summary={"scenario": inputs},
    )


def generate(
    supabase: SupabaseClient,
    scope: WorkspaceScope,
    provider: ModelProvider,
    settings: Settings,
    analysis: CashAnalysis,
    inputs: dict[str, Any],
    model: str | None = None,
) -> dict[str, Any]:
    """Explain a scenario that has already been computed. Nothing here computes anything."""
    from app.ai.brief import UnpricedModelError, UsageRefusedError

    sheet = build_fact_sheet(scope, analysis, inputs)
    if sheet is None:
        return {
            "status": "insufficient_data",
            "run_id": None,
            "explanation": None,
            "message": (
                "There is not enough history to project from, so there is nothing to explain."
            ),
        }

    mode = "live" if provider.live else "replay"
    model = model or settings.ai_model or provider.name
    fingerprint = combined_fingerprint(analysis.input_fingerprint, inputs)
    payload = sheet.payload()
    user_message = prompts.scenario_user_message(payload)

    if provider.live:
        estimate = prices.estimate_micro(
            model,
            prices.estimate_tokens(prompts.SCENARIO_SYSTEM + user_message),
            settings.ai_max_output_tokens,
        )
        if estimate is None:
            raise UnpricedModelError(model)
    else:
        estimate = 0
    reservation = runs.reserve(supabase, runs.identity_of(scope), CAPABILITY, estimate, settings)
    if isinstance(reservation, runs.Refusal):
        raise UsageRefusedError(
            reservation.reason, reservation.identity_spend_micro, reservation.global_spend_micro
        )

    run_id = runs.start_run(
        supabase,
        scope,
        capability=CAPABILITY,
        mode=mode,
        provider=provider.name,
        model_requested=model,
        prompt_version=prompts.SCENARIO_PROMPT_VERSION,
        analysis_version=ANALYSIS_VERSION,
        rules_version=CATEGORY_VERSION,
        analysis_date=analysis.as_of,
        input_fingerprint=fingerprint,
        status="running",
        safe_input=payload,
        input_summary=sheet.input_summary,
        reservation_id=reservation.id,
    )

    request = ModelRequest(
        capability=CAPABILITY,
        prompt_version=prompts.SCENARIO_PROMPT_VERSION,
        model=model,
        system=prompts.SCENARIO_SYSTEM,
        user=user_message,
        json_schema=prompts.BRIEF_SCHEMA,
        max_output_tokens=settings.ai_max_output_tokens,
        timeout_seconds=settings.ai_timeout_seconds,
    )

    attempts = 1
    try:
        response = provider.complete(request)
        validation, explanation = validate(response.text, sheet)
        if validation.rejected:
            second = provider.complete(
                replace(request, user=prompts.repair_user_message(payload, validation.problems))
            )
            attempts = 2
            response = _merge(response, second)
            validation, explanation = validate(response.text, sheet)
    except ProviderTimeoutError as exc:
        runs.settle(supabase, reservation.id, None)
        return _failed(supabase, scope, run_id, "timeout", exc.code, attempts, reservation)
    except ProviderError as exc:
        if exc.code in ("model_not_allowed", "recording_missing"):
            runs.release(supabase, reservation.id)
        else:
            runs.settle(supabase, reservation.id, None)
        return _failed(supabase, scope, run_id, "provider_error", exc.code, attempts, reservation)

    runs.settle(supabase, reservation.id, response.cost_micro)
    record = validation.record()

    if validation.rejected:
        record["output_sha256"] = hashlib.sha256(response.text.encode()).hexdigest()
        _finish(supabase, scope, run_id, "invalid_output", record, attempts, response, None)
        return {
            "status": "invalid_output",
            "run_id": run_id,
            "explanation": None,
            "message": (
                "The explanation did not check out against your figures, so it is not shown. "
                "The projection below is unaffected."
            ),
        }

    # The run is recorded as succeeded because the call did succeed and its answer was
    # checked. What is reported to the interface is whether that answer covered the scenario,
    # which is a different question and is answered separately below.
    _finish(supabase, scope, run_id, "succeeded", record, attempts, response, explanation)
    status, shortfall = completeness(
        validation.coverage_gaps, len(explanation.claims) if explanation else 0, sheet
    )
    runs.log_run(
        scope,
        run_id,
        capability=CAPABILITY,
        status="succeeded",
        coverage=status,
        mode=mode,
        attempts=attempts,
        claims_withheld=validation.claims_withheld,
        coverage_gaps=len(validation.coverage_gaps),
        cost_micro=response.cost_micro,
    )
    return {
        "status": status,
        "run_id": run_id,
        "explanation": explanation,
        "validation": validation,
        "fingerprint": fingerprint,
        "message": shortfall,
    }


def _finish(
    supabase: SupabaseClient,
    scope: WorkspaceScope,
    run_id: str,
    status: str,
    record: dict[str, Any],
    attempts: int,
    response: ModelResponse,
    explanation: ValidatedBrief | None,
) -> None:
    runs.finish_run(
        supabase,
        scope,
        run_id,
        status=status,
        validation=record,
        attempts=attempts,
        latency_ms=response.latency_ms,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        cost_micro=response.cost_micro,
        cost_source=response.cost_source,
        routing=response.extra or None,
        model_used=response.model_used,
        provider_request_id=response.provider_request_id,
        error_code=None if status == "succeeded" else status,
        output=explanation.model_dump() if explanation else None,
    )


def _merge(first: ModelResponse, second: ModelResponse) -> ModelResponse:
    def add(a: int | None, b: int | None) -> int | None:
        return None if a is None and b is None else (a or 0) + (b or 0)

    cost = None
    if first.cost_micro is not None and second.cost_micro is not None:
        cost = first.cost_micro + second.cost_micro
    return ModelResponse(
        text=second.text,
        model_used=second.model_used,
        provider_request_id=second.provider_request_id,
        prompt_tokens=add(first.prompt_tokens, second.prompt_tokens),
        completion_tokens=add(first.completion_tokens, second.completion_tokens),
        cost_micro=cost,
        cost_source=second.cost_source,
        latency_ms=first.latency_ms + second.latency_ms,
        finish_reason=second.finish_reason,
        # Carried from the attempt that actually answered. Without it a repair silently
        # drops how the call was routed, and the run then records a routed provider with
        # no routing at all — exactly the thing the routing record exists to make knowable.
        extra=second.extra,
    )


def _failed(
    supabase: SupabaseClient,
    scope: WorkspaceScope,
    run_id: str,
    status: str,
    error_code: str,
    attempts: int,
    reservation: runs.Reservation,
) -> dict[str, Any]:
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
    runs.log_run(scope, run_id, capability=CAPABILITY, status=status, error_code=error_code)
    return {
        "status": status,
        "run_id": run_id,
        "explanation": None,
        "message": (
            "The explanation is unavailable right now. The projection below is unaffected."
        ),
    }


def latest(
    supabase: SupabaseClient,
    scope: WorkspaceScope,
    analysis_date: str,
    fingerprint: str,
    provider: str,
    model: str,
) -> dict[str, Any] | None:
    """A stored explanation for this data, these assumptions AND this configuration.

    The provider and model are part of the key, not decoration. Without them, asking a second
    model to explain the same scenario would return the first model's answer and report it as
    current: the reuse would be indistinguishable from a fresh call, and a comparison between
    two models would quietly be a comparison of one model with itself.
    """
    rows = supabase.rest(
        "GET",
        "ai_runs",
        params={
            "workspace_id": f"eq.{scope.workspace_id}",
            "business_id": f"eq.{scope.business_id}",
            "capability": f"eq.{CAPABILITY}",
            "status": "eq.succeeded",
            "analysis_date": f"eq.{analysis_date}",
            "input_fingerprint": f"eq.{fingerprint}",
            "provider": f"eq.{provider}",
            "model_requested": f"eq.{model}",
            "select": runs.RUN_FIELDS,
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


__all__ = [
    "CAPABILITY",
    "FactSheet",
    "Validation",
    "build_fact_sheet",
    "combined_fingerprint",
    "forecast_module",
    "generate",
    "latest",
    "scenario_inputs",
    "signals",
]
