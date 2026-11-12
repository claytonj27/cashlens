#!/usr/bin/env python3
"""Supabase Data API (PostgREST) checks with the publishable key, i.e. an untrusted caller.

Reads SUPABASE_URL and SUPABASE_ANON_KEY from the environment or
services/api/.env. Never prints either. Asserts that an anonymous caller can
read demo rows only, cannot read any service-only table (visitors, workspaces,
connections, sync batches, raw events, transaction sources, memberships), and
cannot write anything.

    python3 db/checks/data_api_check.py
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / "services" / "api" / ".env"


def setting(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value and ENV_FILE.exists():
        match = re.search(rf"^{key}=(.*)$", ENV_FILE.read_text(encoding="utf-8"), re.M)
        value = match.group(1).strip() if match else ""
    if not value:
        sys.exit(f"{key} is not set")
    return value


def call(base: str, key: str, path: str, method: str = "GET", body: dict | None = None) -> tuple[int, object]:
    headers = {"apikey": key, "Accept": "application/json", "Content-Type": "application/json", "Prefer": "return=representation"}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base}/rest/v1/{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as exc:
        return exc.code, None


def main() -> int:
    base, key = setting("SUPABASE_URL").rstrip("/"), setting("SUPABASE_ANON_KEY")
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok, detail))

    status, rows = call(base, key, "businesses?select=slug,data_mode")
    check("anon_reads_only_demo_businesses", status == 200 and rows is not None and all(r["data_mode"] == "demo" for r in rows) and len(rows) >= 1, f"HTTP {status}, {len(rows or [])} rows")
    status, rows = call(base, key, "transactions?select=id&limit=1")
    check("anon_reads_demo_transactions", status == 200 and bool(rows), f"HTTP {status}")
    for table in ("workspaces", "visitors", "connections", "sync_batches", "raw_events", "transaction_sources", "business_memberships", "schema_migrations", "ops_probe"):
        status, _ = call(base, key, f"{table}?select=*&limit=1")
        check(f"anon_cannot_read_{table}", status in (401, 403, 404), f"HTTP {status}")
    status, _ = call(base, key, "transactions", "POST", {"business_id": "00000000-0000-0000-0000-000000000000", "account_id": "00000000-0000-0000-0000-000000000000", "source_type": "csv_import", "amount_minor": 1, "posted_date": "2026-08-01", "description": "x"})
    check("anon_cannot_insert_transactions", status in (401, 403), f"HTTP {status}")
    status, _ = call(base, key, "businesses?slug=eq.juniper-kitchen", "PATCH", {"name": "x"})
    check("anon_cannot_update_businesses", status in (401, 403), f"HTTP {status}")
    status, _ = call(base, key, "workspaces", "POST", {"business_id": "00000000-0000-0000-0000-000000000000", "business_data_mode": "demo", "visitor_id": "00000000-0000-0000-0000-000000000000"})
    check("anon_cannot_insert_workspaces", status in (401, 403), f"HTTP {status}")
    status, _ = call(base, key, "transactions?business_id=eq.00000000-0000-0000-0000-000000000000", "DELETE")
    check("anon_cannot_delete_transactions", status in (401, 403), f"HTTP {status}")

    failures = 0
    for name, ok, detail in results:
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<44} {detail}")
    print(f"{len(results) - failures}/{len(results)} Data API checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
