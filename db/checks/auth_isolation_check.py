#!/usr/bin/env python3
"""Authenticated isolation checks with temporary operator-created users.

Creates two throwaway users through the Auth admin API (email pre-confirmed),
signs each in with the normal password grant to obtain access tokens, and then
asserts, through the running CashLens API and directly through the Data API:

- an access token is required for private routes and a bad one is rejected;
- user A can provision a private business once (idempotent), user B cannot
  see it through the API or the Data API, and anonymous callers cannot either;
- the Data API, called with a user's own access token (authenticated role),
  returns only that user's private rows plus demo rows;
- two demo visitors get separate workspaces and cannot see each other's.

These users substitute for nothing about the self-service signup flow; they
only exercise authorization. Everything this run created is deleted at the
end; other accounts in the project, such as retained manual test accounts,
are never listed, modified or deleted.

    API_BASE=http://127.0.0.1:8000 python3 db/checks/auth_isolation_check.py
"""

import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / "services" / "api" / ".env"
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000").rstrip("/")
CLIENT = {"x-cashlens-client": "web"}
results: list[tuple[str, bool, str]] = []


def setting(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value and ENV_FILE.exists():
        match = re.search(rf"^{key}=(.*)$", ENV_FILE.read_text(encoding="utf-8"), re.M)
        value = match.group(1).strip() if match else ""
    if not value:
        sys.exit(f"{key} is not set")
    return value


def http(url: str, method: str = "GET", body: object = None, headers: dict | None = None, opener=None) -> tuple[int, object]:
    hdrs = {"Content-Type": "application/json", "Accept": "application/json", **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with (opener or urllib.request.build_opener()).open(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw[:200]


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def main() -> int:
    base, anon, service = setting("SUPABASE_URL").rstrip("/"), setting("SUPABASE_ANON_KEY"), setting("SUPABASE_SERVICE_ROLE_KEY")
    admin = {"apikey": service, "Authorization": f"Bearer {service}"}
    tag = secrets.token_hex(4)
    users: dict[str, dict] = {}
    try:
        for label in ("a", "b"):
            email, password = f"isolation-{label}-{tag}@example.org", secrets.token_urlsafe(18)
            st, created = http(f"{base}/auth/v1/admin/users", "POST", {"email": email, "password": password, "email_confirm": True}, admin)
            if st not in (200, 201):
                sys.exit(f"could not create test user {label}: {st} {created}")
            st, session = http(f"{base}/auth/v1/token?grant_type=password", "POST", {"email": email, "password": password}, {"apikey": anon})
            if st != 200:
                sys.exit(f"could not sign in test user {label}: {st} {session}")
            users[label] = {"id": created["id"], "email": email, "token": session["access_token"]}
        a, b = users["a"], users["b"]
        auth_a = {"Authorization": f"Bearer {a['token']}", **CLIENT}
        auth_b = {"Authorization": f"Bearer {b['token']}", **CLIENT}

        # --- API: token validation ---
        st, _ = http(f"{API_BASE}/api/me"); check("api_me_requires_token", st == 401, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/me", headers={"Authorization": "Bearer not-a-real-token"}); check("api_rejects_bad_token", st == 401, f"HTTP {st}")
        st, me = http(f"{API_BASE}/api/me", headers=auth_a); check("api_me_returns_signed_in_user", st == 200 and me.get("user_id") == a["id"] and me.get("business") is None, f"HTTP {st}")

        # --- API: provisioning is authorized, idempotent, and never attaches demo data ---
        st, _ = http(f"{API_BASE}/api/private/provision", "POST", {"business_name": "Isolation Test Co"}, {"Authorization": f"Bearer {a['token']}"})
        check("provision_requires_client_header", st == 403, f"HTTP {st}")
        st, first = http(f"{API_BASE}/api/private/provision", "POST", {"business_name": "Isolation Test Co"}, auth_a)
        ok_first = st == 200 and isinstance(first, dict) and first.get("created") is True
        check("provision_creates_private_business", ok_first, f"HTTP {st} {'' if ok_first else str(first)[:120]}")
        if not ok_first:
            raise RuntimeError("provisioning failed; remaining checks skipped")
        st, second = http(f"{API_BASE}/api/private/provision", "POST", {"business_name": "Another Name"}, auth_a)
        check("provision_is_idempotent", st == 200 and isinstance(second, dict) and second.get("created") is False and second["business"]["id"] == first["business"]["id"], f"HTTP {st}")
        business_id = first["business"]["id"]
        st, ws = http(f"{API_BASE}/api/private/workspace", headers=auth_a)
        check("private_workspace_starts_empty", st == 200 and ws.get("transaction_count") == 0 and ws["statements"]["uploads_available"] is False, f"HTTP {st}")
        st, ws_b = http(f"{API_BASE}/api/private/workspace", headers=auth_b)
        check("user_b_has_no_access_to_user_a_workspace", st == 404, f"HTTP {st}")
        st, me_b = http(f"{API_BASE}/api/me", headers=auth_b)
        check("user_b_sees_no_business", st == 200 and me_b.get("business") is None, f"HTTP {st}")

        # --- Data API with each user's own access token (authenticated role) ---
        def data(token: str, path: str):
            return http(f"{base}/rest/v1/{path}", headers={"apikey": anon, "Authorization": f"Bearer {token}"})
        st, rows = data(a["token"], f"businesses?id=eq.{business_id}&select=id,data_mode"); check("data_api_user_a_sees_own_private_business", st == 200 and len(rows) == 1, f"HTTP {st}, {len(rows) if isinstance(rows, list) else rows}")
        st, rows = data(b["token"], f"businesses?id=eq.{business_id}&select=id"); check("data_api_user_b_cannot_see_it", st == 200 and rows == [], f"HTTP {st}, {rows}")
        st, rows = http(f"{base}/rest/v1/businesses?id=eq.{business_id}&select=id", headers={"apikey": anon}); check("data_api_anonymous_cannot_see_it", st == 200 and rows == [], f"HTTP {st}, {rows}")
        st, rows = data(a["token"], f"business_memberships?business_id=eq.{business_id}&select=user_id"); check("data_api_user_a_sees_own_membership", st == 200 and rows and rows[0]["user_id"] == a["id"], f"HTTP {st}")
        st, rows = data(b["token"], f"business_memberships?business_id=eq.{business_id}&select=user_id"); check("data_api_user_b_cannot_see_membership", st == 200 and rows == [], f"HTTP {st}")
        st, rows = data(a["token"], "businesses?data_mode=eq.demo&select=slug"); check("data_api_user_a_still_sees_demo_rows", st == 200 and len(rows) == 3, f"HTTP {st}")
        st, rows = data(a["token"], "workspaces?select=id"); check("data_api_user_cannot_read_workspaces", st in (401, 403), f"HTTP {st}")
        st, rows = http(f"{base}/rest/v1/rpc/provision_private_business", "POST", {"p_user_id": b["id"], "p_business_name": "x"}, {"apikey": anon, "Authorization": f"Bearer {b['token']}"})
        check("data_api_user_cannot_call_provision_rpc", st in (401, 403, 404), f"HTTP {st}")
        st, rows = http(f"{base}/rest/v1/transactions", "POST", {"business_id": business_id, "account_id": business_id, "source_type": "csv_import", "amount_minor": 1, "posted_date": "2026-08-01", "description": "x"}, {"apikey": anon, "Authorization": f"Bearer {a['token']}"})
        check("data_api_user_cannot_write_own_rows", st in (401, 403), f"HTTP {st}")

        # --- demo visitors: separate sessions, separate workspaces ---
        jar_a, jar_b = CookieJar(), CookieJar()
        opener_a = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar_a))
        opener_b = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar_b))
        st, _ = http(f"{API_BASE}/api/demo/session", "POST", None, CLIENT, opener_a); check("visitor_a_session_started", st == 200 and any(c.name == "cl_visitor" for c in jar_a), f"HTTP {st}")
        st, opened = http(f"{API_BASE}/api/demo/workspaces/juniper-kitchen", "POST", None, CLIENT, opener_a); check("visitor_a_opens_demo_workspace", st == 200 and opened.get("workspace_id"), f"HTTP {st}")
        st, summary = http(f"{API_BASE}/api/demo/workspaces/juniper-kitchen", headers=None, opener=opener_a); check("visitor_a_reads_demo_summary", st == 200 and summary.get("posted_transaction_count", 0) > 0, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/demo/session", "POST", None, CLIENT, opener_b)
        st, _ = http(f"{API_BASE}/api/demo/workspaces/juniper-kitchen", headers=None, opener=opener_b); check("visitor_b_cannot_see_visitor_a_workspace", st == 404, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/demo/workspaces/juniper-kitchen"); check("no_cookie_no_demo_workspace", st == 401, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/demo/workspaces/juniper-kitchen", "POST", None, None, opener_b); check("demo_open_requires_client_header", st == 403, f"HTTP {st}")

        # --- M5 workspace reads: every read is authorized independently ---
        wid_a = opened["workspace_id"]
        st, _ = http(f"{API_BASE}/api/workspaces/{wid_a}", opener=opener_a); check("visitor_a_reads_own_workspace", st == 200, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/workspaces/{wid_a}", opener=opener_b); check("visitor_b_cannot_read_visitor_a_workspace_by_id", st == 404, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/workspaces/{wid_a}"); check("anonymous_cannot_read_workspace", st == 401, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/workspaces/{wid_a}", headers=auth_a); check("user_a_cannot_read_visitor_workspace", st == 404, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/workspaces/{wid_a}/transactions?limit=5", opener=opener_b); check("visitor_b_cannot_page_visitor_a_transactions", st == 404, f"HTTP {st}")
        st, page_a = http(f"{API_BASE}/api/workspaces/{wid_a}/transactions?limit=5", opener=opener_a); check("visitor_a_pages_own_transactions", st == 200 and len(page_a.get("items", [])) == 5, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/workspaces/{wid_a}/transactions?limit=5&cursor={page_a['next_cursor']}", opener=opener_b); check("visitor_b_cannot_use_visitor_a_cursor", st == 404, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/workspaces/{wid_a}/transactions/{page_a['items'][0]['id']}", opener=opener_b); check("visitor_b_cannot_read_visitor_a_evidence", st == 404, f"HTTP {st}")
        st, ws_a = http(f"{API_BASE}/api/private/workspace", headers=auth_a)
        private_wid = ws_a["workspace_id"]
        st, _ = http(f"{API_BASE}/api/workspaces/{private_wid}", headers=auth_a); check("user_a_reads_own_private_workspace", st == 200, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/workspaces/{private_wid}", headers=auth_b); check("user_b_cannot_read_user_a_private_workspace", st == 404, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/workspaces/{private_wid}", opener=opener_a); check("visitor_cannot_read_private_workspace", st == 404, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/workspaces/{private_wid}"); check("anonymous_cannot_read_private_workspace", st == 401, f"HTTP {st}")
        st, priv_page = http(f"{API_BASE}/api/workspaces/{private_wid}/transactions", headers=auth_a); check("private_workspace_transactions_are_empty", st == 200 and priv_page.get("total") == 0, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/workspaces/{private_wid}/transactions/{page_a['items'][0]['id']}", headers=auth_a); check("demo_transaction_id_not_reachable_through_private_workspace", st == 404, f"HTTP {st}")
        demo_account = page_a["items"][0]["account"]["id"]
        st, _ = http(f"{API_BASE}/api/workspaces/{private_wid}/transactions?account_id={demo_account}", headers=auth_a); check("demo_account_filter_rejected_in_private_workspace", st == 404, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/workspaces/{private_wid}/transactions?cursor={page_a['next_cursor']}", headers=auth_a); check("visitor_cursor_rejected_in_private_workspace", st == 400, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/workspaces/00000000-0000-4000-8000-00000000dead"); check("guessed_workspace_id_without_identity_is_401", st == 401, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/workspaces/00000000-0000-4000-8000-00000000dead", opener=opener_a); check("guessed_workspace_id_is_404", st == 404, f"HTTP {st}")
    except RuntimeError as exc:
        check("aborted", False, str(exc))
    finally:
        # cleanup: private businesses (memberships and workspaces cascade), then users.
        # Only rows reachable from the users this run created are listed or deleted;
        # retained operator accounts and their private businesses are never touched.
        owned: set[str] = set()
        for u in users.values():
            st, rows = http(f"{base}/rest/v1/business_memberships?user_id=eq.{u['id']}&select=business_id", headers=admin)
            for row in rows or []:
                owned.add(row["business_id"])
                http(f"{base}/rest/v1/businesses?id=eq.{row['business_id']}&data_mode=eq.private", "DELETE", None, admin)
            http(f"{base}/auth/v1/admin/users/{u['id']}", "DELETE", None, admin)
        st, remaining = http(f"{base}/auth/v1/admin/users?page=1&per_page=50", headers=admin)
        leftovers = [x for x in (remaining or {}).get("users", []) if x.get("email", "").startswith("isolation-") and tag in x.get("email", "")]
        check("temporary_users_deleted", not leftovers, f"{len(leftovers)} left")
        if owned:
            st, priv = http(f"{base}/rest/v1/businesses?id=in.({','.join(sorted(owned))})&select=id", headers=admin)
        else:
            st, priv = 200, []
        check("temporary_private_businesses_deleted", st == 200 and priv == [], f"{len(priv) if isinstance(priv, list) else priv} of {len(owned)} tracked rows remain")

    failures = 0
    for name, ok, detail in results:
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<46} {detail}")
    print(f"{len(results) - failures}/{len(results)} authenticated isolation checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
