#!/usr/bin/env python3
"""Sequential CSV statement imports through the running API, checked against an expected-results file.

Drives the real flow (signed upload to Storage, staging, confirmation) for a
temporary user whose private business is named on the command line, importing
every CSV in a directory in sorted order into one account. After each file it
checks the cumulative posted count, the file's credits and debits, the
account's continuity, the statement balances recorded as observations and the
evidence of the newest transaction against the expected-results JSON. Then it
re-uploads the last file and asserts the replay cannot double-count. Nothing
about the business, the files or the expected figures is built into the
application; they are inputs to this check only. The reviewer-only files are
read here and nowhere else. The temporary user and business are deleted at
the end unless --keep is given.

    API_BASE=http://127.0.0.1:8000 services/api/.venv/bin/python db/checks/statement_pack_check.py \\
        --csv-dir <dir with the CSV files> --expected <expected-results.json> \\
        --business "Cedar Corner Cafe" --account "Operating Checking" --mask 6812 [--keep]
"""

import argparse
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "services" / "api" / ".env"
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_BYPASS = os.environ.get("API_BYPASS", "").strip()  # protected preview deployments only
CLIENT = {"x-cashlens-client": "web"}
results: list[tuple[str, bool, str]] = []


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
    print(f"{'PASS' if ok else 'FAIL'}  {name:<66} {detail}")


def http(url, method="GET", body=None, headers=None, raw=None):
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
            return response.status, (json.loads(text) if text and "json" in response.headers.get("content-type", "") else text)
    except urllib.error.HTTPError as exc:
        text = exc.read()
        try:
            return exc.code, json.loads(text)
        except Exception:
            return exc.code, text[:200]


def all_transactions(workspace: str, auth: dict) -> list[dict]:
    items, cursor = [], None
    while True:
        url = f"{API_BASE}/api/workspaces/{workspace}/transactions?limit=100" + (f"&cursor={cursor}" if cursor else "")
        st, page = http(url, headers=auth)
        if st != 200:
            raise RuntimeError(f"transactions page: {st} {page}")
        items.extend(page["items"])
        cursor = page.get("next_cursor")
        if not cursor:
            return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv-dir", help="directory of CSV statements to import in sorted order")
    parser.add_argument("--pdf-dir", help="directory of PDF statements to import in sorted order")
    parser.add_argument("--cross-format", choices=["csv-then-pdf", "pdf-then-csv"], help="import one month in both formats and require the overlap decision")
    parser.add_argument("--expected", required=True)
    parser.add_argument("--business", required=True)
    parser.add_argument("--account", default="Operating Checking")
    parser.add_argument("--mask", default=None)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    expected = json.loads(Path(args.expected).read_text())
    if args.cross_format:
        first_dir, first_glob, second_dir, second_glob = (
            (args.csv_dir, "*.csv", args.pdf_dir, "*.pdf") if args.cross_format == "csv-then-pdf" else (args.pdf_dir, "*.pdf", args.csv_dir, "*.csv")
        )
        if not first_dir or not second_dir:
            sys.exit("--cross-format needs both --csv-dir and --pdf-dir")
        files = sorted(Path(first_dir).glob(first_glob))[1:2]  # one monthly statement in the first format
        cross_second = sorted(Path(second_dir).glob(second_glob))[1:2]
    else:
        files = sorted(Path(args.csv_dir).glob("*.csv")) if args.csv_dir else sorted(Path(args.pdf_dir).glob("*.pdf"))
        cross_second = []
    if not files:
        sys.exit("no statement files found")
    base, admin, anon = env("SUPABASE_URL"), env("SUPABASE_SERVICE_ROLE_KEY"), env("SUPABASE_ANON_KEY")
    admin_h = {"apikey": admin, "Authorization": f"Bearer {admin}"}
    tag = secrets.token_hex(3)
    user_id = business_id = None
    try:
        email, password = f"packcheck-{tag}@example.org", secrets.token_urlsafe(18)
        st, created = http(f"{base}/auth/v1/admin/users", "POST", {"email": email, "password": password, "email_confirm": True}, admin_h)
        if st not in (200, 201):
            raise RuntimeError(f"user: {st} {created}")
        user_id = created["id"]
        st, session = http(f"{base}/auth/v1/token?grant_type=password", "POST", {"email": email, "password": password}, {"apikey": anon})
        auth = {"authorization": f"Bearer {session['access_token']}", **CLIENT}
        st, prov = http(f"{API_BASE}/api/private/provision", "POST", {"business_name": args.business}, auth)
        if st != 200:
            raise RuntimeError(f"provision: {st} {prov}")
        business_id, workspace = prov["business"]["id"], prov["workspace_id"]
        imports = f"{API_BASE}/api/workspaces/{workspace}/imports"
        account_id = None
        expected_files = {f["file"]: f for f in expected.get("files", [])}
        for path in files:
            data = path.read_bytes()
            exp = expected_files.get(path.stem, {})
            body = {"file_label": path.name, "declared_bytes": len(data)}
            if account_id:
                body["account_id"] = account_id
            else:
                body["new_account"] = {"name": args.account, "account_type": "checking", "mask": args.mask}
            st, created = http(imports, "POST", body, auth)
            if st != 201:
                raise RuntimeError(f"{path.name}: create {st} {created}")
            up, import_id = created["upload"], created["import"]["id"]
            st, _ = http(f"{base}/storage/v1/object/upload/sign/{up['bucket']}/{up['path']}?token={up['token']}", "PUT", raw=data, headers={"apikey": anon, "Content-Type": "application/pdf" if path.suffix == ".pdf" else "text/csv"})
            if st != 200:
                raise RuntimeError(f"{path.name}: upload {st}")
            st, staged = http(f"{imports}/{import_id}/uploaded", "POST", {}, auth)
            if st != 200 or staged.get("status") != "staged":
                raise RuntimeError(f"{path.name}: stage {st} {staged}")
            s = staged["summary"]
            check(f"{path.stem}: staged without blocking rows or mapping doubts", s["blocking_rows"] == 0 and s["mapping_flags"] == [], f"rows={s['row_count']} flags={s['mapping_flags']}")
            check(f"{path.stem}: format detected from bytes", staged["format"] == ("pdf" if path.suffix == ".pdf" else "csv"), staged["format"])
            if path.suffix == ".pdf":
                check(f"{path.stem}: reconciles against the statement's own printed totals", s.get("reconciliation", {}).get("status") == "matched" and s["reconciliation"]["source"] == "statement_totals", str(s.get("reconciliation"))[:120])
                st, first_rows = http(f"{imports}/{import_id}/rows?limit=3", headers=auth)
                check(f"{path.stem}: rows carry page and line provenance", st == 200 and all(r["page_number"] and r["source_locator"].startswith("page ") for r in first_rows["items"]), str([r["source_locator"] for r in first_rows["items"]]))
            if exp:
                check(f"{path.stem}: file totals match expectations", s["row_count"] == exp["transaction_count"] and s["credits_minor"] == exp["credits_minor"] and s["debits_minor"] == exp["debits_minor"], f"{s['row_count']} rows, credits {s['credits_minor']}, debits {s['debits_minor']}")
                bal = s.get("balance") or {}
                check(f"{path.stem}: statement balances read and reconciled", bal.get("opening_minor") == exp["opening_balance_minor"] and bal.get("closing_minor") == exp["closing_balance_minor"] and bal.get("reconciles") is True, f"opening {bal.get('opening_minor')} closing {bal.get('closing_minor')}")
            check(f"{path.stem}: no overlap with prior history", s.get("existing_in_period", 0) == 0 and not s["flag_counts"].get("duplicate_candidate") and not s["flag_counts"].get("already_imported"), str(s["flag_counts"]))
            st, result = http(f"{imports}/{import_id}/confirm", "POST", {"revision": staged["draft_revision"]}, auth)
            if st != 200:
                raise RuntimeError(f"{path.name}: confirm {st} {result}")
            confirmed_account = result["import"]["account"]["id"]
            check(f"{path.stem}: confirmed into the same account", account_id in (None, confirmed_account) and result["created"] == s["row_count"], f"created={result['created']} account={confirmed_account[:8]}")
            account_id = confirmed_account
            txs = all_transactions(workspace, auth)
            posted = [t for t in txs if t["status"] == "posted"]
            if exp and not args.cross_format:
                check(f"{path.stem}: cumulative posted count", len(posted) == exp["cumulative_count"], f"{len(posted)} vs {exp['cumulative_count']}")
            elif exp:
                check(f"{path.stem}: posted count equals the file's rows", len(posted) == exp["transaction_count"], f"{len(posted)} vs {exp['transaction_count']}")
            st, accounts = http(f"{API_BASE}/api/workspaces/{workspace}/accounts", headers=auth)
            acct = next((a for a in accounts if a["id"] == account_id), None)
            closing = [b for b in (acct or {}).get("balances", []) if b["kind"] == "statement_closing"]
            check(f"{path.stem}: latest statement closing balance recorded", bool(closing) and (not exp or closing[0]["amount_minor"] == exp["closing_balance_minor"]) and closing[0]["provenance"] == "bank_observed", str(closing[0] if closing else None)[:100])
            activity = sum(t["amount_minor"] for t in posted)
            if exp and expected.get("files"):
                opening_first = exp["opening_balance_minor"] if args.cross_format else expected["files"][0]["opening_balance_minor"]
                check(f"{path.stem}: opening plus all posted activity equals closing", opening_first + activity == exp["closing_balance_minor"], f"{opening_first} + {activity} = {opening_first + activity}")
            newest = sorted(posted, key=lambda t: (t["posted_date"], t["id"]))[-1]
            st, evidence = http(f"{API_BASE}/api/workspaces/{workspace}/transactions/{newest['id']}", headers=auth)
            src = (evidence.get("sources") or [{}])[0] if st == 200 else {}
            check(f"{path.stem}: newest transaction's evidence is its import row", src.get("kind") == "import_row" and src.get("file_label") == path.name, str(src)[:80])
        for path in cross_second:
            data = path.read_bytes()
            st, created = http(imports, "POST", {"account_id": account_id, "file_label": path.name, "declared_bytes": len(data)}, auth)
            up, import_id = created["upload"], created["import"]["id"]
            http(f"{base}/storage/v1/object/upload/sign/{up['bucket']}/{up['path']}?token={up['token']}", "PUT", raw=data, headers={"apikey": anon, "Content-Type": "application/pdf" if path.suffix == ".pdf" else "text/csv"})
            st, staged = http(f"{imports}/{import_id}/uploaded", "POST", {}, auth)
            if st != 200 or staged.get("status") != "staged":
                raise RuntimeError(f"{path.name}: cross-format stage {st} {staged}")
            s = staged["summary"]
            before = len([t for t in all_transactions(workspace, auth) if t["status"] == "posted"])
            check(f"cross-format {path.stem}: every row is a duplicate candidate needing a decision", s["flag_counts"].get("duplicate_candidate") == s["row_count"] and s["overlap"]["undecided_rows"] == s["row_count"] and s["overlap"]["likely_duplicate_statement"] is True, str(s.get("overlap")))
            st, refused = http(f"{imports}/{import_id}/confirm", "POST", {"revision": staged["draft_revision"]}, auth)
            check(f"cross-format {path.stem}: confirmation refused until the owner decides", st == 422 and isinstance(refused, dict) and refused.get("detail", {}).get("kind") == "undecided", f"HTTP {st}")
            st, decided = http(f"{imports}/{import_id}/decisions", "POST", {"decision": "link_existing"}, auth)
            check(f"cross-format {path.stem}: one action links every matched row", st == 200 and decided["updated"] == s["row_count"] and decided["skipped_without_target"] == 0, f"HTTP {st} {decided if st != 200 else ''}")
            st, result = http(f"{imports}/{import_id}/confirm", "POST", {"revision": decided["revision"]}, auth)
            if st != 200 or not isinstance(result, dict):
                raise RuntimeError(f"{path.name}: cross-format confirm HTTP {st}: {str(result)[:200]}")
            after = len([t for t in all_transactions(workspace, auth) if t["status"] == "posted"])
            check(f"cross-format {path.stem}: confirmation adds evidence, not transactions", result["created"] == 0 and result["linked"] == s["row_count"] and after == before, f"created={result['created']} linked={result['linked']} posted {before}->{after}")
            txs_now = all_transactions(workspace, auth)
            sample = next(t for t in txs_now if t["posted_date"] == s["period_start"])
            st, evidence = http(f"{API_BASE}/api/workspaces/{workspace}/transactions/{sample['id']}", headers=auth)
            formats = sorted({src.get("format") for src in evidence.get("sources", [])}) if st == 200 else []
            check(f"cross-format {path.stem}: the transaction now shows both files as evidence", formats == ["csv", "pdf"], str(formats))
        # month totals across everything imported
        txs = all_transactions(workspace, auth)
        by_month: dict[str, dict[str, int]] = defaultdict(lambda: {"credits": 0, "debits": 0, "count": 0})
        for t in txs:
            if t["status"] != "posted":
                continue
            m = by_month[t["posted_date"][:7]]
            m["count"] += 1
            if t["amount_minor"] > 0:
                m["credits"] += t["amount_minor"]
            else:
                m["debits"] -= t["amount_minor"]
        covered = set()
        for path in files:
            exp = expected_files.get(path.stem, {})
            if exp.get("period_start") and exp.get("period_end"):
                y0, m0 = int(exp["period_start"][:4]), int(exp["period_start"][5:7])
                y1, m1 = int(exp["period_end"][:4]), int(exp["period_end"][5:7])
                y, m = y0, m0
                while (y, m) <= (y1, m1):
                    covered.add(f"{y:04d}-{m:02d}")
                    m += 1
                    if m > 12:
                        y, m = y + 1, 1
        for month in expected.get("months", []):
            if month["month"] not in covered:
                continue
            got = by_month.get(month["month"], {})
            check(f"month {month['month']}: credits, debits and count", got.get("credits") == month["credits_minor"] and got.get("debits") == month["debits_minor"] and got.get("count") == month["posted_count"], str(got))
        # replay the last file: must not double-count
        last = files[-1]
        data = last.read_bytes()
        st, created = http(imports, "POST", {"account_id": account_id, "file_label": last.name, "declared_bytes": len(data)}, auth)
        if st != 201 or not isinstance(created, dict):
            raise RuntimeError(f"replay create HTTP {st}: {str(created)[:200]}")
        up = created["upload"]
        http(f"{base}/storage/v1/object/upload/sign/{up['bucket']}/{up['path']}?token={up['token']}", "PUT", raw=data, headers={"apikey": anon, "Content-Type": "application/pdf" if last.suffix == ".pdf" else "text/csv"})
        st, replay = http(f"{imports}/{created['import']['id']}/uploaded", "POST", {}, auth)
        after = len([t for t in all_transactions(workspace, auth) if t["status"] == "posted"])
        check("re-uploading the last file is refused as an exact replay", st == 200 and replay.get("status") == "cancelled" and replay.get("duplicate_of") and after == len([t for t in txs if t["status"] == "posted"]), f"{replay.get('status')} posted={after}")
        st, workspace_view = http(f"{API_BASE}/api/private/workspace", headers=auth)
        check("workspace reports every confirmed import", st == 200 and workspace_view["statements"]["count"] == len(files) + len(cross_second), str(workspace_view.get("statements", {}).get("count")))
    except RuntimeError as exc:
        check("aborted", False, str(exc))
    finally:
        if args.keep:
            print(f"kept: user {user_id} business {business_id}")
        else:
            if business_id:
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
                st, rows = http(f"{base}/rest/v1/businesses?id=eq.{business_id}&select=id", headers=admin_h)
                check("temporary business deleted", rows == [], f"{len(rows) if isinstance(rows, list) else rows} left")
            if user_id:
                http(f"{base}/auth/v1/admin/users/{user_id}", "DELETE", None, admin_h)
                st, remaining = http(f"{base}/auth/v1/admin/users?page=1&per_page=100", headers=admin_h)
                check("temporary user deleted", not any(u["id"] == user_id for u in (remaining or {}).get("users", [])))
    failures = sum(1 for _, ok, _ in results if not ok)
    print(f"{len(results) - failures}/{len(results)} statement pack checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
