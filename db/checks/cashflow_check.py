#!/usr/bin/env python3
"""End-to-end cash-flow classification against the development project through the running API.

What the unit tests cannot answer, because they run against an in-memory stand-in and a scripted
model, is whether the thing works against the real database and the real model:

- does the parser read the uploaded file's sign convention correctly, so a payment out is an
  outflow rather than an inflow;
- does `import_rows.cashflow` exist, and do the PostgREST `is.null` and `not.is.null` filters the
  resume path depends on actually behave as the code assumes;
- does the model, given this prompt and real transaction names, answer for every id it was given
  and stay inside the buckets for each row's direction;
- does the export come back as the original file with the classification appended.

This makes a small number of real, paid model calls, bounded by `--rows` (25 by default, one
batch). It creates one temporary user and one temporary business and deletes both at the end;
no other account, business or import is listed, changed or deleted.

    API_BASE=http://127.0.0.1:8000 services/api/.venv/bin/python db/checks/cashflow_check.py \\
        --csv /path/to/statement.csv --rows 25

With no --csv it uses a small synthetic file in the same shape: outflows positive, with a
balance that moves once a day rather than once a transaction.
"""

import argparse
import csv
import io
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "services" / "api" / ".env"
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_BYPASS = os.environ.get("API_BYPASS", "").strip()
CLIENT = {"x-cashlens-client": "web"}
results: list[tuple[str, bool, str]] = []

INFLOW = {"Organic revenue", "Internal transfers", "Loans income", "Personal income", "Overdraft/Reversals", "Unclassified"}
OUTFLOW = {"Operating expense", "Transfer out", "Loan payments", "Personal expense", "Payroll", "Unclassified"}

# Outflows positive, closing balance repeated across each day, which is the shape that used to
# defeat the sign check. Six days so the day-grouped verdict has enough to agree on.
SYNTHETIC = b"""Date,Transaction ID,Transaction Name,Merchant Name,Amount,Categories,Current Balance,Calculated Daily Balance
2026-01-01,s1,US FOODS INC ACH,US Foods,1309.92,"Food and Drink, Groceries",9000.00,7690.08
2026-01-01,s2,ATM Deposit Main St,,-790.00,"Transfer, Deposit",9000.00,7690.08
2026-01-02,s3,Zelle Instant Pmt To Marco Diaz kitchen help,Marco Diaz,300.00,"Transfer, Withdrawal",9000.00,7390.08
2026-01-03,s4,SQUARE INC PAYOUT,Square,-2500.00,"Transfer, Deposit",9000.00,9890.08
2026-01-04,s5,POWER FUNDING SOLUTIONS ACH,Power Funding,250.00,"Payment, Loan",9000.00,9640.08
2026-01-05,s6,BGE UTILITY PAYMENT,BGE,184.22,"Service, Utilities",9000.00,9455.86
2026-01-06,s7,CHECK 2041,,125.00,"Payment, Check",9000.00,9330.86
"""


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
    print(f"{'PASS' if ok else 'FAIL'}  {name:<58} {detail}")


def http(url: str, method: str = "GET", body=None, headers=None, raw: bytes | None = None, timeout: int = 120):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"User-Agent": "cashlens-check", **(headers or {})}
    if API_BYPASS and url.startswith(API_BASE):
        h["x-vercel-protection-bypass"] = API_BYPASS
    if raw is None and body is not None:
        h["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read()
            ct = response.headers.get("content-type", "")
            return response.status, (json.loads(text) if text and "json" in ct else text)
    except urllib.error.HTTPError as exc:
        text = exc.read()
        try:
            return exc.code, json.loads(text)
        except Exception:
            return exc.code, text[:300]


def sample(data: bytes, rows: int) -> bytes:
    """The header plus a contiguous run of `rows` rows, to bound what one run spends.

    Contiguous, not spread. The file is a statement, so its rows belong to days, and a balance
    that moves once a day is only checkable against consecutive days. Taking every Nth row keeps
    the row count but destroys the days, and the parser would then quite correctly report that it
    could not verify the sign, of a file that has the evidence in it. So the window is whole and
    has to be wide enough to hold several days: with a handful of transactions a day, sixty rows
    is about a week.
    """
    reader = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    header, body = reader[0], reader[1 : 1 + rows]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerows([header, *body])
    return out.getvalue().encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=None, help="a CSV to classify; synthetic if omitted")
    parser.add_argument("--rows", type=int, default=60, help="how many rows to send, bounding the spend")
    args = parser.parse_args()

    source = args.csv.read_bytes() if args.csv else SYNTHETIC
    data = sample(source, args.rows)
    label = (args.csv.name if args.csv else "synthetic.csv")
    print(f"{len(data.splitlines()) - 1} rows from {label}\n")

    base, admin, anon = env("SUPABASE_URL"), env("SUPABASE_SERVICE_ROLE_KEY"), env("SUPABASE_ANON_KEY")
    admin_h = {"apikey": admin, "Authorization": f"Bearer {admin}"}
    tag = secrets.token_hex(3)
    user_id = business_id = None
    try:
        email, password = f"cashflowcheck-{tag}@example.org", secrets.token_urlsafe(18)
        st, created = http(f"{base}/auth/v1/admin/users", "POST", {"email": email, "password": password, "email_confirm": True}, admin_h)
        if st not in (200, 201):
            raise RuntimeError(f"could not create the temporary user: {st} {created}")
        user_id = created["id"]
        st, session = http(f"{base}/auth/v1/token?grant_type=password", "POST", {"email": email, "password": password}, {"apikey": anon})
        auth = {"authorization": f"Bearer {session['access_token']}", **CLIENT}
        st, prov = http(f"{API_BASE}/api/private/provision", "POST", {"business_name": f"Cash flow check {tag}"}, auth)
        if st != 200:
            raise RuntimeError(f"provision: {st} {prov}")
        business_id, workspace = prov["business"]["id"], prov["workspace_id"]
        imports = f"{API_BASE}/api/workspaces/{workspace}/imports"

        # ---- upload and stage through the real flow ----
        st, made = http(imports, "POST", {"new_account": {"name": "Operating Checking", "account_type": "checking", "mask": "6812"}, "file_label": label, "declared_bytes": len(data)}, auth)
        if st != 201:
            raise RuntimeError(f"create import: {st} {made}")
        upload, import_id = made["upload"], made["import"]["id"]
        http(f"{base}/storage/v1/object/upload/sign/{upload['bucket']}/{upload['path']}?token={upload['token']}", "PUT", raw=data, headers={"apikey": anon, "Content-Type": "text/csv"})
        st, staged = http(f"{imports}/{import_id}/uploaded", "POST", {}, auth)
        if st != 200:
            raise RuntimeError(f"stage: {st} {staged}")
        row_count = staged["summary"]["row_count"]
        check("every_row_staged", row_count == len(data.splitlines()) - 1, f"{row_count} rows")

        st, detail = http(f"{imports}/{import_id}", headers=auth)
        mapping = detail.get("mapping") or {}
        convention = mapping.get("sign_convention")
        flags = mapping.get("flags") or []
        check("sign_convention_read_from_the_file", convention == "signed_outflow_positive" and "sign_unverified" not in flags, f"{convention} flags={flags}")
        check("description_column_is_a_name_not_an_id", (mapping.get("columns") or {}).get("description") == "Transaction Name", str((mapping.get("columns") or {}).get("description")))

        # ---- classify for real ----
        started = time.monotonic()
        spent = 0
        for _ in range(20):
            st, progress = http(f"{imports}/{import_id}/cashflow", "POST", {}, auth, timeout=180)
            if st != 200:
                raise RuntimeError(f"classify: {st} {progress}")
            spent += progress["cost_micro"]
            print(f"      {progress['classified']}/{progress['total']} classified, {progress['batches']} batches, stopped={progress['stopped']}")
            if progress["done"] or progress["stopped"] or progress["batches"] == 0:
                break
        elapsed = time.monotonic() - started
        check("every_row_classified", progress["done"] and progress["classified"] == row_count, f"{progress['classified']}/{row_count} in {elapsed:.1f}s, ${spent / 1_000_000:.4f}")

        # ---- what came back ----
        rows, after = [], None
        while True:
            suffix = f"&after={after}" if after is not None else ""
            st, page = http(f"{imports}/{import_id}/cashflow?limit=200{suffix}", headers=auth)
            rows.extend(page["items"])
            after = page["next_after"]
            if after is None:
                break
        check("nothing_lost_between_upload_and_review", len(rows) == row_count, f"{len(rows)} rows")

        original = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        by_id = {r["transaction_id"]: r for r in rows}
        wrong_direction = [
            r["Transaction ID"]
            for r in original
            if (float(r["Amount"]) > 0) != (by_id.get(r["Transaction ID"], {}).get("direction") == "outflow")
        ]
        check("positive_is_outflow_for_every_row", not wrong_direction, f"{len(wrong_direction)} wrong" + (f" e.g. {wrong_direction[:3]}" if wrong_direction else ""))

        mismatched = [r for r in rows if r["category"] not in (INFLOW if r["direction"] == "inflow" else OUTFLOW)]
        check("no_category_belongs_to_the_other_direction", not mismatched, f"{len(mismatched)} mismatched")
        check("every_row_has_a_reason", all((r["reason"] or "").strip() for r in rows), f"{sum(1 for r in rows if not (r['reason'] or '').strip())} missing")
        placed = [r for r in rows if r["category"] != "Unclassified"]
        check("most_rows_were_actually_placed", len(placed) >= max(1, int(row_count * 0.7)), f"{len(placed)}/{row_count} placed")

        buckets: dict[str, int] = {}
        for row in rows:
            buckets[row["category"]] = buckets.get(row["category"], 0) + 1
        print("      " + ", ".join(f"{k} {v}" for k, v in sorted(buckets.items(), key=lambda kv: -kv[1])))

        st, summary = http(f"{imports}/{import_id}/cashflow/summary", headers=auth)
        expected_in = sum(-int(round(float(r["Amount"]) * 100)) for r in original if float(r["Amount"]) < 0)
        expected_out = sum(int(round(float(r["Amount"]) * 100)) for r in original if float(r["Amount"]) > 0)
        check("summary_totals_match_the_file", summary["inflow_minor"] == expected_in and summary["outflow_minor"] == expected_out, f"in {summary['inflow_minor']} vs {expected_in}, out {summary['outflow_minor']} vs {expected_out}")

        # ---- a correction, and the direction rule applied to a person ----
        target = next(r for r in rows if r["direction"] == "outflow")
        st, refused = http(f"{imports}/{import_id}/cashflow/{target['row_index']}", "PATCH", {"category": "Organic revenue"}, auth)
        check("a_person_cannot_cross_the_direction_either", st == 422, f"HTTP {st}")
        st, corrected = http(f"{imports}/{import_id}/cashflow/{target['row_index']}", "PATCH", {"category": "Personal expense"}, auth)
        check("a_correction_is_stored_and_marked_manual", st == 200 and corrected["category"] == "Personal expense" and corrected["source"] == "manual", f"HTTP {st}")

        # ---- repeat, then export ----
        st, again = http(f"{imports}/{import_id}/cashflow", "POST", {}, auth)
        check("a_repeat_run_spends_nothing", st == 200 and again["batches"] == 0 and again["done"], f"batches={again.get('batches')}")

        st, exported = http(f"{imports}/{import_id}/cashflow/export", headers={**auth, "accept": "text/csv"})
        text = exported.decode() if isinstance(exported, bytes) else str(exported)
        out_rows = list(csv.DictReader(io.StringIO(text)))
        check("export_has_a_row_for_every_original_row", len(out_rows) == row_count, f"{len(out_rows)} rows")
        unchanged = all(all(after_row[column] == before_row[column] for column in before_row) for before_row, after_row in zip(original, out_rows))
        check("every_original_column_and_value_survives", unchanged and all(c in (out_rows[0] if out_rows else {}) for c in original[0]), "byte-for-byte per cell")
        added = ["Cash Flow Direction", "Cash Flow Category", "Classification Confidence", "Classification Reason", "Classification Source", "Reviewed"]
        check("export_adds_the_classification_columns", all(c in (out_rows[0] if out_rows else {}) for c in added), ", ".join(added))
        corrected_out = out_rows[target["row_index"]]
        check("export_uses_the_corrected_value", corrected_out["Cash Flow Category"] == "Personal expense" and corrected_out["Classification Source"] == "Manual" and corrected_out["Reviewed"] == "Yes", corrected_out["Cash Flow Category"])

        # A sample, so a person reading the output can judge the answers rather than trust a count.
        print("\n      sample answers")
        for row in rows[:8]:
            print(f"      {row['direction']:<7} {row['amount_minor'] / 100:>10.2f}  {(row['name'] or '')[:36]:<36} {row['category']:<18} {row['confidence']:<6} {row['reason'][:60]}")

        http(f"{imports}/{import_id}/cancel", "POST", {}, auth)
    finally:
        if business_id:
            st, imps = http(f"{base}/rest/v1/document_imports?business_id=eq.{business_id}&select=id", headers=admin_h)
            for one in imps or []:
                http(f"{base}/rest/v1/import_rows?import_id=eq.{one['id']}", "DELETE", None, admin_h)
            st, txs = http(f"{base}/rest/v1/transactions?business_id=eq.{business_id}&select=id", headers=admin_h)
            for one in txs or []:
                http(f"{base}/rest/v1/transaction_sources?transaction_id=eq.{one['id']}", "DELETE", None, admin_h)
            http(f"{base}/rest/v1/transactions?business_id=eq.{business_id}", "DELETE", None, admin_h)
            st, objects = http(f"{base}/storage/v1/object/list/statements", "POST", {"prefix": business_id, "limit": 100}, admin_h)
            if st == 200 and objects:
                http(f"{base}/storage/v1/object/statements", "DELETE", {"prefixes": [f"{business_id}/{o['name']}" for o in objects]}, admin_h)
            http(f"{base}/rest/v1/businesses?id=eq.{business_id}&data_mode=eq.private", "DELETE", None, admin_h)
            st, left = http(f"{base}/rest/v1/businesses?id=eq.{business_id}&select=id", headers=admin_h)
            check("temporary_business_deleted", not left, f"{len(left or [])} left")
        if user_id:
            http(f"{base}/auth/v1/admin/users/{user_id}", "DELETE", None, admin_h)
            st, remaining = http(f"{base}/auth/v1/admin/users?page=1&per_page=100", headers=admin_h)
            stale = [x for x in (remaining or {}).get("users", []) if tag in (x.get("email") or "")]
            check("temporary_user_deleted", not stale, f"{len(stale)} left")

    failures = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{len(results) - failures}/{len(results)} cash flow checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
