#!/usr/bin/env python3
"""Upload quotas against the development project and the real routes.

Two temporary users, one of whom owns two businesses, so the check can prove that quotas are
counted per user across their businesses and that another user is unaffected. It drives the
real create route and the real reservation function:

  * active slots are held by imports awaiting upload or review, across every business;
  * two concurrent creations cannot both take the last slot;
  * the rolling start counters refuse at their limit and explain the recovery;
  * outstanding bytes count a file whose deletion failed, because the object still exists;
  * a terminal import releases its slot, and deleting the object releases its bytes;
  * an exact replay does not consume a second start;
  * cancelling does not erase initiation history, so it cannot reset the rate limit;
  * deleting a business does not reset it either.

Everything is created and removed by this check. The owner's businesses, their pending import
and the shared demos are read before and after and must not change. No cleanup run is invoked.

    API_BASE=http://127.0.0.1:8000 services/api/.venv/bin/python db/checks/quota_check.py
"""

import json
import os
import re
import secrets
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "services" / "api" / ".env"
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_BYPASS = os.environ.get("API_BYPASS", "").strip()
CLIENT = {"x-cashlens-client": "web"}
results: list[tuple[str, bool, str]] = []

CSV = (
    b"date,description,amount,balance,transaction_id\n"
    b"2026-05-01,LEASE PAYMENT,-3500.00,26500.00,q-1\n"
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
    base = env("SUPABASE_URL")
    admin, anon = env("SUPABASE_SERVICE_ROLE_KEY"), env("SUPABASE_ANON_KEY")
    admin_h = {"apikey": admin, "Authorization": f"Bearer {admin}"}
    rpc = f"{base}/rest/v1/rpc/"
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
            email, password = f"quota-{label}-{tag}@example.org", secrets.token_urlsafe(18)
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
        st, one = http(
            f"{API_BASE}/api/private/provision", "POST", {"business_name": f"Quota one {tag}"}, a["auth"]
        )
        st, two = http(
            f"{API_BASE}/api/private/businesses",
            "POST",
            {"name": f"Quota two {tag}", "idempotency_key": f"two-{tag}"},
            a["auth"],
        )
        st, three = http(
            f"{API_BASE}/api/private/businesses",
            "POST",
            {"name": f"Quota three {tag}", "idempotency_key": f"three-{tag}"},
            a["auth"],
        )
        st, other = http(
            f"{API_BASE}/api/private/provision", "POST", {"business_name": f"Quota other {tag}"}, b["auth"]
        )
        ws = [one["workspace_id"], two["workspace_id"], three["workspace_id"]]
        businesses += [one["business"]["id"], two["business"]["id"], three["business"]["id"],
                       other["business"]["id"]]

        def create(workspace: str, auth: dict, declared: int = len(CSV), label: str = "q.csv"):
            return http(
                f"{API_BASE}/api/workspaces/{workspace}/imports",
                "POST",
                {
                    "new_account": {"name": "Checking", "account_type": "checking", "mask": "6812"},
                    "file_label": label,
                    "declared_bytes": declared,
                },
                auth,
            )

        def upload(made: dict, data: bytes = CSV) -> None:
            up = made["upload"]
            http(
                f"{base}/storage/v1/object/upload/sign/{up['bucket']}/{up['path']}?token={up['token']}",
                "PUT",
                raw=data,
                headers={"apikey": anon, "Content-Type": "text/csv"},
            )

        # ---- active slots span the caller's businesses ----
        made = []
        for i, workspace in enumerate(ws):
            st, result = create(workspace, a["auth"], label=f"q{i}.csv")
            made.append((st, result))
        check(
            "three active imports across three businesses are allowed",
            all(st == 201 for st, _ in made),
            str([st for st, _ in made]),
        )
        st, fourth = http(
            f"{API_BASE}/api/workspaces/{ws[0]}/imports",
            "POST",
            {"account_id": None, "new_account": {"name": "Second", "account_type": "checking"},
             "file_label": "q4.csv", "declared_bytes": len(CSV)},
            a["auth"],
        )
        check(
            "a fourth is refused by the per-business rule before the quota",
            st == 409,
            f"HTTP {st}",
        )
        st, usage = http(rpc + "import_quota_usage", "POST", {"p_user_id": a["id"]}, admin_h)
        row = usage[0] if isinstance(usage, list) else usage
        check(
            "the caller's usage counts all three businesses",
            row["active_count"] == 3 and row["started_day"] == 3,
            f"active={row['active_count']} started={row['started_day']}",
        )
        st, isolated = http(rpc + "import_quota_usage", "POST", {"p_user_id": b["id"]}, admin_h)
        other_row = isolated[0] if isinstance(isolated, list) else isolated
        check(
            "another user is unaffected",
            other_row["active_count"] == 0 and other_row["started_day"] == 0,
            f"active={other_row['active_count']}",
        )

        # ---- the last slot cannot be taken twice ----
        st, _ = http(
            f"{API_BASE}/api/workspaces/{ws[2]}/imports/{made[2][1]['import']['id']}/cancel",
            "POST",
            None,
            a["auth"],
        )
        outcomes: list = []
        lock = threading.Lock()

        def race() -> None:
            new_id = secrets.token_hex(4) + "-0000-4000-8000-" + secrets.token_hex(6)
            st, res = http(
                rpc + "reserve_and_create_import",
                "POST",
                {
                    "p_user_id": a["id"],
                    "p_business_id": businesses[2],
                    "p_import_id": new_id,
                    "p_account_id": None,
                    "p_proposed_account": {"name": "Race", "account_type": "checking"},
                    "p_object_path": f"{businesses[2]}/{new_id}.csv",
                    "p_file_label": "race.csv",
                    "p_declared_bytes": 10,
                    "p_expires_at": "2099-01-01T00:00:00+00:00",
                    "p_max_active": 3,
                    "p_max_per_hour": 100,
                    "p_max_per_day": 100,
                    "p_max_outstanding_bytes": 50 * 1024 * 1024,
                },
                admin_h,
            )
            with lock:
                outcomes.append((st, res))

        threads = [threading.Thread(target=race) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        granted = [r for st, r in outcomes if st == 200 and r and r[0].get("allowed")]
        check(
            "two concurrent reservations cannot both take the last slot",
            len(granted) == 1,
            f"{len(granted)} of {len(outcomes)} allowed",
        )

        # ---- outstanding bytes hold while the object exists ----
        upload(made[0][1])
        import_zero = made[0][1]["import"]["id"]
        st, usage_pre = http(rpc + "import_quota_usage", "POST", {"p_user_id": a["id"]}, admin_h)
        active_pre = (usage_pre[0] if isinstance(usage_pre, list) else usage_pre)["active_count"]
        st, staged = http(
            f"{API_BASE}/api/workspaces/{ws[0]}/imports/{import_zero}/uploaded", "POST", None, a["auth"]
        )
        st, detail = http(f"{API_BASE}/api/workspaces/{ws[0]}/imports/{import_zero}", headers=a["auth"])
        st, done = http(
            f"{API_BASE}/api/workspaces/{ws[0]}/imports/{import_zero}/confirm",
            "POST",
            {"revision": detail["draft_revision"]},
            a["auth"],
        )
        check("a confirmed import releases its active slot", st == 200, f"HTTP {st}")
        st, usage = http(rpc + "import_quota_usage", "POST", {"p_user_id": a["id"]}, admin_h)
        row = usage[0] if isinstance(usage, list) else usage
        check(
            "and its bytes are released once the file is gone",
            row["active_count"] == active_pre - 1 and row["outstanding_bytes"] < len(CSV) * 3,
            f"active {active_pre} -> {row['active_count']} bytes={row['outstanding_bytes']}",
        )
        # a failed removal keeps the reservation while the object still exists
        http(
            f"{base}/rest/v1/document_imports?id=eq.{import_zero}",
            "PATCH",
            {"object_deleted_at": None},
            admin_h,
        )
        st, usage = http(rpc + "import_quota_usage", "POST", {"p_user_id": a["id"]}, admin_h)
        row = usage[0] if isinstance(usage, list) else usage
        check(
            "a file whose removal failed keeps holding its bytes",
            row["outstanding_bytes"] >= len(CSV),
            f"bytes={row['outstanding_bytes']}",
        )

        # ---- initiation history survives cancellation and business deletion ----
        st, usage_before = http(rpc + "import_quota_usage", "POST", {"p_user_id": a["id"]}, admin_h)
        started_before = (usage_before[0] if isinstance(usage_before, list) else usage_before)[
            "started_day"
        ]
        st, _ = http(
            f"{API_BASE}/api/workspaces/{ws[1]}/imports/{made[1][1]['import']['id']}/cancel",
            "POST",
            None,
            a["auth"],
        )
        st, usage_after = http(rpc + "import_quota_usage", "POST", {"p_user_id": a["id"]}, admin_h)
        started_after = (usage_after[0] if isinstance(usage_after, list) else usage_after)[
            "started_day"
        ]
        check(
            "cancelling does not erase initiation history",
            started_after == started_before,
            f"{started_before} -> {started_after}",
        )
        st, _ = http(
            f"{API_BASE}/api/private/businesses/{businesses[2]}/deletion",
            "POST",
            {"confirm_name": f"Quota three {tag}"},
            a["auth"],
        )
        st, usage_deleted = http(rpc + "import_quota_usage", "POST", {"p_user_id": a["id"]}, admin_h)
        started_deleted = (usage_deleted[0] if isinstance(usage_deleted, list) else usage_deleted)[
            "started_day"
        ]
        check(
            "deleting a business does not reset the rate limit",
            started_deleted == started_before,
            f"{started_before} -> {started_deleted}",
        )

        # ---- an exact replay does not reserve twice ----
        st, replay = create(ws[0], a["auth"], label="replay.csv")
        if st == 201:
            upload(replay)
            st, outcome = http(
                f"{API_BASE}/api/workspaces/{ws[0]}/imports/{replay['import']['id']}/uploaded",
                "POST",
                None,
                a["auth"],
            )
            st, usage_replay = http(
                rpc + "import_quota_usage", "POST", {"p_user_id": a["id"]}, admin_h
            )
            started_replay = (
                usage_replay[0] if isinstance(usage_replay, list) else usage_replay
            )["started_day"]
            # The replay releases its active slot and its bytes through the cancellation, but
            # keeps its initiation: refunding it would let a known duplicate be re-uploaded in
            # a loop to farm the rate limit back to zero.
            check(
                "an exact replay is cancelled but keeps its initiation",
                outcome.get("duplicate_of") is not None and started_replay == started_before + 1,
                f"duplicate={outcome.get('duplicate_of') is not None} started={started_replay}",
            )
        else:
            check("an exact replay is cancelled but keeps its initiation", False, f"create HTTP {st}")

        # ---- the rate limit refuses with an explanation ----
        st, refused = http(
            rpc + "reserve_import_slot",
            "POST",
            {
                "p_user_id": a["id"],
                "p_business_id": businesses[0],
                "p_import_id": "11111111-1111-4111-8111-11111111aaaa",
                "p_declared_bytes": 10,
                "p_max_active": 99,
                "p_max_per_hour": 1,
                "p_max_per_day": 99,
                "p_max_outstanding_bytes": 50 * 1024 * 1024,
            },
            admin_h,
        )
        entry = refused[0] if isinstance(refused, list) else refused
        check(
            "the hourly limit refuses and names the reason",
            entry["allowed"] is False and entry["reason"] == "rate_hour",
            str(entry.get("reason")),
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
    print(f"{len(results) - failures}/{len(results)} quota checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
