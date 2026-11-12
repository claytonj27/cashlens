#!/usr/bin/env python3
"""Phase 3: isolation, sharing, branching, history, versioning and retirement against the real
development database, through the running API, with the real seed loader.

Loads a disposable fixture set (two templates sharing one section, each with a private section)
under `phase3-gate-*` slugs, creates two throwaway stakeholders A and B each with their own
private business and assignment, then attacks A from B and B from A, answers the shared question
from both sides, exercises the branched question, the composite foreign keys, both history
triggers field by field, a wording change with answers in place, and retirement with and without
`--retire`. It ends by deleting one user in the wrong order to show the regression, the other in
the right order, then removes its fixtures and proves nothing of any of it remains.

    DISCOVERY_ENABLED=true services/api/.venv/bin/uvicorn app.main:app --port 8010   # in services/api
    API_BASE=http://127.0.0.1:8010 services/api/.venv/bin/python db/checks/discovery_isolation_check.py
"""

import importlib.util
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "services" / "api" / ".env"
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8010").rstrip("/")
API_BYPASS = os.environ.get("API_BYPASS", "").strip()
CLIENT = {"x-cashlens-client": "web"}
PREFIX = "phase3-gate"
A_SECRET, B_SECRET = "A_PRIVATE_ANSWER_SENTINEL", "B_PRIVATE_ANSWER_SENTINEL"
HIDDEN = "HIDDEN_QUESTION_SENTINEL"
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
    print(f"{'PASS' if ok else 'FAIL'}  {name:<70} {detail}")


def http(url: str, method: str = "GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": "cashlens-check", **(headers or {})}
    if API_BYPASS and url.startswith(API_BASE):
        h["x-vercel-protection-bypass"] = API_BYPASS
    if body is not None:
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


def account_script():
    spec = importlib.util.spec_from_file_location("discovery_test_account", ROOT / "scripts" / "discovery_test_account.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------- fixtures --
def question(key: str, kind: str, order: int, **extra) -> dict:
    row = {"question_key": key, "question_text": f"{key}?", "question_type": kind, "required_core": True, "priority": 50, "sensitivity": "low", "order": order}
    row.update(extra)
    return row


def fixtures(version: int) -> tuple[list[dict], list[dict]]:
    """v1 as loaded; v2 rewords the shared text question; v3 also drops the A-only text question."""
    shared_text = question(f"{PREFIX}.shared.text".replace("-", "."), "short_text", 0)
    if version >= 2:
        shared_text["question_text"] = "Reworded: what does the shared business do?"
    shared = {
        "note": "PHASE 3 GATE TEST DATA. Disposable.",
        "slug": f"{PREFIX}-shared", "title": "Shared", "is_core": True, "estimated_minutes": 3,
        "questions": [
            shared_text,
            question("phase3.gate.shared.gate", "boolean", 1),
            question("phase3.gate.shared.branch", "short_text", 2, required_core=False, question_text=f"Which one? {HIDDEN}", branch_condition={"question_key": "phase3.gate.shared.gate", "equals": True}),
        ],
    }
    a_questions = [
        question("phase3.gate.a.text", "short_text", 0),
        question("phase3.gate.a.scenario", "scenario_decision", 1, options=[{"value": "nsf_recent", "label": "NSF"}, {"value": "thin_history", "label": "Thin"}], scenario_brief={"synthetic": True, "applicant": "made up"}),
        question("phase3.gate.a.text2", "short_text", 2, required_core=False),
    ]
    if version >= 3:
        a_questions = [dict(a_questions[1], order=0), dict(a_questions[2], order=1)]
    a_only = {"note": "PHASE 3 GATE TEST DATA.", "slug": f"{PREFIX}-a-only", "title": "A only", "is_core": True, "estimated_minutes": 3, "questions": a_questions}
    b_only = {"note": "PHASE 3 GATE TEST DATA.", "slug": f"{PREFIX}-b-only", "title": "B only", "is_core": True, "estimated_minutes": 3, "questions": [question("phase3.gate.b.text", "short_text", 0)]}
    templates = [
        {"slug": f"{PREFIX}-a", "title": "Gate A", "estimated_minutes": 6, "sections": [shared["slug"], a_only["slug"]]},
        {"slug": f"{PREFIX}-b", "title": "Gate B", "estimated_minutes": 6, "sections": [shared["slug"], b_only["slug"]]},
    ]
    return templates, [shared, a_only, b_only]


def seed(directory: Path, version: int, retire: bool = False) -> subprocess.CompletedProcess:
    templates, sections = fixtures(version)
    (directory / "sections").mkdir(exist_ok=True)
    for old in (directory / "sections").glob("*.json"):
        old.unlink()
    (directory / "templates.json").write_text(json.dumps(templates), encoding="utf-8")
    for section in sections:
        (directory / "sections" / f"{section['slug']}.json").write_text(json.dumps(section), encoding="utf-8")
    args = [sys.executable, "-m", "app.discovery.seed", "--fixtures", str(directory)] + (["--retire"] if retire else [])
    return subprocess.run(args, cwd=ROOT / "services" / "api", capture_output=True, text=True)


def main() -> int:
    base, admin, anon = env("SUPABASE_URL"), env("SUPABASE_SERVICE_ROLE_KEY"), env("SUPABASE_ANON_KEY")
    admin_h = {"apikey": admin, "Authorization": f"Bearer {admin}"}
    minimal = {**admin_h, "Prefer": "return=minimal"}
    rest = f"{base}/rest/v1/"
    tag = secrets.token_hex(3)
    users: dict[str, dict] = {}
    businesses: list[str] = []
    script = account_script()
    tmp = Path(tempfile.mkdtemp(prefix="discovery-gate-"))

    def sign_in(email: str, password: str) -> str:
        st, session = http(f"{base}/auth/v1/token?grant_type=password", "POST", {"email": email, "password": password}, {"apikey": anon})
        if st != 200:
            raise RuntimeError(f"sign-in failed: {st}")
        return session["access_token"]

    def questions_by_key() -> dict[str, dict]:
        st, rows = http(f"{rest}discovery_questions?question_key=like.phase3.gate.*&select=id,question_key,question_version,section_id,retired_at", headers=admin_h)
        return {r["question_key"]: r for r in rows or []}

    def api(path: str, method: str = "GET", body=None, auth=None):
        return http(f"{API_BASE}{path}", method, body, auth)

    try:
        # ---- the disposable bank ----
        loaded = seed(tmp, 1)
        check("seed_loads_the_gate_fixtures", loaded.returncode == 0, (loaded.stdout.strip().splitlines() or [loaded.stderr[-200:]])[-1])
        st, templates = http(f"{rest}discovery_templates?slug=like.{PREFIX}-*&select=id,slug", headers=admin_h)
        st, sections = http(f"{rest}discovery_sections?slug=like.{PREFIX}-*&select=id,slug", headers=admin_h)
        tid = {t["slug"]: t["id"] for t in templates or []}
        sid = {s["slug"]: s["id"] for s in sections or []}
        q = questions_by_key()
        if len(tid) != 2 or len(sid) != 3 or len(q) != 7:
            raise RuntimeError(f"bank incomplete: {len(tid)} templates, {len(sid)} sections, {len(q)} questions")
        st, links = http(f"{rest}discovery_template_sections?section_id=eq.{sid[PREFIX + '-shared']}&select=template_id", headers=admin_h)
        check("shared_section_is_one_row_joined_by_two_templates", st == 200 and len(links) == 2, f"{len(links or [])} links")

        # ---- two stakeholders, each with a business and an assignment ----
        for label, template in (("a", f"{PREFIX}-a"), ("b", f"{PREFIX}-b")):
            email, password = f"gatecheck-{label}-{tag}@example.org", secrets.token_urlsafe(18)
            st, created = http(f"{base}/auth/v1/admin/users", "POST", {"email": email, "password": password, "email_confirm": True}, admin_h)
            if st not in (200, 201):
                raise RuntimeError(f"could not create user {label}: {st}")
            users[label] = {"id": created["id"], "email": email, "password": password}
            http(f"{rest}profiles?user_id=eq.{created['id']}", "PATCH", {"role": "stakeholder", "first_name": label.upper()}, minimal)
            st, assignment = http(f"{rest}discovery_assignments", "POST", {"user_id": created["id"], "template_id": tid[template]}, {**admin_h, "Prefer": "return=representation"})
            if st != 201:
                raise RuntimeError(f"could not assign {label}: {st} {assignment}")
            users[label]["assignment"] = assignment[0]["id"]
            users[label]["template"] = tid[template]
            users[label]["token"] = sign_in(email, password)
            users[label]["auth"] = {"authorization": f"Bearer {users[label]['token']}", **CLIENT}
            st, prov = api("/api/private/provision", "POST", {"business_name": f"Gate check {label} {tag}"}, users[label]["auth"])
            if st == 200:
                businesses.append(prov["business"]["id"])
        a, b = users["a"], users["b"]
        A, B = f"/api/discovery/assignments/{a['assignment']}", f"/api/discovery/assignments/{b['assignment']}"
        check("two_stakeholders_two_businesses_two_assignments", len(businesses) == 2 and a["assignment"] != b["assignment"], f"{len(businesses)} businesses")

        # ---- A writes something private; B attacks; then the inverse ----
        st, _ = api(f"{A}/responses/{q['phase3.gate.a.text']['id']}", "PUT", {"answer": A_SECRET}, a["auth"])
        st2, _ = api(f"{A}/responses/{q['phase3.gate.shared.text']['id']}", "PUT", {"answer": A_SECRET + " shared"}, a["auth"])
        check("a_saves_two_private_answers", st == 200 and st2 == 200, f"{st} {st2}")
        attacks = [
            ("GET", A, None), ("GET", f"{A}/sections/{PREFIX}-shared", None), ("GET", f"{A}/sections/{PREFIX}-a-only", None),
            ("GET", f"{A}/responses", None), ("GET", f"{A}/responses/{q['phase3.gate.a.text']['id']}/history", None),
            ("PUT", f"{A}/responses/{q['phase3.gate.a.text']['id']}", {"answer": "intruder"}),
            ("PUT", f"{A}/responses/{q['phase3.gate.shared.text']['id']}", {"answer": "intruder"}),
            ("POST", f"{A}/responses/{q['phase3.gate.a.text2']['id']}/skip", None), ("POST", f"{A}/sessions", None),
        ]
        outcomes = [api(path, method, body, b["auth"]) for method, path, body in attacks]
        check("b_gets_404_from_every_route_on_as_assignment", all(st == 404 for st, _ in outcomes), " ".join(str(st) for st, _ in outcomes))
        check("no_a_answer_or_section_name_reaches_b", all(A_SECRET not in json.dumps(body) and "a-only" not in json.dumps(body) for _, body in outcomes))
        st, a_rows = http(f"{rest}discovery_responses?assignment_id=eq.{a['assignment']}&select=answer,user_id", headers=admin_h)
        check("as_answers_are_unchanged_after_the_attacks", st == 200 and sorted(r["answer"] for r in a_rows) == sorted([A_SECRET, A_SECRET + " shared"]) and all(r["user_id"] == a["id"] for r in a_rows))
        st, _ = api(f"{B}/responses/{q['phase3.gate.b.text']['id']}", "PUT", {"answer": B_SECRET}, b["auth"])
        inverse = [api(path.replace(a["assignment"], b["assignment"]).replace(f"{PREFIX}-a-only", f"{PREFIX}-b-only"), method, body, a["auth"]) for method, path, body in attacks]
        check("a_gets_404_from_every_route_on_bs_assignment_and_sees_nothing", all(st == 404 and B_SECRET not in json.dumps(body) for st, body in inverse), " ".join(str(st) for st, _ in inverse))
        st, _ = api(f"{A}/responses/{q['phase3.gate.b.text']['id']}", "PUT", {"answer": "cross"}, a["auth"])
        st2, _ = api(f"{A}/sections/{PREFIX}-b-only", "GET", None, a["auth"])
        st3, _ = api(f"{B}/responses/{q['phase3.gate.a.text']['id']}", "PUT", {"answer": "cross"}, b["auth"])
        check("a_question_from_the_other_private_section_is_404_through_ones_own_assignment", (st, st2, st3) == (404, 404, 404), f"{st} {st2} {st3}")

        # ---- the shared question: one row, two answers, two owners ----
        st, _ = api(f"{B}/responses/{q['phase3.gate.shared.text']['id']}", "PUT", {"answer": B_SECRET + " shared"}, b["auth"])
        st, shared_rows = http(f"{rest}discovery_responses?question_id=eq.{q['phase3.gate.shared.text']['id']}&select=id,user_id,assignment_id,answer", headers=admin_h)
        check("shared_question_has_two_rows_with_distinct_ids_and_owners", st == 200 and len(shared_rows) == 2 and len({r["id"] for r in shared_rows}) == 2 and {r["user_id"] for r in shared_rows} == {a["id"], b["id"]}, f"{len(shared_rows or [])} rows")
        st, a_view = api(f"{A}/sections/{PREFIX}-shared", "GET", None, a["auth"])
        st2, b_view = api(f"{B}/sections/{PREFIX}-shared", "GET", None, b["auth"])
        a_ans = a_view["questions"][0]["response"]["answer"] if st == 200 else None
        b_ans = b_view["questions"][0]["response"]["answer"] if st2 == 200 else None
        check("each_stakeholder_sees_only_their_own_shared_answer", a_ans == A_SECRET + " shared" and b_ans == B_SECRET + " shared" and B_SECRET not in json.dumps(a_view) and A_SECRET not in json.dumps(b_view))
        st, a_over = api(A, "GET", None, a["auth"])
        st2, b_over = api(B, "GET", None, b["auth"])
        check("progress_on_the_shared_section_is_independent", a_over["sections"][0]["progress"]["answered"] == 1 and b_over["sections"][0]["progress"]["answered"] == 1 and a_over["progress"]["answered"] == 2 and b_over["progress"]["answered"] == 2)

        # ---- the branch: absent, unwritable, per assignment ----
        branch_id = q["phase3.gate.shared.branch"]["id"]
        check("hidden_question_is_absent_from_the_payload_not_merely_flagged", HIDDEN not in json.dumps(a_view) and branch_id not in json.dumps(a_view) and a_view["progress"]["total"] == 2)
        st, _ = api(f"{A}/responses/{branch_id}", "PUT", {"answer": "early"}, a["auth"])
        st2, _ = api(f"{A}/responses/{branch_id}/skip", "POST", None, a["auth"])
        check("hidden_question_cannot_be_answered_or_skipped", (st, st2) == (404, 404), f"{st} {st2}")
        st, _ = api(f"{A}/responses/{q['phase3.gate.shared.gate']['id']}", "PUT", {"answer": True}, a["auth"])
        st, a_view2 = api(f"{A}/sections/{PREFIX}-shared", "GET", None, a["auth"])
        st2, b_view2 = api(f"{B}/sections/{PREFIX}-shared", "GET", None, b["auth"])
        check("branch_reveals_for_a_who_answered_and_stays_hidden_for_b", branch_id in [x["id"] for x in a_view2["questions"]] and branch_id not in [x["id"] for x in b_view2["questions"]])
        st, direct = http(f"{rest}discovery_questions?select=id", headers={"apikey": anon, "Authorization": f"Bearer {a['token']}"})
        check("own_token_cannot_read_the_question_bank_at_the_data_api", st in (401, 403), f"HTTP {st}")

        # ---- the structural keys, as the service role ----
        base_row = {"assignment_id": a["assignment"], "user_id": a["id"], "template_id": a["template"], "section_id": sid[f"{PREFIX}-a-only"], "question_id": q["phase3.gate.a.text2"]["id"], "question_version": 1, "question_type": "short_text", "status": "answered", "answer": "structural"}
        st, _ = http(f"{rest}discovery_responses", "POST", {**base_row, "user_id": b["id"]}, minimal)
        check("db_refuses_an_answer_filed_under_another_user", st == 409, f"HTTP {st}")
        st, _ = http(f"{rest}discovery_responses", "POST", {**base_row, "section_id": sid[f"{PREFIX}-b-only"], "question_id": q["phase3.gate.b.text"]["id"]}, minimal)
        check("db_refuses_a_section_outside_the_assignments_template", st == 409, f"HTTP {st}")
        st, _ = http(f"{rest}discovery_responses", "POST", {**base_row, "question_id": q["phase3.gate.b.text"]["id"]}, minimal)
        check("db_refuses_a_question_outside_its_section", st == 409, f"HTTP {st}")
        st, _ = http(f"{rest}discovery_assignments?id=eq.{a['assignment']}", "PATCH", {"current_section_id": sid[f"{PREFIX}-b-only"]}, minimal)
        check("db_refuses_a_current_section_outside_the_template", st == 409, f"HTTP {st}")

        # ---- generic history: create, update, update, delete ----
        t2 = q["phase3.gate.a.text2"]["id"]
        for value in ("v1", "v2", "v3"):
            api(f"{A}/responses/{t2}", "PUT", {"answer": value}, a["auth"])
        st, hist = api(f"{A}/responses/{t2}/history", "GET", None, a["auth"])
        check("generic_history_holds_each_superseded_value_newest_first", st == 200 and [(e["change"], e["answer"]) for e in hist["entries"]] == [("updated", "v2"), ("updated", "v1")] and hist["entries"][0]["valid_to"] >= hist["entries"][1]["valid_to"])
        http(f"{rest}discovery_responses?assignment_id=eq.{a['assignment']}&question_id=eq.{t2}", "DELETE", None, admin_h)
        st, hist = api(f"{A}/responses/{t2}/history", "GET", None, a["auth"])
        check("deleting_the_current_answer_records_it_as_deleted", st == 200 and [(e["change"], e["answer"]) for e in hist["entries"]] == [("deleted", "v3"), ("updated", "v2"), ("updated", "v1")])

        # ---- scenario history: every field, then delete ----
        sc = q["phase3.gate.a.scenario"]["id"]
        first = {"decision": "approve", "confidence": 3, "reason_codes": ["nsf_recent"], "maximum_exposure_minor": 100000, "additional_information_needed": "a", "what_would_change_my_decision": "b", "notes": "c"}
        st, _ = api(f"{A}/responses/{sc}", "PUT", {"scenario": first}, a["auth"])
        steps = [("decision", "deny"), ("confidence", 5), ("reason_codes", ["thin_history"]), ("maximum_exposure_minor", 200000), ("additional_information_needed", "a2"), ("what_would_change_my_decision", "b2"), ("notes", "c2")]
        current = dict(first)
        priors = []
        for field, value in steps:
            priors.append(dict(current))
            current[field] = value
            st, _ = api(f"{A}/responses/{sc}", "PUT", {"scenario": current}, a["auth"])
            if st != 200:
                raise RuntimeError(f"scenario change {field}: {st}")
        st, shist = api(f"{A}/responses/{sc}/history", "GET", None, a["auth"])
        entries = [e["scenario"] for e in shist["entries"] if e.get("scenario")] if st == 200 else []
        expected = [{k: p[k] for k in first} for p in reversed(priors)]
        got = [{k: e[k] for k in first} for e in entries]
        check("scenario_history_holds_every_prior_value_for_each_field_changed", st == 200 and got == expected, f"{len(entries)} entries")
        st, typed = http(f"{rest}discovery_scenario_responses?question_id=eq.{sc}&user_id=eq.{a['id']}&select=response_id", headers=admin_h)
        st, bad = http(f"{rest}discovery_scenario_responses?response_id=eq.{typed[0]['response_id']}", "PATCH", {"confidence": 6}, minimal)
        check("db_refuses_a_confidence_outside_1_to_5", st == 400, f"HTTP {st}")
        http(f"{rest}discovery_responses?assignment_id=eq.{a['assignment']}&question_id=eq.{sc}", "DELETE", None, admin_h)
        st, shist = api(f"{A}/responses/{sc}/history", "GET", None, a["auth"])
        deleted = [e for e in shist["entries"] if e["change"] == "deleted"] if st == 200 else []
        check("deleting_a_scenario_answer_records_both_halves_as_deleted", len(deleted) == 2 and any(e.get("scenario") and e["scenario"]["decision"] == "deny" for e in deleted), f"{len(deleted)} deleted entries")

        # ---- a wording change with answers in place ----
        reseeded = seed(tmp, 2)
        q2 = questions_by_key()
        st, a_view3 = api(f"{A}/sections/{PREFIX}-shared", "GET", None, a["auth"])
        st2, b_view3 = api(f"{B}/sections/{PREFIX}-shared", "GET", None, b["auth"])
        shared_q = a_view3["questions"][0]
        check("rewording_bumps_the_shared_question_to_version_2_once", reseeded.returncode == 0 and q2["phase3.gate.shared.text"]["question_version"] == 2 and shared_q["question_version"] == 2 and b_view3["questions"][0]["question_version"] == 2)
        check("existing_answers_keep_version_1_under_the_new_wording", shared_q["response"]["question_version"] == 1 and b_view3["questions"][0]["response"]["question_version"] == 1)
        st, saved = api(f"{A}/responses/{q2['phase3.gate.shared.text']['id']}", "PUT", {"answer": "answered under v2"}, a["auth"])
        st2, vh = api(f"{A}/responses/{q2['phase3.gate.shared.text']['id']}/history", "GET", None, a["auth"])
        check("a_new_answer_records_version_2_and_history_keeps_version_1", st == 200 and saved["response"]["question_version"] == 2 and [(e["answer"], e["question_version"]) for e in vh["entries"]] == [(A_SECRET + " shared", 1)])

        # ---- retirement ----
        st, before = http(f"{rest}discovery_responses?assignment_id=eq.{a['assignment']}&question_id=eq.{q2['phase3.gate.a.text']['id']}&select=answer", headers=admin_h)
        plain = seed(tmp, 3)
        q3 = questions_by_key()
        st, after = http(f"{rest}discovery_responses?assignment_id=eq.{a['assignment']}&question_id=eq.{q2['phase3.gate.a.text']['id']}&select=answer", headers=admin_h)
        check("seed_without_retire_leaves_a_vanished_question_and_its_answer", plain.returncode == 0 and q3["phase3.gate.a.text"]["retired_at"] is None and after == before and "pass --retire" in plain.stdout + plain.stderr, "notice printed" if "pass --retire" in plain.stdout + plain.stderr else "no notice")
        retiring = seed(tmp, 3, retire=True)
        q4 = questions_by_key()
        st, after2 = http(f"{rest}discovery_responses?assignment_id=eq.{a['assignment']}&question_id=eq.{q2['phase3.gate.a.text']['id']}&select=answer", headers=admin_h)
        check("seed_with_retire_retires_rather_than_deletes", retiring.returncode == 0 and q4["phase3.gate.a.text"]["retired_at"] is not None and after2 == before and after2[0]["answer"] == A_SECRET, f"rc={retiring.returncode} retired_at={q4['phase3.gate.a.text']['retired_at']} stdout={retiring.stdout.strip()[-1500:]!r} stderr={retiring.stderr.strip()[-1500:]!r}")
        st, a_only = api(f"{A}/sections/{PREFIX}-a-only", "GET", None, a["auth"])
        st2, listed = api(f"{A}/responses", "GET", None, a["auth"])
        st3, rh = api(f"{A}/responses/{q2['phase3.gate.a.text']['id']}/history", "GET", None, a["auth"])
        check("retired_question_is_absent_from_the_section_and_progress_but_its_answer_stays_readable", st == 200 and q2["phase3.gate.a.text"]["id"] not in [x["id"] for x in a_only["questions"]] and a_only["progress"]["total"] == 2 and any(r["question_id"] == q2["phase3.gate.a.text"]["id"] and r["answer"] == A_SECRET for r in listed) and st3 == 200)
        st, _ = api(f"{A}/responses/{q2['phase3.gate.a.text']['id']}", "PUT", {"answer": "after retirement"}, a["auth"])
        check("retired_question_cannot_be_answered_again", st == 404, f"HTTP {st}")

        # ---- the cleanup-order regression, shown and then done right ----
        http(f"{rest}discovery_response_history?user_id=eq.{b['id']}", "DELETE", None, admin_h)
        http(f"{base}/auth/v1/admin/users/{b['id']}", "DELETE", None, admin_h)
        st, orphaned = http(f"{rest}discovery_response_history?user_id=eq.{b['id']}&select=change", headers=admin_h)
        check("sweeping_history_before_the_user_leaves_the_cascades_records_behind", st == 200 and len(orphaned) >= 2 and all(o["change"] == "deleted" for o in orphaned), f"{len(orphaned or [])} left, as the regression predicts")
        for table in ("discovery_response_history", "discovery_scenario_response_history"):
            http(f"{rest}{table}?user_id=eq.{b['id']}", "DELETE", None, admin_h)
        check("the_right_order_for_b_leaves_nothing", script.leftovers(script.Admin(), b["id"]) == [])
        script.remove_account(script.Admin(), a["id"])
        check("remove_account_deletes_the_user_first_and_leaves_nothing", script.leftovers(script.Admin(), a["id"]) == [])
        users.clear()
    except RuntimeError as exc:
        check("aborted", False, str(exc))
    finally:
        for u in users.values():
            try:
                script.remove_account(script.Admin(), u["id"])
            except RuntimeError:
                pass
        for business_id in businesses:
            http(f"{rest}businesses?id=eq.{business_id}&data_mode=eq.private", "DELETE", None, admin_h)
        st, templates = http(f"{rest}discovery_templates?slug=like.{PREFIX}-*&select=id", headers=admin_h)
        for t in templates or []:
            http(f"{rest}discovery_template_sections?template_id=eq.{t['id']}", "DELETE", None, admin_h)
        http(f"{rest}discovery_questions?question_key=like.phase3.gate.*", "DELETE", None, admin_h)
        http(f"{rest}discovery_sections?slug=like.{PREFIX}-*", "DELETE", None, admin_h)
        http(f"{rest}discovery_templates?slug=like.{PREFIX}-*", "DELETE", None, admin_h)
        left = []
        for table, filt in (("discovery_templates", f"slug=like.{PREFIX}-*"), ("discovery_sections", f"slug=like.{PREFIX}-*"), ("discovery_questions", "question_key=like.phase3.gate.*")):
            st, rows = http(f"{rest}{table}?{filt}&select=id", headers=admin_h)
            left += [f"{table}:{r['id']}" for r in rows or []]
        check("gate_fixtures_removed", not left, ", ".join(left))
        # The committed bank is not this check's to touch: the retired proof questions stay
        # retired, and the real founder bank keeps every live row it had.
        st, phase2 = http(f"{rest}discovery_questions?question_key=like.phase2.test.*&select=retired_at", headers=admin_h)
        check("proof_template_stays_retired_and_untouched", st == 200 and len(phase2) == 5 and all(r["retired_at"] is not None for r in phase2), f"{len(phase2 or [])} questions")
        st, shared = http(f"{rest}discovery_questions?question_key=like.sf.*&retired_at=is.null&select=id", headers=admin_h)
        check("real_founder_bank_untouched", st == 200 and len(shared) == 154, f"{len(shared or [])} live shared questions")
        st, remaining = http(f"{base}/auth/v1/admin/users?page=1&per_page=100", headers=admin_h)
        stale = [x for x in (remaining or {}).get("users", []) if tag in (x.get("email") or "")]
        check("throwaway_users_deleted", not stale, f"{len(stale)} left")
        for business_id in businesses:
            st, rows = http(f"{rest}businesses?id=eq.{business_id}&select=id", headers=admin_h)
            if rows:
                check("throwaway_business_deleted", False, business_id)
    failures = sum(1 for _, ok, _ in results if not ok)
    print(f"{len(results) - failures}/{len(results)} discovery isolation checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
