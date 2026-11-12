#!/usr/bin/env python3
"""Transaction calibration against the real development database, through the running API.

Loads a disposable calibration fixture set under `checkcalib-*` slugs with the real seed
loader: the six synthetic proof cases in five families, one of them a typed controlled variant,
plus one claimed variant that names its baseline and the feature the author changed, and one
progressive reveal served as a second stage that names its original (migration 0028). Two
throwaway stakeholders, A and B, are assigned the template and answer the same synthetic
transaction differently. The check proves: the migrations are recorded in order and the type,
tables and function exist; the section payload carries each case without its family metadata,
without either relationship, and without any prediction, while the reveal stage does carry its
added fact; the category must fit the case's direction; a save is one database call that writes
both rows or neither; every calibration field is preserved in history when changed, and a skip
records the deletion; A and B hold separate answers to one question_id and neither can read or
write the other's; a reveal is answered apart from its original so the two readings can be
compared; the stakeholder's own token reaches neither the case table nor the write function;
and everything the check made is removed, with the real bank shown untouched.

    DISCOVERY_ENABLED=true services/api/.venv/bin/uvicorn app.main:app --port 8010   # in services/api
    API_BASE=http://127.0.0.1:8010 services/api/.venv/bin/python db/checks/discovery_calibration_check.py
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
PREFIX = "checkcalib"
TEMPLATE_SLUG = f"{PREFIX}-template"
SECTION_SLUG = f"{PREFIX}-cases"
RPC = "save_discovery_typed_response"
results: list[tuple[str, bool, str]] = []

OUTFLOW = ["Operating expense", "Transfer out", "Loan payments", "Personal expense", "Payroll", "Unclassified"]
INFLOW = ["Organic revenue", "Internal transfers", "Loans income", "Personal income", "Overdraft/Reversals", "Unclassified"]
FACT = "The counterparty's surname matches the name on the business's application."

A_FIRST = {"calibration": {"classification_category": "Personal expense", "classification_confidence": 4, "underwriting_materiality": "low", "underwriting_signal": "negative", "decision_impact": "contributes_negative", "reason_codes": ["merchant_ambiguous", "timing_atypical"], "needs_more_context": False, "reasoning": "A Saturday-night ride for a restaurant looks personal.", "handling_recommendation": "ai_review"}}
B_FIRST = {"calibration": {"classification_category": "Operating expense", "classification_confidence": 3, "underwriting_materiality": "medium", "underwriting_signal": "neutral"}}


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
    print(f"{'PASS' if ok else 'FAIL'}  {name:<72} {detail}")


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


def psql(sql: str) -> str:
    sys.path.insert(0, str(ROOT / "services" / "api"))
    from app.synthetic.seed import database_url  # noqa: PLC0415

    result = subprocess.run(["psql", database_url(), "-X", "-q", "-t", "-A", "-c", sql], capture_output=True, text=True)
    return (result.stdout or result.stderr).strip()


# ------------------------------------------------------------- fixtures --
RESTAURANT = "A twelve-table neighbourhood restaurant, open six days a week."
CODES = [
    {"value": "merchant_ambiguous", "label": "The merchant could be either"},
    {"value": "amount_atypical", "label": "The amount is unusual for this"},
    {"value": "timing_atypical", "label": "The timing points one way"},
    {"value": "counterparty_personal", "label": "The counterparty looks personal"},
    {"value": "recurrence_regular", "label": "It recurs too regularly to be incidental"},
    {"value": "vertical_decides", "label": "The business type settles it"},
    {"value": "need_more_context", "label": "I would need to see more"},
]
UBER = {"case_family": "checkcalib-uber", "variant": "baseline", "synthetic": True, "business_vertical": "restaurant", "business_description": RESTAURANT, "account_type": "checking", "direction": "outflow", "amount_minor": -1840, "descriptor": "UBER *TRIP 8N2K4", "merchant": "Uber", "payment_rail": "card", "posted_date": "2026-03-14", "day_of_week": 6, "time_of_day": "22:41", "frequency_30d": 11, "frequency_90d": 29, "average_amount_minor": -1680, "amount_min_minor": -2400, "amount_max_minor": -900, "recurrence_pattern": "irregular"}
ZELLE = {"case_family": "checkcalib-zelle", "variant": "baseline", "synthetic": True, "business_vertical": "restaurant", "business_description": RESTAURANT, "account_type": "checking", "direction": "outflow", "amount_minor": -95000, "descriptor": "ZELLE PAYMENT TO DELACROIX M", "counterparty": "M. Delacroix (invented name)", "payment_rail": "zelle", "posted_date": "2026-03-15", "day_of_week": 0, "frequency_30d": 2, "frequency_90d": 6, "average_amount_minor": -95000, "amount_min_minor": -95000, "amount_max_minor": -95000, "recurrence_pattern": "semimonthly", "context": {"pattern": "The same amount on the 1st and the 15th for three months. The surname does not match the applicant's."}, "reveal": {"added_context": FACT}}


def case_question(key: str, order: int, case: dict, core: bool = True, text: str = "What is this transaction, and how much does it matter?") -> dict:
    return {"question_key": key, "question_text": text, "helper_text": "A synthetic row.", "question_type": "transaction_calibration", "required_core": core, "priority": 70, "sensitivity": "low", "order": order, "transaction_case": case}


def fixtures() -> tuple[list[dict], list[dict]]:
    late = {**UBER, "variant": "late", "context": {"pattern": "Seen only in the small hours over the last month."}, "baseline_question_key": f"{PREFIX}.uber.baseline", "authored_changed_feature": "timing"}
    stage = {k: v for k, v in ZELLE.items() if k != "reveal"}
    stage.update({"variant": "baseline-reveal", "reveal_of_question_key": f"{PREFIX}.zelle", "context": {**ZELLE["context"], "one more thing": FACT}})
    section = {
        "note": "CALIBRATION CHECK DATA. Disposable; removed by the check that loads it. SYNTHETIC: every merchant relationship, counterparty name, amount and date is invented.",
        "slug": SECTION_SLUG, "title": "Reading individual transactions", "is_core": True, "estimated_minutes": 8,
        "calibration_reason_codes": CODES,
        "questions": [
            case_question(f"{PREFIX}.uber.baseline", 0, UBER),
            case_question(f"{PREFIX}.safeway", 1, {"case_family": "checkcalib-safeway", "variant": "baseline", "synthetic": True, "business_vertical": "restaurant", "business_description": RESTAURANT, "account_type": "checking", "direction": "outflow", "amount_minor": -18450, "descriptor": "SAFEWAY #1472 OAKLAND CA", "merchant": "Safeway", "payment_rail": "card", "posted_date": "2026-03-17", "day_of_week": 2, "time_of_day": "10:12", "frequency_30d": 4, "frequency_90d": 13, "average_amount_minor": -16200, "amount_min_minor": -23100, "amount_max_minor": -9800, "recurrence_pattern": "weekly"}),
            case_question(f"{PREFIX}.zelle", 2, ZELLE),
            case_question(f"{PREFIX}.zelle.reveal", 3, stage, text="One more thing about this row. Knowing that, what is it, and how much does it matter?"),
            case_question(f"{PREFIX}.reversal", 4, {"case_family": "checkcalib-reversal", "variant": "baseline", "synthetic": True, "business_vertical": "restaurant", "business_description": RESTAURANT, "account_type": "checking", "direction": "inflow", "amount_minor": 12999, "descriptor": "SQUARE INC 2039 CREDIT", "merchant": "Square", "payment_rail": "ach", "posted_date": "2026-03-12", "day_of_week": 4, "frequency_30d": 1, "frequency_90d": 2, "recurrence_pattern": "none", "related_transactions": [{"descriptor": "SQUARE INC 2039", "amount_minor": -12999, "posted_date": "2026-03-10", "note": "same amount, two days earlier, no REVERSAL wording"}]}),
            case_question(f"{PREFIX}.ach", 5, {"case_family": "checkcalib-ach", "variant": "baseline", "synthetic": True, "business_vertical": "restaurant", "business_description": RESTAURANT, "account_type": "checking", "direction": "outflow", "amount_minor": -1500000, "descriptor": "ACH DEBIT 8823 HLDGS LLC", "counterparty": "8823 Hldgs LLC (invented name)", "payment_rail": "ach", "posted_date": "2026-03-19", "day_of_week": 4, "frequency_30d": 1, "frequency_90d": 1, "recurrence_pattern": "none"}),
            case_question(f"{PREFIX}.uber.tuesday", 6, {"case_family": "checkcalib-uber", "variant": "tuesday-morning", "changed_feature": "day_of_week", "synthetic": True, "day_of_week": 2, "time_of_day": "09:15", "posted_date": "2026-03-17"}, core=False),
            case_question(f"{PREFIX}.uber.late", 7, late, core=False),
        ],
    }
    template = {"slug": TEMPLATE_SLUG, "title": "Transaction calibration (check)", "subtitle": "Eight made-up bank rows.", "estimated_minutes": 8, "sections": [SECTION_SLUG]}
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
    minimal = {**admin_h, "Prefer": "return=minimal"}
    rest = f"{base}/rest/v1/"
    tag = secrets.token_hex(3)
    users: dict[str, dict] = {}
    script = account_script()
    tmp = Path(tempfile.mkdtemp(prefix="discovery-calib-"))

    def sign_in(email: str, password: str) -> str:
        st, session = http(f"{base}/auth/v1/token?grant_type=password", "POST", {"email": email, "password": password}, {"apikey": anon})
        if st != 200:
            raise RuntimeError(f"sign-in failed: {st}")
        return session["access_token"]

    def api(path: str, method: str = "GET", body=None, auth=None):
        return http(f"{API_BASE}{path}", method, body, auth)

    st, library_before = http(f"{rest}discovery_questions?question_key=like.tc-*&retired_at=is.null&select=id", headers=admin_h)
    try:
        # ---- migrations, type, tables, function ----
        recorded = psql("select string_agg(version, ',' order by version) from public.schema_migrations where version in ('0026', '0027', '0028')")
        check("migrations_0026_0027_and_0028_are_recorded_in_order", recorded == "0026,0027,0028", recorded)
        check("transaction_calibration_is_an_enum_value", psql("select count(*) from pg_enum e join pg_type t on t.oid = e.enumtypid where t.typname = 'discovery_question_type' and e.enumlabel = 'transaction_calibration'") == "1")
        tables = psql("select count(*) from pg_tables where schemaname = 'public' and tablename in ('discovery_transaction_cases', 'discovery_transaction_calibration_responses', 'discovery_transaction_calibration_response_history')")
        check("the_three_calibration_tables_exist", tables == "3", tables)
        columns = psql("select count(*) from information_schema.columns where table_schema = 'public' and table_name = 'discovery_transaction_cases' and column_name in ('baseline_question_id', 'authored_changed_feature', 'reveal_of_question_id', 'reveal')")
        check("the_case_table_carries_the_four_relationship_columns", columns == "4", columns)
        rls = psql("select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and c.relname like 'discovery_transaction%' and c.relrowsecurity")
        policies = psql("select count(*) from pg_policies where schemaname = 'public' and tablename like 'discovery_transaction%'")
        grants = psql("select count(*) from information_schema.role_table_grants where table_schema = 'public' and table_name like 'discovery_transaction%' and grantee in ('anon', 'authenticated')")
        check("rls_on_no_policies_no_anon_or_authenticated_grants", (rls, policies, grants) == ("3", "0", "0"), f"rls={rls} policies={policies} grants={grants}")
        sys.path.insert(0, str(ROOT / "services" / "api"))
        from app.ai.prompts import CASHFLOW_INFLOW_CATEGORIES, CASHFLOW_OUTFLOW_CATEGORIES  # noqa: PLC0415

        definition = psql("select pg_get_constraintdef(oid) from pg_constraint where conname = 'calibration_category_matches_direction'")
        inflow_part, _, outflow_part = definition.partition("'outflow'")
        directions = {"inflow", "outflow"}
        in_db = set(re.findall(r"'([^']+)'::text", inflow_part)) - directions, set(re.findall(r"'([^']+)'::text", outflow_part)) - directions
        check("category_constraint_matches_the_classifier_vocabulary", in_db == (set(CASHFLOW_INFLOW_CATEGORIES), set(CASHFLOW_OUTFLOW_CATEGORIES)), f"{sorted(in_db[0])} / {sorted(in_db[1])}")

        # ---- the committed bank, then the disposable set, both through the real loader ----
        seeded = subprocess.run([sys.executable, "-m", "app.discovery.seed"], cwd=ROOT / "services" / "api", capture_output=True, text=True)
        check("seed_loads_the_committed_bank_idempotently", seeded.returncode == 0, (seeded.stdout.strip().splitlines() or [seeded.stderr[-200:]])[-1])
        loaded = seed(tmp)
        check("seed_loads_the_check_fixtures", loaded.returncode == 0, (loaded.stdout.strip().splitlines() or [loaded.stderr[-200:]])[-1])
        st, template = http(f"{rest}discovery_templates?slug=eq.{TEMPLATE_SLUG}&select=id", headers=admin_h)
        if st != 200 or not template:
            raise RuntimeError("check template missing after seed")
        template_id = template[0]["id"]
        st, qrows = http(f"{rest}discovery_questions?question_key=like.{PREFIX}.*&select=id,question_key", headers=admin_h)
        qid = {r["question_key"]: r["id"] for r in qrows or []}
        st, cases = http(f"{rest}discovery_transaction_cases?question_id=in.({','.join(qid.values())})&select=question_id,case_family,variant,changed_feature,synthetic,direction,baseline_question_id,authored_changed_feature,reveal_of_question_id,reveal", headers=admin_h)
        by_q = {c["question_id"]: c for c in cases or []}
        families = {c["case_family"] for c in cases or []}
        check("eight_synthetic_cases_in_five_families", len(cases or []) == 8 and len(families) == 5 and all(c["synthetic"] for c in cases), f"{len(cases or [])} cases, {len(families)} families")
        check("one_typed_variant_proved_by_the_loader", sum(1 for c in cases if c["changed_feature"]) == 1 and by_q[qid[f"{PREFIX}.uber.tuesday"]]["changed_feature"] == "day_of_week")
        late = by_q[qid[f"{PREFIX}.uber.late"]]
        check("a_claimed_variant_names_its_baseline_and_the_authored_feature", late["baseline_question_id"] == qid[f"{PREFIX}.uber.baseline"] and late["authored_changed_feature"] == "timing" and late["changed_feature"] is None and late["case_family"] == "checkcalib-uber")
        stage, original = by_q[qid[f"{PREFIX}.zelle.reveal"]], by_q[qid[f"{PREFIX}.zelle"]]
        check("a_reveal_stage_names_its_original_and_the_original_keeps_the_fact", stage["reveal_of_question_id"] == qid[f"{PREFIX}.zelle"] and original["reveal"] == {"added_context": FACT} and stage["reveal"] is None and stage["baseline_question_id"] is None)
        baselines = psql(f"select count(*) from public.discovery_transaction_cases where case_family = 'checkcalib-uber' and changed_feature is null and baseline_question_id is null and reveal_of_question_id is null and question_id in ('{qid[PREFIX + '.uber.baseline']}', '{qid[PREFIX + '.uber.late']}', '{qid[PREFIX + '.uber.tuesday']}')")
        check("a_family_with_a_typed_and_a_claimed_variant_still_has_one_baseline", baselines == "1", baselines)

        # ---- two stakeholders on the same template ----
        for label in ("a", "b"):
            email, password = f"calibcheck-{label}-{tag}@example.org", secrets.token_urlsafe(18)
            st, created = http(f"{base}/auth/v1/admin/users", "POST", {"email": email, "password": password, "email_confirm": True}, admin_h)
            if st not in (200, 201):
                raise RuntimeError(f"could not create user {label}: {st}")
            users[label] = {"id": created["id"], "email": email, "password": password}
            http(f"{rest}profiles?user_id=eq.{created['id']}", "PATCH", {"role": "stakeholder", "first_name": label.upper()}, minimal)
            st, assignment = http(f"{rest}discovery_assignments", "POST", {"user_id": created["id"], "template_id": template_id}, {**admin_h, "Prefer": "return=representation"})
            if st != 201:
                raise RuntimeError(f"could not assign {label}: {st} {assignment}")
            users[label]["assignment"] = assignment[0]["id"]
            users[label]["token"] = sign_in(email, password)
            users[label]["auth"] = {"authorization": f"Bearer {users[label]['token']}", **CLIENT}
        a, b = users["a"], users["b"]
        A, B = f"/api/discovery/assignments/{a['assignment']}", f"/api/discovery/assignments/{b['assignment']}"

        # ---- the payload: the case, nothing that links cases, no prediction ----
        st, section = api(f"{A}/sections/{SECTION_SLUG}", "GET", None, a["auth"])
        questions = section["questions"] if st == 200 else []
        by_key = {q["question_key"]: q for q in questions}
        check("section_serves_eight_calibration_questions_each_with_its_case", st == 200 and len(questions) == 8 and all(q["question_type"] == "transaction_calibration" and q["transaction_case"] for q in questions), f"HTTP {st}")
        text = json.dumps(section)
        forbidden = ("case_family", '"variant"', "changed_feature", "baseline_question", "reveal_of", "authored_changed", '"reveal":', "prediction", "cashflow", "model_", 'scenario_brief": {')
        check("payload_carries_no_family_relationship_or_prediction", all(w not in text for w in forbidden), " ".join(w for w in forbidden if w in text))
        uber = by_key[f"{PREFIX}.uber.baseline"]
        reversal = by_key[f"{PREFIX}.reversal"]
        check("case_direction_decides_the_offered_categories", uber["transaction_case"]["direction"] == "outflow" and uber["transaction_case"]["categories"] == OUTFLOW and reversal["transaction_case"]["direction"] == "inflow" and reversal["transaction_case"]["categories"] == INFLOW)
        check("related_rows_and_context_are_served_for_the_cases_that_have_them", len(reversal["transaction_case"]["related_transactions"]) == 1 and by_key[f"{PREFIX}.zelle"]["transaction_case"]["context"].get("pattern"))
        check("the_reveal_stage_shows_the_added_fact_and_the_original_does_not", by_key[f"{PREFIX}.zelle.reveal"]["transaction_case"]["context"].get("one more thing") == FACT and "one more thing" not in by_key[f"{PREFIX}.zelle"]["transaction_case"]["context"] and FACT not in json.dumps(by_key[f"{PREFIX}.zelle"]))
        check("the_claimed_variant_is_served_as_an_ordinary_case", by_key[f"{PREFIX}.uber.late"]["transaction_case"]["descriptor"] == "UBER *TRIP 8N2K4" and by_key[f"{PREFIX}.uber.late"]["transaction_case"]["context"].get("pattern"))

        # ---- validation through the API ----
        q_uber, q_rev, q_variant = uber["id"], reversal["id"], by_key[f"{PREFIX}.uber.tuesday"]["id"]
        refusals = [
            api(f"{A}/responses/{q_uber}", "PUT", {"calibration": {**A_FIRST["calibration"], "classification_category": "Organic revenue"}}, a["auth"])[0],
            api(f"{A}/responses/{q_rev}", "PUT", {"calibration": {**B_FIRST["calibration"], "classification_category": "Operating expense"}}, a["auth"])[0],
            api(f"{A}/responses/{q_uber}", "PUT", {"calibration": {**A_FIRST["calibration"], "classification_confidence": 6}}, a["auth"])[0],
            api(f"{A}/responses/{q_uber}", "PUT", {"calibration": {**A_FIRST["calibration"], "direction": "inflow"}}, a["auth"])[0],
            api(f"{A}/responses/{q_uber}", "PUT", {"calibration": {**A_FIRST["calibration"], "underwriting_signal": "bullish"}}, a["auth"])[0],
            api(f"{A}/responses/{q_uber}", "PUT", {"answer": "Personal expense"}, a["auth"])[0],
        ]
        check("api_refuses_wrong_direction_category_bad_confidence_smuggled_direction_bad_enum_plain_answer", all(r == 422 for r in refusals), " ".join(str(r) for r in refusals))
        st, rows = http(f"{rest}discovery_responses?assignment_id=eq.{a['assignment']}&select=id", headers=admin_h)
        check("nothing_was_written_by_the_refusals", st == 200 and rows == [])

        # ---- two answers to one question ----
        st, a_saved = api(f"{A}/responses/{q_uber}", "PUT", A_FIRST, a["auth"])
        st2, b_saved = api(f"{B}/responses/{q_uber}", "PUT", B_FIRST, b["auth"])
        check("both_stakeholders_save_their_own_reading", st == 200 and st2 == 200 and a_saved["response"]["calibration"]["classification_category"] == "Personal expense" and b_saved["response"]["calibration"]["classification_category"] == "Operating expense", f"{st} {st2}")
        check("the_stored_direction_is_the_cases", st == 200 and a_saved["response"]["calibration"]["direction"] == "outflow")
        st, generic = http(f"{rest}discovery_responses?question_id=eq.{q_uber}&select=id,user_id", headers=admin_h)
        st2, typed = http(f"{rest}discovery_transaction_calibration_responses?question_id=eq.{q_uber}&select=response_id,user_id,classification_confidence,underwriting_materiality,underwriting_signal", headers=admin_h)
        check("same_question_two_generic_rows_two_typed_rows_two_owners", len(generic) == 2 and len(typed) == 2 and len({r["id"] for r in generic}) == 2 and len({t["response_id"] for t in typed}) == 2 and {t["user_id"] for t in typed} == {a["id"], b["id"]}, f"{len(generic or [])}/{len(typed or [])}")
        check("confidence_materiality_and_signal_differ_between_them", {(t["classification_confidence"], t["underwriting_materiality"], t["underwriting_signal"]) for t in typed} == {(4, "low", "negative"), (3, "medium", "neutral")})
        st, a_view = api(f"{A}/sections/{SECTION_SLUG}", "GET", None, a["auth"])
        st2, b_view = api(f"{B}/sections/{SECTION_SLUG}", "GET", None, b["auth"])
        a_ans = next(q for q in a_view["questions"] if q["id"] == q_uber)["response"]["calibration"]
        b_ans = next(q for q in b_view["questions"] if q["id"] == q_uber)["response"]["calibration"]
        check("each_sees_only_their_own_calibration", a_ans["classification_category"] == "Personal expense" and b_ans["classification_category"] == "Operating expense" and "Saturday-night" not in json.dumps(b_view))
        cross = [api(B, "GET", None, a["auth"])[0], api(f"{B}/sections/{SECTION_SLUG}", "GET", None, a["auth"])[0], api(f"{B}/responses/{q_uber}/history", "GET", None, a["auth"])[0], api(f"{B}/responses/{q_uber}", "PUT", A_FIRST, a["auth"])[0], api(f"{B}/responses/{q_variant}/skip", "POST", None, a["auth"])[0]]
        check("a_cannot_read_or_write_bs_assignment", all(c == 404 for c in cross), " ".join(str(c) for c in cross))
        st, b_after = http(f"{rest}discovery_transaction_calibration_responses?question_id=eq.{q_uber}&user_id=eq.{b['id']}&select=classification_category", headers=admin_h)
        check("bs_answer_is_unchanged_after_as_attempts", b_after == [{"classification_category": "Operating expense"}])
        st, a_over = api(A, "GET", None, a["auth"])
        st2, b_over = api(B, "GET", None, b["auth"])
        check("progress_is_independent", a_over["progress"]["answered"] == 1 and b_over["progress"]["answered"] == 1 and a_over["progress"]["total"] == 8)

        # ---- a reveal: two readings of one row, comparable field by field ----
        q_zelle, q_stage = by_key[f"{PREFIX}.zelle"]["id"], by_key[f"{PREFIX}.zelle.reveal"]["id"]
        before = {"calibration": {"classification_category": "Payroll", "classification_confidence": 4, "underwriting_materiality": "high", "underwriting_signal": "negative"}}
        after = {"calibration": {"classification_category": "Transfer out", "classification_confidence": 3, "underwriting_materiality": "medium", "underwriting_signal": "neutral"}}
        st, _ = api(f"{A}/responses/{q_zelle}", "PUT", before, a["auth"])
        st2, _ = api(f"{A}/responses/{q_stage}", "PUT", after, a["auth"])
        st3, pair = http(f"{rest}discovery_transaction_calibration_responses?user_id=eq.{a['id']}&question_id=in.({q_zelle},{q_stage})&select=question_id,classification_category,classification_confidence,underwriting_materiality,underwriting_signal", headers=admin_h)
        readings = {p["question_id"]: p for p in pair or []}
        check("a_reveal_is_answered_apart_from_its_original_so_the_two_readings_compare", st == 200 and st2 == 200 and len(readings) == 2 and readings[q_zelle]["classification_category"] == "Payroll" and readings[q_stage]["classification_category"] == "Transfer out" and by_q[q_stage]["reveal_of_question_id"] == q_zelle, f"{st} {st2} {len(readings)}")

        # ---- inflow case, optional round trip ----
        st, rev = api(f"{A}/responses/{q_rev}", "PUT", {"calibration": {"classification_category": "Overdraft/Reversals", "classification_confidence": 5, "underwriting_materiality": "immaterial", "underwriting_signal": "neutral", "needs_more_context": True, "context_needed": "the matching debit's descriptor", "handling_recommendation": "auto_classify"}}, a["auth"])
        check("inflow_category_on_an_inflow_case_round_trips_with_optional_fields", st == 200 and rev["response"]["calibration"]["direction"] == "inflow" and rev["response"]["calibration"]["context_needed"] == "the matching debit's descriptor" and rev["response"]["calibration"]["handling_recommendation"] == "auto_classify", f"HTTP {st}")

        # ---- history through every field, then a skip ----
        current = dict(A_FIRST["calibration"])
        steps = [("classification_category", "Operating expense"), ("classification_confidence", 2), ("underwriting_materiality", "high"), ("underwriting_signal", "positive"), ("decision_impact", "manual_review"), ("reason_codes", ["vertical_decides"]), ("needs_more_context", True), ("context_needed", "who was driving"), ("reasoning", "changed my mind"), ("handling_recommendation", "human_review")]
        priors = []
        for field_name, value in steps:
            priors.append(dict(current))
            current[field_name] = value
            if field_name == "context_needed":
                current["needs_more_context"] = True
            st, _ = api(f"{A}/responses/{q_uber}", "PUT", {"calibration": current}, a["auth"])
            if st != 200:
                raise RuntimeError(f"calibration change {field_name}: {st} {_}")
        st, hist = api(f"{A}/responses/{q_uber}/history", "GET", None, a["auth"])
        entries = [e["calibration"] for e in hist["entries"] if e.get("calibration")] if st == 200 else []
        keys = ("classification_category", "classification_confidence", "underwriting_materiality", "underwriting_signal", "decision_impact", "reason_codes", "needs_more_context", "context_needed", "reasoning", "handling_recommendation")
        expected = [{k: p.get(k) for k in keys} for p in reversed(priors)]
        got = [{k: e[k] for k in keys} for e in entries]
        check("calibration_history_holds_every_prior_value_for_each_field_changed", st == 200 and got == expected, f"{len(entries)} entries")
        st, sk = api(f"{A}/responses/{q_variant}", "PUT", B_FIRST, a["auth"])
        st2, skipped = api(f"{A}/responses/{q_variant}/skip", "POST", None, a["auth"])
        st3, typed_left = http(f"{rest}discovery_transaction_calibration_responses?question_id=eq.{q_variant}&user_id=eq.{a['id']}&select=response_id", headers=admin_h)
        st4, vh = api(f"{A}/responses/{q_variant}/history", "GET", None, a["auth"])
        deleted = [e for e in vh["entries"] if e["change"] == "deleted" and e.get("calibration")] if st4 == 200 else []
        check("skipping_an_answered_optional_case_removes_the_typed_row_and_records_it", st == 200 and st2 == 200 and skipped["response"]["status"] == "skipped" and typed_left == [] and len(deleted) == 1 and deleted[0]["calibration"]["classification_category"] == "Operating expense", f"{st} {st2} typed={len(typed_left or [])} deleted={len(deleted)}")

        # ---- the write function: atomic, and not a browser surface ----
        st, before_rows = http(f"{rest}discovery_responses?assignment_id=eq.{b['assignment']}&question_id=eq.{q_rev}&select=id", headers=admin_h)
        st, bad = http(f"{rest}rpc/{RPC}", "POST", {"p_assignment_id": b["assignment"], "p_user_id": b["id"], "p_template_id": template_id, "p_section_id": section["id"], "p_question_id": q_rev, "p_question_version": 1, "p_question_type": "transaction_calibration", "p_status": "answered", "p_note": None, "p_typed": {"classification_category": "Organic revenue", "classification_confidence": 6, "underwriting_materiality": "low", "underwriting_signal": "neutral"}}, admin_h)
        st2, after_rows = http(f"{rest}discovery_responses?assignment_id=eq.{b['assignment']}&question_id=eq.{q_rev}&select=id", headers=admin_h)
        check("a_typed_write_the_database_refuses_leaves_no_generic_row", st == 400 and before_rows == [] and after_rows == [], f"HTTP {st}")
        st, hijack = http(f"{rest}rpc/{RPC}", "POST", {"p_assignment_id": b["assignment"], "p_user_id": b["id"], "p_template_id": template_id, "p_section_id": section["id"], "p_question_id": q_rev, "p_question_version": 1, "p_question_type": "transaction_calibration", "p_status": "answered", "p_note": None, "p_typed": B_FIRST["calibration"] | {"classification_category": "Organic revenue"}}, {"apikey": anon, "Authorization": f"Bearer {a['token']}"})
        check("the_write_function_is_not_callable_with_a_stakeholders_token", st in (401, 403, 404), f"HTTP {st}")
        st, direct = http(f"{rest}discovery_transaction_cases?select=question_id", headers={"apikey": anon, "Authorization": f"Bearer {a['token']}"})
        st2, direct2 = http(f"{rest}discovery_transaction_calibration_responses?select=response_id", headers={"apikey": anon, "Authorization": f"Bearer {a['token']}"})
        check("own_token_reaches_neither_the_cases_nor_the_answers_at_the_data_api", st in (401, 403) and st2 in (401, 403), f"{st} {st2}")
        st, anon_rpc = http(f"{rest}rpc/{RPC}", "POST", {}, {"apikey": anon})
        check("the_write_function_is_not_callable_anonymously", st in (401, 403, 404), f"HTTP {st}")
    except RuntimeError as exc:
        check("aborted", False, str(exc))
    finally:
        for u in users.values():
            try:
                script.remove_account(script.Admin(), u["id"])
            except RuntimeError:
                pass
        left = []
        for u in users.values():
            left += script.leftovers(script.Admin(), u["id"])
            st, cal = http(f"{rest}discovery_transaction_calibration_responses?user_id=eq.{u['id']}&select=response_id", headers=admin_h)
            st2, calh = http(f"{rest}discovery_transaction_calibration_response_history?user_id=eq.{u['id']}&select=id", headers=admin_h)
            left += [f"calibration:{len(cal)}" for _ in [0] if cal] + [f"calibration_history:{len(calh)}" for _ in [0] if calh]
        check("throwaway_users_and_their_calibration_rows_removed", not left, ", ".join(left))
        # the disposable bank: links, questions (their cases cascade; the deferred keys settle at
        # the end of the one statement), section, template
        st, templates = http(f"{rest}discovery_templates?slug=eq.{TEMPLATE_SLUG}&select=id", headers=admin_h)
        for t in templates or []:
            http(f"{rest}discovery_template_sections?template_id=eq.{t['id']}", "DELETE", None, admin_h)
        st, gone = http(f"{rest}discovery_questions?question_key=like.{PREFIX}.*", "DELETE", None, admin_h)
        http(f"{rest}discovery_sections?slug=eq.{SECTION_SLUG}", "DELETE", None, admin_h)
        http(f"{rest}discovery_templates?slug=eq.{TEMPLATE_SLUG}", "DELETE", None, admin_h)
        remaining = []
        for table, filt in (("discovery_templates", f"slug=eq.{TEMPLATE_SLUG}"), ("discovery_sections", f"slug=eq.{SECTION_SLUG}"), ("discovery_questions", f"question_key=like.{PREFIX}.*")):
            st, rows = http(f"{rest}{table}?{filt}&select=id", headers=admin_h)
            remaining += [f"{table}:{r['id']}" for r in rows or []]
        st, cases_left = http(f"{rest}discovery_transaction_cases?case_family=like.checkcalib-*&select=question_id", headers=admin_h)
        check("check_fixtures_and_their_cases_removed", not remaining and not cases_left, ", ".join(remaining) or f"{len(cases_left or [])} cases left")
        st, library_after = http(f"{rest}discovery_questions?question_key=like.tc-*&retired_at=is.null&select=id", headers=admin_h)
        check("the_real_calibration_library_is_untouched", st == 200 and len(library_after or []) == len(library_before or []) > 0, f"{len(library_after or [])} live cases")
        st, remaining_users = http(f"{base}/auth/v1/admin/users?page=1&per_page=100", headers=admin_h)
        stale = [x for x in (remaining_users or {}).get("users", []) if tag in (x.get("email") or "")]
        check("throwaway_users_deleted", not stale, f"{len(stale)} left")
    failures = sum(1 for _, ok, _ in results if not ok)
    print(f"{len(results) - failures}/{len(results)} discovery calibration checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
