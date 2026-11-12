#!/usr/bin/env python3
"""Cash analysis against the development project, over the whole statement pack.

Creates one temporary user with one private business, imports the five pack CSVs into it, and
then asks the real analysis routes the questions the pack was built to answer:

  * May: supplier payments rise while customer receipts stay flat;
  * June: the equipment purchase lands and receipts fall;
  * July: financing lifts cash even though receipts are weaker still;
  * August: receipts recover.

It also checks that an earlier analysis date cannot see later months, that another user's
workspace is refused, and that a newly confirmed import changes the answer straight away.

The reviewer-only expectations file is read here as TEST EXPECTATIONS ONLY. It is never given
to the application, which sees nothing but the statements themselves.

Everything this check creates is deleted at the end. The owner's businesses and their pending
import are read before and after and must not change.

    API_BASE=http://127.0.0.1:8000 PACK=/path/to/CashLens-Trend-Test-Pack \\
      services/api/.venv/bin/python db/checks/analysis_check.py
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
PACK = Path(os.environ.get("PACK", "")).expanduser()
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
        with urllib.request.urlopen(request, timeout=180) as response:
            text = response.read()
            ct = response.headers.get("content-type", "")
            return response.status, (json.loads(text) if text and "json" in ct else text)
    except urllib.error.HTTPError as exc:
        text = exc.read()
        try:
            return exc.code, json.loads(text)
        except Exception:
            return exc.code, text[:200]


def month(analysis: dict, key: str) -> dict | None:
    return next((m for m in analysis["months"] if m["month"] == key), None)


def category(analysis: dict, key: str) -> dict | None:
    return next((c for c in analysis["categories"] if c["key"] == key), None)


def main() -> int:
    if not PACK.exists():
        sys.exit("PACK must point at the extracted CashLens-Trend-Test-Pack")
    expected = json.loads((PACK / "reviewer-only" / "expected-results.json").read_text())
    csv_dir = PACK / "upload" / "csv"
    base = env("SUPABASE_URL")
    admin, anon = env("SUPABASE_SERVICE_ROLE_KEY"), env("SUPABASE_ANON_KEY")
    admin_h = {"apikey": admin, "Authorization": f"Bearer {admin}"}
    tag = secrets.token_hex(3)
    users: dict[str, dict] = {}
    businesses: list[str] = []

    def snapshot() -> dict:
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

    before = snapshot()
    try:
        for label in ("a", "b"):
            email, password = f"analysis-{label}-{tag}@example.org", secrets.token_urlsafe(18)
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
        st, prov = http(
            f"{API_BASE}/api/private/provision", "POST", {"business_name": f"Analysis {tag}"}, a["auth"]
        )
        workspace, business = prov["workspace_id"], prov["business"]["id"]
        businesses.append(business)
        st, other = http(
            f"{API_BASE}/api/private/provision", "POST", {"business_name": f"Other {tag}"}, b["auth"]
        )
        businesses.append(other["business"]["id"])
        imports_url = f"{API_BASE}/api/workspaces/{workspace}/imports"
        analysis_url = f"{API_BASE}/api/workspaces/{workspace}/analysis"

        account_id = None
        files = sorted(csv_dir.glob("*.csv"))
        for index, path in enumerate(files):
            body: dict = {"file_label": path.name[:60], "declared_bytes": path.stat().st_size}
            if account_id:
                body["account_id"] = account_id
            else:
                body["new_account"] = {
                    "name": "Operating",
                    "account_type": "checking",
                    "mask": expected["account_last4"],
                }
            st, made = http(imports_url, "POST", body, a["auth"])
            if st != 201:
                raise RuntimeError(f"create {path.name}: {st} {made}")
            up = made["upload"]
            http(
                f"{base}/storage/v1/object/upload/sign/{up['bucket']}/{up['path']}?token={up['token']}",
                "PUT",
                raw=path.read_bytes(),
                headers={"apikey": anon, "Content-Type": "text/csv"},
            )
            iid = made["import"]["id"]
            http(f"{imports_url}/{iid}/uploaded", "POST", None, a["auth"])
            st, detail = http(f"{imports_url}/{iid}", headers=a["auth"])
            st, done = http(
                f"{imports_url}/{iid}/confirm", "POST", {"revision": detail["draft_revision"]}, a["auth"]
            )
            if st != 200:
                raise RuntimeError(f"confirm {path.name}: {st} {done}")
            account_id = done["import"]["account"]["id"]
            if index == 0:
                # The dashboard must reflect a confirmed import at once, with no reseed.
                st, first = http(analysis_url, headers=a["auth"])
                check(
                    "the dashboard answers as soon as the first import is confirmed",
                    st == 200 and first["coverage"]["posted_count"] == 88,
                    f"posted={first['coverage'].get('posted_count')}",
                )
                after_one = first

        st, full = http(analysis_url, headers=a["auth"])
        check("the analysis reads the whole pack", st == 200, f"HTTP {st}")
        check(
            "a later import changes the answer without a reseed",
            full["coverage"]["posted_count"] == 180
            and full["coverage"]["posted_count"] > after_one["coverage"]["posted_count"],
            f"{after_one['coverage']['posted_count']} -> {full['coverage']['posted_count']}",
        )

        # ---- every month matches the pack's own arithmetic ----
        mismatches = []
        for row in expected["months"]:
            got = month(full, row["month"])
            if not got:
                mismatches.append(f"{row['month']} missing")
            elif (
                got["money_in_minor"] != row["credits_minor"]
                or got["money_out_minor"] != row["debits_minor"]
                or got["posted_count"] != row["posted_count"]
            ):
                mismatches.append(
                    f"{row['month']} in {got['money_in_minor']}/{row['credits_minor']} "
                    f"out {got['money_out_minor']}/{row['debits_minor']}"
                )
        check("every month matches the pack's expected totals", not mismatches, "; ".join(mismatches[:3]))

        # ---- the four trends the pack was built to show ----
        def cat_month(key: str, cat: str) -> int:
            st, scoped = http(f"{analysis_url}?as_of={key}-28", headers=a["auth"])
            rows = {c["key"]: c for c in scoped["categories"]}
            return rows.get(cat, {}).get("outflow_minor", 0) or rows.get(cat, {}).get("inflow_minor", 0)

        months = {row["month"]: row for row in expected["months"]}
        check(
            "May: suppliers rise while customer receipts hold flat",
            months["2026-05"]["supplier_payments_minor"] > months["2026-04"]["supplier_payments_minor"]
            and months["2026-05"]["operating_receipts_minor"]
            == months["2026-04"]["operating_receipts_minor"],
            f"suppliers {months['2026-04']['supplier_payments_minor']} -> "
            f"{months['2026-05']['supplier_payments_minor']}, receipts flat at "
            f"{months['2026-05']['operating_receipts_minor']}",
        )
        june = month(full, "2026-06")
        st, june_only = http(f"{analysis_url}?as_of=2026-06-30", headers=a["auth"])
        june_rows = {c["key"]: c for c in june_only["categories"]}
        st, may_only = http(f"{analysis_url}?as_of=2026-05-31", headers=a["auth"])
        may_rows = {c["key"]: c for c in may_only["categories"]}
        check(
            "June: the equipment purchase appears and receipts fall",
            june_rows.get("equipment", {}).get("outflow_minor", 0)
            > may_rows.get("equipment", {}).get("outflow_minor", 0)
            and june["money_in_minor"] < month(full, "2026-05")["money_in_minor"],
            f"equipment {may_rows.get('equipment', {}).get('outflow_minor', 0)} -> "
            f"{june_rows.get('equipment', {}).get('outflow_minor', 0)}, "
            f"money in {month(full, '2026-05')['money_in_minor']} -> {june['money_in_minor']}",
        )
        st, july_only = http(f"{analysis_url}?as_of=2026-07-31", headers=a["auth"])
        july_rows = {c["key"]: c for c in july_only["categories"]}
        july = month(full, "2026-07")
        financing_in = july_rows.get("financing", {}).get("inflow_minor", 0)
        check(
            "July: financing lifts cash while the month still nets positive",
            financing_in > 0 and july["net_minor"] > 0,
            f"financing in {financing_in}, net {july['net_minor']}",
        )
        check(
            "August: receipts recover on the month before",
            months["2026-08"]["operating_receipts_minor"]
            > months["2026-07"]["operating_receipts_minor"],
            f"{months['2026-07']['operating_receipts_minor']} -> "
            f"{months['2026-08']['operating_receipts_minor']}",
        )

        # ---- an earlier analysis date must not see later data ----
        st, early = http(f"{analysis_url}?as_of=2026-04-30", headers=a["auth"])
        seen = [m["month"] for m in early["months"]]
        check(
            "an earlier analysis date sees no later month",
            seen == ["2026-01", "2026-02", "2026-03", "2026-04"]
            and early["coverage"]["posted_count"] == 88,
            f"{seen} posted={early['coverage']['posted_count']}",
        )
        check(
            "and its forecast starts from the balance known on that date",
            early["forecast"]["available"] is True
            and early["forecast"]["anchor"]["as_of"] <= "2026-04-30"
            and early["forecast"]["anchor"]["is_live"] is False,
            f"anchor {early['forecast']['anchor']['as_of']} live={early['forecast']['anchor']['is_live']}",
        )

        # ---- scenarios move the projection and nothing else ----
        st, before_rows = http(
            f"{base}/rest/v1/transactions?business_id=eq.{business}&select=id", headers=admin_h
        )
        st, cut = http(
            f"{analysis_url}/scenario",
            "POST",
            {"as_of": "2026-08-31", "receipts_change_percent": -25},
            a["auth"],
        )
        check(
            "fewer receipts lower the projection",
            st == 200
            and cut["forecast"]["scenario"]["ending_minor"]
            < cut["forecast"]["baseline"]["ending_minor"],
            f"{cut['forecast']['baseline']['ending_minor']} -> "
            f"{cut['forecast']['scenario']['ending_minor']}",
        )
        st, cost = http(
            f"{analysis_url}/scenario",
            "POST",
            {
                "as_of": "2026-08-31",
                "one_time_expense_minor": 1_500_000,
                "one_time_expense_date": "2026-09-10",
                "one_time_expense_label": "New oven",
            },
            a["auth"],
        )
        check(
            "a one-off cost lowers the projection by exactly its amount",
            cost["forecast"]["difference_minor"] == -1_500_000,
            str(cost["forecast"]["difference_minor"]),
        )
        st, after_rows = http(
            f"{base}/rest/v1/transactions?business_id=eq.{business}&select=id", headers=admin_h
        )
        check(
            "no scenario moved any money",
            len(before_rows or []) == len(after_rows or []) == 180,
            f"{len(before_rows or [])} -> {len(after_rows or [])}",
        )

        # ---- isolation ----
        st, _ = http(analysis_url, headers=b["auth"])
        check("another user cannot read this analysis", st == 404, f"HTTP {st}")
        st, _ = http(analysis_url, headers=CLIENT)
        check("an anonymous caller cannot read it", st in (401, 404), f"HTTP {st}")
        st, rules = http(f"{analysis_url}/rules", headers=a["auth"])
        check(
            "the grouping rules are published to the owner",
            st == 200 and any(r["key"] == "uncategorized" for r in rules["rules"]),
            f"{len(rules.get('rules', []))} rules",
        )
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
                http(f"{base}/rest/v1/import_starts?import_id=eq.{i['id']}", "DELETE", None, admin_h)
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
            http(
                f"{base}/rest/v1/storage_cleanup?business_id=eq.{business_id}", "DELETE", None, admin_h
            )
            http(
                f"{base}/rest/v1/businesses?id=eq.{business_id}&data_mode=eq.private",
                "DELETE",
                None,
                admin_h,
            )
        for u in users.values():
            http(f"{base}/rest/v1/import_starts?user_id=eq.{u['id']}", "DELETE", None, admin_h)
            http(f"{base}/auth/v1/admin/users/{u['id']}", "DELETE", None, admin_h)
        after = snapshot()
        kept = {k: v for k, v in before.items() if k != "_imports"}
        now = {k: v for k, v in after.items() if k != "_imports" and k in kept}
        check("every pre-existing business kept its transactions", kept == now, "counts unchanged")
        check("no pre-existing import changed state", before["_imports"] == after["_imports"], "")
    failures = sum(1 for _, ok, _ in results if not ok)
    print(f"{len(results) - failures}/{len(results)} analysis checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
