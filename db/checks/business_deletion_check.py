#!/usr/bin/env python3
"""Owner-requested business deletion, against the development project and the real routes.

Creates two temporary users. User A gets two businesses so the survivor can be checked; user B
exists only to be refused. The check drives the real deletion:

  * the account that created a business may delete it, and a plain member may not;
  * another user, a visitor and an anonymous caller are refused;
  * a shared demo business cannot be deleted by any path;
  * the owner's other business survives untouched;
  * confirmed transactions, their provenance and the accounts behind them go;
  * every scoped read and write for that business is refused from the moment it is requested;
  * a file whose removal fails stays queued and is finished by a later cleanup run, even though
    the import row that named it has already been cascaded away;
  * asking twice is idempotent, and a staging attempt afterwards cannot recreate anything;
  * the business row itself goes only once its files are actually gone.

Everything it creates is deleted at the end. The owner's businesses and their pending import
are read before and after and must not change.

    API_BASE=http://127.0.0.1:8000 CLEANUP_TOKEN=... \
      services/api/.venv/bin/python db/checks/business_deletion_check.py
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
CLEANUP_TOKEN = os.environ.get("CLEANUP_TOKEN", "").strip()
CLIENT = {"x-cashlens-client": "web"}
results: list[tuple[str, bool, str]] = []

CSV = (
    b"date,description,amount,balance,transaction_id\n"
    b"2026-05-01,LEASE PAYMENT,-3500.00,26500.00,del-1\n"
    b"2026-05-03,CARD SETTLEMENT,3307.49,29807.49,del-2\n"
)
CSV_PENDING = (
    b"date,description,amount,balance,transaction_id\n"
    b"2026-06-01,INSURANCE ACH,-450.00,29357.49,del-3\n"
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
    print(f"{'PASS' if ok else 'FAIL'}  {name:<64} {detail}")


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
    users: dict[str, dict] = {}
    businesses: list[str] = []

    def owner_state() -> dict:
        st, rows = http(f"{base}/rest/v1/businesses?select=id,name&order=name", headers=admin_h)
        out = {}
        for b in rows or []:
            st, txs = http(
                f"{base}/rest/v1/transactions?business_id=eq.{b['id']}&select=id", headers=admin_h
            )
            out[b["name"]] = len(txs or [])
        st, imports = http(f"{base}/rest/v1/document_imports?select=id,status", headers=admin_h)
        out["_imports"] = sorted((i["id"], i["status"]) for i in imports or [])
        return out

    before = owner_state()
    try:
        for label in ("a", "b"):
            email, password = f"deletion-{label}-{tag}@example.org", secrets.token_urlsafe(18)
            st, created = http(
                f"{base}/auth/v1/admin/users",
                "POST",
                {"email": email, "password": password, "email_confirm": True},
                admin_h,
            )
            if st not in (200, 201):
                raise RuntimeError(f"could not create user {label}: {st}")
            st, session = http(
                f"{base}/auth/v1/token?grant_type=password",
                "POST",
                {"email": email, "password": password},
                {"apikey": anon},
            )
            users[label] = {
                "id": created["id"],
                "auth": {"authorization": f"Bearer {session['access_token']}", **CLIENT},
            }
        a, b = users["a"], users["b"]

        st, first = http(
            f"{API_BASE}/api/private/provision", "POST", {"business_name": f"Doomed {tag}"}, a["auth"]
        )
        doomed, doomed_ws = first["business"]["id"], first["workspace_id"]
        businesses.append(doomed)
        st, second = http(
            f"{API_BASE}/api/private/businesses",
            "POST",
            {"name": f"Survivor {tag}", "idempotency_key": f"survivor-{tag}"},
            a["auth"],
        )
        survivor, survivor_ws = second["business"]["id"], second["workspace_id"]
        businesses.append(survivor)
        st, other = http(
            f"{API_BASE}/api/private/provision", "POST", {"business_name": f"Bystander {tag}"}, b["auth"]
        )
        businesses.append(other["business"]["id"])
        check(
            "two businesses for one owner and one for another user",
            doomed != survivor and other["business"]["id"] not in (doomed, survivor),
            "",
        )

        imports_url = f"{API_BASE}/api/workspaces/{doomed_ws}/imports"

        def upload(data: bytes, label: str, account_id: str | None = None) -> tuple[str, str]:
            body: dict = {"file_label": label, "declared_bytes": len(data)}
            if account_id:
                body["account_id"] = account_id
            else:
                body["new_account"] = {
                    "name": "Operating Checking",
                    "account_type": "checking",
                    "mask": "6812",
                }
            st, made = http(imports_url, "POST", body, a["auth"])
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

        confirmed_id, _confirmed_path = upload(CSV, "confirmed.csv")
        http(f"{imports_url}/{confirmed_id}/uploaded", "POST", None, a["auth"])
        st, detail = http(f"{imports_url}/{confirmed_id}", headers=a["auth"])
        st, done = http(
            f"{imports_url}/{confirmed_id}/confirm", "POST", {"revision": detail["draft_revision"]}, a["auth"]
        )
        check("the doomed business has confirmed transactions", done.get("created") == 2, f"HTTP {st}")
        account_id = done["import"]["account"]["id"]
        pending_id, pending_path = upload(CSV_PENDING, "pending.csv", account_id=account_id)
        http(f"{imports_url}/{pending_id}/uploaded", "POST", None, a["auth"])

        delete_url = f"{API_BASE}/api/private/businesses/{doomed}/deletion"

        # ---- authorization ----
        st, _ = http(delete_url, "POST", {"confirm_name": f"Doomed {tag}"}, CLIENT)
        check("an anonymous deletion request is refused", st == 401, f"HTTP {st}")
        st, _ = http(delete_url, "POST", {"confirm_name": f"Doomed {tag}"}, b["auth"])
        check("another user cannot delete it", st == 404, f"HTTP {st}")
        st, _ = http(delete_url, "POST", {"confirm_name": "wrong name"}, a["auth"])
        check("a mistyped confirmation is refused", st == 422, f"HTTP {st}")
        st, demos = http(
            f"{base}/rest/v1/businesses?data_mode=eq.demo&select=id,name&limit=1", headers=admin_h
        )
        demo_id = demos[0]["id"]
        st, _ = http(
            f"{API_BASE}/api/private/businesses/{demo_id}/deletion",
            "POST",
            {"confirm_name": demos[0]["name"]},
            a["auth"],
        )
        check("a shared demo business cannot be deleted", st == 404, f"HTTP {st}")
        # a member who did not create the business is not an owner
        http(
            f"{base}/rest/v1/business_memberships",
            "POST",
            {
                "business_id": doomed,
                "business_data_mode": "private",
                "user_id": b["id"],
                "role": "owner",
            },
            {**admin_h, "Prefer": "return=minimal"},
        )
        st, refused = http(delete_url, "POST", {"confirm_name": f"Doomed {tag}"}, b["auth"])
        check(
            "a member who did not create it is refused",
            st == 403 and "created" in json.dumps(refused),
            f"HTTP {st}",
        )
        st, shared = http(delete_url, "POST", {"confirm_name": f"Doomed {tag}"}, a["auth"])
        check("a business with another member is refused", st == 409, f"HTTP {st}")
        http(
            f"{base}/rest/v1/business_memberships?business_id=eq.{doomed}&user_id=eq.{b['id']}",
            "DELETE",
            None,
            admin_h,
        )

        # ---- the owner deletes it ----
        st, result = http(delete_url, "POST", {"confirm_name": f"Doomed {tag}"}, a["auth"])
        check(
            "the account that created it may delete it",
            st == 200 and result["status"] == "removing" and result["transactions_removed"] == 2,
            f"HTTP {st} removed={result.get('transactions_removed')}",
        )
        # The confirmed import's file was already removed at confirmation, so only the
        # statement still in review is left to queue. Queueing happens before any cascade.
        check(
            "the file still stored was queued before anything cascaded",
            result["objects_pending"] == 1,
            f"pending={result.get('objects_pending')}",
        )
        st, rows = http(
            f"{base}/rest/v1/transactions?business_id=eq.{doomed}&select=id", headers=admin_h
        )
        st2, accounts = http(
            f"{base}/rest/v1/accounts?business_id=eq.{doomed}&select=id", headers=admin_h
        )
        st3, imports = http(
            f"{base}/rest/v1/document_imports?business_id=eq.{doomed}&select=id", headers=admin_h
        )
        check(
            "transactions, provenance, accounts and imports are gone",
            rows == [] and accounts == [] and imports == [],
            f"{len(rows or [])} tx, {len(accounts or [])} accounts, {len(imports or [])} imports",
        )

        # ---- access is refused from that moment ----
        st, _ = http(f"{API_BASE}/api/private/workspace?workspace={doomed_ws}", headers=a["auth"])
        check("its workspace is refused", st == 404, f"HTTP {st}")
        st, _ = http(
            imports_url,
            "POST",
            {"account_id": account_id, "file_label": "after.csv", "declared_bytes": 10},
            a["auth"],
        )
        check("a new upload into it is refused", st == 404, f"HTTP {st}")
        st, me_after = http(f"{API_BASE}/api/me", headers=a["auth"])
        names = [x["business"]["name"] for x in me_after["businesses"]]
        check(
            "it disappears from the account's businesses and the survivor remains",
            names == [f"Survivor {tag}"],
            str(names),
        )
        st, resumed = http(
            f"{API_BASE}/api/private/provision", "POST", {"business_name": f"Doomed {tag}"}, a["auth"]
        )
        check(
            "provisioning does not revive it",
            resumed["business"]["id"] != doomed,
            "a new business was created instead",
        )
        businesses.append(resumed["business"]["id"])

        # ---- a stale worker cannot recreate anything ----
        st, lease = http(
            rpc + "acquire_import_job",
            "POST",
            {
                "p_import_id": pending_id,
                "p_kind": "parse",
                "p_idempotency_key": f"del:{tag}:1",
                "p_lease_seconds": 180,
            },
            admin_h,
        )
        check(
            "a lease cannot even be taken on a deleted import",
            st >= 400 or not (lease or []),
            f"HTTP {st}",
        )
        st, staged = http(
            rpc + "stage_import",
            "POST",
            {
                "p_import_id": pending_id,
                "p_job_id": "00000000-0000-4000-8000-000000000001",
                "p_lease_token": "00000000-0000-4000-8000-000000000002",
                "p_rows": [{"row_index": 0, "source_locator": "line 1", "raw": {}, "extracted": {}}],
                "p_fields": {"byte_size": 1, "draft_revision": 1},
            },
            admin_h,
        )
        st2, recreated = http(
            f"{base}/rest/v1/import_rows?import_id=eq.{pending_id}&select=id", headers=admin_h
        )
        check(
            "a stale staging attempt recreates nothing",
            st >= 400 and recreated == [],
            f"HTTP {st}",
        )

        # ---- idempotency ----
        st, again = http(delete_url, "POST", {"confirm_name": f"Doomed {tag}"}, a["auth"])
        check(
            "asking twice is idempotent",
            st == 200 and again["objects_pending"] == 1 and again["status"] == "removing",
            f"HTTP {st} pending={again.get('objects_pending')}",
        )

        # ---- a failed file removal survives the cascade and is retried ----
        st, queued = http(
            f"{base}/rest/v1/storage_cleanup?business_id=eq.{doomed}&select=id,object_path,attempts",
            headers=admin_h,
        )
        check(
            "the inventory outlives the import rows that named it",
            len(queued or []) == 1 and queued[0]["object_path"] == pending_path,
            f"{len(queued or [])} queued, import rows already gone",
        )
        http(
            rpc + "storage_cleanup_mark_failed",
            "POST",
            {"p_id": queued[0]["id"], "p_error": "storage 503"},
            admin_h,
        )
        st, status_mid = http(delete_url, headers=a["auth"])
        check(
            "the status reports a retry rather than success",
            st == 200 and status_mid["status"] == "removing" and status_mid["objects_retrying"] == 1,
            f"retrying={status_mid.get('objects_retrying')}",
        )
        st, run = http(sweep_url, "POST", {"business_id": doomed, "visitor_limit": 0}, clean_h)
        check(
            "a cleanup run removes the queued file and finishes the business",
            st == 200 and run["objects_deleted"] == 1 and run["businesses_finalized"] == 1,
            f"deleted={run.get('objects_deleted')} finalized={run.get('businesses_finalized')}",
        )
        st, objects = http(
            f"{base}/storage/v1/object/list/statements",
            "POST",
            {"prefix": doomed, "limit": 100},
            admin_h,
        )
        check("its Storage prefix is empty", not objects, f"{len(objects or [])} object(s)")
        st, gone = http(f"{base}/rest/v1/businesses?id=eq.{doomed}&select=id", headers=admin_h)
        check("the business row itself is finally gone", gone == [], f"{len(gone or [])} rows")
        st, status_after = http(delete_url, headers=a["auth"])
        check("its status now reads as finished", st == 404, f"HTTP {st}")

        # ---- the survivor is untouched ----
        st, survivor_ws_read = http(
            f"{API_BASE}/api/private/workspace?workspace={survivor_ws}", headers=a["auth"]
        )
        check(
            "the owner's other business still opens",
            st == 200 and survivor_ws_read["business"]["name"] == f"Survivor {tag}",
            f"HTTP {st}",
        )
        st, bystander = http(f"{API_BASE}/api/me", headers=b["auth"])
        check(
            "the other user's business is untouched",
            bystander["business"]["name"] == f"Bystander {tag}",
            bystander["business"]["name"],
        )
        st, demo_after = http(
            f"{base}/rest/v1/businesses?data_mode=eq.demo&select=id", headers=admin_h
        )
        check("the shared demos are all still there", len(demo_after or []) == 3, f"{len(demo_after or [])}")
    except RuntimeError as exc:
        check("aborted", False, str(exc))
    finally:
        for business_id in businesses:
            st, txs = http(
                f"{base}/rest/v1/transactions?business_id=eq.{business_id}&select=id", headers=admin_h
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
                http(f"{base}/rest/v1/{table}?business_id=eq.{business_id}", "DELETE", None, admin_h)
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
            http(f"{base}/rest/v1/storage_cleanup?business_id=eq.{business_id}", "DELETE", None, admin_h)
            http(
                f"{base}/rest/v1/businesses?id=eq.{business_id}&data_mode=eq.private",
                "DELETE",
                None,
                admin_h,
            )
        for u in users.values():
            http(f"{base}/auth/v1/admin/users/{u['id']}", "DELETE", None, admin_h)
        after = owner_state()
        kept = {k: v for k, v in before.items() if k != "_imports"}
        now = {k: v for k, v in after.items() if k != "_imports" and k in kept}
        check("every pre-existing business kept its transactions", kept == now, "counts unchanged")
        check("no pre-existing import changed state", before["_imports"] == after["_imports"], "")
    failures = sum(1 for _, ok, _ in results if not ok)
    print(f"{len(results) - failures}/{len(results)} business deletion checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
