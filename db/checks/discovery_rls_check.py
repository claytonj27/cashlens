#!/usr/bin/env python3
"""Access and structural checks for the Discovery tables, against the development database.

The unit tests cannot answer these: the fake enforces no grants, no foreign keys and no
triggers, and the API bypasses RLS anyway. Two things are checked here, and only a real
Postgres can check either:

  who may reach these tables    anon and authenticated have no privilege at all; service_role
                                has all four verbs; RLS is enabled on all eleven with no
                                policy; and a temporary grant still yields zero rows, which is
                                what makes "RLS is the second lock" a fact rather than a claim.

  what the tables refuse        the composite ownership and membership keys, the resume
                                pointer bound to the template's own section list, the check
                                constraints, and both history triggers, driven by raw SQL so
                                the proof is about the database and not about the API.

Runs one transaction as the postgres role, creates two throwaway auth users with everything
hanging off them, asserts, and rolls back. Reports each check as PASS or FAIL and exits
non-zero on any failure. Follows db/checks/rls_check.py.

    python3 db/checks/discovery_rls_check.py
"""

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / "services" / "api" / ".env"

LEAD, PARTNER = str(uuid.uuid4()), str(uuid.uuid4())
T_LEAD, T_PARTNER = str(uuid.uuid4()), str(uuid.uuid4())
S_CORE, S_LEAD, S_PARTNER = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
Q_TEXT, Q_SCENARIO, Q_LEAD, Q_PARTNER, Q_CALIB = (str(uuid.uuid4()) for _ in range(5))
A_LEAD, A_PARTNER = str(uuid.uuid4()), str(uuid.uuid4())
R_TEXT, R_SCENARIO = str(uuid.uuid4()), str(uuid.uuid4())

TABLES = (
    "profiles",
    "discovery_templates",
    "discovery_sections",
    "discovery_template_sections",
    "discovery_questions",
    "discovery_assignments",
    "discovery_responses",
    "discovery_response_history",
    "discovery_scenario_responses",
    "discovery_scenario_response_history",
    "discovery_sessions",
    # phase 3.5, migration 0027
    "discovery_transaction_cases",
    "discovery_transaction_calibration_responses",
    "discovery_transaction_calibration_response_history",
)
TABLE_LIST = ", ".join(f"'{t}'" for t in TABLES)
CURRENT_SECTION_FK = "discovery_assignments_template_id_current_section_id_fkey"


def database_url() -> str:
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url and ENV_FILE.exists():
        match = re.search(r"^SUPABASE_DB_URL=(.*)$", ENV_FILE.read_text(encoding="utf-8"), re.M)
        url = match.group(1).strip() if match else ""
    if not url:
        sys.exit("SUPABASE_DB_URL is not set")
    return url


# ---------------------------------------------------------------- notice helpers --
def attempt(name: str, statement: str, expect: str) -> str:
    """ALLOWED or DENIED by privilege."""
    return f"""
do $$ begin
  {statement};
  raise notice 'CHECK {name} expect={expect} got=ALLOWED';
exception when insufficient_privilege then
  raise notice 'CHECK {name} expect={expect} got=DENIED';
end $$;
"""


def refused(name: str, statement: str, condition: str) -> str:
    """ALLOWED or REFUSED by a named constraint class (foreign_key_violation, check_violation)."""
    return f"""
do $$ begin
  {statement};
  raise notice 'CHECK {name} expect=REFUSED got=ALLOWED';
exception when {condition} then
  raise notice 'CHECK {name} expect=REFUSED got=REFUSED';
end $$;
"""


def allowed(name: str, statement: str) -> str:
    """The positive case: the statement must succeed, so a constraint is not refusing everything."""
    return f"""
do $$ begin
  {statement};
  raise notice 'CHECK {name} expect=ALLOWED got=ALLOWED';
exception when others then
  raise notice 'CHECK {name} expect=ALLOWED got=%', sqlstate;
end $$;
"""


def refused_by(name: str, statement: str, constraint: str) -> str:
    """Refused by exactly the named constraint, read back from the error's own diagnostics."""
    return f"""
do $$ declare v_constraint text; begin
  {statement};
  raise notice 'CHECK {name} expect={constraint} got=ALLOWED';
exception when foreign_key_violation then
  get stacked diagnostics v_constraint = constraint_name;
  raise notice 'CHECK {name} expect={constraint} got=%', v_constraint;
end $$;
"""


def count(name: str, query: str, expect: int) -> str:
    return (
        f"do $$ declare n int; begin select ({query}) into n; "
        f"raise notice 'CHECK {name} expect={expect} got=%', n; end $$;\n"
    )


def equals(name: str, query: str, expect: str) -> str:
    """A scalar compared as text. Keep expected values free of whitespace."""
    return (
        f"do $$ declare v text; begin select ({query})::text into v; "
        f"raise notice 'CHECK {name} expect={expect} got=%', coalesce(v, 'NULL'); end $$;\n"
    )


def read_denied_for_every_table(role: str) -> str:
    return "".join(
        attempt(f"{role}_cannot_read_{t}", f"perform 1 from public.{t}", "DENIED") for t in TABLES
    )


# ---------------------------------------------------------------- statements --
# Every statement that carries a double quote (the "order" column, JSON keys) lives here in a
# triple-single-quoted string, so the big script below never needs a backslash inside an
# f-string expression, which Python before 3.12 refuses.

QUESTION_COLUMNS = (
    "section_id, question_key, question_text, question_type, "
    '"order", required_core, options, scenario_brief'
)
SEED_QUESTIONS = f'''insert into public.discovery_questions (id, {QUESTION_COLUMNS}) values
  ('{Q_TEXT}',     '{S_CORE}',   'check.core.text',     'A shared text question?', 'short_text',        0, true,  '[]'::jsonb, null),
  ('{Q_SCENARIO}', '{S_CORE}',   'check.core.scenario', 'Would you approve?',      'scenario_decision', 1, true,  '[{{"value": "thin_file", "label": "Thin file"}}, {{"value": "income_unverified", "label": "Income unverified"}}]'::jsonb, '{{"applicant": "synthetic", "requested_limit_minor": 500000}}'::jsonb),
  ('{Q_LEAD}',   '{S_LEAD}', 'check.lead.only',   'Only Lead sees this',   'short_text',        0, false, '[]'::jsonb, null),
  ('{Q_PARTNER}',     '{S_PARTNER}',   'check.partner.only',     'Only Partner sees this',     'boolean',           0, false, '[]'::jsonb, null);'''

OPTIONS_A = '[{"value": "a", "label": "A"}]'
BAD_SCENARIO_NO_OPTIONS = f'''insert into public.discovery_questions (section_id, question_key, question_text, question_type, "order", options, scenario_brief) values ('{S_CORE}', 'check.bad.scn1', 'x', 'scenario_decision', 90, '[]'::jsonb, '{{}}'::jsonb)'''
BAD_SCENARIO_NO_BRIEF = f'''insert into public.discovery_questions (section_id, question_key, question_text, question_type, "order", options) values ('{S_CORE}', 'check.bad.scn2', 'x', 'scenario_decision', 91, '{OPTIONS_A}'::jsonb)'''
BAD_TEXT_WITH_BRIEF = f'''insert into public.discovery_questions (section_id, question_key, question_text, question_type, "order", scenario_brief) values ('{S_CORE}', 'check.bad.txt1', 'x', 'short_text', 92, '{{}}'::jsonb)'''
BAD_TEXT_WITH_OPTIONS = f'''insert into public.discovery_questions (section_id, question_key, question_text, question_type, "order", options) values ('{S_CORE}', 'check.bad.txt2', 'x', 'short_text', 93, '{OPTIONS_A}'::jsonb)'''
BAD_HIGH_SENSITIVITY_CORE = f'''insert into public.discovery_questions (section_id, question_key, question_text, question_type, "order", sensitivity, required_core) values ('{S_CORE}', 'check.bad.sens', 'x', 'short_text', 94, 'high', true)'''

RESPONSE_COLUMNS = (
    "assignment_id, user_id, template_id, section_id, question_id, "
    "question_version, question_type, status, answer"
)


def response_insert(
    assignment: str,
    user: str,
    template: str,
    section: str,
    question: str,
    kind: str,
    status: str,
    answer: str,
) -> str:
    return (
        f"insert into public.discovery_responses ({RESPONSE_COLUMNS}) values "
        f"('{assignment}', '{user}', '{template}', '{section}', '{question}', 1, "
        f"'{kind}', '{status}', {answer})"
    )


TEXT_ANSWER = """'"hello"'::jsonb"""
X_ANSWER = """'"x"'::jsonb"""
Y_ANSWER = """'"y"'::jsonb"""
AGAIN_ANSWER = """'"again"'::jsonb"""
CHANGED_ANSWER = """'"changed"'::jsonb"""

SCENARIO_FIELDS = (
    ("decision", "'deny'", "approve"),
    ("confidence", "2", "4"),
    ("reason_codes", "'{income_unverified}'", "{thin_file}"),
    ("maximum_exposure_minor", "250000", "100000"),
    ("additional_information_needed", "'bank statements'", "a"),
    ("what_would_change_my_decision", "'a guarantor'", "b"),
    ("notes", "'revisited'", "c"),
)


def scenario_history_checks() -> str:
    """Change each of the seven typed fields on its own; each must leave one more history row
    carrying the value it replaced."""
    out = []
    for n, (column, new_value, previous) in enumerate(SCENARIO_FIELDS, start=1):
        out.append(
            f"update public.discovery_scenario_responses set {column} = {new_value} "
            f"where response_id = '{R_SCENARIO}';\n"
        )
        out.append(
            count(
                f"scenario_change_{n}_{column}_writes_one_more_history_row",
                f"select count(*) from public.discovery_scenario_response_history "
                f"where response_id = '{R_SCENARIO}'",
                n,
            )
        )
        out.append(
            equals(
                f"scenario_history_{n}_carries_previous_{column}",
                # now() is fixed for the whole transaction, so valid_to and created_at tie;
                # ctid breaks the tie in insertion order for rows appended and never updated.
                f"select {column} from public.discovery_scenario_response_history "
                f"where response_id = '{R_SCENARIO}' order by valid_to desc, ctid desc "
                f"limit 1",
                previous,
            )
        )
    return "".join(out)


# ---- phase 3.5: transaction calibration ----
OPTIONS_CALIB = '[{"value": "merchant_ambiguous", "label": "Ambiguous"}, {"value": "timing_atypical", "label": "Timing"}]'
CALIB_QUESTION = f'''insert into public.discovery_questions (id, section_id, question_key, question_text, question_type, "order", required_core, options) values ('{Q_CALIB}', '{S_CORE}', 'check.core.calib', 'What is this row?', 'transaction_calibration', 2, true, '{OPTIONS_CALIB}'::jsonb);'''
BAD_CALIB_NO_OPTIONS = f'''insert into public.discovery_questions (section_id, question_key, question_text, question_type, "order", options) values ('{S_CORE}', 'check.bad.calib1', 'x', 'transaction_calibration', 95, '[]'::jsonb)'''
CALIB_CASE = f"insert into public.discovery_transaction_cases (question_id, case_family, variant, direction, amount_minor, descriptor) values ('{Q_CALIB}', 'check-uber', 'baseline', 'outflow', -1840, 'UBER *TRIP 8N2K4')"
BAD_CASE_SIGN = f"insert into public.discovery_transaction_cases (question_id, case_family, variant, direction, amount_minor, descriptor) values ('{Q_LEAD}', 'check-bad', 'baseline', 'inflow', -5, 'x')"
BAD_CASE_FEATURE = f"insert into public.discovery_transaction_cases (question_id, case_family, variant, changed_feature, direction, amount_minor, descriptor) values ('{Q_LEAD}', 'check-bad', 'variant', 'colour', 'outflow', -5, 'x')"
BAD_CASE_REAL = f"insert into public.discovery_transaction_cases (question_id, case_family, variant, synthetic, direction, amount_minor, descriptor) values ('{Q_LEAD}', 'check-bad', 'baseline', false, 'outflow', -5, 'x')"
SECOND_BASELINE = f"insert into public.discovery_transaction_cases (question_id, case_family, variant, direction, amount_minor, descriptor) values ('{Q_LEAD}', 'check-uber', 'other', 'outflow', -5, 'x')"
# migration 0028: the authored relationships. Two more calibration questions carry the accepted
# rows; every refused row is aimed at Q_LEAD so it never persists either way.
Q_CALIB_LATE, Q_CALIB_STAGE = str(uuid.uuid4()), str(uuid.uuid4())
CALIB_QUESTION_LATE = f'''insert into public.discovery_questions (id, section_id, question_key, question_text, question_type, "order", required_core, options) values ('{Q_CALIB_LATE}', '{S_CORE}', 'check.core.calib.late', 'What is this row, later?', 'transaction_calibration', 3, false, '{OPTIONS_CALIB}'::jsonb);'''
CALIB_QUESTION_STAGE = f'''insert into public.discovery_questions (id, section_id, question_key, question_text, question_type, "order", required_core, options) values ('{Q_CALIB_STAGE}', '{S_CORE}', 'check.core.calib.stage', 'One more thing about this row?', 'transaction_calibration', 4, false, '{OPTIONS_CALIB}'::jsonb);'''
CASE_COLS = "question_id, case_family, variant, direction, amount_minor, descriptor"
CLAIMED_VARIANT = f"insert into public.discovery_transaction_cases ({CASE_COLS}, baseline_question_id, authored_changed_feature) values ('{Q_CALIB_LATE}', 'check-uber', 'late', 'outflow', -1840, 'UBER *TRIP 8N2K4', '{Q_CALIB}', 'timing')"
REVEAL_STAGE = f"insert into public.discovery_transaction_cases ({CASE_COLS}, reveal_of_question_id) values ('{Q_CALIB_STAGE}', 'check-uber', 'baseline-reveal', 'outflow', -1840, 'UBER *TRIP 8N2K4', '{Q_CALIB}')"
LINK_WITHOUT_FEATURE = f"insert into public.discovery_transaction_cases ({CASE_COLS}, baseline_question_id) values ('{Q_LEAD}', 'check-uber', 'nofeature', 'outflow', -5, 'x', '{Q_CALIB}')"
PROVED_AND_CLAIMED = f"insert into public.discovery_transaction_cases ({CASE_COLS}, changed_feature, baseline_question_id, authored_changed_feature) values ('{Q_LEAD}', 'check-uber', 'both', 'outflow', -5, 'x', 'amount_minor', '{Q_CALIB}', 'amount')"
VARIANT_AND_REVEAL = f"insert into public.discovery_transaction_cases ({CASE_COLS}, baseline_question_id, authored_changed_feature, reveal_of_question_id) values ('{Q_LEAD}', 'check-uber', 'twice', 'outflow', -5, 'x', '{Q_CALIB}', 'x', '{Q_CALIB}')"
# The link keys are deferred so a load may write either row first; made immediate here so the
# refusal is raised inside the block that expects it rather than at commit.
DANGLING_BASELINE = f"set constraints all immediate; insert into public.discovery_transaction_cases ({CASE_COLS}, baseline_question_id, authored_changed_feature) values ('{Q_LEAD}', 'check-uber', 'dangling', 'outflow', -5, 'x', '{uuid.uuid4()}', 'x')"

# Categories without spaces here, because the notice parser reads expected values as one token.
TYPED_OK = json.dumps({"classification_category": "Payroll", "classification_confidence": 4, "underwriting_materiality": "low", "underwriting_signal": "negative", "reason_codes": ["merchant_ambiguous"], "needs_more_context": False})
TYPED_BAD_CONFIDENCE = json.dumps({"classification_category": "Payroll", "classification_confidence": 6, "underwriting_materiality": "low", "underwriting_signal": "negative"})
TYPED_WRONG_DIRECTION = json.dumps({"classification_category": "Organic revenue", "classification_confidence": 4, "underwriting_materiality": "low", "underwriting_signal": "negative"})
TYPED_CHANGED = json.dumps({"classification_category": "Unclassified", "classification_confidence": 4, "underwriting_materiality": "low", "underwriting_signal": "negative", "reason_codes": ["merchant_ambiguous"], "needs_more_context": False})
RPC_SIGNATURE = "public.save_discovery_typed_response(uuid,uuid,uuid,uuid,uuid,integer,public.discovery_question_type,text,text,jsonb)"


def rpc_call(typed: str | None, status: str = "answered") -> str:
    literal = "null" if typed is None else f"'{typed}'::jsonb"
    return (
        f"perform public.save_discovery_typed_response('{A_LEAD}', '{LEAD}', '{T_LEAD}', "
        f"'{S_CORE}', '{Q_CALIB}', 1, 'transaction_calibration', '{status}', null, {literal})"
    )


CALIB_FIELDS = (
    ("classification_category", "'Personal expense'", "Unclassified"),
    ("classification_confidence", "2", "4"),
    ("underwriting_materiality", "'high'", "low"),
    ("underwriting_signal", "'positive'", "negative"),
    ("decision_impact", "'manual_review'", "NULL"),
    ("reason_codes", "'{timing_atypical}'", "{merchant_ambiguous}"),
    ("needs_more_context", "true", "false"),
    ("context_needed", "'the counterparty'", "NULL"),
    ("reasoning", "'looks personal'", "NULL"),
    ("handling_recommendation", "'human_review'", "NULL"),
)


def calibration_history_checks() -> str:
    """Change each typed calibration field on its own; each must leave one more history row
    carrying the value it replaced. Starts from one row already written by the changed RPC call."""
    out = []
    for n, (column, new_value, previous) in enumerate(CALIB_FIELDS, start=2):
        out.append(
            f"update public.discovery_transaction_calibration_responses set {column} = {new_value} "
            f"where question_id = '{Q_CALIB}' and user_id = '{LEAD}';\n"
        )
        out.append(
            count(
                f"calibration_change_{n}_{column}_writes_one_more_history_row",
                f"select count(*) from public.discovery_transaction_calibration_response_history "
                f"where question_id = '{Q_CALIB}' and user_id = '{LEAD}'",
                n,
            )
        )
        out.append(
            equals(
                f"calibration_history_{n}_carries_previous_{column}",
                f"select {column} from public.discovery_transaction_calibration_response_history "
                f"where question_id = '{Q_CALIB}' and user_id = '{LEAD}' "
                f"order by valid_to desc, ctid desc limit 1",
                previous,
            )
        )
    return "".join(out)


SERVICE_ROLE_ALL_VERBS = " and ".join(
    f"has_table_privilege('service_role', 'public.' || t, '{verb}')"
    for verb in ("SELECT", "INSERT", "UPDATE", "DELETE")
)
EVERY_PRIVILEGE = "unnest(array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'])"
LEAD_CLAIMS = f"""select set_config('request.jwt.claims', '{{"sub": "{LEAD}", "role": "authenticated"}}', true);"""

SCRIPT = f"""
begin;

-- ---------------------------------------------------------------- throwaway people --
insert into auth.users (id, instance_id, aud, role, email, created_at, updated_at) values
  ('{LEAD}', '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'discovery-check-lead@example.invalid', now(), now()),
  ('{PARTNER}',   '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'discovery-check-partner@example.invalid', now(), now());

-- the auth.users trigger, and the default it applies
{count("trigger_creates_a_profile_for_each_new_user", f"select count(*) from public.profiles where user_id in ('{LEAD}', '{PARTNER}')", 2)}
{equals("a_new_profile_defaults_to_member", f"select role from public.profiles where user_id = '{LEAD}'", "member")}
update public.profiles set role = 'stakeholder' where user_id in ('{LEAD}', '{PARTNER}');

-- ---------------------------------------------------------------- the question bank --
insert into public.discovery_templates (id, slug, title) values
  ('{T_LEAD}', 'check-founder-lead', 'Business + Underwriting Discovery'),
  ('{T_PARTNER}',   'check-founder-partner',   'Business + Underwriting Discovery');
insert into public.discovery_sections (id, slug, title, is_core) values
  ('{S_CORE}',   'check-founder-core',    'Shared core', true),
  ('{S_LEAD}', 'check-lead-specific', 'Lead only', false),
  ('{S_PARTNER}',   'check-partner-specific',   'Partner only',   false);
-- one shared section in both templates, one specific section each
insert into public.discovery_template_sections (template_id, section_id, "order") values
  ('{T_LEAD}', '{S_CORE}', 0), ('{T_LEAD}', '{S_LEAD}', 1),
  ('{T_PARTNER}',   '{S_CORE}', 0), ('{T_PARTNER}',   '{S_PARTNER}',   1);
{SEED_QUESTIONS}

-- check constraints on questions
{refused("scenario_question_without_options_is_refused", BAD_SCENARIO_NO_OPTIONS, "check_violation")}
{refused("scenario_question_without_a_brief_is_refused", BAD_SCENARIO_NO_BRIEF, "check_violation")}
{refused("text_question_with_a_brief_is_refused", BAD_TEXT_WITH_BRIEF, "check_violation")}
{refused("text_question_with_options_is_refused", BAD_TEXT_WITH_OPTIONS, "check_violation")}
{refused("high_sensitivity_core_question_is_refused", BAD_HIGH_SENSITIVITY_CORE, "check_violation")}

-- ---------------------------------------------------------------- assignments --
insert into public.discovery_assignments (id, user_id, template_id) values
  ('{A_LEAD}', '{LEAD}', '{T_LEAD}'),
  ('{A_PARTNER}',   '{PARTNER}',   '{T_PARTNER}');
{equals("a_new_assignment_has_no_current_section", f"select current_section_id from public.discovery_assignments where id = '{A_LEAD}'", "NULL")}
{allowed("current_section_in_the_template_is_allowed", f"update public.discovery_assignments set current_section_id = '{S_CORE}' where id = '{A_LEAD}'")}
{refused_by("current_section_outside_the_template_is_refused_by_the_named_key", f"update public.discovery_assignments set current_section_id = '{S_PARTNER}' where id = '{A_LEAD}'", CURRENT_SECTION_FK)}
{allowed("current_section_may_be_cleared_to_null", f"update public.discovery_assignments set current_section_id = null where id = '{A_LEAD}'")}

-- ---------------------------------------------------------------- responses: four keys --
insert into public.discovery_responses (id, {RESPONSE_COLUMNS}) values
  ('{R_TEXT}', '{A_LEAD}', '{LEAD}', '{T_LEAD}', '{S_CORE}', '{Q_TEXT}', 1, 'short_text', 'answered', {TEXT_ANSWER});
{refused("response_for_a_user_who_is_not_the_assignment_owner_is_refused", response_insert(A_LEAD, PARTNER, T_LEAD, S_CORE, Q_LEAD, "short_text", "answered", X_ANSWER), "foreign_key_violation")}
{refused("response_naming_a_template_that_is_not_the_assignments_is_refused", response_insert(A_LEAD, LEAD, T_PARTNER, S_CORE, Q_LEAD, "short_text", "answered", X_ANSWER), "foreign_key_violation")}
{refused("response_in_a_section_outside_the_template_is_refused", response_insert(A_LEAD, LEAD, T_LEAD, S_PARTNER, Q_PARTNER, "boolean", "answered", "'true'::jsonb"), "foreign_key_violation")}
{refused("response_to_a_question_outside_its_section_is_refused", response_insert(A_LEAD, LEAD, T_LEAD, S_CORE, Q_LEAD, "short_text", "answered", X_ANSWER), "foreign_key_violation")}
{refused("response_whose_answer_does_not_match_its_type_is_refused", response_insert(A_LEAD, LEAD, T_LEAD, S_LEAD, Q_LEAD, "short_text", "answered", "'42'::jsonb"), "check_violation")}
{refused("a_skip_with_an_answer_is_refused", response_insert(A_LEAD, LEAD, T_LEAD, S_LEAD, Q_LEAD, "short_text", "skipped", X_ANSWER), "check_violation")}
{refused("a_second_answer_to_the_same_question_is_refused", response_insert(A_LEAD, LEAD, T_LEAD, S_CORE, Q_TEXT, "short_text", "answered", AGAIN_ANSWER), "unique_violation")}

-- ---------------------------------------------------------------- generic history --
{count("first_answer_writes_no_history", f"select count(*) from public.discovery_response_history where response_id = '{R_TEXT}'", 0)}
update public.discovery_responses set answer = {CHANGED_ANSWER} where id = '{R_TEXT}';
{count("changing_an_answer_writes_exactly_one_history_row", f"select count(*) from public.discovery_response_history where response_id = '{R_TEXT}'", 1)}
{equals("history_carries_the_previous_answer", f"select answer from public.discovery_response_history where response_id = '{R_TEXT}'", '"hello"')}
{equals("history_carries_the_version_in_force", f"select question_version from public.discovery_response_history where response_id = '{R_TEXT}'", "1")}
{equals("history_carries_the_type_in_force", f"select question_type from public.discovery_response_history where response_id = '{R_TEXT}'", "short_text")}
{equals("history_records_an_update_as_updated", f"select change from public.discovery_response_history where response_id = '{R_TEXT}'", "updated")}
{equals("the_current_row_holds_the_new_answer", f"select answer from public.discovery_responses where id = '{R_TEXT}'", '"changed"')}
{count("history_has_no_foreign_key_so_it_outlives_the_row", "select count(*) from pg_constraint where conrelid = 'public.discovery_response_history'::regclass and contype = 'f'", 0)}

-- ---------------------------------------------------------------- scenario history --
insert into public.discovery_responses (id, {RESPONSE_COLUMNS}) values
  ('{R_SCENARIO}', '{A_LEAD}', '{LEAD}', '{T_LEAD}', '{S_CORE}', '{Q_SCENARIO}', 1, 'scenario_decision', 'answered', null);
insert into public.discovery_scenario_responses (response_id, assignment_id, user_id, question_id, decision, confidence, reason_codes, maximum_exposure_minor, additional_information_needed, what_would_change_my_decision, notes) values
  ('{R_SCENARIO}', '{A_LEAD}', '{LEAD}', '{Q_SCENARIO}', 'approve', 4, '{{thin_file}}', 100000, 'a', 'b', 'c');
{refused("scenario_confidence_out_of_range_is_refused", f"update public.discovery_scenario_responses set confidence = 6 where response_id = '{R_SCENARIO}'", "check_violation")}
{refused("scenario_for_a_user_who_is_not_the_owner_is_refused", f"insert into public.discovery_scenario_responses (response_id, assignment_id, user_id, question_id, decision, confidence) values (gen_random_uuid(), '{A_LEAD}', '{PARTNER}', '{Q_SCENARIO}', 'deny', 1)", "foreign_key_violation")}
{count("first_scenario_answer_writes_no_history", f"select count(*) from public.discovery_scenario_response_history where response_id = '{R_SCENARIO}'", 0)}
{scenario_history_checks()}
{count("all_seven_typed_scenario_fields_have_history_columns", "select count(*) from information_schema.columns where table_schema = 'public' and table_name = 'discovery_scenario_response_history' and column_name in ('decision', 'confidence', 'reason_codes', 'maximum_exposure_minor', 'additional_information_needed', 'what_would_change_my_decision', 'notes')", 7)}

-- deleting the parent response cascades, and both triggers preserve what was deleted
delete from public.discovery_responses where id = '{R_SCENARIO}';
{count("deleting_a_response_writes_a_deleted_history_row", f"select count(*) from public.discovery_response_history where response_id = '{R_SCENARIO}' and change = 'deleted'", 1)}
{count("the_cascade_writes_a_deleted_scenario_history_row", f"select count(*) from public.discovery_scenario_response_history where response_id = '{R_SCENARIO}' and change = 'deleted'", 1)}
{equals("the_deleted_scenario_row_carries_its_last_decision", f"select decision from public.discovery_scenario_response_history where response_id = '{R_SCENARIO}' and change = 'deleted'", "deny")}
{count("the_scenario_row_itself_is_gone", f"select count(*) from public.discovery_scenario_responses where response_id = '{R_SCENARIO}'", 0)}

-- ---------------------------------------------------------------- transaction calibration --
{count("transaction_calibration_is_a_question_type", "select count(*) from pg_enum e join pg_type t on t.oid = e.enumtypid where t.typname = 'discovery_question_type' and e.enumlabel = 'transaction_calibration'", 1)}
{refused("calibration_question_without_options_is_refused", BAD_CALIB_NO_OPTIONS, "check_violation")}
{CALIB_QUESTION}
{allowed("a_synthetic_case_is_accepted", CALIB_CASE)}
{refused("case_with_direction_not_matching_the_amount_is_refused", BAD_CASE_SIGN, "check_violation")}
{refused("case_with_an_unknown_changed_feature_is_refused", BAD_CASE_FEATURE, "check_violation")}
{refused("a_real_case_without_authorisation_is_refused", BAD_CASE_REAL, "check_violation")}
{refused("a_second_baseline_in_a_family_is_refused", SECOND_BASELINE, "unique_violation")}

-- migration 0028: a claimed variant names its baseline and feature, a reveal names its original,
-- neither is a baseline, and the malformed combinations are refused
{count("the_case_table_carries_the_four_relationship_columns", "select count(*) from information_schema.columns where table_schema = 'public' and table_name = 'discovery_transaction_cases' and column_name in ('baseline_question_id', 'authored_changed_feature', 'reveal_of_question_id', 'reveal')", 4)}
{refused("a_baseline_link_without_an_authored_feature_is_refused", LINK_WITHOUT_FEATURE, "check_violation")}
{refused("a_variant_both_proved_and_claimed_is_refused", PROVED_AND_CLAIMED, "check_violation")}
{refused("a_case_that_is_both_variant_and_reveal_is_refused", VARIANT_AND_REVEAL, "check_violation")}
{refused("a_link_to_a_case_that_does_not_exist_is_refused", DANGLING_BASELINE, "foreign_key_violation")}
{CALIB_QUESTION_LATE}
{CALIB_QUESTION_STAGE}
{allowed("a_claimed_variant_naming_its_baseline_is_accepted", CLAIMED_VARIANT)}
{allowed("a_reveal_stage_naming_its_original_is_accepted", REVEAL_STAGE)}
{count("a_claimed_variant_and_a_reveal_are_not_baselines", "select count(*) from public.discovery_transaction_cases where case_family = 'check-uber' and changed_feature is null and baseline_question_id is null and reveal_of_question_id is null", 1)}
{refused("a_second_baseline_is_still_refused_beside_them", SECOND_BASELINE, "unique_violation")}

-- the typed write function: one call, both rows, or neither
{refused("rpc_refuses_a_confidence_outside_1_to_5", rpc_call(TYPED_BAD_CONFIDENCE), "check_violation")}
{count("no_generic_row_survives_a_failed_typed_write", f"select count(*) from public.discovery_responses where assignment_id = '{A_LEAD}' and question_id = '{Q_CALIB}'", 0)}
{refused("rpc_refuses_a_category_from_the_other_direction", rpc_call(TYPED_WRONG_DIRECTION), "check_violation")}
{count("no_generic_row_survives_a_wrong_direction_category", f"select count(*) from public.discovery_responses where assignment_id = '{A_LEAD}' and question_id = '{Q_CALIB}'", 0)}
{allowed("rpc_writes_the_generic_and_typed_rows_together", rpc_call(TYPED_OK))}
{count("rpc_left_one_generic_row", f"select count(*) from public.discovery_responses where assignment_id = '{A_LEAD}' and question_id = '{Q_CALIB}' and status = 'answered' and answer is null", 1)}
{count("rpc_left_one_typed_row", f"select count(*) from public.discovery_transaction_calibration_responses where question_id = '{Q_CALIB}' and user_id = '{LEAD}'", 1)}
{equals("the_typed_row_carries_the_cases_direction_not_a_callers", f"select direction from public.discovery_transaction_calibration_responses where question_id = '{Q_CALIB}' and user_id = '{LEAD}'", "outflow")}
{refused("typed_row_with_a_direction_that_is_not_the_cases_is_refused_by_the_composite_key", f"update public.discovery_transaction_calibration_responses set direction = 'inflow', classification_category = 'Unclassified' where question_id = '{Q_CALIB}' and user_id = '{LEAD}'", "foreign_key_violation")}
{refused("typed_row_with_a_category_from_the_other_direction_is_refused", f"update public.discovery_transaction_calibration_responses set classification_category = 'Loans income' where question_id = '{Q_CALIB}' and user_id = '{LEAD}'", "check_violation")}
{refused("context_needed_without_the_flag_is_refused", f"update public.discovery_transaction_calibration_responses set context_needed = 'x', needs_more_context = false where question_id = '{Q_CALIB}' and user_id = '{LEAD}'", "check_violation")}
{count("first_calibration_answer_writes_no_history", f"select count(*) from public.discovery_transaction_calibration_response_history where question_id = '{Q_CALIB}' and user_id = '{LEAD}'", 0)}
{allowed("rpc_updates_in_place_on_a_second_call", rpc_call(TYPED_CHANGED))}
{count("rpc_second_call_is_still_one_generic_row", f"select count(*) from public.discovery_responses where assignment_id = '{A_LEAD}' and question_id = '{Q_CALIB}'", 1)}
{count("rpc_second_call_wrote_one_generic_history_row", f"select count(*) from public.discovery_response_history where assignment_id = '{A_LEAD}' and question_id = '{Q_CALIB}'", 1)}
{count("rpc_second_call_wrote_one_calibration_history_row", f"select count(*) from public.discovery_transaction_calibration_response_history where question_id = '{Q_CALIB}' and user_id = '{LEAD}'", 1)}
{equals("calibration_history_carries_the_previous_category", f"select classification_category from public.discovery_transaction_calibration_response_history where question_id = '{Q_CALIB}' and user_id = '{LEAD}'", "Payroll")}
{calibration_history_checks()}
{allowed("rpc_skip_removes_the_typed_row_in_the_same_call", rpc_call(None, "skipped"))}
{equals("rpc_skip_left_the_generic_row_skipped", f"select status from public.discovery_responses where assignment_id = '{A_LEAD}' and question_id = '{Q_CALIB}'", "skipped")}
{count("rpc_skip_removed_the_typed_row", f"select count(*) from public.discovery_transaction_calibration_responses where question_id = '{Q_CALIB}' and user_id = '{LEAD}'", 0)}
{count("rpc_skip_recorded_the_typed_row_as_deleted", f"select count(*) from public.discovery_transaction_calibration_response_history where question_id = '{Q_CALIB}' and user_id = '{LEAD}' and change = 'deleted'", 1)}
{count("anon_cannot_execute_the_typed_write", f"select count(*) from (select 1 where has_function_privilege('anon', '{RPC_SIGNATURE}', 'execute')) x", 0)}
{count("authenticated_cannot_execute_the_typed_write", f"select count(*) from (select 1 where has_function_privilege('authenticated', '{RPC_SIGNATURE}', 'execute')) x", 0)}
{count("service_role_can_execute_the_typed_write", f"select count(*) from (select 1 where has_function_privilege('service_role', '{RPC_SIGNATURE}', 'execute')) x", 1)}
{count("calibration_history_has_no_foreign_key_so_it_outlives_the_row", "select count(*) from pg_constraint where conrelid = 'public.discovery_transaction_calibration_response_history'::regclass and contype = 'f'", 0)}

-- ---------------------------------------------------------------- the resume pointer --
update public.discovery_assignments set current_section_id = '{S_LEAD}' where id = '{A_LEAD}';
delete from public.discovery_template_sections where template_id = '{T_LEAD}' and section_id = '{S_LEAD}';
{equals("removing_the_template_section_nulls_the_pointer", f"select current_section_id from public.discovery_assignments where id = '{A_LEAD}'", "NULL")}
{equals("removing_the_template_section_leaves_template_id_intact", f"select template_id from public.discovery_assignments where id = '{A_LEAD}'", T_LEAD)}
{refused("a_section_a_template_still_uses_cannot_be_deleted", f"delete from public.discovery_sections where id = '{S_CORE}'", "foreign_key_violation")}

-- ---------------------------------------------------------------- privileges --
{count("every_discovery_table_exists", f"select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and c.relname in ({TABLE_LIST}) and c.relkind = 'r'", len(TABLES))}
{count("rls_is_enabled_on_every_discovery_table", f"select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and c.relname in ({TABLE_LIST}) and c.relrowsecurity", len(TABLES))}
{count("no_policy_ships_on_any_discovery_table", f"select count(*) from pg_policies where schemaname = 'public' and tablename in ({TABLE_LIST})", 0)}
{count("service_role_has_all_four_verbs_on_every_discovery_table", f"select count(*) from unnest(array[{TABLE_LIST}]) t where {SERVICE_ROLE_ALL_VERBS}", len(TABLES))}
{count("anon_holds_no_privilege_on_any_discovery_table", f"select count(*) from unnest(array[{TABLE_LIST}]) t, {EVERY_PRIVILEGE} p where has_table_privilege('anon', 'public.' || t, p)", 0)}
{count("authenticated_holds_no_privilege_on_any_discovery_table", f"select count(*) from unnest(array[{TABLE_LIST}]) t, {EVERY_PRIVILEGE} p where has_table_privilege('authenticated', 'public.' || t, p)", 0)}
{count("no_security_definer_role_helper_exists", "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and p.proname in ('app_role', 'current_app_role', 'is_discovery_admin')", 0)}

-- as anon: permission denied on every table, not zero rows
set local role anon;
{read_denied_for_every_table("anon")}
reset role;

-- as an authenticated stakeholder with a real claim: still permission denied
set local role authenticated;
{LEAD_CLAIMS}
{read_denied_for_every_table("authenticated")}
{attempt("authenticated_cannot_insert_a_response", response_insert(A_LEAD, LEAD, T_LEAD, S_CORE, Q_LEAD, "short_text", "answered", X_ANSWER), "DENIED")}
{attempt("authenticated_cannot_update_a_response", f"update public.discovery_responses set answer = {Y_ANSWER} where id = '{R_TEXT}'", "DENIED")}
{attempt("authenticated_cannot_delete_a_response", f"delete from public.discovery_responses where id = '{R_TEXT}'", "DENIED")}
{attempt("authenticated_cannot_change_a_role", f"update public.profiles set role = 'admin' where user_id = '{LEAD}'", "DENIED")}
reset role;

-- the second lock: a grant that should never happen still yields nothing, because RLS is
-- enabled with no policy to permit anything. R_TEXT is Lead's own row, and he still gets 0.
grant select on public.discovery_responses to authenticated;
set local role authenticated;
{LEAD_CLAIMS}
{count("a_stray_grant_still_yields_zero_rows_under_rls_with_no_policy", "select count(*) from public.discovery_responses", 0)}
reset role;
revoke select on public.discovery_responses from authenticated;

rollback;
"""


def main() -> int:
    result = subprocess.run(
        ["psql", database_url(), "-X", "-q", "-v", "ON_ERROR_STOP=1"],
        input=SCRIPT,
        capture_output=True,
        text=True,
        env={**os.environ, "PGCONNECT_TIMEOUT": "15"},
    )
    checks = re.findall(r"CHECK (\S+) expect=(\S+) got=(\S+)", result.stdout + result.stderr)
    failures = 0
    for name, expect, got in checks:
        ok = expect == got
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<66} expected {expect}, got {got}")
    if result.returncode != 0:
        print("psql error:", result.stderr.strip()[-2000:])
        return 1
    print(f"{len(checks) - failures}/{len(checks)} discovery checks passed")
    return 1 if failures or not checks else 0


if __name__ == "__main__":
    sys.exit(main())
