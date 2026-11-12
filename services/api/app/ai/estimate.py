"""What a brief would cost, worked out without asking for one.

Pressing "Generate financial brief" is not a dry run: it reserves budget and dispatches. Before
the first paid call there needs to be a way to see the number the reservation would hold, so
this builds the real fact sheet for a real workspace through the same code the route uses, and
prints what `reserve_ai_usage` would be asked for — and then stops. No provider is constructed
and nothing is written.

    services/api/.venv/bin/python -m app.ai.estimate <workspace-id> [--model MODEL]

The model's price must already be recorded in fixtures/ai/prices.json. If it is not, this says
so and prints nothing else, for the same reason the route refuses: a reservation against an
assumed price is not a ceiling anybody can rely on.
"""

import argparse
import sys

from app.ai import prices, prompts, signals
from app.analysis.routes import build_bundle
from app.config import get_settings
from app.supabase import get_supabase
from app.workspaces import WorkspaceScope


def _scope(supabase, workspace_id: str) -> WorkspaceScope:
    rows = supabase.rest(
        "GET",
        "workspaces",
        params={"id": f"eq.{workspace_id}", "select": "id,business_id,visitor_id,user_id"},
    )
    if not rows:
        sys.exit(f"no workspace {workspace_id}")
    row = rows[0]
    businesses = supabase.rest(
        "GET",
        "businesses",
        params={"id": f"eq.{row['business_id']}", "select": "id,slug,name,industry,data_mode"},
    )
    if not businesses:
        sys.exit("workspace has no business")
    business = businesses[0]
    return WorkspaceScope(
        workspace_id=row["id"],
        business_id=business["id"],
        data_mode=business["data_mode"],
        slug=business["slug"],
        name=business["name"],
        industry=business["industry"],
        identity="user" if row.get("user_id") else "visitor",
        identity_id=str(row.get("user_id") or row.get("visitor_id")),
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace_id")
    parser.add_argument("--model", default=None, help="defaults to AI_MODEL")
    parser.add_argument("--as-of", default=None)
    args = parser.parse_args(argv)

    settings = get_settings()
    model = args.model or settings.ai_model
    if not model:
        sys.exit("no model: pass --model or set AI_MODEL")

    supabase = get_supabase()
    scope = _scope(supabase, args.workspace_id)
    bundle = build_bundle(supabase, scope, args.as_of)
    analysis = bundle.analysis

    sheet = signals.build(
        analysis,
        bundle.transactions,
        bundle.overrides,
        industry=scope.industry,
        imports=supabase.rest(
            "GET",
            "document_imports",
            params={
                "business_id": f"eq.{scope.business_id}",
                "status": "eq.confirmed",
                "select": "id,file_label,confirmed_at",
                "order": "confirmed_at",
            },
        ),
    )
    user_message = prompts.brief_user_message(sheet.payload())
    input_tokens = prices.estimate_tokens(prompts.BRIEF_SYSTEM + user_message)
    output_cap = settings.ai_max_output_tokens

    print(f"workspace     {scope.workspace_id} ({scope.name}, {scope.data_mode})")
    print(f"data as of    {analysis.as_of}  fingerprint {analysis.input_fingerprint[:16]}…")
    print(f"fact sheet    {len(sheet.signals)} facts, about {input_tokens:,} input tokens")
    print(f"model         {model}")
    print(f"price table   {prices.PRICES_PATH.name}, prices recorded {prices.recorded_on()}")

    if not prices.has_price(model):
        print()
        print("NO RECORDED PRICE for this model, so no call would be dispatched at all.")
        print("Record its real per-million prices in fixtures/ai/prices.json first.")
        return 1

    one = prices.estimate_micro(model, input_tokens, output_cap, attempts=1)
    whole = prices.estimate_micro(model, input_tokens, output_cap)
    print()
    print(f"output cap    {output_cap:,} tokens per call, at most {prices.MAX_BILLABLE_ATTEMPTS}")
    print(f"one call      {one:>10,} micro-dollars  (${one / 1_000_000:.4f})")
    print(f"RESERVATION   {whole:>10,} micro-dollars  (${whole / 1_000_000:.4f})")
    print("              ^ this is what is held before anything is dispatched")
    print()
    per_day = "micro-dollars per rolling day"
    print(f"identity cap  {settings.ai_identity_daily_cost_cap_micro:>10,} {per_day}")
    print(f"global cap    {settings.ai_global_daily_cost_cap_micro:>10,} {per_day}")

    spend = (supabase.rpc("ai_milestone_spend", {}) or [{}])[0]
    settled = int(spend.get("settled_micro") or 0)
    held = int(spend.get("held_micro") or 0)
    total = int(spend.get("total_micro") or 0)
    cap = settings.ai_milestone_total_cap_micro
    remaining = cap - total
    after = remaining - whole
    live_runs = spend.get("live_runs")
    print()
    print(f"milestone cap {cap:>10,} micro-dollars  (${cap / 1_000_000:.2f}) cumulative, no window")
    print(f"  spent       {settled:>10,}  (${settled / 1_000_000:.4f}) over {live_runs} live runs")
    print(f"  held now    {held:>10,}  (${held / 1_000_000:.4f})")
    print(f"  remaining   {remaining:>10,}  (${remaining / 1_000_000:.4f}) before this operation")
    print(f"  after this  {after:>10,}  (${after / 1_000_000:.4f}) if held in full")

    if whole > cap - total:
        print("\nThe reservation exceeds the milestone budget: the request would be refused (429).")
    elif whole > settings.ai_identity_daily_cost_cap_micro:
        print(
            "\nThe reservation exceeds the identity daily cap: the request would be refused (429)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
