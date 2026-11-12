#!/usr/bin/env python3
"""A small development comparison: direct OpenAI against one model from another family.

This is NOT a benchmark and NOT a model ranking. It is four task contracts, run once each on
two routes, on synthetic data built for this check, so that the differences that matter to this
application — does the answer survive validation, does it cover what was asked, is it readable —
can be looked at side by side instead of guessed at. One run of one prompt version on one set of
inputs supports no general claim about either model.

Development cases only. Nothing here is held out, and every case exists to be inspected rather
than scored.

The workspace is a demo business created for this run and removed at the end, for two reasons:
the OpenRouter route is refused on anything but synthetic demo data, which is the point of the
restriction; and the three shipped demos have no unplaced transactions at all, so there would be
no classification case to compare. The three shipped demos are never touched.

    services/api/.venv/bin/python db/checks/model_comparison.py --setup
    ... --live      run the comparison (paid calls, recorded against the milestone budget)
    ... --teardown  remove the throwaway demo business

Spend records are never deleted: a settled reservation is the milestone's ledger.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "services" / "api" / ".env"
# The API to drive. Defaults to a local one; point it at a deployed preview with API_BASE
# and that deployment's protection bypass, exactly as the other checks in this directory do.
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_BYPASS = os.environ.get("API_BYPASS", "").strip()
CLIENT = {"x-cashlens-client": "web"}
STATE = ROOT / "local-data" / "model-comparison-state.json"

SLUG = "comparison-bench"
BUSINESS_ID = "b0000000-0000-4000-8000-00000000c001"
ACCOUNT_ID = "b0000000-0000-4000-8000-00000000c002"
CONNECTION_ID = "b0000000-0000-4000-8000-00000000c003"

# The two classification cases. Neither word appears in the category keyword lists, so the rules
# leave both alone and the model is answering from the description, amount, date and recurrence.
CLEAR = "GREENLEAF LANDSCAPING CREW LLC"
AMBIGUOUS = "DEPOSIT 5591"

# This run's own ceiling, on top of the milestone cap the server enforces. Micro-dollars.
# Overridable so a later, narrower run can be held to a tighter limit than the one the first
# comparison was given, without editing the file each time.
TASK_BUDGET_MICRO = int(os.environ.get("TASK_BUDGET_MICRO", "2000000"))


def env(key: str) -> str:
    text = ENV_FILE.read_text(encoding="utf-8")
    match = re.search(rf"^{key}=(.*)$", text, re.MULTILINE)
    if not match or not match.group(1).strip():
        sys.exit(f"{key} is not set in services/api/.env")
    return match.group(1).strip()


def psql(sql: str) -> str:
    result = subprocess.run(
        ["psql", env("SUPABASE_DB_URL"), "-X", "-v", "ON_ERROR_STOP=1", "-q", "-tA"],
        input=sql,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.exit(f"psql failed:\n{result.stderr.strip()[:1500]}")
    return result.stdout.strip()


def http(url: str, method: str = "GET", body: Any = None, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": "cashlens-comparison", **(headers or {})}
    # Only ever sent to the API this run was pointed at, never to another host.
    if API_BYPASS and url.startswith(API_BASE):
        h["x-vercel-protection-bypass"] = API_BYPASS
    if body is not None:
        h["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            text = response.read()
            cookie = response.headers.get("set-cookie", "")
            return response.status, (json.loads(text) if text else {}), cookie
    except urllib.error.HTTPError as exc:
        text = exc.read()
        try:
            return exc.code, json.loads(text), ""
        except ValueError:
            return exc.code, {"raw": text[:300].decode(errors="replace")}, ""


# ------------------------------------------------------------------ the ledger --
def ledger() -> dict[str, int]:
    row = psql("select settled_micro, held_micro from public.ai_milestone_spend();")
    settled, held = (int(v) for v in row.split("|"))
    return {"settled": settled, "held": held}


def money(micro: int | None) -> str:
    return "—" if micro is None else f"${micro / 1_000_000:.6f}"


# ------------------------------------------------------------------ the fixture --
def rows() -> str:
    """Three months of a small services business, with the two classification cases in it.

    Deliberately ordinary: weekly card settlements, monthly rent and payroll, a supplier that
    rises in the last month. Enough complete months for a brief and enough history to project
    from, which is what the scenario cases need.
    """
    values = []

    def row(day: str, amount: int, description: str) -> None:
        values.append(
            f"('{uuid.uuid4()}', '{BUSINESS_ID}', '{ACCOUNT_ID}', 'synthetic_provider', "
            f"{amount}, '{day}', '{description}', 'USD', 'posted', 1)"
        )

    # Deliberately tight. A comfortable business cannot demonstrate a delayed payment at all:
    # if every projected day is higher than the last, the lowest point is day zero whatever the
    # owner assumes, and the difference between the two troughs is always nothing. The month
    # here roughly breaks even and carries one large obligation in the middle, so the projection
    # actually dips and the timing of an expected payment can change how deep the dip goes.
    for month in (6, 7, 8):
        for day in (3, 10, 17, 24):
            row(f"2026-{month:02d}-{day:02d}", 120_000, "CARD PROCESSOR SETTLEMENT")
        row(f"2026-{month:02d}-05", -95_000, "CEDAR COMMONS LEASE PAYMENT")
        row(f"2026-{month:02d}-15", -430_000, "PAYROLL PROVIDER ACH")
        row(f"2026-{month:02d}-10", -42_500, CLEAR)
    # A supplier that climbs, so the brief has a change worth naming. The amounts differ each
    # month, so this is not recurring and stays inside the daily rate rather than being an
    # explicit obligation.
    for month, amount in ((6, 58_000), (7, 64_000), (8, 91_000)):
        row(f"2026-{month:02d}-08", -amount, "PINE VALLEY FOOD DISTRIBUTION")
    # One financing entry and one large one-off, both things the brief is expected to address.
    row("2026-07-22", 500_000, "SBA LOAN PROCEEDS")
    row("2026-08-19", -128_000, "HOBART SERVICE EQUIPMENT REPAIR")
    # The abstention case: strip the instrument word and the digits and nothing is left.
    row("2026-08-20", 320_000, AMBIGUOUS)
    return ",\n  ".join(values)


def setup() -> int:
    """Create the throwaway demo business. Demo immutability is lifted only for this write."""
    psql(f"""
begin;
set local cashlens.allow_demo_mutation = 'on';
insert into public.businesses (id, name, slug, industry, data_mode)
values ('{BUSINESS_ID}', 'Comparison Bench', '{SLUG}', 'services', 'demo')
on conflict (id) do nothing;
insert into public.connections (id, business_id, provider, external_item_id, status)
values ('{CONNECTION_ID}', '{BUSINESS_ID}', 'synthetic', 'item-comparison-bench-001', 'active')
on conflict (id) do nothing;
insert into public.accounts
  (id, business_id, connection_id, external_account_id, name, account_type, mask, source_type)
values ('{ACCOUNT_ID}', '{BUSINESS_ID}', '{CONNECTION_ID}', 'acct-comparison-chk',
        'Business checking', 'checking', '7781', 'synthetic_provider')
on conflict (id) do nothing;
delete from public.transactions where business_id = '{BUSINESS_ID}';
insert into public.transactions
  (id, business_id, account_id, source_type, amount_minor, posted_date, description,
   currency, status, revision)
values
  {rows()};
-- Dated on the last posted day, not after it: the analysis date comes from the newest
-- transaction, and a balance dated later than that is not usable as an anchor, which leaves
-- the projection unavailable and both scenario cases with nothing to explain.
delete from public.balance_snapshots where business_id = '{BUSINESS_ID}';
insert into public.balance_snapshots
  (business_id, account_id, kind, provenance, amount_minor, as_of, source_type)
values
  ('{BUSINESS_ID}', '{ACCOUNT_ID}', 'current', 'bank_observed', 300000,
   '2026-08-24T23:59:59+00:00', 'synthetic_provider');
commit;
""")
    count = psql(f"select count(*) from public.transactions where business_id='{BUSINESS_ID}';")
    print(f"created demo business '{SLUG}' with {count} transactions")
    print("the three shipped demos were not touched")
    return 0


def teardown() -> int:
    psql(f"""
begin;
set local cashlens.allow_demo_mutation = 'on';
delete from public.classification_suggestions where business_id = '{BUSINESS_ID}';
delete from public.category_overrides where business_id = '{BUSINESS_ID}';
delete from public.ai_runs where business_id = '{BUSINESS_ID}';
delete from public.transactions where business_id = '{BUSINESS_ID}';
delete from public.balance_snapshots where business_id = '{BUSINESS_ID}';
delete from public.accounts where business_id = '{BUSINESS_ID}';
delete from public.connections where business_id = '{BUSINESS_ID}';
delete from public.workspaces where business_id = '{BUSINESS_ID}';
delete from public.businesses where id = '{BUSINESS_ID}';
commit;
""")
    # Reservations are deliberately NOT deleted: a settled row is the milestone's spend ledger,
    # it holds no content, and removing it would understate what was actually spent.
    print("throwaway demo business removed; spend records kept")
    return 0


# ------------------------------------------------------------- running the cases --
def open_workspace() -> tuple[str, dict[str, str]]:
    status, _, cookie = http(f"{API_BASE}/api/demo/session", "POST", None, CLIENT)
    if status != 200:
        sys.exit(f"could not start a demo session: {status}")
    visitor = re.search(r"cl_visitor=([^;]+)", cookie)
    if not visitor:
        sys.exit("no visitor cookie was issued")
    headers = {**CLIENT, "Cookie": f"cl_visitor={visitor.group(1)}"}
    status, workspace, _ = http(
        f"{API_BASE}/api/demo/workspaces/{SLUG}", "POST", None, headers
    )
    if status != 200:
        sys.exit(f"could not open the demo workspace: {status} {workspace}")
    return workspace["workspace_id"], headers


def layers(run: dict[str, Any]) -> dict[str, Any]:
    v = (run or {}).get("validation") or {}

    def ok(name: str) -> str:
        layer = v.get(name)
        return "—" if layer is None else ("ok" if layer.get("ok") else "FAILED")

    return {
        "schema": ok("schema"),
        "evidence": ok("evidence"),
        "numbers": "ok" if not v.get("claims_withheld") else f"{v['claims_withheld']} withheld",
        "support": ok("support"),
        "withheld": v.get("claims_withheld", 0),
        "withheld_reasons": v.get("withheld_reasons", []),
        "coverage_gaps": v.get("coverage_gaps", []),
        "headline_replaced": v.get("headline_replaced", False),
    }


def usage(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "latency_ms": (run or {}).get("latency_ms"),
        "prompt_tokens": (run or {}).get("prompt_tokens"),
        "completion_tokens": (run or {}).get("completion_tokens"),
        "cost_micro": (run or {}).get("cost_micro"),
        "cost_source": (run or {}).get("cost_source"),
        "attempts": (run or {}).get("attempts"),
        "model_used": (run or {}).get("model_used"),
        "provider": (run or {}).get("provider"),
        "routing": (run or {}).get("routing"),
    }


def run_case(name: str, workspace: str, headers: dict, route: str) -> dict[str, Any]:
    """One task contract on one route. Returns everything the report needs, failures included."""
    q = f"?route={route}" if route != "direct" else ""
    base = f"{API_BASE}/api/workspaces/{workspace}/ai"

    if name == "brief":
        status, body, _ = http(f"{base}/brief{q}", "POST", None, headers)
    elif name == "classify":
        status, body, _ = http(f"{base}/suggestions{q}", "POST", None, headers)
    elif name == "scenario_receipts":
        status, body, _ = http(
            f"{base}/scenario/explain{q}",
            "POST",
            {"receipts_change_percent": -20.0},
            headers,
        )
    elif name == "scenario_late":
        status, body, _ = http(
            f"{base}/scenario/explain{q}",
            "POST",
            # Chosen because, on this fixture, it is the case the feature exists for: the
            # ending balance is IDENTICAL either way (the money still arrives inside the
            # window) while the low point drops $1,358.68 and moves to a different day. A
            # difference of zero on one fact and a large one on another, in the same answer.
            {
                "expected_payment_minor": 250_000,
                "expected_payment_date": "2026-09-02",
                "delay_days": 18,
            },
            headers,
        )
    else:
        raise ValueError(name)

    run = body.get("run") or {}
    result: dict[str, Any] = {
        "case": name,
        "route": route,
        "http": status,
        "status": body.get("status"),
        "message": body.get("message"),
        "reused": body.get("reused", False),
        **layers(run),
        **usage(run),
    }

    if name == "classify":
        result["created"] = body.get("created", 0)
        result["dropped"] = body.get("dropped", 0)
        listed_status, listed, _ = http(f"{base}/suggestions", "GET", None, headers)
        items = listed.get("items", []) if listed_status == 200 else []
        result["items"] = [
            {
                "description": i["description"],
                "outcome": i["outcome"],
                "category": i.get("category_label"),
                "rationale": i.get("rationale"),
                "question": i.get("question"),
                "alternatives": i.get("alternative_labels", []),
            }
            for i in items
            if i["run_id"] == run.get("id")
        ]
    else:
        answer = body.get("brief") or body.get("explanation") or {}
        result["headline"] = answer.get("headline")
        result["claims"] = [
            {"signal_id": c["signal_id"], "text": c["text"]} for c in answer.get("claims", [])
        ]
        result["questions"] = [q["text"] for q in answer.get("questions", [])]
        result["caveats"] = answer.get("caveats", [])
        result["claims_returned"] = len(answer.get("claims", [])) + int(
            answer.get("claims_withheld") or 0
        )
        result["claims_kept"] = len(answer.get("claims", []))
    return result


def reset_between_routes(workspace: str) -> None:
    """Classification suggestions are one-open-per-transaction, so the second route needs room.

    Explanations and briefs do not need this: their reuse key already includes the provider and
    model, so the second route generates its own rather than being handed the first's.
    """
    psql(f"""
delete from public.classification_suggestions where business_id = '{BUSINESS_ID}';
delete from public.category_overrides where business_id = '{BUSINESS_ID}';
""")


CASES = ("brief", "classify", "scenario_receipts", "scenario_late")


def live(routes: list[str]) -> int:
    start = ledger()
    print(f"milestone spend before: {money(start['settled'])}  held {money(start['held'])}")
    workspace, headers = open_workspace()
    print(f"workspace {workspace}\n")

    results = []
    for route in routes:
        reset_between_routes(workspace)
        for case in CASES:
            spent = ledger()["settled"] - start["settled"]
            if spent >= TASK_BUDGET_MICRO:
                print(f"\n!! task ceiling reached ({money(spent)}); stopping live calls")
                break
            outcome = run_case(case, workspace, headers, route)
            results.append(outcome)
            print(
                f"[{route:10s}] {case:18s} http {outcome['http']} "
                f"status {outcome['status']!s:14s} "
                f"kept {outcome.get('claims_kept', outcome.get('created', '-'))} "
                f"cost {money(outcome.get('cost_micro'))} "
                f"{outcome.get('latency_ms')} ms"
            )

    end = ledger()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(
        json.dumps(
            {
                "ledger_before": start,
                "ledger_after": end,
                "task_spend_micro": end["settled"] - start["settled"],
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nmilestone spend after: {money(end['settled'])}  held {money(end['held'])}")
    print(f"this run spent: {money(end['settled'] - start['settled'])}")
    print(f"full detail written to {STATE}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--teardown", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--routes", default="direct,openrouter")
    args = parser.parse_args()

    if args.setup:
        return setup()
    if args.teardown:
        return teardown()
    if args.live:
        return live([r.strip() for r in args.routes.split(",") if r.strip()])
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
