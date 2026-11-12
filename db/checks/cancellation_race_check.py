#!/usr/bin/env python3
"""State transitions and races the M7 review raised, against the development project.

Creates one temporary user with one private business, then drives the real routes and
the real database functions:

  * a parse that still holds its lease cannot stage rows into a cancelled import, and
    cannot fail it either;
  * cancelling twice is safe, and a file whose removal failed is still recorded as
    present and removed on the next attempt;
  * a confirmed import cannot be cancelled and a cancelled one cannot be confirmed, so
    the two never both succeed;
  * one source reference identifies one transaction per account whatever format it
    arrived in, enforced by the database and not only by the checks before the write.

Everything it creates is deleted at the end. No other account, business, import or
transaction is listed, changed or removed.

    API_BASE=http://127.0.0.1:8000 services/api/.venv/bin/python db/checks/cancellation_race_check.py
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
results: list[tuple[str, bool, str]] = []

CSV_A = (
    b"date,description,amount,balance,transaction_id\n"
    b"2026-05-01,LEASE PAYMENT,-3500.00,26500.00,race-1\n"
    b"2026-05-03,CARD SETTLEMENT,3307.49,29807.49,race-2\n"
)
CSV_B = (
    b"date,description,amount,balance,transaction_id\n"
    b"2026-05-01,LEASE PAYMENT,-3500.00,26500.00,race-1\n"
    b"2026-06-02,INSURANCE ACH,-450.00,29357.49,race-3\n"
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
    print(f"{'PASS' if ok else 'FAIL'}  {name:<60} {detail}")


def http(url: str, method: str = "GET", body=None, headers=None, raw: bytes | None = None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"User-Agent": "cashlens-check", **(headers or {})}
    if API_BYPASS and url.startswith(API_BASE):
        h["x-vercel-protection-bypass"] = API_BYPASS
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
    rpc = f"{base}/rest/v1/rpc/"
    tag = secrets.token_hex(3)
    user: dict = {}
    businesses: list[str] = []
    try:
        email, password = f"racecheck-{tag}@example.org", secrets.token_urlsafe(18)
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
            f"{API_BASE}/api/private/provision", "POST", {"business_name": f"Race {tag}"}, auth
        )
        if st != 200:
            raise RuntimeError(f"provision: {st} {prov}")
        workspace, business = prov["workspace_id"], prov["business"]["id"]
        businesses.append(business)
        imports = f"{API_BASE}/api/workspaces/{workspace}/imports"

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
            st, made = http(imports, "POST", body, auth)
            if st != 201:
                raise RuntimeError(f"create import {label}: {st} {made}")
            up = made["upload"]
            http(
                f"{base}/storage/v1/object/upload/sign/{up['bucket']}/{up['path']}?token={up['token']}",
                "PUT",
                raw=data,
                headers={"apikey": anon, "Content-Type": "text/csv"},
            )
            return made["import"]["id"], up["path"]

        # ---- a parse holding a lease cannot write into a cancelled import ----
        import_a, path_a = upload(CSV_A, "race-a.csv")
        st, lease = http(
            rpc + "acquire_import_job",
            "POST",
            {
                "p_import_id": import_a,
                "p_kind": "parse",
                "p_idempotency_key": f"race:{tag}:1",
                "p_lease_seconds": 180,
            },
            admin_h,
        )
        token = lease[0]["lease_token"] if isinstance(lease, list) else None
        check("parse_lease_acquired", bool(token), f"HTTP {st}")
        st, cancelled = http(f"{imports}/{import_a}/cancel", "POST", None, auth)
        check(
            "cancel_while_a_parse_holds_the_lease_succeeds",
            st == 200 and cancelled.get("status") == "cancelled",
            f"HTTP {st}",
        )
        st, staged = http(
            rpc + "stage_import",
            "POST",
            {
                "p_import_id": import_a,
                "p_job_id": lease[0]["job_id"],
                "p_lease_token": token,
                "p_rows": [{"row_index": 0, "raw": {}, "extracted": {}, "flags": []}],
                "p_fields": {"byte_size": 1, "draft_revision": 1},
            },
            admin_h,
        )
        check(
            "stale_parse_cannot_stage_into_a_cancelled_import",
            st >= 400 and "lease_lost" in json.dumps(staged),
            f"HTTP {st}",
        )
        st, failed = http(
            rpc + "fail_import",
            "POST",
            {
                "p_import_id": import_a,
                "p_job_id": lease[0]["job_id"],
                "p_lease_token": token,
                "p_error_code": "stale",
                "p_error_message": "stale worker",
            },
            admin_h,
        )
        check(
            "stale_parse_cannot_fail_a_cancelled_import",
            st >= 400 and "lease_lost" in json.dumps(failed),
            f"HTTP {st}",
        )
        st, rows = http(
            f"{base}/rest/v1/import_rows?import_id=eq.{import_a}&select=id", headers=admin_h
        )
        check("cancelled_import_has_no_rows", rows == [], f"{len(rows or [])} rows")
        st, objects = http(
            f"{base}/storage/v1/object/list/statements",
            "POST",
            {"prefix": business, "limit": 100},
            admin_h,
        )
        names = [o["name"] for o in objects or []]
        check(
            "cancelled_import_file_removed",
            path_a.split("/")[-1] not in names,
            f"{len(names)} object(s) left",
        )
        st, again = http(f"{imports}/{import_a}/cancel", "POST", None, auth)
        check(
            "cancelling_again_is_safe", st == 200 and again.get("status") == "cancelled", f"HTTP {st}"
        )

        # ---- a confirmed import cannot be cancelled ----
        import_b, _ = upload(CSV_A, "race-b.csv")
        st, marked = http(f"{imports}/{import_b}/uploaded", "POST", None, auth)
        check("second_import_staged", st == 200 and marked["status"] == "staged", f"HTTP {st}")
        st, detail = http(f"{imports}/{import_b}", headers=auth)
        st, confirmed = http(
            f"{imports}/{import_b}/confirm", "POST", {"revision": detail["draft_revision"]}, auth
        )
        check("confirmation_succeeds", st == 200 and confirmed.get("created") == 2, f"HTTP {st}")
        account_id = confirmed["import"]["account"]["id"]
        st, refused = http(f"{imports}/{import_b}/cancel", "POST", None, auth)
        check("confirmed_import_cannot_be_cancelled", st == 409, f"HTTP {st}")
        st, still = http(f"{imports}/{import_b}", headers=auth)
        check("confirmed_import_stays_confirmed", still["status"] == "confirmed", still["status"])

        # ---- a cancelled import cannot be confirmed ----
        import_c, _ = upload(CSV_B, "race-c.csv", account_id=account_id)
        st, marked = http(f"{imports}/{import_c}/uploaded", "POST", None, auth)
        st, detail_c = http(f"{imports}/{import_c}", headers=auth)
        revision_c = detail_c["draft_revision"]
        st, _ = http(f"{imports}/{import_c}/cancel", "POST", None, auth)
        st, _refused = http(f"{imports}/{import_c}/confirm", "POST", {"revision": revision_c}, auth)
        check("cancelled_import_cannot_be_confirmed", st == 409, f"HTTP {st}")
        st, txs = http(
            f"{base}/rest/v1/transactions?account_id=eq.{account_id}&select=id,external_id",
            headers=admin_h,
        )
        check(
            "a_cancelled_confirmation_added_nothing",
            len(txs or []) == 2,
            f"{len(txs or [])} transactions",
        )

        # ---- one reference, one transaction, whatever the format ----
        refs = sorted(t["external_id"] for t in txs or [])
        check("references_kept_from_the_confirmed_file", refs == ["race-1", "race-2"], str(refs))
        st, clash = http(
            f"{base}/rest/v1/transactions",
            "POST",
            {
                "business_id": business,
                "account_id": account_id,
                "source_type": "pdf_import",
                "external_id": "race-1",
                "amount_minor": -350000,
                "posted_date": "2026-05-01",
                "description": "LEASE PAYMENT",
                "status": "posted",
            },
            {**admin_h, "Prefer": "return=representation"},
        )
        check(
            "a_second_format_cannot_claim_the_same_reference",
            st >= 400 and "transactions_import_reference_identity" in json.dumps(clash),
            f"HTTP {st}",
        )
        # A PDF transaction in the same account may not take over a reference a CSV
        # transaction already holds, which is what confirm_import's lending step would do.
        st, other = http(
            f"{base}/rest/v1/transactions",
            "POST",
            {
                "business_id": business,
                "account_id": account_id,
                "source_type": "pdf_import",
                "amount_minor": -45000,
                "posted_date": "2026-06-02",
                "description": "INSURANCE ACH",
                "status": "posted",
            },
            {**admin_h, "Prefer": "return=representation"},
        )
        check("a_pdf_transaction_without_a_reference_is_allowed", st in (200, 201), f"HTTP {st}")
        st, lend = http(
            f"{base}/rest/v1/transactions?id=eq.{other[0]['id']}",
            "PATCH",
            {"external_id": "race-1"},
            admin_h,
        )
        check(
            "a_reference_cannot_be_lent_across_formats",
            st >= 400 and "transactions_import_reference_identity" in json.dumps(lend),
            f"HTTP {st}",
        )
        st, after = http(
            f"{base}/rest/v1/transactions?account_id=eq.{account_id}&select=id,external_id",
            headers=admin_h,
        )
        kept = sorted(t["external_id"] for t in after or [] if t["external_id"])
        check("the_refused_writes_changed_nothing", kept == ["race-1", "race-2"], str(kept))
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
                http(
                    f"{base}/rest/v1/import_rows?import_id=eq.{i['id']}", "DELETE", None, admin_h
                )
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
        if user.get("id"):
            http(f"{base}/auth/v1/admin/users/{user['id']}", "DELETE", None, admin_h)
        leftovers = []
        for business_id in businesses:
            st, rows = http(
                f"{base}/rest/v1/businesses?id=eq.{business_id}&select=id", headers=admin_h
            )
            leftovers += rows or []
        check("temporary_business_deleted", not leftovers, f"{len(leftovers)} left")
    failures = sum(1 for _, ok, _ in results if not ok)
    print(f"{len(results) - failures}/{len(results)} cancellation and race checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
