#!/usr/bin/env python3
"""Two development cases for classification, on a business that exists only for this check.

Fifteen live candidates in a row abstained. Before calling that a model failure, the question is
whether the context and the instructions actually allow a suggestion — so this builds the case
where one is clearly justified and the case where abstaining is clearly right, side by side, and
asks once.

  * a plainly described payee, paid the same amount on the same day three months running, whose
    nature a person reading the line would not hesitate over;
  * a bare deposit that names nobody, which must stay unresolved.

The answer is NOT in the prompt or in the descriptions: no category word appears in either, and
the rules cannot place either one. What the model gets is the description, the amount, the date,
the account, the recurrence, and the category list — the same context any candidate gets.

Nothing here touches an owner's data or a shared demo. It provisions its own private business
through the real route, seeds its own rows, and removes everything it made. Settled spend
records are never deleted: they are the milestone's ledger.

    API_BASE=http://127.0.0.1:8000 services/api/.venv/bin/python db/checks/classify_dev_cases.py
    ... --live      also asks the model once (one paid call, recorded against the budget)
"""

import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "services" / "api" / ".env"
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_BYPASS = os.environ.get("API_BYPASS", "").strip()
CLIENT = {"x-cashlens-client": "web"}

# The clear case. No word here is a categorisation keyword, so the rules cannot place it; a
# person reading "landscaping crew", paid monthly, would not hesitate.
CLEAR = "GREENLEAF LANDSCAPING CREW LLC"
# The abstention case. Strip the instrument word and the digits and nothing is left.
AMBIGUOUS = "DEPOSIT 5591"


def env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value and ENV_FILE.exists():
        match = re.search(rf"^{key}=(.*)$", ENV_FILE.read_text(encoding="utf-8"), re.M)
        value = match.group(1).strip() if match else ""
    if not value:
        sys.exit(f"{key} is not set")
    return value


def http(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": "cashlens-check", **(headers or {})}
    if API_BYPASS and url.startswith(API_BASE):
        h["x-vercel-protection-bypass"] = API_BYPASS
    if body is not None:
        h["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            text = response.read()
            ct = response.headers.get("content-type", "")
            return response.status, (json.loads(text) if text and "json" in ct else text)
    except urllib.error.HTTPError as exc:
        text = exc.read()
        try:
            return exc.code, json.loads(text)
        except Exception:
            return exc.code, text[:300]


def one(rows):
    return (rows[0] if rows else {}) if isinstance(rows, list) else (rows or {})


def rows_for(business, account):
    """A small but plausible ledger: an ordinary business with two rows the rules leave alone."""
    out = []

    def row(day, amount, description):
        out.append(
            {
                "business_id": business,
                "account_id": account,
                "source_type": "csv_import",
                "amount_minor": amount,
                "posted_date": day,
                "description": description,
            }
        )

    for month in (6, 7, 8):
        for day in (3, 10, 17, 24):
            row(f"2026-{month:02d}-{day:02d}", 150_000, "CARD PROCESSOR SETTLEMENT")
        row(f"2026-{month:02d}-05", -80_000, "CEDAR COMMONS LEASE PAYMENT")
        row(f"2026-{month:02d}-15", -120_000, "PAYROLL PROVIDER ACH")
        # The clear case: same payee, same amount, same day of the month, three months running.
        row(f"2026-{month:02d}-10", -42_500, CLEAR)
    row("2026-08-20", 320_000, AMBIGUOUS)
    return out


def main() -> int:
    live = "--live" in sys.argv
    base = env("SUPABASE_URL")
    admin, anon = env("SUPABASE_SERVICE_ROLE_KEY"), env("SUPABASE_ANON_KEY")
    admin_h = {"apikey": admin, "Authorization": f"Bearer {admin}"}
    represent = {**admin_h, "Prefer": "return=representation"}
    rest = f"{base}/rest/v1/"
    tag = secrets.token_hex(3)
    business = workspace = user_id = None

    try:
        email, password = f"devcase-{tag}@example.org", secrets.token_urlsafe(18)
        st, made = http(
            f"{base}/auth/v1/admin/users",
            "POST",
            {"email": email, "password": password, "email_confirm": True},
            admin_h,
        )
        if st not in (200, 201):
            sys.exit(f"could not create the temporary user: {st} {made}")
        user_id = made["id"]
        st, session = http(
            f"{base}/auth/v1/token?grant_type=password",
            "POST",
            {"email": email, "password": password},
            {"apikey": anon},
        )
        auth = {"authorization": f"Bearer {session['access_token']}", **CLIENT}
        st, provisioned = http(
            f"{API_BASE}/api/private/provision",
            "POST",
            {"business_name": f"Dev cases {tag}"},
            auth,
        )
        if st != 200:
            sys.exit(f"could not provision: {st} {provisioned}")
        business = provisioned["business"]["id"]
        workspace = provisioned["workspace_id"]

        st, account = http(
            f"{rest}accounts",
            "POST",
            {
                "business_id": business,
                "name": "Business checking",
                "account_type": "checking",
                "source_type": "csv_import",
            },
            represent,
        )
        account_id = one(account)["id"]
        http(f"{rest}transactions", "POST", rows_for(business, account_id), admin_h)
        http(
            f"{rest}balance_snapshots",
            "POST",
            {
                "business_id": business,
                "account_id": account_id,
                "kind": "statement_closing",
                "provenance": "bank_observed",
                "amount_minor": 1_450_000,
                "as_of": "2026-08-31T23:59:59+00:00",
                "source_type": "csv_import",
            },
            admin_h,
        )

        st, listed = http(f"{API_BASE}/api/workspaces/{workspace}/ai/suggestions", headers=auth)
        waiting = listed.get("candidates_remaining") if st == 200 else None
        print(f"workspace  {workspace}")
        print(f"candidates the rules left alone: {waiting}")
        print(f"  clear case:     {CLEAR}  -$425.00, monthly")
        print(f"  abstention case:{AMBIGUOUS}  $3,200.00, names nobody")

        if not live:
            print("\nsetup only. Re-run with --live to ask the model once.")
            return 0

        st, result = http(
            f"{API_BASE}/api/workspaces/{workspace}/ai/suggestions", "POST", None, auth
        )
        if st != 200:
            print(f"\nthe request was refused: {st} {result}")
            return 1
        run = result.get("run") or {}
        print(
            f"\nrun {str(run.get('id'))[:8]}  created {result['created']}  dropped "
            f"{result['dropped']}  cost_micro {run.get('cost_micro')}"
        )
        st, listed = http(f"{API_BASE}/api/workspaces/{workspace}/ai/suggestions", headers=auth)
        outcome = {}
        for item in listed["items"]:
            outcome[item["description"]] = item
            kind = "SUGGESTED" if item["outcome"] == "suggested" else "UNRESOLVED"
            head = item["category_label"] or item["question"]
            print(f"  [{kind}] {item['description'][:34]:34s} -> {head}")
            print(f"            {item['rationale']}")

        clear = outcome.get(CLEAR)
        vague = outcome.get(AMBIGUOUS)
        ok_clear = bool(clear and clear["outcome"] == "suggested")
        ok_vague = bool(vague and vague["outcome"] == "unresolved")
        print(f"\nclear case suggested:      {'PASS' if ok_clear else 'FAIL'}")
        print(f"ambiguous case unresolved: {'PASS' if ok_vague else 'FAIL'}")

        if ok_clear:
            decision = {"action": "accept"}
            st, applied = http(
                f"{API_BASE}/api/workspaces/{workspace}/ai/suggestions/{clear['id']}/decision",
                "POST",
                decision,
                auth,
            )
            st, analysis = http(
                f"{API_BASE}/api/workspaces/{workspace}/analysis", headers=auth
            )
            grouped = {r["key"]: r for r in analysis["categories"]}
            moved = grouped.get(applied.get("category") or "", {}).get("outflow_minor")
            print(
                f"accepted -> {applied.get('category')} | that group now {moved} "
                f"| overrides {analysis['overrides_applied']}"
            )
        return 0 if (ok_clear and ok_vague) else 1
    finally:
        if business:
            for table in (
                "classification_suggestions",
                "ai_runs",
                "category_overrides",
                "transactions",
                "balance_snapshots",
                "accounts",
            ):
                http(f"{rest}{table}?business_id=eq.{business}", "DELETE", None, admin_h)
            http(f"{rest}workspaces?business_id=eq.{business}", "DELETE", None, admin_h)
            http(f"{rest}businesses?id=eq.{business}", "DELETE", None, admin_h)
        if user_id:
            # Reservations are deliberately NOT deleted: a settled row is the milestone's
            # spend ledger, it holds no content, and removing it would understate what was
            # actually spent.
            http(f"{base}/auth/v1/admin/users/{user_id}", "DELETE", None, admin_h)
        print("\ntemporary business, rows and user removed; spend records kept.")


if __name__ == "__main__":
    raise SystemExit(main())
