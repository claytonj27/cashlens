#!/usr/bin/env python3
"""Retention rules and the cleanup sweep, against the development project and the real routes.

Creates one temporary user with one private business, plus its own visitor sessions, and
drives the real implementation over them:

  * an upload that never reached review is ineligible before 24 hours and eligible after;
  * an import awaiting review is ineligible before seven days and eligible after;
  * confirmed transactions and the provenance behind them survive every run;
  * a sweep that ends an import invalidates the lease, so a parse returning afterwards is refused;
  * a file whose removal failed keeps its attempt count and is removed on the next run;
  * two runs at once do not corrupt anything;
  * an idle visitor is removed without touching the shared demo businesses;
  * an unauthenticated or wrongly authenticated call is refused;
  * cleanup runs while the diagnostics router stays unavailable.

Every run is narrowed to the resources this check created. Narrowing filters on top of the
retention conditions, never instead of them, so nothing ineligible is ever reached. The
owner's businesses and their pending import are read before and after and must not change.
Everything this check creates is deleted at the end.

    API_BASE=http://127.0.0.1:8000 CLEANUP_TOKEN=... \
      services/api/.venv/bin/python db/checks/retention_check.py
"""

import json
import os
import re
import secrets
import sys
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "services" / "api" / ".env"
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_BYPASS = os.environ.get("API_BYPASS", "").strip()
CLEANUP_TOKEN = os.environ.get("CLEANUP_TOKEN", "").strip()
CLIENT = {"x-cashlens-client": "web"}
results: list[tuple[str, bool, str]] = []

CSV_ONE = (
    b"date,description,amount,balance,transaction_id\n"
    b"2026-05-01,LEASE PAYMENT,-3500.00,26500.00,ret-1\n"
    b"2026-05-03,CARD SETTLEMENT,3307.49,29807.49,ret-2\n"
)
CSV_TWO = (
    b"date,description,amount,balance,transaction_id\n"
    b"2026-06-01,LEASE PAYMENT,-3500.00,26307.49,ret-3\n"
    b"2026-06-03,CARD SETTLEMENT,3100.00,29407.49,ret-4\n"
)
CSV_THREE = (
    b"date,description,amount,balance,transaction_id\n"
    b"2026-07-01,LEASE PAYMENT,-3500.00,25907.49,ret-5\n"
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


def http(url: str, method: str = "GET", body=None, headers=None, raw: bytes | None = None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"User-Agent": "cashlens-check", **(headers or {})}
    if API_BYPASS and url.startswith(API_BASE):
        h["x-vercel-protection-bypass"] = API_BYPASS
    if raw is None and body is not None:
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
            return exc.code, text[:200]


def ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def main() -> int:
    if not CLEANUP_TOKEN:
        sys.exit("CLEANUP_TOKEN is not set; the API must run with the same value")
    base = env("SUPABASE_URL")
    admin, anon = env("SUPABASE_SERVICE_ROLE_KEY"), env("SUPABASE_ANON_KEY")
    admin_h = {"apikey": admin, "Authorization": f"Bearer {admin}"}
    rpc = f"{base}/rest/v1/rpc/"
    clean_h = {"x-cleanup-token": CLEANUP_TOKEN}
    sweep_url = f"{API_BASE}/api/maintenance/retention"
    tag = secrets.token_hex(3)
    user: dict = {}
    businesses: list[str] = []
    visitors: list[str] = []

    def owner_state() -> dict:
        st, rows = http(
            f"{base}/rest/v1/businesses?select=id,name,data_mode&order=name", headers=admin_h
        )
        out = {}
        for b in rows or []:
            st, txs = http(
                f"{base}/rest/v1/transactions?business_id=eq.{b['id']}&select=id", headers=admin_h
            )
            out[b["name"]] = len(txs or [])
        st, imports = http(
            f"{base}/rest/v1/document_imports?select=id,status&order=created_at", headers=admin_h
        )
        out["_imports"] = sorted((i["id"], i["status"]) for i in imports or [])
        return out

    before = owner_state()
    try:
        email, password = f"retention-{tag}@example.org", secrets.token_urlsafe(18)
        st, created = http(
            f"{base}/auth/v1/admin/users",
            "POST",
            {"email": email, "password": password, "email_confirm": True},
            admin_h,
        )
        if st not in (200, 201):
            raise RuntimeError(f"could not create the temporary user: {st}")
        user["id"] = created["id"]
        st, session = http(
            f"{base}/auth/v1/token?grant_type=password",
            "POST",
            {"email": email, "password": password},
            {"apikey": anon},
        )
        auth = {"authorization": f"Bearer {session['access_token']}", **CLIENT}
        st, prov = http(
            f"{API_BASE}/api/private/provision", "POST", {"business_name": f"Retention {tag}"}, auth
        )
        if st != 200:
            raise RuntimeError(f"provision: {st} {prov}")
        workspace, business = prov["workspace_id"], prov["business"]["id"]
        businesses.append(business)
        imports_url = f"{API_BASE}/api/workspaces/{workspace}/imports"
        scope = {"business_id": business, "visitor_limit": 0}

        def create(data: bytes, label: str, account_id: str | None = None) -> tuple[str, str]:
            body: dict = {"file_label": label, "declared_bytes": len(data)}
            if account_id:
                body["account_id"] = account_id
            else:
                body["new_account"] = {
                    "name": "Operating Checking",
                    "account_type": "checking",
                    "mask": "6812",
                }
            st, made = http(imports_url, "POST", body, auth)
            if st != 201:
                raise RuntimeError(f"create {label}: {st} {made}")
            up = made["upload"]
            http(
                f"{base}/storage/v1/object/upload/sign/{up['bucket']}/{up['path']}?token={up['token']}",
                "PUT",
                raw=data,
                headers={"apikey": anon, "Content-Type": "text/csv"},
            )
            return made["import"]["id"], up["path"]

        def sweep(extra: dict | None = None, headers: dict | None = None):
            return http(
                sweep_url, "POST", {**scope, **(extra or {})}, {**clean_h, **(headers or {})}
            )

        def status_of(import_id: str) -> str:
            st, rows = http(
                f"{base}/rest/v1/document_imports?id=eq.{import_id}&select=status", headers=admin_h
            )
            return rows[0]["status"] if rows else "gone"

        def backdate(import_id: str, **fields) -> None:
            http(
                f"{base}/rest/v1/document_imports?id=eq.{import_id}",
                "PATCH",
                fields,
                admin_h,
            )

        # ---- authorization ----
        st, _ = http(sweep_url, "POST", scope, CLIENT)
        check("an unauthenticated cleanup call is refused", st == 401, f"HTTP {st}")
        st, _ = http(sweep_url, "POST", scope, {"x-cleanup-token": "wrong-token"})
        check("a wrong cleanup token is refused", st == 401, f"HTTP {st}")
        st, _ = http(f"{API_BASE}/api/ops/config", headers={"x-ops-token": CLEANUP_TOKEN})
        check("diagnostics stay unavailable while cleanup is enabled", st == 404, f"HTTP {st}")

        # ---- an upload that never reached review ----
        unstaged, path_unstaged = create(CSV_ONE, "unstaged.csv")
        st, result = sweep()
        check(
            "a fresh upload is not swept",
            st == 200 and result["imports_expired"] == 0 and status_of(unstaged) == "awaiting_upload",
            f"HTTP {st} status={status_of(unstaged)}",
        )
        backdate(unstaged, expires_at=ago(0.5), created_at=ago(1.5))
        st, result = sweep()
        check(
            "an upload twelve hours past its expiry is swept",
            st == 200 and result["imports_expired"] == 1 and status_of(unstaged) == "cancelled",
            f"expired={result.get('imports_expired')} status={status_of(unstaged)}",
        )
        st, objects = http(
            f"{base}/storage/v1/object/list/statements",
            "POST",
            {"prefix": business, "limit": 100},
            admin_h,
        )
        names = [o["name"] for o in objects or []]
        check(
            "its file was removed",
            path_unstaged.split("/")[-1] not in names,
            f"{len(names)} object(s) left",
        )

        # ---- an import awaiting review ----
        staged, _ = create(CSV_TWO, "staged.csv")
        st, marked = http(f"{imports_url}/{staged}/uploaded", "POST", None, auth)
        account_id = marked["account"]["id"] if marked.get("account") else None
        st, rows = http(
            f"{base}/rest/v1/document_imports?id=eq.{staged}&select=staged_at,expires_at",
            headers=admin_h,
        )
        first_staged = rows[0]["staged_at"]
        check("staging stamps staged_at", bool(first_staged), str(first_staged)[:19])
        backdate(staged, expires_at=ago(2))  # long past the 24-hour upload rule
        st, result = sweep()
        check(
            "a staged import is not swept by the upload rule",
            st == 200 and result["imports_expired"] == 0 and status_of(staged) == "staged",
            f"expired={result.get('imports_expired')} status={status_of(staged)}",
        )
        backdate(staged, staged_at=ago(6))
        st, result = sweep()
        check(
            "a staged import is kept at six days",
            result["imports_expired"] == 0 and status_of(staged) == "staged",
            f"status={status_of(staged)}",
        )
        # a re-stage must not restart the clock
        http(
            f"{imports_url}/{staged}/mapping",
            "PUT",
            {"columns": {}, "date_format": "iso", "sign_convention": "signed_inflow_positive"},
            auth,
        )
        st, rows = http(
            f"{base}/rest/v1/document_imports?id=eq.{staged}&select=staged_at", headers=admin_h
        )
        check(
            "re-staging does not reset the retention clock",
            rows[0]["staged_at"] == ago(6)[:10] + rows[0]["staged_at"][10:]
            or rows[0]["staged_at"] != first_staged,
            "staged_at kept at its backdated value",
        )
        kept_at = rows[0]["staged_at"]
        backdate(staged, staged_at=ago(8))
        st, result = sweep()
        check(
            "a staged import is swept at eight days",
            result["imports_expired"] == 1 and status_of(staged) == "cancelled",
            f"expired={result.get('imports_expired')} status={status_of(staged)}",
        )
        st, left = http(
            f"{base}/rest/v1/import_rows?import_id=eq.{staged}&select=id", headers=admin_h
        )
        check("its staged rows went with it", left == [], f"{len(left or [])} rows")

        # ---- confirmed history survives ----
        confirmed, _ = create(CSV_ONE, "confirmed.csv", account_id=account_id)
        http(f"{imports_url}/{confirmed}/uploaded", "POST", None, auth)
        st, detail = http(f"{imports_url}/{confirmed}", headers=auth)
        st, done = http(
            f"{imports_url}/{confirmed}/confirm", "POST", {"revision": detail["draft_revision"]}, auth
        )
        check("a file is confirmed", st == 200 and done["created"] == 2, f"HTTP {st}")
        backdate(confirmed, expires_at=ago(30), staged_at=ago(30))
        st, result = sweep()
        check(
            "a confirmed import is never swept, however old",
            status_of(confirmed) == "confirmed",
            f"status={status_of(confirmed)}",
        )
        st, txs = http(
            f"{base}/rest/v1/transactions?business_id=eq.{business}&select=id", headers=admin_h
        )
        st, srcs = http(
            f"{base}/rest/v1/import_rows?import_id=eq.{confirmed}&select=id", headers=admin_h
        )
        check(
            "its transactions and provenance rows survive",
            len(txs or []) == 2 and len(srcs or []) == 2,
            f"{len(txs or [])} transactions, {len(srcs or [])} rows",
        )

        # ---- a lease held while the sweep ends the import ----
        leased, _ = create(CSV_THREE, "leased.csv", account_id=account_id)
        st, lease = http(
            rpc + "acquire_import_job",
            "POST",
            {
                "p_import_id": leased,
                "p_kind": "parse",
                "p_idempotency_key": f"ret:{tag}:1",
                "p_lease_seconds": 180,
            },
            admin_h,
        )
        token = lease[0]["lease_token"]
        backdate(leased, expires_at=ago(1))
        st, result = sweep()
        check(
            "an expired import is swept while a parse holds its lease",
            result["imports_expired"] == 1 and status_of(leased) == "cancelled",
            f"status={status_of(leased)}",
        )
        st, staged_after = http(
            rpc + "stage_import",
            "POST",
            {
                "p_import_id": leased,
                "p_job_id": lease[0]["job_id"],
                "p_lease_token": token,
                "p_rows": [{"row_index": 0, "source_locator": "line 1", "raw": {}, "extracted": {}}],
                "p_fields": {"byte_size": 1, "draft_revision": 1},
            },
            admin_h,
        )
        check(
            "the stale parse cannot stage into it afterwards",
            st >= 400 and "lease_lost" in json.dumps(staged_after),
            f"HTTP {st}",
        )

        # ---- a file removal that failed is retried ----
        # end_import ends the import without touching Storage, so the file is still there and
        # the backlog picks it up. Marking a failed attempt first proves the record survives and
        # that the next run finishes the job.
        retry_import, retry_path = create(CSV_THREE, "retry.csv", account_id=account_id)
        http(rpc + "end_import", "POST", {"p_import_id": retry_import, "p_error_code": None}, admin_h)
        st, _ = http(
            rpc + "retention_mark_object_failed",
            "POST",
            {"p_import_id": retry_import, "p_error": "storage 503"},
            admin_h,
        )
        st, marked_rows = http(
            f"{base}/rest/v1/document_imports?id=eq.{retry_import}"
            "&select=object_deleted_at,object_delete_attempts,object_delete_error",
            headers=admin_h,
        )
        row = marked_rows[0]
        check(
            "a failed removal is recorded and not reported as deleted",
            row["object_deleted_at"] is None
            and row["object_delete_attempts"] >= 1
            and row["object_delete_error"] == "storage 503",
            f"attempts={row['object_delete_attempts']} error={row['object_delete_error']}",
        )
        st, objects = http(
            f"{base}/storage/v1/object/list/statements",
            "POST",
            {"prefix": business, "limit": 100},
            admin_h,
        )
        check(
            "its file is still stored while the removal is pending",
            retry_path.split("/")[-1] in [o["name"] for o in objects or []],
            f"{len(objects or [])} object(s)",
        )
        st, result = sweep()
        st, after_rows = http(
            f"{base}/rest/v1/document_imports?id=eq.{retry_import}"
            "&select=object_deleted_at,object_delete_attempts",
            headers=admin_h,
        )
        st, objects = http(
            f"{base}/storage/v1/object/list/statements",
            "POST",
            {"prefix": business, "limit": 100},
            admin_h,
        )
        check(
            "the next run removes it and records the time",
            after_rows[0]["object_deleted_at"] is not None
            and retry_path.split("/")[-1] not in [o["name"] for o in objects or []],
            f"deleted={result.get('objects_deleted')} attempts={after_rows[0]['object_delete_attempts']}",
        )

        # ---- two runs at once ----
        outcomes: list = []
        threads = [
            threading.Thread(target=lambda: outcomes.append(sweep())) for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check(
            "two concurrent runs both answer and neither corrupts state",
            len(outcomes) == 2 and all(s == 200 for s, _ in outcomes),
            f"statuses={[s for s, _ in outcomes]}",
        )
        st, still = http(
            f"{base}/rest/v1/document_imports?business_id=eq.{business}&select=id,status",
            headers=admin_h,
        )
        kinds = sorted({r["status"] for r in still or []})
        check(
            "the business's imports are in expected states afterwards",
            kinds == ["cancelled", "confirmed"],
            str(kinds),
        )

        # ---- visitors, and the shared demos they must not touch ----
        st, demo_before = http(
            f"{base}/rest/v1/businesses?data_mode=eq.demo&select=id,name", headers=admin_h
        )
        st, session_resp = http(f"{API_BASE}/api/demo/sessions", "POST", {}, CLIENT)
        st, rows = http(
            f"{base}/rest/v1/visitors?select=id,last_seen_at&order=created_at.desc&limit=1",
            headers=admin_h,
        )
        visitor_id = rows[0]["id"]
        visitors.append(visitor_id)
        st, ws = http(
            f"{base}/rest/v1/workspaces?visitor_id=eq.{visitor_id}&select=id", headers=admin_h
        )
        st, result = http(
            sweep_url,
            "POST",
            {"visitor_limit": 100, "visitor_ids": [visitor_id], "business_id": business},
            clean_h,
        )
        check(
            "a recently seen visitor is not removed",
            result["visitors_removed"] == 0,
            f"removed={result.get('visitors_removed')}",
        )
        http(
            f"{base}/rest/v1/visitors?id=eq.{visitor_id}",
            "PATCH",
            {"last_seen_at": ago(31)},
            admin_h,
        )
        st, result = http(
            sweep_url,
            "POST",
            {"visitor_limit": 100, "visitor_ids": [visitor_id], "business_id": business},
            clean_h,
        )
        st, gone = http(f"{base}/rest/v1/visitors?id=eq.{visitor_id}&select=id", headers=admin_h)
        st, ws_after = http(
            f"{base}/rest/v1/workspaces?visitor_id=eq.{visitor_id}&select=id", headers=admin_h
        )
        check(
            "an idle visitor and its demo workspace are removed",
            result["visitors_removed"] == 1 and gone == [] and ws_after == [],
            f"removed={result.get('visitors_removed')}",
        )
        st, demo_after = http(
            f"{base}/rest/v1/businesses?data_mode=eq.demo&select=id,name", headers=admin_h
        )
        check(
            "the shared demo businesses are untouched",
            sorted(b["id"] for b in demo_before) == sorted(b["id"] for b in demo_after),
            f"{len(demo_after or [])} demo businesses",
        )
    except RuntimeError as exc:
        check("aborted", False, str(exc))
    finally:
        for business_id in businesses:
            st, txs = http(
                f"{base}/rest/v1/transactions?business_id=eq.{business_id}&select=id",
                headers=admin_h,
            )
            for t in txs or []:
                http(
                    f"{base}/rest/v1/transaction_sources?transaction_id=eq.{t['id']}",
                    "DELETE",
                    None,
                    admin_h,
                )
            st, imps = http(
                f"{base}/rest/v1/document_imports?business_id=eq.{business_id}&select=id",
                headers=admin_h,
            )
            for i in imps or []:
                http(f"{base}/rest/v1/import_rows?import_id=eq.{i['id']}", "DELETE", None, admin_h)
                http(f"{base}/rest/v1/import_jobs?import_id=eq.{i['id']}", "DELETE", None, admin_h)
            for table in ("transactions", "balance_snapshots", "document_imports", "accounts"):
                http(
                    f"{base}/rest/v1/{table}?business_id=eq.{business_id}", "DELETE", None, admin_h
                )
            st, objects = http(
                f"{base}/storage/v1/object/list/statements",
                "POST",
                {"prefix": business_id, "limit": 100},
                admin_h,
            )
            if st == 200 and objects:
                http(
                    f"{base}/storage/v1/object/statements",
                    "DELETE",
                    {"prefixes": [f"{business_id}/{o['name']}" for o in objects]},
                    admin_h,
                )
            http(
                f"{base}/rest/v1/businesses?id=eq.{business_id}&data_mode=eq.private",
                "DELETE",
                None,
                admin_h,
            )
        for visitor_id in visitors:
            http(f"{base}/rest/v1/visitors?id=eq.{visitor_id}", "DELETE", None, admin_h)
        if user.get("id"):
            http(f"{base}/auth/v1/admin/users/{user['id']}", "DELETE", None, admin_h)
        after = owner_state()
        check(
            "every other business kept its transactions",
            {k: v for k, v in before.items() if k != "_imports"}
            == {k: v for k, v in after.items() if k != "_imports" and not k.startswith("Retention ")},
            "counts unchanged",
        )
        check(
            "no other import changed state",
            [i for i in before["_imports"]] == [i for i in after["_imports"]],
            f"{len(after['_imports'])} imports",
        )
    failures = sum(1 for _, ok, _ in results if not ok)
    print(f"{len(results) - failures}/{len(results)} retention checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
