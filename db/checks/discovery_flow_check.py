#!/usr/bin/env python3
"""The Discovery portal's thin slice, end to end against the development database through the
running API.

Loads a disposable five-question fixture set under `checkflow-*` slugs with the real seed loader
(idempotent), one question of each plain kind the portal takes plus a scenario, so the journey
is exercised against the real schema without depending on the retired proof template or on the
real bank. Creates two throwaway users through the Auth admin API: A, made a stakeholder in
`profiles` and assigned the check template; B, an ordinary member. Provisions A a private
business through the API and drives A's flow exactly as the browser would: read the overview,
read the section, save three answers, leave, sign in again, find them hydrated and the resume
pointer set, finish the core, skip the optional question, change two answers, and read the
trigger-written history back. B, an anonymous caller, and A's own token at the Data API must all
be refused. Everything the check creates is deleted at the end, the real bank is shown untouched,
and nothing else is listed, modified or deleted.

    DISCOVERY_ENABLED=true services/api/.venv/bin/uvicorn app.main:app --port 8000   # in services/api
    API_BASE=http://127.0.0.1:8000 services/api/.venv/bin/python db/checks/discovery_flow_check.py
"""

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
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_BYPASS = os.environ.get("API_BYPASS", "").strip()
CLIENT = {"x-cashlens-client": "web"}
PREFIX = "checkflow"
TEMPLATE_SLUG = f"{PREFIX}-template"
SECTION_SLUG = f"{PREFIX}-core"
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


# ------------------------------------------------------------- fixtures --
def fixtures() -> tuple[list[dict], list[dict]]:
    """One question of each plain kind the portal takes, plus a scenario. Synthetic throughout."""
    section = {
        "note": "FLOW CHECK DATA. Disposable; removed by the check that loads it. Synthetic.",
        "slug": SECTION_SLUG,
        "title": "Flow check section",
        "description": "Five made-up questions that exercise one input of each kind.",
        "is_core": True,
        "estimated_minutes": 10,
        "questions": [
            {"question_key": f"{PREFIX}.short_text", "question_text": "In a sentence, what does the test business do?", "question_type": "short_text", "required_core": True, "priority": 50, "sensitivity": "low", "order": 0},
            {"question_key": f"{PREFIX}.boolean", "question_text": "Does the test business take card payments?", "question_type": "boolean", "required_core": True, "priority": 50, "sensitivity": "low", "order": 1},
            {"question_key": f"{PREFIX}.single_select", "question_text": "How often does the test business review its cash position?", "question_type": "single_select", "required_core": True, "priority": 50, "sensitivity": "low", "order": 2, "options": [{"value": "daily", "label": "Daily"}, {"value": "weekly", "label": "Weekly"}, {"value": "monthly", "label": "Monthly"}, {"value": "rarely", "label": "Rarely"}]},
            {"question_key": f"{PREFIX}.currency", "question_text": "Roughly what is the test business's monthly rent?", "question_type": "currency", "required_core": False, "priority": 30, "sensitivity": "high", "order": 3},
            {"question_key": f"{PREFIX}.scenario", "question_text": "Would you approve, deny, or manually review this applicant?", "question_type": "scenario_decision", "required_core": True, "priority": 60, "sensitivity": "low", "order": 4, "options": [{"value": "revenue_stable", "label": "Revenue stable"}, {"value": "nsf_recent", "label": "Recent NSF event"}, {"value": "debt_load", "label": "Existing debt load"}, {"value": "thin_history", "label": "Short operating history"}], "scenario_brief": {"synthetic": True, "applicant": "Synthetic restaurant applicant", "monthly_revenue_minor": 7000000, "nsf_events_last_90_days": 1, "notes": "Made-up case for the flow check. No real applicant."}},
        ],
    }
    template = {"slug": TEMPLATE_SLUG, "title": "Discovery (flow check)", "subtitle": "Five made-up questions.", "estimated_minutes": 10, "sections": [SECTION_SLUG]}
    return [template], [section]


def seed(directory: Path) -> subprocess.CompletedProcess:
    templates, sections = fixtures()
    (directory / "sections").mkdir(exist_ok=True)
    (directory / "templates.json").write_text(json.dumps(templates), encoding="utf-8")
    for section in sections:
        (directory / "sections" / f"{section['slug']}.json").write_text(json.dumps(section), encoding="utf-8")
    return subprocess.run([sys.executable, "-m", "app.discovery.seed", "--fixtures", str(directory)], cwd=ROOT / "services" / "api", capture_output=True, text=True)


def main() -> int:
    base, admin, anon = env("SUPABASE_URL"), env("SUPABASE_SERVICE_ROLE_KEY"), env("SUPABASE_ANON_KEY")
    admin_h = {"apikey": admin, "Authorization": f"Bearer {admin}"}
    rest = f"{base}/rest/v1/"
    tag = secrets.token_hex(3)
    users: dict[str, dict] = {}
    businesses: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="discovery-flow-"))

    def sign_in(email: str, password: str) -> str:
        st, session = http(f"{base}/auth/v1/token?grant_type=password", "POST", {"email": email, "password": password}, {"apikey": anon})
        if st != 200:
            raise RuntimeError(f"sign-in failed: {st}")
        return session["access_token"]

    st, shared_before = http(f"{rest}discovery_questions?question_key=like.sf.*&retired_at=is.null&select=id", headers=admin_h)
    try:
        # ---- the disposable template, through the real loader ----
        seeded = seed(tmp)
        check("seed_loader_loads_the_flow_fixtures", seeded.returncode == 0, seeded.stdout.strip().splitlines()[-1] if seeded.stdout.strip() else seeded.stderr[-200:])
        st, template = http(f"{rest}discovery_templates?slug=eq.{TEMPLATE_SLUG}&select=id", headers=admin_h)
        if st != 200 or not template:
            raise RuntimeError("template missing after seed")
        template_id = template[0]["id"]
        seeded_again = seed(tmp)
        st, questions = http(f"{rest}discovery_questions?question_key=like.{PREFIX}.*&select=id,question_key,question_version,retired_at", headers=admin_h)
        check("seed_loader_is_idempotent", seeded_again.returncode == 0 and len(questions or []) == 5 and all(q["question_version"] == 1 and q["retired_at"] is None for q in questions), f"{len(questions or [])} questions")

        # ---- two throwaway users ----
        for label in ("a", "b"):
            email, password = f"discoverycheck-{label}-{tag}@example.org", secrets.token_urlsafe(18)
            st, created = http(f"{base}/auth/v1/admin/users", "POST", {"email": email, "password": password, "email_confirm": True}, admin_h)
            if st not in (200, 201):
                raise RuntimeError(f"could not create user {label}: {st}")
            users[label] = {"id": created["id"], "email": email, "password": password}
            users[label]["token"] = sign_in(email, password)
            users[label]["auth"] = {"authorization": f"Bearer {users[label]['token']}", **CLIENT}
        a, b = users["a"], users["b"]

        st, profile = http(f"{rest}profiles?user_id=eq.{a['id']}&select=role", headers=admin_h)
        check("profile_is_created_by_the_auth_trigger_as_member", st == 200 and profile and profile[0]["role"] == "member", f"HTTP {st} {profile}")
        st, _ = http(f"{rest}profiles?user_id=eq.{a['id']}", "PATCH", {"role": "stakeholder", "first_name": "Tester"}, {**admin_h, "Prefer": "return=minimal"})
        check("stakeholder_role_written_to_profiles", st in (200, 204), f"HTTP {st}")
        st, assignment = http(f"{rest}discovery_assignments", "POST", {"user_id": a["id"], "template_id": template_id}, {**admin_h, "Prefer": "return=representation"})
        if st != 201:
            raise RuntimeError(f"could not assign: {st} {assignment}")
        assignment_id = assignment[0]["id"]
        prefix = f"{API_BASE}/api/discovery/assignments/{assignment_id}"

        st, prov = http(f"{API_BASE}/api/private/provision", "POST", {"business_name": f"Discovery check {tag}"}, a["auth"])
        check("stakeholder_can_provision_a_private_business", st == 200, f"HTTP {st}")
        if st == 200:
            businesses.append(prov["business"]["id"])

        # ---- recognised, landed, workspace open ----
        st, me = http(f"{API_BASE}/api/me", headers=a["auth"])
        check("api_me_reports_stakeholder_and_the_assignment", st == 200 and me["role"] == "stakeholder" and me["discovery"]["assignment_id"] == assignment_id and me["discovery"]["core_complete"] is False, f"HTTP {st}")
        st, ws = http(f"{API_BASE}/api/private/workspace", headers=a["auth"])
        check("workspace_is_open_while_core_is_incomplete", st == 200, f"HTTP {st}")
        st, portal = http(f"{API_BASE}/api/discovery/me", headers=a["auth"])
        check("discovery_me_shows_one_template_one_section_five_questions", st == 200 and portal["first_name"] == "Tester" and [s["slug"] for s in portal["assignment"]["sections"]] == [SECTION_SLUG] and portal["assignment"]["progress"]["total"] == 5 and portal["assignment"]["progress"]["core_total"] == 4, f"HTTP {st}")
        st, section = http(f"{prefix}/sections/{SECTION_SLUG}", headers=a["auth"])
        types = [q["question_type"] for q in section["questions"]] if st == 200 else []
        check("section_lists_the_five_question_types_unanswered", types == ["short_text", "boolean", "single_select", "currency", "scenario_decision"] and all(q["response"] is None for q in section["questions"]), f"HTTP {st} {types}")
        by_type = {q["question_type"]: q for q in section["questions"]}

        # ---- save three, leave ----
        st, r1 = http(f"{prefix}/responses/{by_type['short_text']['id']}", "PUT", {"answer": "Sells synthetic sandwiches."}, a["auth"])
        check("short_text_answer_saved", st == 200 and r1["response"]["answer"] == "Sells synthetic sandwiches." and r1["status"] == "in_progress", f"HTTP {st}")
        st, r2 = http(f"{prefix}/responses/{by_type['boolean']['id']}", "PUT", {"answer": True}, a["auth"])
        check("boolean_answer_saved", st == 200 and r2["response"]["answer"] is True, f"HTTP {st}")
        st, r3 = http(f"{prefix}/responses/{by_type['single_select']['id']}", "PUT", {"answer": "weekly"}, a["auth"])
        check("single_select_answer_saved", st == 200 and r3["response"]["answer"] == "weekly" and r3["section_progress"]["answered"] == 3, f"HTTP {st}")
        st, bad = http(f"{prefix}/responses/{by_type['currency']['id']}", "PUT", {"answer": 12.5}, a["auth"])
        check("a_fractional_currency_answer_is_refused", st == 422, f"HTTP {st}")
        st, bad = http(f"{prefix}/responses/{by_type['single_select']['id']}", "PUT", {"answer": "hourly"}, a["auth"])
        check("an_option_not_offered_is_refused", st == 422, f"HTTP {st}")
        st, sess = http(f"{prefix}/sessions", "POST", None, a["auth"])
        check("a_visit_is_recorded", st == 200 and "session_id" in sess, f"HTTP {st}")

        # ---- sign in again: resume, hydrated ----
        a["token"] = sign_in(a["email"], a["password"])
        a["auth"] = {"authorization": f"Bearer {a['token']}", **CLIENT}
        st, me2 = http(f"{API_BASE}/api/me", headers=a["auth"])
        check("after_re_signin_core_is_still_incomplete_so_login_routes_to_discovery", st == 200 and me2["role"] == "stakeholder" and me2["discovery"]["core_complete"] is False and me2["discovery"]["status"] == "in_progress", f"HTTP {st}")
        st, portal2 = http(f"{API_BASE}/api/discovery/me", headers=a["auth"])
        check("resume_points_at_the_section_left_off", st == 200 and portal2["assignment"]["resume_section_slug"] == SECTION_SLUG and portal2["assignment"]["current_section_slug"] == SECTION_SLUG and portal2["assignment"]["progress"]["answered"] == 3, f"HTTP {st}")
        st, section2 = http(f"{prefix}/sections/{SECTION_SLUG}", headers=a["auth"])
        hydrated = {q["question_type"]: (q["response"] or {}).get("answer") for q in section2["questions"]} if st == 200 else {}
        check("previous_answers_are_hydrated_from_the_database", hydrated.get("short_text") == "Sells synthetic sandwiches." and hydrated.get("boolean") is True and hydrated.get("single_select") == "weekly" and hydrated.get("currency") is None, f"HTTP {st}")

        # ---- finish the core, skip the optional ----
        scenario = {"scenario": {"decision": "manual_review", "confidence": 4, "reason_codes": ["nsf_recent", "debt_load"], "maximum_exposure_minor": 1500000, "notes": "synthetic case"}}
        st, r5 = http(f"{prefix}/responses/{by_type['scenario_decision']['id']}", "PUT", scenario, a["auth"])
        check("scenario_answer_saved_and_core_complete", st == 200 and r5["response"]["scenario"]["decision"] == "manual_review" and r5["status"] == "core_complete", f"HTTP {st} {r5 if st != 200 else ''}")
        st, me3 = http(f"{API_BASE}/api/me", headers=a["auth"])
        check("api_me_now_reports_core_complete", st == 200 and me3["discovery"]["core_complete"] is True, f"HTTP {st}")
        st, refused = http(f"{prefix}/responses/{by_type['short_text']['id']}/skip", "POST", None, a["auth"])
        check("a_core_question_cannot_be_skipped", st == 422, f"HTTP {st}")
        st, skipped = http(f"{prefix}/responses/{by_type['currency']['id']}/skip", "POST", None, a["auth"])
        check("the_optional_currency_question_can_be_skipped_to_complete", st == 200 and skipped["response"]["status"] == "skipped" and skipped["status"] == "complete", f"HTTP {st}")

        # ---- change of mind: history by trigger, never by hand ----
        st, r6 = http(f"{prefix}/responses/{by_type['short_text']['id']}", "PUT", {"answer": "Sells synthetic soup."}, a["auth"])
        st, hist = http(f"{prefix}/responses/{by_type['short_text']['id']}/history", headers=a["auth"])
        check("editing_an_answer_preserves_the_previous_one_in_history", st == 200 and [e["answer"] for e in hist["entries"]] == ["Sells synthetic sandwiches."], f"HTTP {st} {hist if st != 200 else ''}")
        changed = {"scenario": {**scenario["scenario"], "decision": "deny", "confidence": 5}}
        st, r7 = http(f"{prefix}/responses/{by_type['scenario_decision']['id']}", "PUT", changed, a["auth"])
        st, shist = http(f"{prefix}/responses/{by_type['scenario_decision']['id']}/history", headers=a["auth"])
        decisions = [e["scenario"]["decision"] for e in shist["entries"] if e.get("scenario")] if st == 200 else []
        check("changing_a_scenario_decision_preserves_the_earlier_decision", st == 200 and decisions == ["manual_review"], f"HTTP {st} {decisions}")
        st, rows = http(f"{rest}discovery_response_history?user_id=eq.{a['id']}&select=change,answer", headers=admin_h)
        st2, srows = http(f"{rest}discovery_scenario_response_history?user_id=eq.{a['id']}&select=change,decision", headers=admin_h)
        check("history_rows_were_written_by_the_database_triggers", st == 200 and st2 == 200 and len(rows) >= 2 and len(srows) >= 1, f"{len(rows or [])} generic, {len(srows or [])} scenario")
        st, current = http(f"{rest}discovery_responses?assignment_id=eq.{assignment_id}&select=question_type", headers=admin_h)
        check("one_current_row_per_question_however_often_saved", st == 200 and len(current) == 5, f"{len(current or [])} rows")

        # ---- isolation ----
        st, _ = http(f"{prefix}", headers=b["auth"])
        st2, _ = http(f"{prefix}/sections/{SECTION_SLUG}", headers=b["auth"])
        st3, _ = http(f"{prefix}/responses/{by_type['short_text']['id']}", "PUT", {"answer": "intruder"}, b["auth"])
        st4, other = http(f"{API_BASE}/api/discovery/me", headers=b["auth"])
        check("a_member_cannot_read_or_write_the_stakeholders_assignment", (st, st2, st3) == (404, 404, 404) and st4 == 200 and other["assignment"] is None and other["role"] == "member", f"{st} {st2} {st3} {st4}")
        st, _ = http(f"{prefix}", headers=CLIENT)
        check("an_anonymous_caller_is_refused", st == 401, f"HTTP {st}")
        st, direct = http(f"{rest}discovery_responses?select=id", headers={"apikey": anon, "Authorization": f"Bearer {a['token']}"})
        check("the_stakeholders_own_token_is_refused_at_the_data_api", st in (401, 403) or (st == 200 and direct == []), f"HTTP {st}")
        st, spoof = http(f"{rest}discovery_responses", "POST", {"assignment_id": assignment_id, "user_id": b["id"], "template_id": template_id, "section_id": section["id"], "question_id": by_type["short_text"]["id"], "question_version": 1, "question_type": "short_text", "status": "answered", "answer": "spoof"}, {**admin_h, "Prefer": "return=minimal"})
        check("even_the_service_role_cannot_file_an_answer_under_another_user", st == 409 or st == 400 or st == 403, f"HTTP {st}")
    except RuntimeError as exc:
        check("aborted", False, str(exc))
    finally:
        # cleanup: only what this run created. The auth user goes first: its cascade deletes the
        # answers, and the delete triggers write those to history, so history is cleared last.
        for business_id in businesses:
            http(f"{rest}businesses?id=eq.{business_id}&data_mode=eq.private", "DELETE", None, admin_h)
        for u in users.values():
            http(f"{base}/auth/v1/admin/users/{u['id']}", "DELETE", None, admin_h)
        if "a" in users:
            st, gone = http(f"{rest}discovery_response_history?user_id=eq.{users['a']['id']}&change=eq.deleted&select=question_type", headers=admin_h)
            st2, sgone = http(f"{rest}discovery_scenario_response_history?user_id=eq.{users['a']['id']}&change=eq.deleted&select=decision", headers=admin_h)
            check("cascade_delete_is_recorded_by_the_triggers_before_history_is_cleared", st == 200 and st2 == 200 and len(gone or []) == 5 and len(sgone or []) == 1, f"{len(gone or [])} generic, {len(sgone or [])} scenario")
        for u in users.values():
            for table in ("discovery_response_history", "discovery_scenario_response_history"):
                http(f"{rest}{table}?user_id=eq.{u['id']}", "DELETE", None, admin_h)
        leftovers = []
        for u in users.values():
            for table in ("profiles", "discovery_assignments", "discovery_responses", "discovery_scenario_responses", "discovery_sessions", "discovery_response_history", "discovery_scenario_response_history"):
                st, rows = http(f"{rest}{table}?user_id=eq.{u['id']}&select=user_id", headers=admin_h)
                if rows:
                    leftovers.append(f"{table}:{len(rows)}")
        for business_id in businesses:
            st, rows = http(f"{rest}businesses?id=eq.{business_id}&select=id", headers=admin_h)
            leftovers += [f"businesses:{r['id']}" for r in rows or []]
        check("throwaway_data_deleted", not leftovers, ", ".join(leftovers))
        # the disposable bank: links, questions, section, template, in that order
        st, templates = http(f"{rest}discovery_templates?slug=eq.{TEMPLATE_SLUG}&select=id", headers=admin_h)
        for t in templates or []:
            http(f"{rest}discovery_template_sections?template_id=eq.{t['id']}", "DELETE", None, admin_h)
        http(f"{rest}discovery_questions?question_key=like.{PREFIX}.*", "DELETE", None, admin_h)
        http(f"{rest}discovery_sections?slug=eq.{SECTION_SLUG}", "DELETE", None, admin_h)
        http(f"{rest}discovery_templates?slug=eq.{TEMPLATE_SLUG}", "DELETE", None, admin_h)
        left = []
        for table, filt in (("discovery_templates", f"slug=eq.{TEMPLATE_SLUG}"), ("discovery_sections", f"slug=eq.{SECTION_SLUG}"), ("discovery_questions", f"question_key=like.{PREFIX}.*")):
            st, rows = http(f"{rest}{table}?{filt}&select=id", headers=admin_h)
            left += [f"{table}:{r['id']}" for r in rows or []]
        check("flow_fixtures_removed", not left, ", ".join(left))
        st, shared_after = http(f"{rest}discovery_questions?question_key=like.sf.*&retired_at=is.null&select=id", headers=admin_h)
        check("real_founder_bank_untouched", st == 200 and len(shared_after or []) == len(shared_before or []) == 154, f"{len(shared_after or [])} live shared questions")
        st, remaining = http(f"{base}/auth/v1/admin/users?page=1&per_page=100", headers=admin_h)
        stale = [x for x in (remaining or {}).get("users", []) if tag in (x.get("email") or "")]
        check("throwaway_users_deleted", not stale, f"{len(stale)} left")
    failures = sum(1 for _, ok, _ in results if not ok)
    print(f"{len(results) - failures}/{len(results)} discovery flow checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
