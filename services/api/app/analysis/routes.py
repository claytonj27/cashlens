"""Cash analysis for one workspace. Reads only; nothing here writes or moves money.

Authorisation and workspace selection are the existing ones: the scope comes from
`resolve_workspace`, so a demo visitor sees their demo workspace and a signed-in owner sees a
business they belong to, and neither can name another. The results are versioned and typed so
M10 can explain them without recomputing anything.

Reads go straight to the database on every request, so a newly confirmed import appears
without a reseed and nothing is served from a stale cache.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.analysis import fingerprint as fingerprint_module
from app.analysis import forecast as forecast_module
from app.analysis import metrics
from app.analysis.categorize import (
    CATEGORY_VERSION,
    INSTRUMENT_WORDS,
    REASON_CODES,
    REASON_GENERIC_INSTRUMENT,
    place,
    published_rules,
)
from app.auth import require_client_header, require_features
from app.workspaces import Scope, Supabase

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/workspaces", tags=["analysis"], dependencies=[Depends(require_features)]
)

ANALYSIS_VERSION = "analysis-2"
MAX_TRANSACTIONS = 20000
MAX_CLASSIFICATION_EXAMPLES = 3


class Money(BaseModel):
    amount_minor: int
    currency: str = "USD"


class BalanceRow(BaseModel):
    account_id: str
    account_name: str
    mask: str | None
    kind: str
    provenance: str
    amount_minor: int
    as_of: str
    is_live: bool


class Balances(BaseModel):
    """Latest known balance per account. Current and available are never added together."""

    rows: list[BalanceRow]
    live_total_minor: int | None
    statement_total_minor: int | None
    as_of_dates: list[str]
    mixed_dates: bool
    label: str


class MonthRow(BaseModel):
    month: str
    money_in_minor: int
    money_out_minor: int
    net_minor: int
    posted_count: int
    transfers_netted_minor: int
    complete: bool


class CategoryRow(BaseModel):
    key: str
    label: str
    group: str
    inflow_minor: int
    outflow_minor: int
    count: int
    netted_pair: bool
    transaction_ids: list[str]


class ChangeRow(BaseModel):
    key: str
    label: str
    group: str
    current_minor: int
    previous_minor: int
    change_minor: int


class Comparison(BaseModel):
    month: str
    previous_month: str
    month_totals: MonthRow | None
    previous_totals: MonthRow | None
    changes: list[ChangeRow]


class RecurringRow(BaseModel):
    label: str
    category: str
    typical_amount_minor: int
    cadence_days: int
    occurrences: int
    last_seen: str
    next_expected: str
    direction: str
    transaction_ids: list[str]


class Coverage(BaseModel):
    months_available: int
    complete_months: int
    posted_count: int
    first_posted: str | None
    last_posted: str | None
    stale_days: int | None
    sufficient_for_forecast: bool
    reasons: list[str]


class ForecastPoint(BaseModel):
    date: str
    balance_minor: int
    events: list[dict[str, Any]] = []


class ForecastPath(BaseModel):
    points: list[ForecastPoint]
    ending_minor: int
    lowest_minor: int
    lowest_date: str


class Assumption(BaseModel):
    key: str
    label: str
    detail: str
    estimated: bool


class Forecast(BaseModel):
    version: str
    available: bool
    as_of: str
    horizon_days: int
    anchor: dict[str, Any] | None
    daily_rate_minor: int | None = None
    reasons: list[str]
    baseline: ForecastPath | None
    scenario: ForecastPath | None
    difference_minor: int | None = None
    lowest_difference_minor: int | None = None
    assumptions: list[Assumption]


class Classification(BaseModel):
    """What the rules could not place, which is what a person is asked about next."""

    uncategorized_count: int
    generic_instrument_count: int
    examples: list[str]


class CashAnalysis(BaseModel):
    version: str
    metrics_version: str
    category_rules_version: str
    workspace_id: str
    business_name: str
    data_mode: str
    as_of: str
    data_through: str | None
    currency: str = "USD"
    balances: Balances
    months: list[MonthRow]
    categories: list[CategoryRow]
    comparison: Comparison | None
    recurring: list[RecurringRow]
    coverage: Coverage
    forecast: Forecast
    pending_excluded: int
    # Everything a stored explanation is checked against: the same inputs give the same hash,
    # so a result written against a different one is stale by comparison rather than by guess.
    input_fingerprint: str
    overrides_applied: int
    classification: Classification
    read_at: str


class ScenarioRequest(BaseModel):
    as_of: str | None = None
    receipts_change_percent: float = Field(default=0.0, ge=-100, le=500)
    expected_payment_minor: int | None = Field(default=None, ge=1, le=10_000_000_00)
    expected_payment_date: str | None = None
    delay_days: int = Field(default=0, ge=0, le=365)
    one_time_expense_minor: int | None = Field(default=None, ge=1, le=10_000_000_00)
    one_time_expense_date: str | None = None
    one_time_expense_label: str | None = Field(default=None, max_length=80)


def _iso(day: str | None, field: str) -> str | None:
    if day is None:
        return None
    try:
        return date.fromisoformat(day[:10]).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field} must be YYYY-MM-DD") from exc


def _current_overrides(supabase: Any, scope: Scope) -> dict[str, dict[str, Any]]:
    """The newest category decision per transaction for THIS workspace, reverts removed.

    Overrides are append-only, so the history is the whole table and the current state is the
    last row per transaction. A row with a null category is the owner putting that transaction
    back under the standard rules, so it cancels rather than applies.
    """
    rows = supabase.rest_all(
        "category_overrides",
        {
            "workspace_id": f"eq.{scope.workspace_id}",
            "business_id": f"eq.{scope.business_id}",
            "select": "transaction_id,category,transaction_revision,source,created_at",
            "order": "transaction_id,created_at",
        },
    )
    newest: dict[str, dict[str, Any]] = {}
    for row in rows:  # ordered by time, so the last row for a transaction wins
        newest[row["transaction_id"]] = row
    return {tid: row for tid, row in newest.items() if row.get("category")}


def _applied_overrides(
    overrides: dict[str, dict[str, Any]], transactions: list[metrics.Txn]
) -> dict[str, str]:
    """Only the decisions still made against the row as it stands today.

    If a later import ever revises a transaction, the decision made against the earlier
    revision is held back rather than applied to data the person never saw. Nothing in the
    product revises a confirmed row today, so this is a guarantee kept in advance.
    """
    revisions = {t.id: t.revision for t in transactions}
    return {
        tid: row["category"]
        for tid, row in overrides.items()
        if revisions.get(tid) == row["transaction_revision"]
    }


def _load(supabase: Any, scope: Scope) -> tuple[list[metrics.Txn], list[dict], dict[str, dict]]:
    """Posted and pending transactions, plus accounts and balances, for this workspace only."""
    accounts = {
        a["id"]: a
        for a in supabase.rest_all(
            "accounts",
            {
                "business_id": f"eq.{scope.business_id}",
                "select": "id,name,mask,source_type",
                "order": "id",
            },
        )
    }
    rows = supabase.rest_all(
        "transactions",
        {
            "business_id": f"eq.{scope.business_id}",
            "removed_at": "is.null",
            "select": "id,account_id,posted_date,description,amount_minor,status,revision",
            "order": "posted_date,id",
            "limit": str(MAX_TRANSACTIONS),
        },
    )
    transactions = [
        metrics.Txn(
            id=r["id"],
            account_id=r["account_id"],
            posted_date=r["posted_date"],
            description=r.get("description") or "",
            amount_minor=int(r["amount_minor"]),
            status=r["status"],
            revision=int(r.get("revision") or 1),
        )
        for r in rows
        if r["account_id"] in accounts
    ]
    balances = supabase.rest_all(
        "balance_snapshots",
        {
            "business_id": f"eq.{scope.business_id}",
            "select": "account_id,kind,provenance,amount_minor,as_of",
            "order": "as_of.desc,account_id",
        },
    )
    return transactions, balances, accounts


def _latest_balances(balances: list[dict], accounts: dict[str, dict], as_of: str) -> Balances:
    """One row per account per kind, newest on or before the analysis date.

    Current and available are reported separately and never summed with each other. When the
    rows carry different dates the aggregate is labelled as latest-known with mixed dates
    rather than implying one snapshot.
    """
    usable = [b for b in balances if b["as_of"][:10] <= as_of and b["account_id"] in accounts]
    newest: dict[tuple[str, str], dict] = {}
    for b in usable:
        key = (b["account_id"], b["kind"])
        if key not in newest or b["as_of"] > newest[key]["as_of"]:
            newest[key] = b
    rows = []
    for (account_id, kind), b in sorted(newest.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        account = accounts[account_id]
        rows.append(
            BalanceRow(
                account_id=account_id,
                account_name=account["name"],
                mask=account.get("mask"),
                kind=kind,
                provenance=b["provenance"],
                amount_minor=int(b["amount_minor"]),
                as_of=b["as_of"][:10],
                is_live=kind == "current" and b["provenance"] == "bank_observed",
            )
        )
    live = [r for r in rows if r.is_live]
    statement = [r for r in rows if r.kind == "statement_closing"]
    dates = sorted({r.as_of for r in (live or statement)})
    mixed = len(dates) > 1
    if live:
        label = "Latest known balances, mixed dates" if mixed else f"Bank balance as of {dates[0]}"
    elif statement:
        label = (
            "Latest statement balances, mixed dates"
            if mixed
            else f"Statement closing balance, {dates[0]}"
        )
    else:
        label = "No balance recorded yet"
    return Balances(
        rows=rows,
        live_total_minor=sum(r.amount_minor for r in live) if live else None,
        statement_total_minor=sum(r.amount_minor for r in statement) if statement else None,
        as_of_dates=dates,
        mixed_dates=mixed,
        label=label,
    )


def _previous_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"


def _analysis_date(requested: str | None, transactions: list[metrics.Txn]) -> str:
    """The explicit analysis date, or the latest observed data, so history reads correctly."""
    if requested:
        return requested
    latest = max((t.posted_date[:10] for t in transactions), default=None)
    today = datetime.now(UTC).date().isoformat()
    return max(latest, today) if latest and latest > today else (latest or today)


def _classification(
    transactions: list[metrics.Txn], as_of: str, applied: dict[str, str]
) -> Classification:
    """How many posted rows the rules left alone, and why, newest first."""
    unplaced = [
        t
        for t in metrics.within(transactions, as_of)
        if t.status == "posted"
        and t.id not in applied
        and place(t.description, t.amount_minor).category.key == "uncategorized"
    ]
    generic = [
        t
        for t in unplaced
        if place(t.description, t.amount_minor).reason == REASON_GENERIC_INSTRUMENT
    ]
    newest = sorted(unplaced, key=lambda t: (t.posted_date, t.id), reverse=True)
    return Classification(
        uncategorized_count=len(unplaced),
        generic_instrument_count=len(generic),
        examples=[t.id for t in newest[:MAX_CLASSIFICATION_EXAMPLES]],
    )


@dataclass(frozen=True)
class AnalysisBundle:
    """The analysis plus the rows it was built from.

    The AI layer needs the transactions themselves — to name a one-off purchase, to list the
    financing rows — and re-reading them would risk explaining figures that were computed from
    a different read. One read, one bundle, one fingerprint.
    """

    analysis: CashAnalysis
    transactions: list[metrics.Txn]
    overrides: dict[str, str]


def build_bundle(
    supabase: Any,
    scope: Scope,
    as_of: str | None = None,
    scenario: forecast_module.Scenario | None = None,
) -> AnalysisBundle:
    transactions, balances, accounts = _load(supabase, scope)
    applied = _applied_overrides(_current_overrides(supabase, scope), transactions)
    analysis_date = _analysis_date(as_of, transactions)
    observed = metrics.within(transactions, analysis_date)
    months = metrics.monthly(transactions, analysis_date, overrides=applied)
    comparison = None
    if months:
        latest_month = months[-1]["month"]
        comparison = metrics.compare_months(
            transactions, analysis_date, latest_month, _previous_month(latest_month), applied
        )
    versions = {
        "analysis": ANALYSIS_VERSION,
        "metrics": metrics.METRICS_VERSION,
        "rules": CATEGORY_VERSION,
        "forecast": forecast_module.FORECAST_VERSION,
    }
    analysis = CashAnalysis(
        version=ANALYSIS_VERSION,
        metrics_version=metrics.METRICS_VERSION,
        category_rules_version=CATEGORY_VERSION,
        workspace_id=scope.workspace_id,
        business_name=scope.name,
        data_mode=scope.data_mode,
        as_of=analysis_date,
        data_through=max((t.posted_date[:10] for t in observed), default=None),
        balances=_latest_balances(balances, accounts, analysis_date),
        months=[MonthRow(**m) for m in months],
        categories=[
            CategoryRow(**c)
            for c in metrics.by_category(transactions, analysis_date, None, applied)
        ],
        comparison=Comparison(**comparison) if comparison else None,
        recurring=[
            RecurringRow(**r) for r in metrics.recurring(transactions, analysis_date, 3, applied)
        ],
        coverage=Coverage(**metrics.coverage(transactions, analysis_date)),
        forecast=Forecast(**forecast_module.build(transactions, balances, analysis_date, scenario)),
        pending_excluded=sum(1 for t in observed if t.status != "posted"),
        input_fingerprint=fingerprint_module.compute(
            versions=versions,
            as_of=analysis_date,
            transactions=transactions,
            balances=balances,
            overrides=applied,
        ),
        overrides_applied=len(applied),
        classification=_classification(transactions, analysis_date, applied),
        read_at=datetime.now(UTC).isoformat(),
    )
    return AnalysisBundle(analysis=analysis, transactions=transactions, overrides=applied)


def _build(
    supabase: Any, scope: Scope, as_of: str | None, scenario: forecast_module.Scenario | None
) -> CashAnalysis:
    return build_bundle(supabase, scope, as_of, scenario).analysis


@router.get("/{workspace_id}/analysis", response_model=CashAnalysis)
def cash_analysis(
    scope: Scope,
    supabase: Supabase,
    as_of: Annotated[str | None, Query()] = None,
) -> CashAnalysis:
    """Balances, monthly cash flow, categories, recurring payments and a 30-day baseline."""
    return _build(supabase, scope, _iso(as_of, "as_of"), None)


def scenario_from_request(body: ScenarioRequest) -> forecast_module.Scenario:
    """The owner's assumptions, validated. Shared so the explanation runs on the same inputs."""
    if (body.expected_payment_minor is None) != (body.expected_payment_date is None):
        raise HTTPException(
            status_code=422,
            detail="an expected payment needs both an amount and a date you expect it on",
        )
    if (body.one_time_expense_minor is None) != (body.one_time_expense_date is None):
        raise HTTPException(
            status_code=422, detail="a one-off cost needs both an amount and a date"
        )
    return forecast_module.Scenario(
        receipts_change_percent=body.receipts_change_percent,
        expected_payment_minor=body.expected_payment_minor,
        expected_payment_date=_iso(body.expected_payment_date, "expected_payment_date"),
        delay_days=body.delay_days,
        one_time_expense_minor=body.one_time_expense_minor,
        one_time_expense_date=_iso(body.one_time_expense_date, "one_time_expense_date"),
        one_time_expense_label=body.one_time_expense_label,
    )


@router.post(
    "/{workspace_id}/analysis/scenario",
    response_model=CashAnalysis,
    dependencies=[Depends(require_client_header)],
)
def cash_scenario(body: ScenarioRequest, scope: Scope, supabase: Supabase) -> CashAnalysis:
    """The same analysis with a scenario laid over the forecast. Nothing is written."""
    return _build(supabase, scope, _iso(body.as_of, "as_of"), scenario_from_request(body))


@router.get("/{workspace_id}/analysis/rules")
def category_rules(scope: Scope, supabase: Supabase) -> dict[str, Any]:
    """The categorisation rules exactly as applied, so nothing about them is hidden."""
    return {
        "version": CATEGORY_VERSION,
        "note": (
            "Categories come from these keyword rules alone. Anything that matches nothing "
            "stays uncategorised. These are cash groupings for reading your statements, not "
            "confirmed accounting classifications."
        ),
        "counterparty_note": (
            "A description that names only how the money moved, such as a deposit number, a "
            "cheque number or a wire reference, is left uncategorised rather than placed by "
            "the instrument word, because the same word means different things on different "
            "rows."
        ),
        "instrument_words": list(INSTRUMENT_WORDS),
        "reason_codes": [{"key": key, "meaning": text} for key, text in REASON_CODES.items()],
        "rules": published_rules(),
    }
