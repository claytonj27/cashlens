#!/usr/bin/env python3
"""Reconciliation and pagination integrity through the running API.

For each demo business: start a visitor session, open the workspace, page
through every posted transaction with the smallest page size that keeps the
run short, and assert:

- no duplicate or missing rows across pages and the count equals the
  evaluation file's expected posted count (the evaluation file is read by
  this check only, never by an application route);
- per account, opening balance (from the provider fixture) plus the signed
  posted activity through the current snapshot's as-of date equals that
  snapshot's amount as reported by the accounts route, with pending and
  removed records excluded;
- the accounts route reports each balance with its kind, provenance and
  timestamp and never substitutes zero.

    API_BASE=http://127.0.0.1:8000 python3 db/checks/api_reconciliation_check.py
"""

import json
import os
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000").rstrip("/")
CLIENT = {"x-cashlens-client": "web"}
PAGE = int(os.environ.get("PAGE_SIZE", "37"))  # odd size so page boundaries fall on duplicate dates
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def main() -> int:
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(path: str, method: str = "GET", headers: dict | None = None):
        req = urllib.request.Request(f"{API_BASE}{path}", method=method, data=b"{}" if method == "POST" else None,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with opener.open(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read().decode() or "null")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode(errors="replace")[:200]

    st, _ = call("/api/demo/session", "POST", CLIENT)
    check("demo_session", st == 200, f"HTTP {st}")
    for provider_path in sorted((ROOT / "fixtures" / "provider").glob("*.json")):
        fixture = json.loads(provider_path.read_text())
        slug = fixture["business"]["slug"]
        evaluation = json.loads((ROOT / "fixtures" / "evaluation" / f"{slug}.json").read_text())
        st, opened = call(f"/api/demo/workspaces/{slug}", "POST", CLIENT)
        if st != 200:
            check(f"{slug}: open workspace", False, f"HTTP {st}"); continue
        wid = opened["workspace_id"]
        st, accounts = call(f"/api/workspaces/{wid}/accounts")
        check(f"{slug}: accounts route", st == 200 and len(accounts) == 2, f"HTTP {st}")
        rows: list[dict] = []
        cursor = None
        pages = 0
        while True:
            qs = f"?limit={PAGE}" + (f"&cursor={cursor}" if cursor else "")
            st, page = call(f"/api/workspaces/{wid}/transactions{qs}")
            if st != 200:
                check(f"{slug}: page {pages + 1}", False, f"HTTP {st} {page}"); break
            rows += page["items"]; pages += 1
            cursor = page["next_cursor"]
            if cursor is None:
                break
        ids = [r["id"] for r in rows]
        expected = evaluation["expected"]["posted_transaction_count"]
        check(f"{slug}: no duplicate or missing rows across {pages} pages", len(ids) == len(set(ids)) and len(ids) == page["total"], f"{len(ids)} rows, total {page['total']}")
        check(f"{slug}: posted count equals evaluation expectation", len(ids) == expected, f"{len(ids)} vs {expected}")
        ordered = all((a["posted_date"], a["id"]) > (b["posted_date"], b["id"]) for a, b in zip(rows, rows[1:], strict=False))
        check(f"{slug}: strict keyset order (date desc, id desc)", ordered)
        check(f"{slug}: no pending or removed rows in posted pages", all(r["status"] == "posted" for r in rows))
        by_external = {a["external_account_id"]: a for a in fixture["accounts"]}
        for account in accounts:
            spec = next((s for s in by_external.values() if s["mask"] == account["mask"]), None)
            current = next((b for b in account["balances"] if b["kind"] == "current" and b["provenance"] == "bank_observed"), None)
            if spec is None or current is None:
                check(f"{slug}: {account['name']} has a bank-observed current balance", False); continue
            as_of = current["as_of"][:10]
            activity = sum(r["amount_minor"] for r in rows if r["account"]["id"] == account["id"] and r["posted_date"] <= as_of)
            check(f"{slug}: {account['name']} opening + posted activity through {as_of} = current", spec["opening_balance_minor"] + activity == current["amount_minor"], f"{spec['opening_balance_minor']} + {activity} vs {current['amount_minor']}")
            check(f"{slug}: {account['name']} balance carries kind, provenance and timestamp", bool(current["kind"] and current["provenance"] and current["as_of"]))
        st, pending = call(f"/api/workspaces/{wid}/transactions?status=pending&limit=10")
        check(f"{slug}: one remaining pending item", st == 200 and pending["total"] == 1, f"total {pending.get('total') if isinstance(pending, dict) else pending}")
        st, detail = call(f"/api/workspaces/{wid}/transactions/{rows[0]['id']}")
        check(f"{slug}: evidence route returns allowlisted provenance", st == 200 and detail["sources"] and "payload" not in json.dumps(detail), f"HTTP {st}")

    failures = 0
    for name, ok, detail in results:
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<70} {detail}")
    print(f"{len(results) - failures}/{len(results)} reconciliation checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
