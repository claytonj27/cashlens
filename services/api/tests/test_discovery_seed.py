"""The question-bank loader: what it admits, what it refuses, and what its SQL would do.

The committed phase 2 fixture must validate; every rule the schema document gives the loader
must refuse a fixture that breaks it; and the statements it builds must be the idempotent,
non-destructive ones the design promises. Nothing here touches a database: `build_sql` returns
text, and the text is what is asserted on.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.discovery.seed import (
    FIXTURES,
    Bank,
    Question,
    Section,
    Template,
    build_sql,
    load_bank,
    main,
    materialise_cases,
    question_id,
    section_id,
    template_id,
    validate,
)


def question(**overrides: object) -> Question:
    base = Question(
        question_key="t.short",
        question_text="A short one?",
        question_type="short_text",
        order=0,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def bank(*questions: Question, slug: str = "test-section") -> Bank:
    section = Section(slug=slug, title="Test", questions=list(questions))
    template = Template(slug="test-template", title="Test", sections=[slug])
    return Bank(templates=[template], sections={slug: section})


# ---------------------------------------------------------------- the fixture --
# The committed fixture is the two synthetic proof templates; the real bank is not in this snapshot.
REVEAL_STAGES = {"tc-06.reveal": "tc-06"}
PROOF_TEMPLATES = ["phase2-discovery-test", "phase35-calibration-test"]
PROOF_SECTIONS = ["phase2-core-test", "phase35-calibration-proof"]


def test_the_committed_fixture_validates_and_lists_the_proof_templates_first() -> None:
    loaded = load_bank()
    assert validate(loaded) == []
    assert [t.slug for t in loaded.templates] == PROOF_TEMPLATES
    for slug in PROOF_SECTIONS:
        assert loaded.sections[slug].retired is True
        assert loaded.sections[slug].is_core is False
    types = [x.question_type for x in loaded.sections["phase2-core-test"].questions]
    assert types == ["short_text", "boolean", "single_select", "currency", "scenario_decision"]


def test_the_real_bank_uses_only_types_the_portal_can_answer() -> None:
    loaded = load_bank()
    live = [x for s in loaded.sections.values() if not s.retired for x in s.questions]
    assert {x.question_type for x in live} <= {
        "short_text",
        "long_text",
        "single_select",
        "multi_select",
        "ranking",
        "scenario_decision",
        "transaction_calibration",
    }
    for x in live:
        if x.question_type in ("single_select", "multi_select", "ranking"):
            assert len(x.options) >= 2, x.question_key
        if x.sensitivity == "high":
            assert not x.required_core, x.question_key
    # Every borrower scenario is a real scenario question with a brief and reason codes.
    for x in live:
        if x.question_key.startswith("bs-"):
            assert x.question_type == "scenario_decision" and x.scenario_brief and x.options
        if x.question_key.startswith("tc-"):
            assert x.question_type == "transaction_calibration" and x.transaction_case
        if x.question_key.startswith("ds-"):
            assert x.question_type == "long_text" and not x.required_core


def test_a_claimed_variant_must_name_a_baseline_in_its_family() -> None:
    base = calibration("t.base", 0, BASE_CASE)
    late = calibration(
        "t.late",
        1,
        {
            **BASE_CASE,
            "variant": "late",
            "context": {"pattern": "later"},
            "baseline_question_key": "t.base",
            "authored_changed_feature": "timing",
        },
    )
    assert problems(base, late) == []
    cases, _ = materialise_cases(bank(base, late))
    assert cases["t.late"]["baseline_question_key"] == "t.base"
    dangling = calibration(
        "t.dangling",
        2,
        {
            **BASE_CASE,
            "variant": "d",
            "baseline_question_key": "t.ghost",
            "authored_changed_feature": "x",
        },
    )
    assert any("is not a calibration question" in p for p in problems(base, late, dangling))
    elsewhere = calibration(
        "t.elsewhere",
        2,
        {
            **BASE_CASE,
            "case_family": "other-family",
            "variant": "e",
            "baseline_question_key": "t.base",
            "authored_changed_feature": "x",
        },
    )
    assert any("is in another family" in p for p in problems(base, late, elsewhere))
    no_feature = calibration(
        "t.nofeature", 2, {**BASE_CASE, "variant": "n", "baseline_question_key": "t.base"}
    )
    assert any("go together" in p for p in problems(base, late, no_feature))
    both = calibration(
        "t.both",
        2,
        {
            **BASE_CASE,
            "variant": "b",
            "changed_feature": "amount_minor",
            "amount_minor": -99,
            "baseline_question_key": "t.base",
            "authored_changed_feature": "amount",
        },
    )
    assert any("not both" in p for p in problems(base, late, both))


def test_a_reveal_must_match_its_original_in_everything_but_context() -> None:
    base = calibration(
        "t.base", 0, {**BASE_CASE, "reveal": {"added_context": "the driver was the owner"}}
    )
    stage = calibration(
        "t.base.reveal",
        1,
        {
            **BASE_CASE,
            "variant": "baseline-reveal",
            "reveal_of_question_key": "t.base",
            "context": {"one more thing": "the driver was the owner"},
        },
    )
    assert problems(base, stage) == []
    drifted = calibration(
        "t.drift",
        2,
        {
            **BASE_CASE,
            "variant": "drift",
            "reveal_of_question_key": "t.base",
            "amount_minor": -9999,
            "context": {"one more thing": "x"},
        },
    )
    found = problems(base, stage, drifted)
    assert any("differs in amount_minor" in p for p in found), found
    twice = calibration(
        "t.twice",
        2,
        {
            **BASE_CASE,
            "variant": "twice",
            "reveal_of_question_key": "t.base",
            "baseline_question_key": "t.base",
            "authored_changed_feature": "x",
        },
    )
    assert any("variant or a reveal, not both" in p for p in problems(base, stage, twice))
    # A reveal is not a second baseline: the family still has exactly one.
    cases, found = materialise_cases(bank(base, stage))
    assert found == [] and cases["t.base.reveal"]["reveal_of_question_key"] == "t.base"


def test_case_rows_carry_the_relationship_columns_as_ids() -> None:
    base = calibration("t.base", 0, {**BASE_CASE, "reveal": {"added_context": "more"}})
    late = calibration(
        "t.late",
        1,
        {
            **BASE_CASE,
            "variant": "late",
            "context": {"pattern": "later"},
            "baseline_question_key": "t.base",
            "authored_changed_feature": "timing",
        },
    )
    sql = build_sql(bank(base, late))
    late_row = next(
        line
        for line in sql.splitlines()
        if question_id("t.late") in line and "discovery_transaction_cases" in line
    )
    assert "baseline_question_id" in late_row and question_id("t.base") in late_row
    assert "$cl$timing$cl$" in late_row
    base_row = next(
        line
        for line in sql.splitlines()
        if question_id("t.base") in line
        and "discovery_transaction_cases" in line
        and "reveal" in line
    )
    assert "added_context" in base_row


def test_the_committed_calibration_fixture_is_the_phase_3_5_proof() -> None:
    """Six synthetic cases in five families; one family carries a controlled variant."""
    section = load_bank().sections["phase35-calibration-proof"]
    assert all(x.question_type == "transaction_calibration" for x in section.questions)
    assert len(section.questions) == 6
    cases, problems = materialise_cases(load_bank())
    assert problems == []
    cases = {k: c for k, c in cases.items() if k.startswith("calib.")}
    families = {c["case_family"] for c in cases.values()}
    assert len(families) == 5
    variants = [c for c in cases.values() if c["changed_feature"]]
    assert len(variants) == 1 and variants[0]["changed_feature"] == "day_of_week"
    assert all(c["synthetic"] is True for c in cases.values())
    # The variant inherited everything but the day, the time and the date.
    base = cases["calib.uber.restaurant.baseline"]
    tuesday = cases["calib.uber.restaurant.tuesday"]
    assert tuesday["amount_minor"] == base["amount_minor"] == -1840
    assert tuesday["merchant"] == base["merchant"] == "Uber"
    assert (base["day_of_week"], tuesday["day_of_week"]) == (6, 2)
    # Every calibration question got the section's reason codes.
    assert all(len(x.options) == 7 for x in section.questions)
    text = (FIXTURES / "sections" / "phase35-calibration-proof.json").read_text(encoding="utf-8")
    assert "PHASE 3.5 TEST DATA" in text and "SYNTHETIC" in text


def test_the_fixture_is_marked_as_test_data_and_carries_no_real_figures() -> None:
    """Every file says what it is, and the scenario says it is synthetic in its own data."""
    for path in sorted((FIXTURES / "sections").glob("*.json")):
        text = path.read_text(encoding="utf-8")
        if path.stem.startswith("phase"):
            assert "TEST DATA" in text and "RETIRED" in text, path.name
        else:
            assert "Generated by scripts/build_discovery_fixtures.py" in text, path.name
            assert "SYNTHETIC" in text, path.name
    scenario = next(
        x
        for x in load_bank().sections["phase2-core-test"].questions
        if x.question_type == "scenario_decision"
    )
    assert scenario.scenario_brief is not None
    assert scenario.scenario_brief["synthetic"] is True
    assert scenario.options, "a scenario question offers reason codes"


def test_the_high_sensitivity_question_is_the_optional_one() -> None:
    """So the skip control has something to be exercised on, and the core stays askable."""
    questions = load_bank().sections["phase2-core-test"].questions
    high = [x for x in questions if x.sensitivity == "high"]
    assert len(high) == 1
    assert high[0].required_core is False
    assert all(x.required_core for x in questions if x.sensitivity != "high")


def test_check_mode_reads_no_database(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--check"]) == 0
    out = capsys.readouterr().out
    assert (
        "fixtures valid: 2 template(s), 2 section(s), 11 question(s), 6 transaction case(s)" in out
    )


# ------------------------------------------------------------------- stable ids --
def test_ids_are_deterministic_functions_of_their_keys() -> None:
    assert question_id("phase2.test.boolean") == question_id("phase2.test.boolean")
    assert question_id("phase2.test.boolean") != question_id("phase2.test.currency")
    assert section_id("phase2-core-test") != template_id("phase2-core-test")
    assert len(question_id("x.y")) == 36


def test_the_same_fixture_builds_the_same_statements() -> None:
    loaded = load_bank()
    assert build_sql(loaded) == build_sql(loaded)


# ----------------------------------------------------------------- the sql --
def test_every_insert_is_an_upsert_on_its_stable_id() -> None:
    sql = build_sql(load_bank())
    assert sql.count("insert into public.discovery_sections") == 2
    assert sql.count("insert into public.discovery_questions") == 11
    assert sql.count("insert into public.discovery_templates") == 2
    assert sql.count("insert into public.discovery_template_sections") == 2
    assert sql.count("on conflict (id) do update") == 2 + 11 + 2
    assert sql.count("insert into public.discovery_transaction_cases") == 6
    assert sql.count("on conflict (question_id) do update") == 6
    assert "on conflict (template_id, section_id) do update" in sql
    assert question_id("phase2.test.scenario") in sql
    assert sql.rstrip().endswith("notify pgrst, 'reload schema';")


def test_a_wording_change_bumps_the_version_in_place() -> None:
    sql = build_sql(load_bank())
    assert (
        "question_version = case when public.discovery_questions.question_text "
        "is distinct from excluded.question_text "
        "then public.discovery_questions.question_version + 1"
    ) in sql


def test_nothing_answered_is_ever_deleted() -> None:
    """No delete on questions at all; a template link goes only where no response uses it."""
    sql = build_sql(load_bank(), retire=True)
    assert "delete from public.discovery_questions" not in sql
    assert "delete from public.discovery_responses" not in sql
    assert "delete from public.discovery_sections" not in sql
    assert "delete from public.discovery_templates" not in sql
    assert (
        "delete from public.discovery_template_sections ts" in sql
        and "not exists (select 1 from public.discovery_responses r" in sql
    )


def test_retirement_is_opt_in_and_sets_a_timestamp_rather_than_deleting() -> None:
    plain = build_sql(load_bank())
    retiring = build_sql(load_bank(), retire=True)
    assert "retired_at = coalesce(retired_at, now())" not in plain
    assert "pass --retire to retire them" in plain
    assert "retired_at = coalesce(retired_at, now())" in retiring
    # Both committed proof sections are retired, so nothing here is named live again; the
    # clearing upsert is covered by the "written retired and a rerun keeps it so" test below.


def test_a_retired_section_is_written_retired_and_a_rerun_keeps_it_so() -> None:
    """A section marked retired in its fixture has its questions inserted retired and, on
    conflict, kept retired rather than revived; a live section still clears retired_at."""
    live = Section(slug="live-one", title="Live", questions=[question()])
    gone = Section(
        slug="gone-one", title="Gone", retired=True, questions=[question(question_key="t.gone")]
    )
    t = Template(slug="tmpl", title="T", sections=["live-one", "gone-one"])
    sql = build_sql(Bank(templates=[t], sections={"live-one": live, "gone-one": gone}))
    gone_line = next(
        line
        for line in sql.splitlines()
        if question_id("t.gone") in line and "discovery_questions" in line
    )
    live_line = next(
        line
        for line in sql.splitlines()
        if question_id("t.short") in line and "discovery_questions" in line
    )
    assert "retired_at = coalesce(public.discovery_questions.retired_at, now())" in gone_line
    assert (
        gone_line.rstrip(";").split(", ")[-1].startswith("now()")
        or ", now()) on conflict" in gone_line
    )
    assert "retired_at = null" in live_line and ", null) on conflict" in live_line
    # The committed proof sections are exactly this shape.
    committed = build_sql(load_bank())
    proof_line = next(
        line
        for line in committed.splitlines()
        if question_id("phase2.test.boolean") in line and "discovery_questions" in line
    )
    assert "coalesce(public.discovery_questions.retired_at, now())" in proof_line


def test_reordering_moves_rows_aside_before_placing_them() -> None:
    """unique (section_id, order) would otherwise collide half way through a reorder."""
    sql = build_sql(load_bank())
    # By one more than the current maximum, never a constant: a constant shift collided with a
    # question dropped from the fixture but not retired, which kept its shifted position.
    assert (
        'set "order" = "order" + (select coalesce(max("order"), 0) + 1 '
        "from public.discovery_questions where section_id ="
    ) in sql
    assert (
        'set "order" = "order" + (select coalesce(max("order"), 0) + 1 '
        "from public.discovery_template_sections where template_id ="
    ) in sql
    assert "+ 100000" not in sql


def test_a_question_moved_between_sections_moves_or_fails_loudly() -> None:
    """The upsert carries section_id, so a move happens where no answer references the
    question, and where one does the composite key refuses the transaction rather than the
    loader quietly leaving the question where it was."""
    sql = build_sql(load_bank())
    assert "section_id = excluded.section_id" in sql


def test_the_quoting_tag_is_refused_by_check_mode() -> None:
    """A literal containing the dollar-quote tag cannot be quoted; --check says so up front."""
    found = problems(question(question_text="contains $cl$ inside"))
    assert any("cannot be quoted" in p for p in found)


# ------------------------------------------------------- transaction cases --
CODES = [{"value": "merchant_ambiguous", "label": "Ambiguous"}]
BASE_CASE = {
    "case_family": "uber-restaurant",
    "variant": "baseline",
    "synthetic": True,
    "business_vertical": "restaurant",
    "account_type": "checking",
    "direction": "outflow",
    "amount_minor": -1840,
    "descriptor": "UBER *TRIP",
    "merchant": "Uber",
    "payment_rail": "card",
    "posted_date": "2026-03-14",
    "day_of_week": 6,
    "time_of_day": "22:41",
    "frequency_30d": 11,
    "recurrence_pattern": "irregular",
}


def calibration(key: str, order: int, case: dict, **extra: object) -> Question:
    return question(
        question_key=key,
        question_type="transaction_calibration",
        options=CODES,
        transaction_case=case,
        order=order,
        **extra,
    )


def variant(name: str, feature: str | None, **overrides: object) -> dict:
    return {
        "case_family": "uber-restaurant",
        "variant": name,
        "changed_feature": feature,
        "synthetic": True,
        **overrides,
    }


def test_a_variant_that_changes_exactly_its_named_feature_passes() -> None:
    base = calibration("t.base", 0, BASE_CASE)
    tuesday = calibration(
        "t.tuesday",
        1,
        variant(
            "tuesday", "day_of_week", day_of_week=2, time_of_day="09:15", posted_date="2026-03-17"
        ),
    )
    bigger = calibration("t.bigger", 2, variant("bigger", "amount_minor", amount_minor=-14500))
    elsewhere = calibration(
        "t.consult", 3, variant("consulting", "business_vertical", business_vertical="consulting")
    )
    assert problems(base, tuesday, bigger, elsewhere) == []
    cases, _ = materialise_cases(bank(base, tuesday, bigger, elsewhere))
    assert cases["t.bigger"]["merchant"] == "Uber"  # inherited
    assert cases["t.bigger"]["amount_minor"] == -14500
    assert cases["t.tuesday"]["day_of_week"] == 2


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        (
            variant("drift", "amount_minor", amount_minor=-14500, merchant="Lyft"),
            "also differs from the baseline in merchant",
        ),
        (
            variant("drift2", "day_of_week", day_of_week=2, business_vertical="consulting"),
            "also differs from the baseline in business_vertical",
        ),
        (
            variant("undeclared", "amount_minor", merchant="Lyft"),
            "declares changed_feature 'amount_minor' but amount_minor is the same",
        ),
        (
            variant("same", "merchant"),
            "declares changed_feature 'merchant' but merchant is the same",
        ),
        (variant("colour", "colour", merchant="Lyft"), "changed_feature 'colour' is not one of"),
        (variant("real", "merchant", merchant="Lyft", synthetic=False), "must be synthetic"),
        (variant("typo", "merchant", merchant="Lyft", merchnt="x"), "unknown fields: merchnt"),
        (variant("float", "amount_minor", amount_minor=-18.4), "must be an integer in minor units"),
        (
            variant("flip", "amount_minor", amount_minor=1840),
            "direction does not match the sign of amount_minor",
        ),
    ],
    ids=[
        "two-feature",
        "two-feature-timing",
        "undeclared",
        "unchanged",
        "unknown-feature",
        "real",
        "typo",
        "float",
        "sign",
    ],
)
def test_an_uncontrolled_or_unsafe_variant_is_refused(case: dict, expected: str) -> None:
    found = problems(calibration("t.base", 0, BASE_CASE), calibration("t.variant", 1, case))
    assert any(expected in p for p in found), found


def test_a_family_needs_exactly_one_baseline() -> None:
    orphan = calibration("t.orphan", 0, variant("only", "merchant", merchant="Lyft"))
    assert any("has no baseline" in p for p in problems(orphan))
    twin = calibration("t.twin", 1, {**BASE_CASE, "variant": "twin"})
    found = problems(calibration("t.base", 0, BASE_CASE), twin)
    assert any("more than one baseline" in p for p in found)
    duplicate = calibration("t.dup", 1, variant("baseline", "merchant", merchant="Lyft"))
    found = problems(calibration("t.base", 0, BASE_CASE), duplicate)
    assert any("variant names are not unique" in p for p in found)


def test_direction_may_carry_the_amounts_with_it() -> None:
    base = calibration("t.base", 0, BASE_CASE)
    refund = calibration(
        "t.refund", 1, variant("refund", "direction", direction="inflow", amount_minor=1840)
    )
    assert problems(base, refund) == []


def test_a_calibration_question_needs_a_case_and_a_plain_one_must_not_carry_one() -> None:
    bare = question(question_type="transaction_calibration", options=CODES)
    assert any("needs a transaction_case" in p for p in problems(bare))
    plain = question(transaction_case=BASE_CASE)
    assert any("must not carry a transaction_case" in p for p in problems(plain))


def test_section_reason_codes_are_expanded_onto_calibration_questions(tmp_path: Path) -> None:
    write_fixtures(
        tmp_path,
        {
            "slug": "calib",
            "title": "C",
            "calibration_reason_codes": CODES,
            "questions": [
                {
                    "question_key": "c.one",
                    "question_text": "?",
                    "question_type": "transaction_calibration",
                    "order": 0,
                    "transaction_case": BASE_CASE,
                }
            ],
        },
        [{"slug": "calib-template", "title": "C", "sections": ["calib"]}],
    )
    assert main(["--check", "--fixtures", str(tmp_path)]) == 0
    loaded = load_bank(tmp_path)
    assert loaded.sections["calib"].questions[0].options == CODES


def test_a_calibration_question_without_any_reason_codes_is_refused(tmp_path: Path, capsys) -> None:
    write_fixtures(
        tmp_path,
        {
            "slug": "calib",
            "title": "C",
            "questions": [
                {
                    "question_key": "c.one",
                    "question_text": "?",
                    "question_type": "transaction_calibration",
                    "order": 0,
                    "transaction_case": BASE_CASE,
                }
            ],
        },
        [{"slug": "calib-template", "title": "C", "sections": ["calib"]}],
    )
    assert main(["--check", "--fixtures", str(tmp_path)]) == 1
    assert "needs at least one option" in capsys.readouterr().out


def test_a_branch_may_not_target_a_typed_question() -> None:
    base = calibration("t.base", 0, BASE_CASE)
    follow = question(
        question_key="t.follow",
        order=1,
        branch_condition={"question_key": "t.base", "equals": "Personal expense"},
    )
    found = problems(base, follow)
    assert any("whose answer is not a scalar" in p for p in found)
    scenario = question(
        question_key="t.scn",
        question_type="scenario_decision",
        options=CODES,
        scenario_brief={"synthetic": True},
        order=0,
    )
    follow2 = question(
        question_key="t.follow2",
        order=1,
        branch_condition={"question_key": "t.scn", "equals": "approve"},
    )
    assert any("whose answer is not a scalar" in p for p in problems(scenario, follow2))


def test_case_rows_are_upserted_on_the_question_id_with_the_full_row() -> None:
    sql = build_sql(load_bank())
    assert (
        "insert into public.discovery_transaction_cases (question_id, case_family, variant" in sql
    )
    tuesday = question_id("calib.uber.restaurant.tuesday")
    # The variant is stored complete: it names Uber even though the fixture inherited it.
    statement = next(
        line
        for line in sql.splitlines()
        if tuesday in line and "discovery_transaction_cases" in line
    )
    assert (
        "$cl$Uber$cl$" in statement and "$cl$day_of_week$cl$" in statement and ", 2, " in statement
    )


# ---------------------------------------------------------------- validation --
def problems(*questions: Question) -> list[str]:
    return validate(bank(*questions))


def test_a_valid_minimal_bank_has_no_problems() -> None:
    assert problems(question()) == []


@pytest.mark.parametrize(
    ("bad", "expected"),
    [
        (question(question_type="essay"), "unknown question_type"),
        (question(question_type="single_select"), "needs at least one option"),
        (question(question_type="ranking"), "needs at least one option"),
        (question(question_type="multi_select"), "needs at least one option"),
        (
            question(question_type="scenario_decision", scenario_brief={"synthetic": True}),
            "needs at least one option",
        ),
        (
            question(question_type="scenario_decision", options=[{"value": "a", "label": "A"}]),
            "needs a scenario_brief",
        ),
        (question(options=[{"value": "a", "label": "A"}]), "must not carry options"),
        (
            question(question_type="boolean", scenario_brief={"x": 1}),
            "must not carry a scenario_brief",
        ),
        (question(sensitivity="high", required_core=True), "cannot be required_core"),
        (question(sensitivity="secret"), "unknown sensitivity"),
        (question(question_key="Bad Key"), "not a valid key"),
        (question(question_text=""), "question_text must be"),
        (question(priority=101), "priority must be"),
        (
            question(question_type="single_select", options=[{"label": "no value"}]),
            "every option needs a value",
        ),
        (
            question(
                question_type="single_select",
                options=[{"value": "a", "label": "A"}, {"value": "a", "label": "Again"}],
            ),
            "option values are not unique",
        ),
        (
            question(question_type="single_select", options=[{"value": "a", "label": " "}]),
            "needs a label",
        ),
        (question(branch_condition={"equals": True}), "needs a question_key"),
        (question(branch_condition={"question_key": "x", "equals": 1, "in": [1]}), "exactly one"),
    ],
)
def test_a_malformed_question_is_refused(bad: Question, expected: str) -> None:
    found = problems(bad)
    assert any(expected in p for p in found), found


def test_scenario_decision_is_valid_with_both_options_and_a_brief() -> None:
    ok = question(
        question_type="scenario_decision",
        options=[{"value": "nsf_recent", "label": "Recent NSF"}],
        scenario_brief={"synthetic": True, "applicant": "Made up"},
    )
    assert problems(ok) == []


def test_a_branch_may_only_reference_an_earlier_question_in_the_same_section() -> None:
    first = question(question_key="t.first", question_type="boolean", order=0)
    later = question(
        question_key="t.later",
        order=1,
        branch_condition={"question_key": "t.first", "equals": True},
    )
    assert problems(first, later) == []
    backwards = replace(first, branch_condition={"question_key": "t.later", "equals": "x"})
    assert any("comes later" in p for p in problems(backwards, later))
    elsewhere = replace(later, branch_condition={"question_key": "other.section", "equals": 1})
    assert any("not in this section" in p for p in problems(first, elsewhere))


def test_question_keys_are_unique_across_sections() -> None:
    a = Section(slug="sec-a", title="A", questions=[question()])
    b = Section(slug="sec-b", title="B", questions=[question()])
    t = Template(slug="tmpl", title="T", sections=["sec-a", "sec-b"])
    found = validate(Bank(templates=[t], sections={"sec-a": a, "sec-b": b}))
    assert any("is also in section" in p for p in found)


def test_orders_must_be_contiguous_from_zero() -> None:
    found = problems(question(order=0), question(question_key="t.two", order=2))
    assert any("without gaps" in p for p in found)


def test_a_template_must_name_sections_that_exist_once_each() -> None:
    section = Section(slug="sec", title="S", questions=[question()])
    missing = Template(slug="tmpl", title="T", sections=["sec", "ghost"])
    twice = Template(slug="tmpl2", title="T", sections=["sec", "sec"])
    empty = Template(slug="tmpl3", title="T", sections=[])
    found = validate(Bank(templates=[missing, twice, empty], sections={"sec": section}))
    assert any("has no fixture file" in p for p in found)
    assert any("lists a section twice" in p for p in found)
    assert any("names no sections" in p for p in found)


def test_a_section_needs_a_valid_slug_and_at_least_one_question() -> None:
    section = Section(slug="Bad_Slug", title="S", questions=[])
    t = Template(slug="tmpl", title="T", sections=["Bad_Slug"])
    found = validate(Bank(templates=[t], sections={"Bad_Slug": section}))
    assert any("not a valid slug" in p for p in found)
    assert any("has no questions" in p for p in found)


# ----------------------------------------------------------------- the cli --
def write_fixtures(root: Path, section: dict, templates: list[dict]) -> None:
    (root / "sections").mkdir(parents=True)
    (root / "templates.json").write_text(json.dumps(templates), encoding="utf-8")
    (root / "sections" / f"{section['slug']}.json").write_text(
        json.dumps(section), encoding="utf-8"
    )


def test_check_fails_on_a_malformed_fixture_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_fixtures(
        tmp_path,
        {
            "slug": "broken",
            "title": "Broken",
            "questions": [
                {
                    "question_key": "b.one",
                    "question_text": "?",
                    "question_type": "single_select",
                    "order": 0,
                }
            ],
        },
        [{"slug": "broken-template", "title": "Broken", "sections": ["broken", "missing"]}],
    )
    assert main(["--check", "--fixtures", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "2 problem(s)" in out
    assert "needs at least one option" in out
    assert "has no fixture file" in out


def test_check_fails_when_a_file_name_and_its_slug_disagree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "sections").mkdir(parents=True)
    (tmp_path / "templates.json").write_text("[]", encoding="utf-8")
    (tmp_path / "sections" / "one.json").write_text(
        json.dumps({"slug": "two", "title": "T", "questions": []}), encoding="utf-8"
    )
    assert main(["--check", "--fixtures", str(tmp_path)]) == 1
    assert "file name and slug differ" in capsys.readouterr().out


def test_check_fails_on_invalid_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "sections").mkdir(parents=True)
    (tmp_path / "templates.json").write_text("[{", encoding="utf-8")
    assert main(["--check", "--fixtures", str(tmp_path)]) == 1
    assert "fixture error" in capsys.readouterr().out
