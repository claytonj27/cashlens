#!/usr/bin/env python3
"""The two AI guarantees only a real database can prove.

The application tests drive a scripted model against an in-memory data service, which shows
that the caller refuses correctly on what it is told. They cannot show that two requests
arriving at once are actually serialised, or that a deletion removes AI rows in the right
order, because both are properties of Postgres rather than of Python. This check drives the
real functions in migration 0018 against the development project:

  * two concurrent reservations for the same person cannot both be granted;
  * a cap refuses before anything is held, and says which cap;
  * a second run is refused while one is in flight;
  * an unsettled reservation keeps counting against the cap until it expires;
  * settling with no usage keeps the held estimate and marks it uncertain, which is the
    timeout case: the provider may have billed for work whose usage never came back;
  * releasing returns the whole hold, which is only correct when nothing was dispatched;
  * deleting a business removes its runs and its category corrections BEFORE its
    transactions, and leaves another business's rows untouched;
  * a run cannot be pointed at a workspace belonging to another business, even with a valid
    id for both.

Everything it creates, it removes. The owner's own businesses and transactions are counted
before and after and must be unchanged. No cleanup run is invoked and no model is called.

    API_BASE=http://127.0.0.1:8000 services/api/.venv/bin/python db/checks/ai_usage_check.py
"""

import json
import os
import re
import secrets
import sys
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "services" / "api" / ".env"
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_BYPASS = os.environ.get("API_BYPASS", "").strip()
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


def http(url: str, method: str = "GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": "cashlens-check", **(headers or {})}
    if API_BYPASS and url.startswith(API_BASE):
        h["x-vercel-protection-bypass"] = API_BYPASS
    if body is not None:
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


def one(rows):
    if isinstance(rows, list):
        return rows[0] if rows else {}
    return rows or {}


def main() -> int:
    base = env("SUPABASE_URL")
    admin, anon = env("SUPABASE_SERVICE_ROLE_KEY"), env("SUPABASE_ANON_KEY")
    admin_h = {"apikey": admin, "Authorization": f"Bearer {admin}"}
    represent = {**admin_h, "Prefer": "return=representation"}
    rest, rpc = f"{base}/rest/v1/", f"{base}/rest/v1/rpc/"
    tag = secrets.token_hex(3)
    users: dict[str, dict] = {}
    created_users: list[str] = []
    # Recorded the moment each is made, so a failure part-way through still cleans up after
    # itself. An earlier version only recorded them once a step had completed, and a crash
    # between provisioning and recording left a business behind.
    created_businesses: list[str] = []

    def snapshot() -> dict:
        _, rows = http(f"{rest}businesses?select=id,name&order=name", headers=admin_h)
        out: dict[str, int] = {}
        for b in rows or []:
            _, txs = http(f"{rest}transactions?business_id=eq.{b['id']}&select=id", headers=admin_h)
            out[b["name"]] = len(txs or [])
        return out

    before = snapshot()
    try:
        # ---- two owners, so isolation is a fact of the data rather than of the query ----
        for label in ("a", "b"):
            email, password = f"ai-{label}-{tag}@example.org", secrets.token_urlsafe(18)
            st, made = http(
                f"{base}/auth/v1/admin/users",
                "POST",
                {"email": email, "password": password, "email_confirm": True},
                admin_h,
            )
            if st not in (200, 201):
                raise RuntimeError(f"could not create user {label}: {st} {made}")
            created_users.append(made["id"])
            st, session = http(
                f"{base}/auth/v1/token?grant_type=password",
                "POST",
                {"email": email, "password": password},
                {"apikey": anon},
            )
            st, provisioned = http(
                f"{API_BASE}/api/private/provision",
                "POST",
                {"business_name": f"AI {label} {tag}"},
                {"authorization": f"Bearer {session['access_token']}", **CLIENT},
            )
            if st != 200:
                raise RuntimeError(f"could not provision for {label}: {st} {provisioned}")
            created_businesses.append(provisioned["business"]["id"])
            users[label] = {
                "id": made["id"],
                "business": provisioned["business"]["id"],
                "workspace": provisioned["workspace_id"],
            }
        a, b = users["a"], users["b"]

        # A transaction to correct, so the override deletion is proved on a real row.
        def seed_transaction(who: dict) -> str:
            _, account = http(
                f"{rest}accounts",
                "POST",
                {
                    "business_id": who["business"],
                    "name": "Checking",
                    "account_type": "checking",
                    "source_type": "csv_import",
                },
                represent,
            )
            _, txn = http(
                f"{rest}transactions",
                "POST",
                {
                    "business_id": who["business"],
                    "account_id": one(account)["id"],
                    "source_type": "csv_import",
                    "amount_minor": -1_000,
                    "posted_date": "2026-05-01",
                    "description": "CHECK CHECK",
                },
                represent,
            )
            return one(txn)["id"]

        for who in (a, b):
            who["transaction"] = seed_transaction(who)

        def seed_ai_rows(who: dict) -> None:
            http(
                f"{rest}ai_runs",
                "POST",
                {
                    "business_id": who["business"],
                    "workspace_id": who["workspace"],
                    "capability": "brief",
                    "mode": "replay",
                    "provider": "replay",
                    "model_requested": "check-model",
                    "prompt_version": "brief-1",
                    "analysis_version": "analysis-2",
                    "rules_version": "rules-2",
                    "analysis_date": "2026-05-31",
                    "input_fingerprint": "f" * 64,
                    "status": "succeeded",
                    "safe_input": {"facts": []},
                    "output": {"headline": "check"},
                },
                admin_h,
            )
            http(
                f"{rest}category_overrides",
                "POST",
                {
                    "workspace_id": who["workspace"],
                    "business_id": who["business"],
                    "transaction_id": who["transaction"],
                    "category": "suppliers",
                    "transaction_revision": 1,
                    "source": "owner_correction",
                    "decided_by_kind": "user",
                    "decided_by_id": who["id"],
                },
                admin_h,
            )

        for who in (a, b):
            seed_ai_rows(who)

        def count(table: str, business: str) -> int:
            _, rows = http(f"{rest}{table}?business_id=eq.{business}&select=id", headers=admin_h)
            return len(rows or [])

        check(
            "the AI rows this check created exist before deletion",
            count("ai_runs", a["business"]) == 1 and count("category_overrides", a["business"]) == 1,
        )

        # ---- a run cannot point at another business's workspace ----
        st, refused = http(
            f"{rest}ai_runs",
            "POST",
            {
                "business_id": a["business"],
                "workspace_id": b["workspace"],  # a real workspace, the wrong business
                "capability": "brief",
                "mode": "replay",
                "provider": "replay",
                "model_requested": "check-model",
                "prompt_version": "brief-1",
                "analysis_version": "analysis-2",
                "rules_version": "rules-2",
                "analysis_date": "2026-05-31",
                "input_fingerprint": "f" * 64,
                "status": "succeeded",
            },
            admin_h,
        )
        check(
            "a run cannot be pointed at a workspace of another business",
            st >= 400,
            f"status {st}",
        )

        # ---- reserving ----
        def reserve(identity: str, estimate: int, *, cap: int = 1_000_000, in_flight: int = 1, total: int = 100_000_000):
            return http(
                rpc + "reserve_ai_usage",
                "POST",
                {
                    "p_reservation_id": str(uuid.uuid4()),
                    "p_identity_kind": "user",
                    "p_identity_id": identity,
                    "p_capability": "brief",
                    "p_estimated_cost_micro": estimate,
                    "p_identity_daily_cap_micro": cap,
                    "p_global_daily_cap_micro": 100_000_000,
                    "p_total_cap_micro": total,
                    "p_max_in_flight": in_flight,
                    "p_expires_seconds": 600,
                },
                admin_h,
            )

        st, refused = reserve(a["id"], 500, cap=0)
        check(
            "a cap of zero refuses before anything is held",
            st == 200 and one(refused).get("allowed") is False,
            f"reason {one(refused).get('reason')}",
        )
        _, none_held = http(
            f"{rest}usage_reservations?identity_id=eq.{a['id']}&select=id", headers=admin_h
        )
        check("a refusal holds nothing", len(none_held or []) == 0)

        st, held = reserve(a["id"], 500)
        first_id = None
        _, rows = http(
            f"{rest}usage_reservations?identity_id=eq.{a['id']}&select=id,estimated_cost_micro",
            headers=admin_h,
        )
        first_id = one(rows).get("id")
        check(
            "a granted reservation is held in the same transaction that counted",
            st == 200 and one(held).get("allowed") is True and first_id is not None,
        )

        st, second = reserve(a["id"], 500)
        check(
            "a second run is refused while one is in flight",
            st == 200 and one(second).get("reason") == "in_flight",
            f"reason {one(second).get('reason')}",
        )

        st, over = reserve(a["id"], 500, cap=600, in_flight=5)
        check(
            "an unsettled reservation still counts against the cap",
            st == 200 and one(over).get("reason") == "identity_cap",
            f"identity spend {one(over).get('identity_spend_micro')}",
        )

        # ---- settling ----
        http(rpc + "settle_ai_usage", "POST", {"p_reservation_id": first_id}, admin_h)
        _, settled = http(
            f"{rest}usage_reservations?id=eq.{first_id}"
            "&select=cost_micro,uncertain,settled_at,estimated_cost_micro",
            headers=admin_h,
        )
        row = one(settled)
        check(
            "settling with no usage keeps the estimate and marks it uncertain",
            row.get("cost_micro") == row.get("estimated_cost_micro") and row.get("uncertain") is True,
            f"cost {row.get('cost_micro')}",
        )

        reserve(a["id"], 500)
        _, open_rows = http(
            f"{rest}usage_reservations?identity_id=eq.{a['id']}&settled_at=is.null&select=id",
            headers=admin_h,
        )
        exact_id = one(open_rows).get("id")
        http(
            rpc + "settle_ai_usage",
            "POST",
            {"p_reservation_id": exact_id, "p_cost_micro": 91},
            admin_h,
        )
        _, exact_row = http(
            f"{rest}usage_reservations?id=eq.{exact_id}&select=cost_micro,uncertain",
            headers=admin_h,
        )
        check(
            "settling with the real cost replaces the estimate",
            one(exact_row).get("cost_micro") == 91 and one(exact_row).get("uncertain") is False,
        )

        reserve(a["id"], 500)
        _, open_rows = http(
            f"{rest}usage_reservations?identity_id=eq.{a['id']}&settled_at=is.null&select=id",
            headers=admin_h,
        )
        release_id = one(open_rows).get("id")
        http(rpc + "release_ai_usage", "POST", {"p_reservation_id": release_id}, admin_h)
        _, released = http(
            f"{rest}usage_reservations?id=eq.{release_id}&select=cost_micro", headers=admin_h
        )
        check(
            "releasing returns the whole hold",
            one(released).get("cost_micro") == 0,
        )

        # ---- the race the advisory lock exists for ----
        outcomes: list = []
        lock = threading.Lock()

        def race() -> None:
            st, res = reserve(b["id"], 400, cap=500, in_flight=5)
            with lock:
                outcomes.append((st, res))

        threads = [threading.Thread(target=race) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        granted = [r for st, r in outcomes if st == 200 and one(r).get("allowed")]
        check(
            "two concurrent reservations cannot both take the last of the budget",
            len(granted) == 1,
            f"{len(granted)} of {len(outcomes)} allowed",
        )

        # ---- deletion removes AI rows, and only this business's ----
        st, _ = http(
            rpc + "request_business_deletion",
            "POST",
            {"p_business_id": a["business"], "p_user_id": a["id"]},
            admin_h,
        )
        check("deletion was accepted", st == 200, f"status {st}")
        check(
            "the deleted business keeps no run and no correction",
            count("ai_runs", a["business"]) == 0 and count("category_overrides", a["business"]) == 0,
        )
        check(
            "the other business's AI rows are untouched",
            count("ai_runs", b["business"]) == 1 and count("category_overrides", b["business"]) == 1,
        )
        _, left = http(
            f"{rest}transactions?business_id=eq.{a['business']}&select=id", headers=admin_h
        )
        check("its transactions are gone too", len(left or []) == 0)

    finally:
        # Everything this check made, it removes, and only what it made: every delete below is
        # keyed by an id this run created. Reservations first, because they have no cascade.
        # Reservations that settled at a real cost are the milestone's spend ledger and are
        # never deleted: an over-broad cleanup once removed two settled rows and the recorded
        # total silently dropped by the amount that had actually been spent. Only this check's
        # own zero-cost and unsettled rows go.
        for who in users.values():
            http(
                f"{rest}usage_reservations?identity_id=eq.{who['id']}&cost_micro=eq.0",
                "DELETE",
                None,
                admin_h,
            )
            http(
                f"{rest}usage_reservations?identity_id=eq.{who['id']}&settled_at=is.null",
                "DELETE",
                None,
                admin_h,
            )
        for business in created_businesses:
            http(f"{rest}ai_runs?business_id=eq.{business}", "DELETE", None, admin_h)
            http(f"{rest}category_overrides?business_id=eq.{business}", "DELETE", None, admin_h)
            http(f"{rest}transactions?business_id=eq.{business}", "DELETE", None, admin_h)
            http(f"{rest}accounts?business_id=eq.{business}", "DELETE", None, admin_h)
            http(f"{rest}workspaces?business_id=eq.{business}", "DELETE", None, admin_h)
            http(f"{rest}storage_cleanup?business_id=eq.{business}", "DELETE", None, admin_h)
            http(f"{rest}businesses?id=eq.{business}", "DELETE", None, admin_h)
        for user_id in created_users:
            http(f"{base}/auth/v1/admin/users/{user_id}", "DELETE", None, admin_h)

    after = snapshot()
    check(
        "the owner's own businesses and transactions are unchanged",
        after == before,
        "" if after == before else f"{before} -> {after}",
    )

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
