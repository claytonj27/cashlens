#!/usr/bin/env python3
"""A caller with two owned businesses, against the development project through the running API.

Creates two temporary users, gives the first an ordinary business and an
additional one through the authorized keyed path, then asserts that onboarding
still resumes the first business, that retries create only one additional
business, that both workspaces are reachable, that the other user is refused,
that an invalid selection never falls back to the first business, and that a
mismatched workspace in a link cannot move an import or its confirmation.
Everything created here is deleted afterwards; no other account is touched.

    API_BASE=http://127.0.0.1:8000 services/api/.venv/bin/python db/checks/workspace_selection_check.py
"""

import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "services" / "api" / ".env"
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_BYPASS = os.environ.get("API_BYPASS", "").strip()  # protected preview deployments only
CLIENT = {"x-cashlens-client": "web"}
results: list[tuple[str, bool, str]] = []

CSV_ONE = (
    b"date,description,amount,transaction_id\n"
    b"2026-05-01,LEASE PAYMENT,-3500.00,sel-1\n"
    b"2026-05-03,CARD SETTLEMENT,3307.49,sel-2\n"
)


def env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value and ENV_FILE.exists():
        match = re.search(rf"^{key}=(.*)$", ENV_FILE.read_text(encoding="utf-8"), re.M)
        value = match.group(1).strip() if match else ""
    if not value:
        sys.exit(f"{key} is not set")
    return value


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name:<62} {detail}")


def http(url, method="GET", body=None, headers=None, raw=None):
    h = {"User-Agent": "cashlens-check", **(headers or {})}
    if API_BYPASS and url.startswith(API_BASE):
        h["x-vercel-protection-bypass"] = API_BYPASS
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if raw is None and body is not None:
        h["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            text = response.read()
            ct = response.headers.get("content-type", "")
            return response.status, (json.loads(text) if text and "json" in ct else text)
    except urllib.error.HTTPError as exc:
        text = exc.read()
        try:
            return exc.code, json.loads(text)
        except Exception:
            return exc.code, text[:200]


def main() -> int:
    base = env("SUPABASE_URL")
    admin, anon = env("SUPABASE_SERVICE_ROLE_KEY"), env("SUPABASE_ANON_KEY")
    admin_h = {"apikey": admin, "Authorization": f"Bearer {admin}"}
    tag = secrets.token_hex(3)
    users: dict[str, dict] = {}
    businesses: list[str] = []
    try:
        for label in ("a", "b"):
            email, password = f"selcheck-{label}-{tag}@example.org", secrets.token_urlsafe(18)
            st, created = http(f"{base}/auth/v1/admin/users", "POST", {"email": email, "password": password, "email_confirm": True}, admin_h)
            if st not in (200, 201):
                raise RuntimeError(f"user {label}: {st}")
            st, session = http(f"{base}/auth/v1/token?grant_type=password", "POST", {"email": email, "password": password}, {"apikey": anon})
            users[label] = {"id": created["id"], "auth": {"authorization": f"Bearer {session['access_token']}", **CLIENT}}
        a, b = users["a"], users["b"]

        # ---- ordinary onboarding is unchanged ----
        st, first = http(f"{API_BASE}/api/private/provision", "POST", {"business_name": "First Co"}, a["auth"])
        check("onboarding creates the first business", st == 200 and first["created"] is True, f"HTTP {st}")
        businesses.append(first["business"]["id"])
        st, resumed = http(f"{API_BASE}/api/private/provision", "POST", {"business_name": "Ignored Name"}, a["auth"])
        check("onboarding resumes it and never renames or duplicates", st == 200 and resumed["created"] is False and resumed["business"] == first["business"], f"HTTP {st}")

        # ---- an additional business, idempotent under retry ----
        key = f"selcheck-{tag}"
        seen = []
        for _ in range(3):
            st, extra = http(f"{API_BASE}/api/private/businesses", "POST", {"name": "Cedar Corner PDF Test", "idempotency_key": key}, a["auth"])
            if st != 200:
                raise RuntimeError(f"additional business: {st} {extra}")
            seen.append((extra["business"]["id"], extra["created"]))
        businesses.append(seen[0][0])
        check("retrying additional creation produces exactly one business", len({s[0] for s in seen}) == 1 and [s[1] for s in seen] == [True, False, False], str(seen))
        st, rows = http(f"{base}/rest/v1/business_memberships?user_id=eq.{a['id']}&select=business_id", headers=admin_h)
        check("the caller now owns exactly two businesses", st == 200 and len(rows) == 2, f"{len(rows) if isinstance(rows, list) else rows}")
        second = {"business": {"id": seen[0][0]}, "workspace_id": extra["workspace_id"]}

        # ---- both workspaces are reachable, and the default is still the first ----
        st, me = http(f"{API_BASE}/api/me", headers=a["auth"])
        names = [x["business"]["name"] for x in me.get("businesses", [])] if st == 200 else []
        check("/api/me lists both and keeps the earliest as the default", st == 200 and names == ["First Co", "Cedar Corner PDF Test"] and me["business"] == first["business"], str(names))
        st, default_ws = http(f"{API_BASE}/api/private/workspace", headers=a["auth"])
        check("no selection still resolves the first business", st == 200 and default_ws["workspace_id"] == first["workspace_id"], f"HTTP {st}")
        st, selected = http(f"{API_BASE}/api/private/workspace?workspace={second['workspace_id']}", headers=a["auth"])
        check("the second workspace resolves to its own business", st == 200 and selected["business"]["name"] == "Cedar Corner PDF Test" and selected["transaction_count"] == 0, f"HTTP {st}")

        # ---- denial and no fallback ----
        st, _ = http(f"{API_BASE}/api/private/workspace?workspace={second['workspace_id']}", headers=b["auth"])
        check("another user cannot select it", st == 404, f"HTTP {st}")
        for bad, label in ((str(uuid.uuid4()), "unknown id"), ("not-a-uuid", "malformed id")):
            st, body = http(f"{API_BASE}/api/private/workspace?workspace={bad}", headers=a["auth"])
            fell_back = st == 200 and isinstance(body, dict) and body.get("workspace_id") == first["workspace_id"]
            check(f"an invalid selection ({label}) is refused, not replaced", st == 404 and not fell_back, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/private/workspace?workspace={second['workspace_id']}", headers=CLIENT)
        check("selection still requires a session", st == 401, f"HTTP {st}")

        # ---- an import in the second workspace keeps its own ownership ----
        imports_second = f"{API_BASE}/api/workspaces/{second['workspace_id']}/imports"
        st, created = http(imports_second, "POST", {"new_account": {"name": "Operating Checking", "account_type": "checking", "mask": "6812"}, "file_label": "sel.csv", "declared_bytes": len(CSV_ONE)}, a["auth"])
        if st != 201:
            raise RuntimeError(f"create import: {st} {created}")
        up, import_id = created["upload"], created["import"]["id"]
        http(f"{base}/storage/v1/object/upload/sign/{up['bucket']}/{up['path']}?token={up['token']}", "PUT", raw=CSV_ONE, headers={"apikey": anon, "Content-Type": "text/csv"})
        st, staged = http(f"{imports_second}/{import_id}/uploaded", "POST", {}, a["auth"])
        check("the import stages in the second workspace", st == 200 and staged["status"] == "staged", f"HTTP {st}")
        st, located = http(f"{API_BASE}/api/private/imports/{import_id}", headers=a["auth"])
        check("its workspace is resolved from the record, not from a link", st == 200 and located["workspace_id"] == second["workspace_id"] and located["workspace_id"] != first["workspace_id"], f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/private/imports/{import_id}", headers=b["auth"])
        check("another user cannot locate it", st == 404, f"HTTP {st}")

        # ---- a mismatched workspace cannot retarget the import or its confirmation ----
        wrong = f"{API_BASE}/api/workspaces/{first['workspace_id']}/imports/{import_id}"
        statuses = {}
        for name, url, method, body in (("detail", wrong, "GET", None), ("rows", f"{wrong}/rows", "GET", None), ("confirm", f"{wrong}/confirm", "POST", {"revision": staged["draft_revision"]}), ("cancel", f"{wrong}/cancel", "POST", {}), ("decisions", f"{wrong}/decisions", "POST", {"decision": "keep"})):
            st, _ = http(url, method, body, a["auth"])
            statuses[name] = st
        check("a mismatched workspace refuses every import action", set(statuses.values()) == {404}, str(statuses))
        st, record = http(f"{base}/rest/v1/document_imports?id=eq.{import_id}&select=business_id,status", headers=admin_h)
        check("the import still belongs to the second business and is untouched", st == 200 and record[0]["business_id"] == second["business"]["id"] and record[0]["status"] == "staged", str(record))
        st, confirmed = http(f"{imports_second}/{import_id}/confirm", "POST", {"revision": staged["draft_revision"]}, a["auth"])
        check("confirming through its own workspace works", st == 200 and confirmed["created"] == 2, f"HTTP {st}")
        st, first_view = http(f"{API_BASE}/api/workspaces/{first['workspace_id']}/transactions", headers=a["auth"])
        check("the first business received none of those transactions", st == 200 and first_view["total"] == 0, f"total={first_view.get('total') if st == 200 else st}")
        st, second_view = http(f"{API_BASE}/api/workspaces/{second['workspace_id']}/transactions", headers=a["auth"])
        check("the second business received all of them", st == 200 and second_view["total"] == 2, f"total={second_view.get('total') if st == 200 else st}")
    except RuntimeError as exc:
        check("aborted", False, str(exc))
    finally:
        for business_id in businesses:
            st, txs = http(f"{base}/rest/v1/transactions?business_id=eq.{business_id}&select=id", headers=admin_h)
            for t in txs or []:
                http(f"{base}/rest/v1/transaction_sources?transaction_id=eq.{t['id']}", "DELETE", None, admin_h)
            st, imps = http(f"{base}/rest/v1/document_imports?business_id=eq.{business_id}&select=id", headers=admin_h)
            for i in imps or []:
                http(f"{base}/rest/v1/import_rows?import_id=eq.{i['id']}", "DELETE", None, admin_h)
            http(f"{base}/rest/v1/transactions?business_id=eq.{business_id}", "DELETE", None, admin_h)
            st, objects = http(f"{base}/storage/v1/object/list/statements", "POST", {"prefix": business_id, "limit": 100}, admin_h)
            if st == 200 and objects:
                http(f"{base}/storage/v1/object/statements", "DELETE", {"prefixes": [f"{business_id}/{o['name']}" for o in objects]}, admin_h)
            http(f"{base}/rest/v1/businesses?id=eq.{business_id}&data_mode=eq.private", "DELETE", None, admin_h)
        for u in users.values():
            http(f"{base}/auth/v1/admin/users/{u['id']}", "DELETE", None, admin_h)
        left = []
        for business_id in businesses:
            st, rows = http(f"{base}/rest/v1/businesses?id=eq.{business_id}&select=id", headers=admin_h)
            left += rows or []
        check("temporary businesses deleted", not left, f"{len(left)} left")
        st, remaining = http(f"{base}/auth/v1/admin/users?page=1&per_page=100", headers=admin_h)
        stale = [x for x in (remaining or {}).get("users", []) if tag in (x.get("email") or "")]
        check("temporary users deleted", not stale, f"{len(stale)} left")
    failures = sum(1 for _, ok, _ in results if not ok)
    print(f"{len(results) - failures}/{len(results)} workspace selection checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
