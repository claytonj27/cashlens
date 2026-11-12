#!/usr/bin/env python3
"""End-to-end statement import checks against the development project through the running API.

Creates two temporary users through the Auth admin API, provisions a private
business for each through the API, and drives the real flow for user A:
signed upload straight to Storage, parse and staging, review, atomic
confirmation, repeat confirmation, exact-file replay, a refused confirmation
that must leave nothing behind, and evidence. User B, an anonymous caller,
the publishable key and user tokens at the Storage and Data APIs must all be
denied. Everything the check creates is deleted at the end; other accounts
and businesses are never listed, modified or deleted.

    API_BASE=http://127.0.0.1:8000 services/api/.venv/bin/python db/checks/import_flow_check.py
"""

import hashlib
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
API_BYPASS = os.environ.get("API_BYPASS", "").strip()  # protected preview deployments only
CLIENT = {"x-cashlens-client": "web"}
results: list[tuple[str, bool, str]] = []

CSV_ONE = (
    b"date,description,amount,balance,transaction_id\n"
    b"2026-05-01,LEASE PAYMENT,-3500.00,26500.00,ref-1\n"
    b"2026-05-03,CARD SETTLEMENT,3307.49,29807.49,ref-2\n"
    b"2026-05-03,CARD SETTLEMENT,3307.49,33114.98,ref-3\n"
    b"2026-05-09,SUPPLIER INC,-2420.68,30694.30,ref-4\n"
)
CSV_TWO = (
    b"date,description,amount,balance,transaction_id\n"
    b"2026-05-09,SUPPLIER INC,-2420.68,30694.30,ref-4\n"  # identical replay of an existing row
    b"2026-06-01,LEASE PAYMENT,-3500.00,27194.30,ref-5\n"
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
    base, admin, anon = env("SUPABASE_URL"), env("SUPABASE_SERVICE_ROLE_KEY"), env("SUPABASE_ANON_KEY")
    admin_h = {"apikey": admin, "Authorization": f"Bearer {admin}"}
    tag = secrets.token_hex(3)
    users: dict[str, dict] = {}
    businesses: list[str] = []
    try:
        for label in ("a", "b"):
            email, password = f"importcheck-{label}-{tag}@example.org", secrets.token_urlsafe(18)
            st, created = http(f"{base}/auth/v1/admin/users", "POST", {"email": email, "password": password, "email_confirm": True}, admin_h)
            if st not in (200, 201):
                raise RuntimeError(f"could not create user {label}: {st}")
            st, session = http(f"{base}/auth/v1/token?grant_type=password", "POST", {"email": email, "password": password}, {"apikey": anon})
            users[label] = {"id": created["id"], "token": session["access_token"]}
            auth = {"authorization": f"Bearer {session['access_token']}", **CLIENT}
            st, prov = http(f"{API_BASE}/api/private/provision", "POST", {"business_name": f"Import check {label} {tag}"}, auth)
            if st != 200:
                raise RuntimeError(f"provision {label}: {st} {prov}")
            users[label].update({"workspace": prov["workspace_id"], "business": prov["business"]["id"], "auth": auth})
            businesses.append(prov["business"]["id"])
        a, b = users["a"], users["b"]
        imports = f"{API_BASE}/api/workspaces/{a['workspace']}/imports"

        # ---- create, upload straight to Storage, stage ----
        st, created = http(imports, "POST", {"new_account": {"name": "Operating Checking", "account_type": "checking", "mask": "6812"}, "file_label": "may.csv", "declared_bytes": len(CSV_ONE)}, a["auth"])
        check("create_import_issues_upload_target", st == 201 and created["upload"]["path"].startswith(a["business"] + "/"), f"HTTP {st}")
        upload, import_id = created["upload"], created["import"]["id"]
        st, _ = http(f"{base}/storage/v1/object/upload/sign/{upload['bucket']}/{upload['path']}?token={upload['token']}", "PUT", raw=CSV_ONE, headers={"apikey": anon, "Content-Type": "text/csv"})
        check("browser_style_upload_with_token_only", st == 200, f"HTTP {st}")
        other_path = f"{a['business']}/{import_id}-other.csv"
        st, _ = http(f"{base}/storage/v1/object/upload/sign/{upload['bucket']}/{other_path}?token={upload['token']}", "PUT", raw=b"x", headers={"apikey": anon, "Content-Type": "text/csv"})
        check("upload_token_is_bound_to_its_path", st == 400, f"HTTP {st}")
        st, info = http(f"{base}/storage/v1/object/{upload['bucket']}/{upload['path']}", headers={"apikey": anon, "Authorization": f"Bearer {anon}"})
        check("publishable_key_cannot_read_the_object", st in (400, 403, 404), f"HTTP {st}")
        st, info = http(f"{base}/storage/v1/object/{upload['bucket']}/{upload['path']}", headers={"apikey": anon, "Authorization": f"Bearer {a['token']}"})
        check("owner_token_cannot_read_the_object_directly", st in (400, 403, 404), f"HTTP {st}")
        st, listing = http(f"{base}/storage/v1/object/list/{upload['bucket']}", "POST", {"prefix": a["business"]}, {"apikey": anon, "Authorization": f"Bearer {a['token']}"})
        check("owner_token_cannot_list_the_bucket", st != 200 or listing == [], f"HTTP {st} {len(listing) if isinstance(listing, list) else ''}")
        st, rows = http(f"{base}/rest/v1/document_imports?select=id", headers={"apikey": anon, "Authorization": f"Bearer {a['token']}"})
        check("owner_token_cannot_read_import_records_via_data_api", st != 200 or rows == [], f"HTTP {st}")
        st, rows = http(f"{base}/rest/v1/import_rows?select=id", headers={"apikey": anon, "Authorization": f"Bearer {a['token']}"})
        check("owner_token_cannot_read_staging_rows_via_data_api", st != 200 or rows == [], f"HTTP {st}")
        st, staged = http(f"{imports}/{import_id}/uploaded", "POST", {}, a["auth"])
        if st != 200 or not isinstance(staged, dict):
            raise RuntimeError(f"uploaded returned HTTP {st}: {str(staged)[:120]}")
        check("uploaded_parses_and_stages", staged["status"] == "staged", f"HTTP {st} {staged.get('status')}")
        summary = staged["summary"]
        check("summary_totals_known_answer", summary["credits_minor"] == 661498 and summary["debits_minor"] == 592068 and summary["row_count"] == 4, f"{summary['credits_minor']}/{summary['debits_minor']}")
        check("statement_balances_read_from_file", summary["balance"]["opening_minor"] == 3000000 and summary["balance"]["closing_minor"] == 3069430 and summary["balance"]["reconciles"], str(summary["balance"]))
        check("hash_recorded_and_parser_versioned", staged["parser_version"] == "csv-1" and staged["byte_size"] == len(CSV_ONE))
        st, page = http(f"{imports}/{import_id}/rows", headers=a["auth"])
        check("rows_staged_with_effective_values", st == 200 and page["total"] == 4 and page["items"][1]["effective"]["amount_minor"] == 330749, f"HTTP {st}")
        st, patched = http(f"{imports}/{import_id}/rows/0", "PATCH", {"description": "LEASE PAYMENT (MAY)"}, a["auth"])
        check("owner_correction_bumps_revision", st == 200 and patched["effective"]["description"] == "LEASE PAYMENT (MAY)", f"HTTP {st}")
        st, detail = http(f"{imports}/{import_id}", headers=a["auth"])
        check("detail_reports_revision_two", st == 200 and detail["draft_revision"] == 2, f"HTTP {st}")

        # ---- other identities cannot touch it ----
        for name, url, method, body, headers in (
            ("user_b_cannot_read_import", f"{imports}/{import_id}", "GET", None, b["auth"]),
            ("user_b_cannot_read_rows", f"{imports}/{import_id}/rows", "GET", None, b["auth"]),
            ("user_b_cannot_patch_rows", f"{imports}/{import_id}/rows/0", "PATCH", {"description": "x"}, b["auth"]),
            ("user_b_cannot_confirm", f"{imports}/{import_id}/confirm", "POST", {"revision": 2}, b["auth"]),
            ("user_b_cannot_cancel", f"{imports}/{import_id}/cancel", "POST", {}, b["auth"]),
            ("user_b_cannot_list_a_imports", imports, "GET", None, b["auth"]),
        ):
            st, _ = http(url, method, body, headers)
            check(name, st == 404, f"HTTP {st}")
        st, _ = http(f"{imports}/{import_id}", headers=CLIENT)
        check("anonymous_cannot_read_import", st == 401, f"HTTP {st}")
        st, _ = http(f"{imports}/{import_id}/confirm", "POST", {"revision": 2}, {"authorization": f"Bearer {a['token']}"})
        check("confirm_without_client_header_is_403", st == 403, f"HTTP {st}")

        # ---- confirm atomically, repeat, evidence, balances, file deletion ----
        st, refused = http(f"{imports}/{import_id}/confirm", "POST", {"revision": 1}, a["auth"])
        check("stale_revision_is_refused", st == 409, f"HTTP {st}")
        st, confirmed = http(f"{imports}/{import_id}/confirm", "POST", {"revision": 2}, a["auth"])
        if st != 200 or not isinstance(confirmed, dict):
            raise RuntimeError(f"confirm returned HTTP {st}: {str(confirmed)[:200]}")
        check("confirm_publishes_rows", confirmed["created"] == 4 and confirmed["import"]["status"] == "confirmed", f"HTTP {st}")
        account_id = confirmed["import"]["account"]["id"]
        st, txs = http(f"{API_BASE}/api/workspaces/{a['workspace']}/transactions?limit=100", headers=a["auth"])
        check("transactions_visible_in_workspace", st == 200 and txs["total"] == 4, f"total={txs.get('total') if st == 200 else st}")
        corrected = [t for t in txs["items"] if t["description"] == "LEASE PAYMENT (MAY)"]
        check("corrected_description_published", len(corrected) == 1)
        st, evidence = http(f"{API_BASE}/api/workspaces/{a['workspace']}/transactions/{corrected[0]['id']}", headers=a["auth"])
        source = evidence["sources"][0] if st == 200 and evidence["sources"] else {}
        check("evidence_points_at_the_import_row", source.get("kind") == "import_row" and source.get("row_index") == 0 and source.get("reviewed") is True and source.get("extracted", {}).get("description") == "LEASE PAYMENT", str(source)[:120])
        st, accounts = http(f"{API_BASE}/api/workspaces/{a['workspace']}/accounts", headers=a["auth"])
        kinds = {(bal["kind"], bal["amount_minor"]) for acc in accounts for bal in acc["balances"]} if st == 200 else set()
        check("statement_balances_recorded_as_observations", ("statement_closing", 3069430) in kinds and ("statement_opening", 3000000) in kinds, str(sorted(kinds)))
        st, info = http(f"{base}/storage/v1/object/info/{upload['bucket']}/{upload['path']}", headers=admin_h)
        check("original_file_deleted_after_confirmation", st in (400, 404), f"HTTP {st}")
        st, again = http(f"{imports}/{import_id}/confirm", "POST", {"revision": 2}, a["auth"])
        st2, txs2 = http(f"{API_BASE}/api/workspaces/{a['workspace']}/transactions?limit=100", headers=a["auth"])
        check("repeat_confirmation_is_a_no_op", st == 200 and again["already_confirmed"] and txs2["total"] == 4, f"HTTP {st}")

        # ---- exact replay ----
        st, created2 = http(imports, "POST", {"account_id": account_id, "file_label": "may-again.csv", "declared_bytes": len(CSV_ONE)}, a["auth"])
        up2 = created2["upload"]
        http(f"{base}/storage/v1/object/upload/sign/{up2['bucket']}/{up2['path']}?token={up2['token']}", "PUT", raw=CSV_ONE, headers={"apikey": anon, "Content-Type": "text/csv"})
        st, replay = http(f"{imports}/{created2['import']['id']}/uploaded", "POST", {}, a["auth"])
        check("exact_file_replay_returns_existing_import", st == 200 and replay["status"] == "cancelled" and replay["duplicate_of"] == import_id, f"HTTP {st} {replay.get('status') if isinstance(replay, dict) else ''}")
        st, info = http(f"{base}/storage/v1/object/info/{up2['bucket']}/{up2['path']}", headers=admin_h)
        check("replayed_file_deleted", st in (400, 404), f"HTTP {st}")

        # ---- overlap: keep a replayed reference -> refused atomically; link -> retry commits once ----
        st, created3 = http(imports, "POST", {"account_id": account_id, "file_label": "june.csv", "declared_bytes": len(CSV_TWO)}, a["auth"])
        up3, import3 = created3["upload"], created3["import"]["id"]
        http(f"{base}/storage/v1/object/upload/sign/{up3['bucket']}/{up3['path']}?token={up3['token']}", "PUT", raw=CSV_TWO, headers={"apikey": anon, "Content-Type": "text/csv"})
        st, staged3 = http(f"{imports}/{import3}/uploaded", "POST", {}, a["auth"])
        check("overlap_flags_already_imported_reference", st == 200 and staged3["summary"]["flag_counts"].get("already_imported") == 1 and staged3["summary"]["existing_in_period"] == 1, str(staged3.get("summary", {}).get("flag_counts")))
        st, kept = http(f"{imports}/{import3}/rows/0", "PATCH", {"decision": "keep"}, a["auth"])
        check("keeping_a_reference_the_account_already_holds_is_refused", st == 422 and (kept.get("detail") or {}).get("kind") == "conflicting_reference", f"HTTP {st}")
        # The route's refusal is a courtesy. The database must refuse the same write even when
        # the review carries no flag, which is what happens if another file claims the reference
        # after this one was staged. The staged row is edited directly to reach that state.
        st, staged_rows = http(f"{base}/rest/v1/import_rows?import_id=eq.{import3}&row_index=eq.0&select=id", headers=admin_h)
        http(f"{base}/rest/v1/import_rows?id=eq.{staged_rows[0]['id']}", "PATCH", {"flags": [], "decision": "keep", "decision_by": "owner", "linked_transaction_id": None}, admin_h)
        st3, detail3 = http(f"{imports}/{import3}", headers=a["auth"])
        st, refused = http(f"{imports}/{import3}/confirm", "POST", {"revision": detail3["draft_revision"]}, a["auth"])
        st2, txs3 = http(f"{API_BASE}/api/workspaces/{a['workspace']}/transactions?limit=100", headers=a["auth"])
        st3, detail3 = http(f"{imports}/{import3}", headers=a["auth"])
        check("duplicate_reference_refused_and_nothing_inserted", st == 409 and txs3["total"] == 4 and detail3["status"] == "staged", f"HTTP {st} total={txs3.get('total')}")
        target = next(t["id"] for t in txs3["items"] if t["description"] == "SUPPLIER INC")
        st, relinked = http(f"{imports}/{import3}/rows/0", "PATCH", {"decision": "link_existing", "linked_transaction_id": target}, a["auth"])
        st3, detail3 = http(f"{imports}/{import3}", headers=a["auth"])
        st, result3 = http(f"{imports}/{import3}/confirm", "POST", {"revision": detail3["draft_revision"]}, a["auth"])
        st2, txs4 = http(f"{API_BASE}/api/workspaces/{a['workspace']}/transactions?limit=100", headers=a["auth"])
        check("retry_after_refusal_commits_exactly_once", st == 200 and result3["created"] == 1 and result3["linked"] == 1 and txs4["total"] == 5, f"HTTP {st} total={txs4.get('total')}")
        st, evidence2 = http(f"{API_BASE}/api/workspaces/{a['workspace']}/transactions/{target}", headers=a["auth"])
        check("linked_row_adds_provenance_to_existing_transaction", st == 200 and len(evidence2["sources"]) == 2 and all(s["kind"] == "import_row" for s in evidence2["sources"]), f"sources={len(evidence2.get('sources', [])) if st == 200 else st}")
        st, workspace = http(f"{API_BASE}/api/private/workspace", headers=a["auth"])
        check("private_workspace_counts_confirmed_imports", st == 200 and workspace["statements"]["count"] == 2 and workspace["transaction_count"] == 5, str(workspace.get("statements") if st == 200 else st))

        # ---- a PDF upload is isolated exactly like a CSV one ----
        pdf_bytes = (ROOT / "fixtures" / "statements" / "layout-a-running-balance.pdf").read_bytes()
        st, created_pdf = http(imports, "POST", {"account_id": account_id, "file_label": "layout-a.pdf", "declared_bytes": len(pdf_bytes)}, a["auth"])
        up_pdf, import_pdf = created_pdf["upload"], created_pdf["import"]["id"]
        http(f"{base}/storage/v1/object/upload/sign/{up_pdf['bucket']}/{up_pdf['path']}?token={up_pdf['token']}", "PUT", raw=pdf_bytes, headers={"apikey": anon, "Content-Type": "application/pdf"})
        st, obj = http(f"{base}/storage/v1/object/{up_pdf['bucket']}/{up_pdf['path']}", headers={"apikey": anon, "Authorization": f"Bearer {b['token']}"})
        check("user_b_cannot_read_the_pdf_object", st in (400, 403, 404), f"HTTP {st}")
        st, staged_pdf = http(f"{imports}/{import_pdf}/uploaded", "POST", {}, a["auth"])
        check("pdf_stages_with_page_provenance", st == 200 and staged_pdf["format"] == "pdf" and staged_pdf["summary"]["row_count"] == 24 and staged_pdf["summary"]["reconciliation"]["status"] == "matched", f"HTTP {st} {staged_pdf.get('summary', {}).get('reconciliation') if isinstance(staged_pdf, dict) else staged_pdf}")
        for name, url, method, body in (("user_b_cannot_read_pdf_import", f"{imports}/{import_pdf}", "GET", None), ("user_b_cannot_read_pdf_rows", f"{imports}/{import_pdf}/rows", "GET", None), ("user_b_cannot_decide_pdf_rows", f"{imports}/{import_pdf}/decisions", "POST", {"decision": "keep"})):
            st, _ = http(url, method, body, b["auth"])
            check(name, st == 404, f"HTTP {st}")
        st, rows_pdf = http(f"{imports}/{import_pdf}/rows?limit=2", headers=a["auth"])
        check("pdf_rows_carry_page_and_line", st == 200 and all(r["page_number"] == 1 and r["source_locator"].startswith("page 1 line") for r in rows_pdf["items"]), str([r["source_locator"] for r in rows_pdf["items"]]) if st == 200 else str(st))
        st, cancelled_pdf = http(f"{imports}/{import_pdf}/cancel", "POST", {}, a["auth"])
        st2, info = http(f"{base}/storage/v1/object/info/{up_pdf['bucket']}/{up_pdf['path']}", headers=admin_h)
        check("pdf_cancel_removes_rows_and_object", st == 200 and cancelled_pdf["status"] == "cancelled" and st2 in (400, 404), f"HTTP {st}/{st2}")

        # ---- lease-guarded staging at the database: a stale worker's writes are refused ----
        rpc = f"{base}/rest/v1/rpc/"
        st, created_g = http(imports, "POST", {"account_id": account_id, "file_label": "guard.csv", "declared_bytes": len(CSV_ONE)}, a["auth"])
        import_g = created_g["import"]["id"]
        st, lease_a = http(rpc + "acquire_import_job", "POST", {"p_import_id": import_g, "p_kind": "parse", "p_idempotency_key": f"guard:{tag}", "p_lease_seconds": 60}, admin_h)
        lease_a = lease_a[0] if isinstance(lease_a, list) else lease_a
        http(f"{base}/rest/v1/import_jobs?id=eq.{lease_a['job_id']}", "PATCH", {"lease_expires_at": "2000-01-01T00:00:00Z"}, {**admin_h, "Prefer": "return=minimal"})
        st, lease_b = http(rpc + "acquire_import_job", "POST", {"p_import_id": import_g, "p_kind": "parse", "p_idempotency_key": f"guard:{tag}", "p_lease_seconds": 60}, admin_h)
        lease_b = lease_b[0] if isinstance(lease_b, list) else lease_b
        fields = {"format": "csv", "byte_size": 1, "content_hash": "0" * 64, "parser_version": "csv-1", "mapping": {}, "summary": {}, "draft_revision": 1}
        stale_rows = [{"row_index": 0, "source_locator": "line 2", "raw": {}, "extracted": {"posted_date": "2026-01-01", "amount_minor": 1, "description": "STALE"}}]
        st, stale = http(rpc + "stage_import", "POST", {"p_import_id": import_g, "p_job_id": lease_a["job_id"], "p_lease_token": lease_a["lease_token"], "p_rows": stale_rows, "p_fields": fields}, admin_h)
        st2, rows_g = http(f"{base}/rest/v1/import_rows?import_id=eq.{import_g}&select=id", headers=admin_h)
        st3, rec_g = http(f"{base}/rest/v1/document_imports?id=eq.{import_g}&select=status", headers=admin_h)
        check("stale_worker_cannot_stage_rows_or_change_state", st >= 400 and "lease_lost" in str(stale) and rows_g == [] and rec_g[0]["status"] == "awaiting_upload", f"HTTP {st} rows={len(rows_g) if isinstance(rows_g, list) else rows_g} status={rec_g[0]['status'] if rec_g else rec_g}")
        st, stale_fail = http(rpc + "fail_import", "POST", {"p_import_id": import_g, "p_job_id": lease_a["job_id"], "p_lease_token": lease_a["lease_token"], "p_error_code": "x", "p_error_message": "stale"}, admin_h)
        st3, rec_g = http(f"{base}/rest/v1/document_imports?id=eq.{import_g}&select=status", headers=admin_h)
        check("stale_worker_cannot_fail_the_import", st >= 400 and "lease_lost" in str(stale_fail) and rec_g[0]["status"] == "awaiting_upload", f"HTTP {st}")
        st, current = http(rpc + "stage_import", "POST", {"p_import_id": import_g, "p_job_id": lease_b["job_id"], "p_lease_token": lease_b["lease_token"], "p_rows": stale_rows, "p_fields": fields}, admin_h)
        st3, rec_g = http(f"{base}/rest/v1/document_imports?id=eq.{import_g}&select=status", headers=admin_h)
        check("current_lease_holder_stages_atomically", st == 200 and current == 1 and rec_g[0]["status"] == "staged", f"HTTP {st} {current}")
        http(f"{imports}/{import_g}/cancel", "POST", {}, a["auth"])

        # ---- lease tokens at the database: a stale holder cannot close a newer lease ----
        rpc = f"{base}/rest/v1/rpc/"
        st, first = http(rpc + "acquire_import_job", "POST", {"p_import_id": import3, "p_kind": "parse", "p_idempotency_key": f"leasecheck:{tag}", "p_lease_seconds": 60}, admin_h)
        first = first[0] if isinstance(first, list) else first
        st, _ = http(f"{base}/rest/v1/import_jobs?id=eq.{first['job_id']}", "PATCH", {"lease_expires_at": "2000-01-01T00:00:00Z"}, {**admin_h, "Prefer": "return=minimal"})
        st, second = http(rpc + "acquire_import_job", "POST", {"p_import_id": import3, "p_kind": "parse", "p_idempotency_key": f"leasecheck:{tag}", "p_lease_seconds": 60}, admin_h)
        second = second[0] if isinstance(second, list) else second
        check("expired_lease_is_reacquired_with_a_new_token", st == 200 and second["attempts"] == 2 and second["lease_token"] != first["lease_token"], f"HTTP {st}")
        st, stale = http(rpc + "finish_import_job", "POST", {"p_job_id": first["job_id"], "p_lease_token": first["lease_token"], "p_state": "succeeded", "p_safe_error": None}, admin_h)
        st2, row = http(f"{base}/rest/v1/import_jobs?id=eq.{first['job_id']}&select=state,attempts", headers=admin_h)
        check("stale_lease_holder_cannot_close_the_newer_lease", stale is False and row[0]["state"] == "leased" and row[0]["attempts"] == 2, f"finish={stale} state={row[0]['state'] if row else row}")
        st, current = http(rpc + "finish_import_job", "POST", {"p_job_id": second["job_id"], "p_lease_token": second["lease_token"], "p_state": "failed", "p_safe_error": "lease check"}, admin_h)
        st2, row = http(f"{base}/rest/v1/import_jobs?id=eq.{first['job_id']}&select=state", headers=admin_h)
        check("current_lease_holder_closes_its_lease", current is True and row[0]["state"] == "failed", f"finish={current}")
    except RuntimeError as exc:
        check("aborted", False, str(exc))
    finally:
        # cleanup: only what this run created
        for business_id in businesses:
            for table, key in (("transaction_sources", None), ("transactions", "business_id"), ("import_rows", None)):
                if table == "transaction_sources":
                    st, txs = http(f"{base}/rest/v1/transactions?business_id=eq.{business_id}&select=id", headers=admin_h)
                    for t in txs or []:
                        http(f"{base}/rest/v1/transaction_sources?transaction_id=eq.{t['id']}", "DELETE", None, admin_h)
                elif table == "import_rows":
                    st, imps = http(f"{base}/rest/v1/document_imports?business_id=eq.{business_id}&select=id", headers=admin_h)
                    for i in imps or []:
                        http(f"{base}/rest/v1/import_rows?import_id=eq.{i['id']}", "DELETE", None, admin_h)
                else:
                    http(f"{base}/rest/v1/{table}?{key}=eq.{business_id}", "DELETE", None, admin_h)
            st, objects = http(f"{base}/storage/v1/object/list/statements", "POST", {"prefix": business_id, "limit": 100}, admin_h)
            if st == 200 and objects:
                http(f"{base}/storage/v1/object/statements", "DELETE", {"prefixes": [f"{business_id}/{o['name']}" for o in objects]}, admin_h)
            http(f"{base}/rest/v1/businesses?id=eq.{business_id}&data_mode=eq.private", "DELETE", None, admin_h)
        for u in users.values():
            http(f"{base}/auth/v1/admin/users/{u['id']}", "DELETE", None, admin_h)
        leftovers = []
        for business_id in businesses:
            st, rows = http(f"{base}/rest/v1/businesses?id=eq.{business_id}&select=id", headers=admin_h)
            leftovers += rows or []
        check("temporary_businesses_deleted", not leftovers, f"{len(leftovers)} left")
        st, remaining = http(f"{base}/auth/v1/admin/users?page=1&per_page=100", headers=admin_h)
        stale = [x for x in (remaining or {}).get("users", []) if tag in (x.get("email") or "")]
        check("temporary_users_deleted", not stale, f"{len(stale)} left")
    failures = sum(1 for _, ok, _ in results if not ok)
    print(f"{len(results) - failures}/{len(results)} import flow checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
