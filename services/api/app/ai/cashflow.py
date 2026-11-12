"""Cash-flow buckets for every row of an uploaded statement.

The division of labour here is the whole design, and it is worth stating plainly.

**Direction is arithmetic.** It comes from the amount, in this file, before a batch is built. It
is told to the model as a decided fact and re-checked after the model answers. The model cannot
change it, and a category belonging to the wrong direction is discarded rather than displayed.

**The bucket is judgement.** That is what the model is for, and it is checked afterwards against
the ids that were actually sent.

**The last word is the owner's.** A manual correction overwrites the category and is what gets
exported.

Everything else is the existing machinery: the same provider boundary, the same reservation
before dispatch, the same `ai_runs` rows. Nothing here calls a model directly or counts its own
spend.

A run over fifteen hundred rows does not fit in one request, so it does not try to. Each call
does as many batches as its deadline allows and returns where it got to; a row with no
classification yet is simply one the next call picks up. That is what makes the whole thing
resumable after a timeout, a refusal or a closed tab, and it is why there is no background work
left running after the response.
"""

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from app.ai import prices, prompts, runs
from app.ai.brief import UnpricedModelError, UsageRefusedError
from app.ai.provider import ModelProvider, ModelRequest, ProviderError, ProviderTimeoutError
from app.config import Settings
from app.supabase import SupabaseClient
from app.workspaces import WorkspaceScope

logger = logging.getLogger(__name__)

CAPABILITY = "cashflow"

Direction = Literal["inflow", "outflow"]

UNCLASSIFIED = "Unclassified"

ALLOWED: dict[str, frozenset[str]] = {
    "inflow": frozenset(prompts.CASHFLOW_INFLOW_CATEGORIES),
    "outflow": frozenset(prompts.CASHFLOW_OUTFLOW_CATEGORIES),
}

# What one answered item costs in output tokens, and what the surrounding JSON costs regardless.
#
# Measured rather than assumed: a batch of twenty-five real transactions against gpt-5.2 came
# back at 53.3 output tokens an item. The figure below is that with room on top, because the
# consequence of running out is not a short answer but an unparseable one, and a batch whose JSON
# is cut off loses every row in it to Unclassified at once.
OUTPUT_TOKENS_PER_ITEM = 75
OUTPUT_TOKENS_OVERHEAD = 150

# Time to allow, after a wave of batches answers, for writing its verdicts back and reading the
# counts: one request per row plus two counts, so a few seconds at the sizes involved.
WRITE_BACK_SECONDS = 8.0


def batch_size_for(settings: Settings) -> int:
    """How many rows may be asked about at once, given what the model is allowed to write.

    The configured batch size is a ceiling, not a promise. A deployment that lowers
    `ai_max_output_tokens` gets smaller batches rather than truncated ones, which is the failure
    the caller cannot detect from the outside: the answer is valid JSON right up until it isn't.
    """
    affordable = (settings.ai_max_output_tokens - OUTPUT_TOKENS_OVERHEAD) // OUTPUT_TOKENS_PER_ITEM
    return max(1, min(settings.ai_cashflow_batch_size, affordable))


# Fields from the uploaded file that may reach the model. Everything else in `raw` stays here.
#
# The exclusion that matters is the source file's own category column. The brief is explicit
# that an existing Plaid or bank category must not influence this classification, so rather than
# filtering those names out — which fails the moment a file spells one differently — this is an
# allowlist. A column not named here cannot reach the model, whatever it is called.
SENT_FIELDS: tuple[str, ...] = (
    "Date",
    "Transaction Name",
    "Merchant Name",
    "Account Name",
    "Account Type",
    "Account Subtype",
    "Institution",
    "Description",
    "Memo",
    "Name",
)

# Names that carry a pre-existing categorisation. Held as a second check on top of the allowlist,
# so a future edit that widens SENT_FIELDS cannot quietly let one through.
FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {
        "categories",
        "category",
        "plaid category",
        "personal finance category",
        "plaid_category",
        "personal_finance_category",
    }
)

# Keys this module puts in a payload itself, which are therefore not file columns.
_OWN_KEYS = frozenset({"transaction_id", "direction", "amount", "allowed_categories"})


def direction_of(amount_minor: int) -> Direction:
    """Which way the money went.

    `amount_minor` is this application's canonical signed value: inflows positive. An upload
    whose own file puts outflows positive was turned around by the CSV mapping before staging,
    which is where a sign convention belongs. By the time a row reaches here there is one
    convention, so this is a comparison and not a judgement.

    Zero counts as an outflow, so that every row lands in exactly one direction and none is left
    without a list of buckets to choose from.
    """
    return "inflow" if amount_minor > 0 else "outflow"


def allowed_for(direction: Direction) -> tuple[str, ...]:
    return (
        prompts.CASHFLOW_INFLOW_CATEGORIES
        if direction == "inflow"
        else prompts.CASHFLOW_OUTFLOW_CATEGORIES
    )


@dataclass(frozen=True)
class Candidate:
    """One staged row, reduced to what the model is allowed to see."""

    row_id: str
    row_index: int
    transaction_id: str
    amount_minor: int
    direction: Direction
    fields: dict[str, str]

    def payload(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "direction": self.direction,
            "amount": f"{abs(self.amount_minor) / 100:.2f}",
            "allowed_categories": list(allowed_for(self.direction)),
            **self.fields,
        }


@dataclass(frozen=True)
class Verdict:
    row_id: str
    row_index: int
    transaction_id: str
    direction: Direction
    category: str
    confidence: str
    reason: str
    source: str = "ai"


@dataclass
class BatchOutcome:
    verdicts: list[Verdict] = field(default_factory=list)
    run_id: str | None = None
    cost_micro: int = 0
    problems: list[str] = field(default_factory=list)
    failed: str | None = None


def _clean(value: Any, limit: int = 200) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.split())[:limit]


def candidate_from_row(row: dict[str, Any]) -> Candidate | None:
    """Reduce a staged row to a candidate, or None when it carries no usable amount."""
    extracted = row.get("extracted") or {}
    reviewed = row.get("reviewed") or {}
    amount = reviewed.get("amount_minor", extracted.get("amount_minor"))
    if not isinstance(amount, int):
        return None

    raw = row.get("raw") or {}
    fields: dict[str, str] = {}
    for name in SENT_FIELDS:
        if name.strip().lower() in FORBIDDEN_FIELDS:
            continue
        value = _clean(raw.get(name, ""))
        if value:
            fields[name] = value
    if not fields:
        # Nothing from the original columns was usable, so fall back to the deterministic
        # extraction rather than sending a row with no description at all.
        description = _clean(reviewed.get("description", extracted.get("description", "")))
        if description:
            fields["Description"] = description

    # A stable id: the file's own where it has one, otherwise the staged row's index, which is
    # stable for the life of the import and is what the answer is merged back on.
    transaction_id = _clean(raw.get("Transaction ID") or raw.get("transaction_id") or "", 64)
    if not transaction_id:
        transaction_id = f"row-{row.get('row_index')}"

    return Candidate(
        row_id=str(row.get("id")),
        row_index=int(row.get("row_index", 0)),
        transaction_id=transaction_id,
        amount_minor=amount,
        direction=direction_of(amount),
        fields=fields,
    )


def assert_nothing_forbidden(batch: list[dict[str, Any]]) -> None:
    """Fail loudly rather than send a pre-existing category to the model.

    This runs on the exact payload about to be serialised, which is the only place worth
    checking: an allowlist someone widened, a column renamed upstream, or a raw dict that leaked
    in would all surface here rather than in the request body.
    """
    for item in batch:
        for key in item:
            if key in _OWN_KEYS:
                continue
            if key.strip().lower() in FORBIDDEN_FIELDS:
                raise ValueError(f"refusing to send a pre-existing category field: {key!r}")


def validate_items(
    payload: dict[str, Any], batch: list[Candidate]
) -> tuple[list[Verdict], list[str]]:
    """Every candidate gets exactly one verdict. Nothing invented survives.

    Four things can go wrong, and each falls back to that direction's Unclassified rather than
    dropping the row: an id nobody asked about, the same id twice, a category belonging to the
    other direction, and no answer at all. A transaction that could not be classified still has
    to appear in the review and in the export, so it keeps its place in both.
    """
    by_id = {c.transaction_id: c for c in batch}
    seen: dict[str, Verdict] = {}
    problems: list[str] = []

    items = payload.get("items")
    if not isinstance(items, list):
        items = []

    for item in items:
        if not isinstance(item, dict):
            problems.append("an item was not an object")
            continue
        tid = str(item.get("transaction_id", ""))
        candidate = by_id.get(tid)
        if candidate is None:
            # An id nobody asked about. Discarded rather than stored against a guess.
            problems.append(f"unknown transaction_id {tid[:40]!r}")
            continue
        if tid in seen:
            problems.append(f"duplicate answer for {tid[:40]!r}")
            continue

        category = str(item.get("cash_flow_category", "")).strip()
        confidence = str(item.get("confidence", "")).strip().lower()
        reason = _clean(item.get("reason", ""), 160)

        if category not in ALLOWED[candidate.direction]:
            problems.append(f"{tid[:40]!r}: {category!r} is not a {candidate.direction} category")
            category = UNCLASSIFIED
            confidence = "low"
            reason = "The suggested category did not belong to this transaction's direction."
        if confidence not in prompts.CASHFLOW_CONFIDENCE:
            confidence = "low"

        seen[tid] = Verdict(
            row_id=candidate.row_id,
            row_index=candidate.row_index,
            transaction_id=tid,
            direction=candidate.direction,
            category=category,
            confidence=confidence,
            reason=reason,
        )

    for candidate in batch:
        if candidate.transaction_id not in seen:
            problems.append(f"no answer for {candidate.transaction_id[:40]!r}")
            seen[candidate.transaction_id] = Verdict(
                row_id=candidate.row_id,
                row_index=candidate.row_index,
                transaction_id=candidate.transaction_id,
                direction=candidate.direction,
                category=UNCLASSIFIED,
                confidence="low",
                reason="The model did not return an answer for this transaction.",
            )

    return [seen[c.transaction_id] for c in batch], problems


def fingerprint(import_id: str, payload: list[dict[str, Any]]) -> str:
    """What this batch asked, as a hash: the same question always fingerprints the same.

    A hex digest rather than a readable label because `ai_runs.input_fingerprint` is exactly
    sixty-four characters across every capability, so that two runs can be compared by it without
    knowing which capability wrote them.
    """
    material = json.dumps(
        {"v": prompts.CASHFLOW_PROMPT_VERSION, "import": import_id, "batch": payload},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def unclassified_for(batch: list[Candidate], reason: str) -> list[Verdict]:
    """What a failed batch leaves behind, so that one bad batch cannot destroy an upload."""
    return [
        Verdict(
            row_id=c.row_id,
            row_index=c.row_index,
            transaction_id=c.transaction_id,
            direction=c.direction,
            category=UNCLASSIFIED,
            confidence="low",
            reason=_clean(reason, 160),
        )
        for c in batch
    ]


# ----------------------------------------------------------------- one batch --
def classify_batch(
    supabase: SupabaseClient,
    scope: WorkspaceScope,
    provider: ModelProvider,
    settings: Settings,
    batch: list[Candidate],
    *,
    import_id: str,
    model: str | None = None,
) -> BatchOutcome:
    """One batch: reserve, record, ask once, check every item, settle.

    A provider failure returns an outcome whose verdicts are all Unclassified rather than
    raising, because the other fifty-nine batches are unaffected by it. A spend refusal does
    raise, because it will refuse the next batch too and the run should stop rather than write
    Unclassified over rows it never asked about.
    """
    model = model or settings.ai_model or provider.name
    payload = [c.payload() for c in batch]
    assert_nothing_forbidden(payload)
    user_message = prompts.cashflow_user_message(payload)

    if provider.live:
        # One attempt, not two: there is no repair round here, so holding for a second call
        # would reserve twice what a batch can actually spend.
        estimate = prices.estimate_micro(
            model,
            prices.estimate_tokens(prompts.CASHFLOW_SYSTEM + user_message),
            settings.ai_max_output_tokens,
            attempts=1,
        )
        if estimate is None:
            raise UnpricedModelError(model)
    else:
        estimate = 0

    reservation = runs.reserve(supabase, runs.identity_of(scope), CAPABILITY, estimate, settings)
    if isinstance(reservation, runs.Refusal):
        raise UsageRefusedError(
            reservation.reason,
            reservation.identity_spend_micro,
            reservation.global_spend_micro,
        )

    run_id = runs.start_run(
        supabase,
        scope,
        capability=CAPABILITY,
        mode="live" if provider.live else "replay",
        provider=provider.name,
        model_requested=model,
        prompt_version=prompts.CASHFLOW_PROMPT_VERSION,
        analysis_version=prompts.CASHFLOW_PROMPT_VERSION,
        rules_version=prompts.CASHFLOW_PROMPT_VERSION,
        analysis_date=datetime.now(UTC).date().isoformat(),
        input_fingerprint=fingerprint(import_id, payload),
        status="running",
        safe_input={"batch": payload},
        input_summary={"rows": len(batch), "first_row_index": batch[0].row_index},
        reservation_id=reservation.id,
    )

    try:
        response = provider.complete(
            ModelRequest(
                capability=CAPABILITY,
                prompt_version=prompts.CASHFLOW_PROMPT_VERSION,
                model=model,
                system=prompts.CASHFLOW_SYSTEM,
                user=user_message,
                json_schema=prompts.CASHFLOW_SCHEMA,
                max_output_tokens=settings.ai_max_output_tokens,
                timeout_seconds=settings.ai_timeout_seconds,
            )
        )
    except (ProviderTimeoutError, ProviderError) as exc:
        code = getattr(exc, "code", "provider_error")
        if code in ("model_not_allowed", "recording_missing"):
            # The request never left this process, so the budget goes back.
            runs.release(supabase, reservation.id)
        else:
            # It may well have been billed for work whose usage never came back. The hold stands.
            runs.settle(supabase, reservation.id, None)
        runs.finish_run(
            supabase,
            scope,
            run_id,
            status="timeout" if isinstance(exc, ProviderTimeoutError) else "provider_error",
            error_code=code,
        )
        logger.warning("cashflow.batch_failed", extra={"code": code, "rows": len(batch)})
        return BatchOutcome(
            verdicts=unclassified_for(batch, "This batch could not be classified."),
            run_id=run_id,
            failed=code,
        )

    runs.settle(supabase, reservation.id, response.cost_micro)

    try:
        parsed = json.loads(response.text)
    except (json.JSONDecodeError, TypeError, ValueError):
        parsed = {}
    verdicts, problems = validate_items(parsed if isinstance(parsed, dict) else {}, batch)

    kept = sum(1 for v in verdicts if v.category != UNCLASSIFIED)
    runs.finish_run(
        supabase,
        scope,
        run_id,
        status="succeeded" if kept else "invalid_output",
        error_code=None if kept else "invalid_output",
        latency_ms=response.latency_ms,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        cost_micro=response.cost_micro,
        cost_source=response.cost_source,
        routing=response.extra or None,
        model_used=response.model_used,
        validation={
            "rows": len(batch),
            "items_kept": kept,
            "items_unclassified": len(batch) - kept,
            "problems": problems[:10],
        },
        output={"categories": [v.category for v in verdicts]},
    )
    return BatchOutcome(
        verdicts=verdicts,
        run_id=run_id,
        cost_micro=response.cost_micro or 0,
        problems=problems,
    )


def store(supabase: SupabaseClient, verdicts: list[Verdict], run_id: str | None) -> None:
    """Write each verdict onto its own staged row.

    One request per row rather than one per batch: PostgREST has no bulk update by differing
    values, and an upsert here would have to restate `raw`, which is the one column the export
    depends on being left exactly as uploaded.
    """
    now = datetime.now(UTC).isoformat()
    for verdict in verdicts:
        supabase.rest(
            "PATCH",
            "import_rows",
            params={"id": f"eq.{verdict.row_id}"},
            json_body={
                "cashflow": {
                    "direction": verdict.direction,
                    "category": verdict.category,
                    "confidence": verdict.confidence,
                    "reason": verdict.reason,
                    "source": verdict.source,
                    "run_id": run_id,
                    "decided_at": now,
                }
            },
            prefer="return=minimal",
        )


# ------------------------------------------------------------- the whole run --
@dataclass
class RunProgress:
    total: int
    classified: int
    remaining: int
    done: bool
    batches: int
    cost_micro: int
    stopped: str | None = None


def pending_rows(supabase: SupabaseClient, import_id: str, limit: int) -> list[dict[str, Any]]:
    """The next rows with no classification yet, in file order.

    This is the whole of the resume logic. A batch that was refused leaves its rows null, and
    null is what comes back here, so a later call continues from exactly where the last one
    stopped without any job table to lease.
    """
    return supabase.rest(
        "GET",
        "import_rows",
        params={
            "import_id": f"eq.{import_id}",
            "cashflow": "is.null",
            "select": "id,row_index,raw,extracted,reviewed",
            "order": "row_index",
            "limit": str(limit),
        },
    )


def classify_import(
    supabase: SupabaseClient,
    scope: WorkspaceScope,
    provider: ModelProvider,
    settings: Settings,
    *,
    import_id: str,
    deadline_seconds: float | None = None,
    model: str | None = None,
) -> RunProgress:
    """As many batches as the deadline allows, then report where it got to.

    Batches within one call run concurrently, bounded by `ai_cashflow_concurrency`, because each
    is an independent question about a disjoint set of rows. The bound is the point: every batch
    still passes through the reservation ledger, and `ai_max_in_flight_per_identity` still has
    the final say on how many may be outstanding at once.

    The deadline is a ceiling on the whole call, not on when the last batch may start. A wave
    dispatched with two seconds left would still run for a provider timeout afterwards, and this
    runs on a platform that kills a request at a fixed limit: work cut off there is work nobody
    can account for, since the reservation stands and the answer is lost. So a wave is only
    started when there is room for it to finish.
    """
    total = supabase.count("import_rows", {"import_id": f"eq.{import_id}"})
    size = batch_size_for(settings)
    # Never dispatch more at once than the identity is allowed to hold reservations for, or the
    # surplus batches would be refused by a cap this code set itself up to breach.
    lanes = max(1, min(settings.ai_cashflow_concurrency, settings.ai_max_in_flight_per_identity))
    deadline = time.monotonic() + (
        deadline_seconds if deadline_seconds is not None else settings.ai_cashflow_deadline_seconds
    )
    # A wave costs at most one provider timeout, plus the writes that follow it.
    wave_seconds = settings.ai_timeout_seconds + WRITE_BACK_SECONDS

    batches_done = 0
    cost_micro = 0
    stopped: str | None = None

    while stopped is None and time.monotonic() + wave_seconds <= deadline:
        rows = pending_rows(supabase, import_id, size * lanes)
        if not rows:
            break

        reduced = [(row, candidate_from_row(row)) for row in rows]
        candidates = [c for _, c in reduced if c is not None]
        unusable = [row for row, c in reduced if c is None]
        if unusable:
            # A row with no amount has no direction, so it cannot be classified. It is recorded
            # as such rather than left null, which would have it picked up again forever.
            store(
                supabase,
                [
                    Verdict(
                        row_id=str(row["id"]),
                        row_index=int(row["row_index"]),
                        transaction_id=f"row-{row['row_index']}",
                        direction="outflow",
                        category=UNCLASSIFIED,
                        confidence="low",
                        reason="This row has no readable amount, so it has no direction.",
                    )
                    for row in unusable
                ],
                None,
            )
        if not candidates:
            continue

        groups = [candidates[i : i + size] for i in range(0, len(candidates), size)]
        with ThreadPoolExecutor(max_workers=lanes) as pool:
            futures = [
                pool.submit(
                    classify_batch,
                    supabase,
                    scope,
                    provider,
                    settings,
                    group,
                    import_id=import_id,
                    model=model,
                )
                for group in groups
            ]
            for future, group in zip(futures, groups, strict=True):
                try:
                    outcome = future.result()
                except UsageRefusedError as exc:
                    # Leave these rows null so a later call retries them, rather than writing
                    # Unclassified over rows that were never actually asked about.
                    stopped = exc.reason
                    continue
                except UnpricedModelError:
                    stopped = "unpriced_model"
                    continue
                except Exception as exc:  # noqa: BLE001 - one batch must not end the run
                    # The message as well as the type: a batch that swallows its own failure has
                    # to leave behind something a person can act on, and these messages are
                    # written not to carry credentials or row content.
                    logger.warning(
                        "cashflow.batch_error",
                        extra={"error": type(exc).__name__, "detail": str(exc)[:300]},
                    )
                    outcome = BatchOutcome(
                        verdicts=unclassified_for(group, "This batch could not be classified."),
                        failed=type(exc).__name__,
                    )
                store(supabase, outcome.verdicts, outcome.run_id)
                batches_done += 1
                cost_micro += outcome.cost_micro

    classified = supabase.count(
        "import_rows", {"import_id": f"eq.{import_id}", "cashflow": "not.is.null"}
    )
    return RunProgress(
        total=total,
        classified=classified,
        remaining=max(0, total - classified),
        done=classified >= total,
        batches=batches_done,
        cost_micro=cost_micro,
        stopped=stopped,
    )
