"""Load the Discovery question bank from fixtures into the database, idempotently.

    .venv/bin/python -m app.discovery.seed --check    # validate the fixtures; no database
    .venv/bin/python -m app.discovery.seed            # insert or update; never delete
    .venv/bin/python -m app.discovery.seed --retire   # also retire questions the fixtures dropped

Fixtures are `fixtures/discovery/templates.json`, which lists each template as an ordered set
of section slugs, and one file per section under `fixtures/discovery/sections/`, holding that
section and its questions. A section named by two templates is inserted once and joined twice,
which is what makes a shared question one row rather than two.

Properties, each a property of this loader rather than of the operator's care:

* Ids derive from stable keys (`stable_id`, the same helper the synthetic seed uses), so a
  re-run addresses the same rows and a reload is deterministic.
* A question whose wording changed gets `question_version` incremented in place; responses keep
  the version they were written against.
* Nothing is deleted. A question the fixtures no longer name is left alone by default and
  retired with `--retire`; a template-section link is removed only where no response references
  it, because that removal would cascade to answers.
* A transaction calibration question carries a synthetic case, and a variant of a case is proved
  to differ from its family's baseline in exactly the one feature it names, so "controlled" is a
  property the fixture cannot violate rather than a claim its author makes.
* The whole load is one transaction, validated before a single statement is built.

Mirrors `app.synthetic.seed` in shape, and borrows its literal quoting and database lookup.
"""

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.synthetic.generator import stable_id
from app.synthetic.seed import database_url, q

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "discovery"

QUESTION_TYPES = frozenset(
    {
        "short_text",
        "long_text",
        "boolean",
        "single_select",
        "multi_select",
        "number",
        "currency",
        "percentage",
        "scale",
        "ranking",
        "scenario_decision",
        "transaction_calibration",
    }
)
# Types that offer a choice and therefore need options. The two typed types are here because
# their options are the reason codes an answer may cite; migration 0025 (and 0027) require them.
CHOICE_TYPES = frozenset(
    {"single_select", "multi_select", "ranking", "scenario_decision", "transaction_calibration"}
)
# Types whose answer lives in a typed table, so their generic answer is null and a branch
# condition can never read it.
TYPED_TYPES = frozenset({"scenario_decision", "transaction_calibration"})
SENSITIVITIES = frozenset({"low", "medium", "high"})
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")
KEY = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,80}$")
OPTION_VALUE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,60}$")

# ---- transaction cases: the vocabulary migration 0027 enforces, repeated here so --check
# ---- refuses a bad fixture before a transaction starts.
CASE_FEATURES = (
    "amount_minor",
    "day_of_week",
    "time_of_day",
    "business_vertical",
    "merchant",
    "counterparty",
    "payment_rail",
    "recurrence_pattern",
    "frequency_30d",
    "descriptor",
    "account_type",
    "related_transactions",
    "direction",
)
# What a feature change may legitimately carry with it. A day is not a date, so moving the day of
# the week may move the date and the time; flipping the direction flips the sign of every amount.
# Every other pairing is one column and nothing else.
CARRIED_WITH: dict[str, frozenset[str]] = {
    "day_of_week": frozenset({"posted_date", "time_of_day"}),
    "direction": frozenset(
        {"amount_minor", "average_amount_minor", "amount_min_minor", "amount_max_minor"}
    ),
}
CASE_STIMULUS = (
    "business_vertical",
    "business_description",
    "account_type",
    "direction",
    "amount_minor",
    "descriptor",
    "merchant",
    "counterparty",
    "payment_rail",
    "posted_date",
    "day_of_week",
    "time_of_day",
    "memo",
    "frequency_30d",
    "frequency_90d",
    "average_amount_minor",
    "amount_min_minor",
    "amount_max_minor",
    "recurrence_pattern",
    "related_transactions",
    "context",
)
CASE_META = ("case_family", "variant", "changed_feature", "synthetic", "source")
# The authored relationships migration 0028 stores beside the typed metadata. A variant whose
# difference is prose rather than one typed column names its baseline and the feature the author
# changed; the loader cannot prove that difference, so it records the claim and proves only that
# the baseline exists in the same family. A reveal names its original and must equal it in every
# stimulus column but `context`, which is proved. `reveal` on an original holds the fact a later
# stage adds. None of the four is served to a respondent.
CASE_LINKS = (
    "baseline_question_key",
    "authored_changed_feature",
    "reveal_of_question_key",
    "reveal",
)
CASE_KEYS = frozenset(CASE_STIMULUS) | frozenset(CASE_META) | frozenset(CASE_LINKS)
CASE_MONEY = ("amount_minor", "average_amount_minor", "amount_min_minor", "amount_max_minor")
CASE_COUNTS = ("frequency_30d", "frequency_90d")
RAILS = frozenset({"ach", "card", "zelle", "wire", "check", "cash", "internal", "other"})
RECURRENCES = frozenset({"none", "weekly", "biweekly", "semimonthly", "monthly", "irregular"})
ACCOUNT_TYPES = frozenset({"checking", "savings"})
DIRECTIONS = frozenset({"inflow", "outflow"})
VERTICAL = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
VARIANT = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME = re.compile(r"^\d{2}:\d{2}(:\d{2})?$")


@dataclass(frozen=True)
class Question:
    question_key: str
    question_text: str
    question_type: str
    order: int
    helper_text: str | None = None
    options: list[dict[str, str]] = field(default_factory=list)
    scenario_brief: dict[str, Any] | None = None
    transaction_case: dict[str, Any] | None = None
    required_core: bool = False
    priority: int = 50
    sensitivity: str = "low"
    compare_key: str | None = None
    data_key: str | None = None
    branch_condition: dict[str, Any] | None = None


@dataclass(frozen=True)
class Section:
    slug: str
    title: str
    questions: list[Question]
    description: str | None = None
    is_core: bool = False
    estimated_minutes: int | None = None
    # Declared once per section and expanded by the loader onto every calibration question that
    # offers no codes of its own, so the vocabulary has one home in the file.
    calibration_reason_codes: list[dict[str, Any]] = field(default_factory=list)
    # A section the bank keeps but no longer serves. Its questions are upserted with
    # `retired_at` set rather than cleared, so a re-run cannot revive them, their answers and
    # history stay where they are, and the routes (which read only live questions) show nothing.
    retired: bool = False


@dataclass(frozen=True)
class Template:
    slug: str
    title: str
    sections: list[str]
    subtitle: str | None = None
    estimated_minutes: int | None = None


@dataclass(frozen=True)
class Bank:
    templates: list[Template]
    sections: dict[str, Section]


class FixtureError(Exception):
    """A fixture that must not reach the database. The message lists every problem found."""


def template_id(slug: str) -> str:
    return stable_id("discovery", "template", slug)


def section_id(slug: str) -> str:
    return stable_id("discovery", "section", slug)


def question_id(key: str) -> str:
    return stable_id("discovery", "question", key)


# ------------------------------------------------------------------- loading --
def _question(raw: dict[str, Any], codes: list[dict[str, Any]]) -> Question:
    options = list(raw.get("options") or [])
    if raw.get("question_type") == "transaction_calibration" and not options:
        options = list(codes)
    case = raw.get("transaction_case")
    return Question(
        question_key=str(raw.get("question_key", "")),
        question_text=str(raw.get("question_text", "")),
        question_type=str(raw.get("question_type", "")),
        order=int(raw.get("order", -1)),
        helper_text=raw.get("helper_text"),
        options=options,
        scenario_brief=raw.get("scenario_brief"),
        transaction_case=dict(case) if isinstance(case, dict) else case,
        required_core=bool(raw.get("required_core", False)),
        priority=int(raw.get("priority", 50)),
        sensitivity=str(raw.get("sensitivity", "low")),
        compare_key=raw.get("compare_key"),
        data_key=raw.get("data_key"),
        branch_condition=raw.get("branch_condition"),
    )


def load_bank(root: Path = FIXTURES) -> Bank:
    """Read the fixtures. Shape problems are reported by `validate`, not here."""
    templates_path = root / "templates.json"
    raw_templates = json.loads(templates_path.read_text(encoding="utf-8"))
    templates = [
        Template(
            slug=str(t.get("slug", "")),
            title=str(t.get("title", "")),
            sections=[str(s) for s in t.get("sections", [])],
            subtitle=t.get("subtitle"),
            estimated_minutes=t.get("estimated_minutes"),
        )
        for t in raw_templates
    ]
    sections: dict[str, Section] = {}
    for path in sorted((root / "sections").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        codes = list(raw.get("calibration_reason_codes") or [])
        section = Section(
            slug=str(raw.get("slug", "")),
            title=str(raw.get("title", "")),
            questions=[_question(x, codes) for x in raw.get("questions", [])],
            description=raw.get("description"),
            is_core=bool(raw.get("is_core", False)),
            estimated_minutes=raw.get("estimated_minutes"),
            calibration_reason_codes=codes,
            retired=bool(raw.get("retired", False)),
        )
        if path.stem != section.slug:
            raise FixtureError(f"{path.name}: file name and slug differ ({section.slug!r})")
        sections[section.slug] = section
    return Bank(templates=templates, sections=sections)


# ---------------------------------------------------------------- validation --
def validate(bank: Bank) -> list[str]:
    """Every problem with the bank, as one line each. Empty means it may be loaded."""
    problems: list[str] = []
    seen_keys: dict[str, str] = {}
    template_slugs = [t.slug for t in bank.templates]
    if len(set(template_slugs)) != len(template_slugs):
        problems.append("template slugs are not unique")
    for t in bank.templates:
        if not SLUG.match(t.slug):
            problems.append(f"template {t.slug!r}: slug is not a valid slug")
        if not 1 <= len(t.title) <= 120:
            problems.append(f"template {t.slug!r}: title must be 1 to 120 characters")
        if not t.sections:
            problems.append(f"template {t.slug!r}: names no sections")
        if len(set(t.sections)) != len(t.sections):
            problems.append(f"template {t.slug!r}: lists a section twice")
        for slug in t.sections:
            if slug not in bank.sections:
                problems.append(f"template {t.slug!r}: section {slug!r} has no fixture file")
    for section in bank.sections.values():
        prefix = f"section {section.slug!r}"
        if not SLUG.match(section.slug):
            problems.append(f"{prefix}: slug is not a valid slug")
        if not 1 <= len(section.title) <= 120:
            problems.append(f"{prefix}: title must be 1 to 120 characters")
        if not section.questions:
            problems.append(f"{prefix}: has no questions")
        orders = sorted(x.order for x in section.questions)
        if orders != list(range(len(orders))):
            problems.append(f"{prefix}: question order must run 0..{len(orders) - 1} without gaps")
        keys_here: list[str] = []
        for x in section.questions:
            keys_here.append(x.question_key)
            problems.extend(f"{prefix}, question {x.question_key!r}: {p}" for p in _check(x))
            if x.question_key in seen_keys and seen_keys[x.question_key] != section.slug:
                problems.append(
                    f"{prefix}: question key {x.question_key!r} is also in "
                    f"section {seen_keys[x.question_key]!r}"
                )
            seen_keys[x.question_key] = section.slug
        if len(set(keys_here)) != len(keys_here):
            problems.append(f"{prefix}: question keys are not unique within the section")
        # Branch conditions may reference only an EARLIER question in the SAME section, and only
        # one whose answer is a scalar. A shared section can sit at different positions in
        # different templates, so anything else would be answerable in one template and not
        # another; and a typed question's generic answer is null, so a condition on it could
        # never hold.
        by_key = {x.question_key: x for x in section.questions}
        for x in section.questions:
            cond = x.branch_condition
            if cond is None:
                continue
            target = by_key.get(str(cond.get("question_key", "")))
            if target is None:
                problems.append(
                    f"{prefix}, question {x.question_key!r}: branch condition references "
                    f"{cond.get('question_key')!r}, which is not in this section"
                )
            elif target.order >= x.order:
                problems.append(
                    f"{prefix}, question {x.question_key!r}: branch condition references a "
                    f"question that comes later, so it could never be shown"
                )
            elif target.question_type in TYPED_TYPES:
                problems.append(
                    f"{prefix}, question {x.question_key!r}: branch condition references "
                    f"{target.question_key!r}, a {target.question_type} question whose answer "
                    f"is not a scalar, so it could never hold"
                )
    _cases, case_problems = materialise_cases(bank)
    problems.extend(case_problems)
    if not problems:
        # The quoting tag is the one string no literal may contain. Caught here so `--check`
        # refuses it rather than a real run failing after validation passed.
        try:
            build_sql(bank)
        except ValueError as exc:
            problems.append(f"a literal cannot be quoted: {exc}")
    return problems


def _check(x: Question) -> list[str]:
    out: list[str] = []
    if not KEY.match(x.question_key):
        out.append("question_key is not a valid key")
    if not 1 <= len(x.question_text) <= 1000:
        out.append("question_text must be 1 to 1000 characters")
    if x.helper_text is not None and len(str(x.helper_text)) > 600:
        out.append("helper_text is longer than 600 characters")
    if x.question_type not in QUESTION_TYPES:
        out.append(f"unknown question_type {x.question_type!r}")
    if x.sensitivity not in SENSITIVITIES:
        out.append(f"unknown sensitivity {x.sensitivity!r}")
    if not 0 <= x.priority <= 100:
        out.append("priority must be 0 to 100")
    if x.order < 0:
        out.append("order must not be negative")
    if x.sensitivity == "high" and x.required_core:
        out.append("a high-sensitivity question cannot be required_core")
    if x.question_type in CHOICE_TYPES:
        if not x.options:
            out.append(f"{x.question_type} needs at least one option")
        values = [str(o.get("value", "")) for o in x.options if isinstance(o, dict)]
        if len(values) != len(x.options) or any(not OPTION_VALUE.match(v) for v in values):
            out.append("every option needs a value of lowercase letters, digits, '_', '.' or '-'")
        if len(set(values)) != len(values):
            out.append("option values are not unique")
        if any(not str(o.get("label", "")).strip() for o in x.options if isinstance(o, dict)):
            out.append("every option needs a label")
    elif x.options:
        out.append(f"{x.question_type} must not carry options")
    if x.question_type == "scenario_decision":
        if not isinstance(x.scenario_brief, dict) or not x.scenario_brief:
            out.append("scenario_decision needs a scenario_brief")
    elif x.scenario_brief is not None:
        out.append(f"{x.question_type} must not carry a scenario_brief")
    if x.question_type == "transaction_calibration":
        if not isinstance(x.transaction_case, dict) or not x.transaction_case:
            out.append("transaction_calibration needs a transaction_case")
    elif x.transaction_case is not None:
        out.append(f"{x.question_type} must not carry a transaction_case")
    for name, value in (("compare_key", x.compare_key), ("data_key", x.data_key)):
        if value is not None and not KEY.match(str(value)):
            out.append(f"{name} is not a valid key")
    cond = x.branch_condition
    if cond is not None:
        if not isinstance(cond, dict) or "question_key" not in cond:
            out.append("branch_condition needs a question_key")
        elif ("equals" in cond) == ("in" in cond):
            out.append("branch_condition needs exactly one of 'equals' or 'in'")
        elif "in" in cond and not isinstance(cond["in"], list):
            out.append("branch_condition 'in' must be a list")
    return out


# ----------------------------------------------------------- transaction cases --
def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _case_shape(raw: dict[str, Any]) -> list[str]:
    """Problems with one case as written in the fixture, before inheritance."""
    out: list[str] = []
    unknown = sorted(set(raw) - CASE_KEYS)
    if unknown:
        out.append(f"transaction_case has unknown fields: {', '.join(unknown)}")
    family, variant = raw.get("case_family"), raw.get("variant")
    if not isinstance(family, str) or not SLUG.match(family):
        out.append("transaction_case.case_family is not a valid slug")
    if not isinstance(variant, str) or not VARIANT.match(variant):
        out.append("transaction_case.variant is not a valid slug")
    feature = raw.get("changed_feature")
    if feature is not None and feature not in CASE_FEATURES:
        out.append(
            f"transaction_case.changed_feature {feature!r} is not one of {', '.join(CASE_FEATURES)}"
        )
    if raw.get("synthetic", True) is not True:
        out.append(
            "transaction_case must be synthetic: a committed fixture never holds a real "
            "transaction (CLAUDE.md), and the loader refuses one"
        )
    source = raw.get("source")
    if source is not None and not (isinstance(source, str) and 1 <= len(source) <= 200):
        out.append("transaction_case.source must be 1 to 200 characters")
    base_key, feature_text = raw.get("baseline_question_key"), raw.get("authored_changed_feature")
    reveal_key, reveal = raw.get("reveal_of_question_key"), raw.get("reveal")
    for name, value in (
        ("baseline_question_key", base_key),
        ("reveal_of_question_key", reveal_key),
    ):
        if value is not None and not (isinstance(value, str) and KEY.match(value)):
            out.append(f"transaction_case.{name} is not a valid question key")
    if (base_key is None) != (feature_text is None):
        out.append(
            "transaction_case.baseline_question_key and authored_changed_feature go together"
        )
    if feature_text is not None and not (
        isinstance(feature_text, str) and 1 <= len(feature_text) <= 60
    ):
        out.append("transaction_case.authored_changed_feature must be 1 to 60 characters")
    if base_key is not None and feature is not None:
        out.append(
            "transaction_case: a variant is proved by changed_feature or claimed by "
            "baseline_question_key, not both"
        )
    if base_key is not None and reveal_key is not None:
        out.append("transaction_case: a case is a variant or a reveal, not both")
    if reveal is not None and not isinstance(reveal, dict):
        out.append("transaction_case.reveal must be an object")
    return out


def _linked(raw: dict[str, Any]) -> bool:
    """A case that names a baseline or an original: never itself a baseline candidate."""
    return (
        raw.get("baseline_question_key") is not None
        or raw.get("reveal_of_question_key") is not None
    )


def _case_values(case: dict[str, Any]) -> list[str]:
    """Problems with a complete case: what migration 0027's constraints would refuse."""
    out: list[str] = []
    direction, amount = case.get("direction"), case.get("amount_minor")
    if direction not in DIRECTIONS:
        out.append("transaction_case.direction must be inflow or outflow")
    for name in CASE_MONEY:
        value = case.get(name)
        if value is not None and not _is_int(value):
            out.append(f"transaction_case.{name} must be an integer in minor units, not a float")
    if amount is None:
        out.append("transaction_case.amount_minor is required")
    elif (
        _is_int(amount)
        and direction in DIRECTIONS
        and ((direction == "inflow") != (amount > 0) or amount == 0)
    ):
        out.append("transaction_case.direction does not match the sign of amount_minor")
    descriptor = case.get("descriptor")
    if not isinstance(descriptor, str) or not 1 <= len(descriptor) <= 200:
        out.append("transaction_case.descriptor must be 1 to 200 characters")
    for name in CASE_COUNTS:
        value = case.get(name)
        if value is not None and (not _is_int(value) or value < 0):
            out.append(f"transaction_case.{name} must be a whole number of 0 or more")
    low, high, average = (
        case.get(k) for k in ("amount_min_minor", "amount_max_minor", "average_amount_minor")
    )
    if (
        all(_is_int(v) for v in (low, high) if v is not None)
        and low is not None
        and high is not None
    ):
        if low > high:
            out.append("transaction_case.amount_min_minor is above amount_max_minor")
        elif _is_int(average) and not low <= average <= high:
            out.append("transaction_case.average_amount_minor is outside the amount range")
    dow = case.get("day_of_week")
    if dow is not None and (not _is_int(dow) or not 0 <= dow <= 6):
        out.append("transaction_case.day_of_week must be 0 to 6")
    for name, allowed in (
        ("payment_rail", RAILS),
        ("recurrence_pattern", RECURRENCES),
        ("account_type", ACCOUNT_TYPES),
    ):
        value = case.get(name)
        if value is not None and value not in allowed:
            out.append(f"transaction_case.{name} must be one of {', '.join(sorted(allowed))}")
    vertical = case.get("business_vertical")
    if vertical is not None and not (isinstance(vertical, str) and VERTICAL.match(vertical)):
        out.append("transaction_case.business_vertical is not a valid slug")
    for name, limit in (
        ("business_description", 400),
        ("merchant", 80),
        ("counterparty", 80),
        ("memo", 300),
    ):
        value = case.get(name)
        if value is not None and not (isinstance(value, str) and len(value) <= limit):
            out.append(f"transaction_case.{name} must be text of at most {limit} characters")
    date = case.get("posted_date")
    if date is not None and not (isinstance(date, str) and DATE.match(date)):
        out.append("transaction_case.posted_date must be YYYY-MM-DD")
    time = case.get("time_of_day")
    if time is not None and not (isinstance(time, str) and TIME.match(time)):
        out.append("transaction_case.time_of_day must be HH:MM")
    if not isinstance(case.get("related_transactions"), list):
        out.append("transaction_case.related_transactions must be a list")
    if not isinstance(case.get("context"), dict):
        out.append("transaction_case.context must be an object")
    return out


def materialise_cases(bank: Bank) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Every calibration case in full, keyed by question key, plus every problem found.

    A variant inherits its family's baseline and overrides only what it changes; the full row is
    what the database holds, so two cases are independently readable. The proof that a family is
    controlled happens here: the materialised variant is compared with its baseline column by
    column, and the set of columns that differ must be exactly the feature the variant names
    (plus what that feature is allowed to carry with it). Anything else is refused.
    """
    problems: list[str] = []
    raws: dict[str, tuple[str, dict[str, Any]]] = {}  # question_key -> (section slug, raw case)
    for section in bank.sections.values():
        for x in section.questions:
            if x.question_type == "transaction_calibration" and isinstance(
                x.transaction_case, dict
            ):
                raws[x.question_key] = (section.slug, x.transaction_case)
    for key, (slug, raw) in raws.items():
        problems.extend(f"section {slug!r}, question {key!r}: {p}" for p in _case_shape(raw))
    if problems:
        return {}, problems

    by_family: dict[str, list[str]] = {}
    for key, (_slug, raw) in raws.items():
        by_family.setdefault(str(raw["case_family"]), []).append(key)
    cases: dict[str, dict[str, Any]] = {}
    for family, keys in by_family.items():
        variants = [str(raws[k][1]["variant"]) for k in keys]
        if len(set(variants)) != len(variants):
            problems.append(f"case family {family!r}: variant names are not unique")
        baselines = [
            k for k in keys if raws[k][1].get("changed_feature") is None and not _linked(raws[k][1])
        ]
        if not baselines:
            problems.append(
                f"case family {family!r}: has no baseline (a case with changed_feature null "
                "that names no baseline or original)"
            )
            continue
        if len(baselines) > 1:
            problems.append(f"case family {family!r}: has more than one baseline")
            continue
        base_key = baselines[0]
        base = _full(raws[base_key][1])
        cases[base_key] = {**_meta(raws[base_key][1]), **base}
        for key in keys:
            if key == base_key:
                continue
            raw = raws[key][1]
            if _linked(raw):
                # A claimed variant or a reveal is a complete row of its own; the link is
                # checked below, once every family has been materialised.
                cases[key] = {**_meta(raw), **_full(raw)}
                continue
            feature = str(raw["changed_feature"])
            merged = {**base, **{k: raw[k] for k in CASE_STIMULUS if k in raw}}
            differs = {k for k in CASE_STIMULUS if merged[k] != base[k]}
            allowed = {feature} | CARRIED_WITH.get(feature, frozenset())
            if feature not in differs:
                problems.append(
                    f"question {key!r}: declares changed_feature {feature!r} but {feature} is "
                    f"the same as the baseline {base_key!r}"
                )
            extra = sorted(differs - allowed)
            if extra:
                problems.append(
                    f"question {key!r}: declares changed_feature {feature!r} but also differs "
                    f"from the baseline in {', '.join(extra)}; a variant changes one feature"
                )
            cases[key] = {**_meta(raw), **merged}
    # The claimed relationships: the case named must exist, sit in the same family and be that
    # family's baseline; and a reveal must be its original in every column but context.
    for key, (_slug, raw) in raws.items():
        for field_name in ("baseline_question_key", "reveal_of_question_key"):
            target = raw.get(field_name)
            if target is None:
                continue
            if target not in raws:
                problems.append(
                    f"question {key!r}: {field_name} {target!r} is not a calibration question"
                )
                continue
            other = raws[target][1]
            if other.get("case_family") != raw.get("case_family"):
                problems.append(f"question {key!r}: {field_name} {target!r} is in another family")
            if other.get("changed_feature") is not None or _linked(other):
                problems.append(f"question {key!r}: {field_name} {target!r} is not a baseline")
            if field_name == "reveal_of_question_key" and key in cases and target in cases:
                differs = sorted(
                    k for k in CASE_STIMULUS if k != "context" and cases[key][k] != cases[target][k]
                )
                if differs:
                    problems.append(
                        f"question {key!r}: a reveal must match its original in every column "
                        f"but context; it differs in {', '.join(differs)}"
                    )
    for key, case in cases.items():
        problems.extend(f"question {key!r}: {p}" for p in _case_values(case))
    return cases, problems


def _full(raw: dict[str, Any]) -> dict[str, Any]:
    full = {k: raw.get(k) for k in CASE_STIMULUS}
    if full["related_transactions"] is None:
        full["related_transactions"] = []
    if full["context"] is None:
        full["context"] = {}
    return full


def _meta(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_family": raw["case_family"],
        "variant": raw["variant"],
        "changed_feature": raw.get("changed_feature"),
        "synthetic": True,
        "source": raw.get("source"),
        "baseline_question_key": raw.get("baseline_question_key"),
        "authored_changed_feature": raw.get("authored_changed_feature"),
        "reveal_of_question_key": raw.get("reveal_of_question_key"),
        "reveal": raw.get("reveal"),
    }


# ---------------------------------------------------------------------- sql --
def _jsonb(value: Any) -> str:
    return q(json.dumps(value, sort_keys=True)) + "::jsonb"


# The relationship columns of migration 0028: keys in the fixture, ids in the row.
LINK_COLUMNS = (
    "baseline_question_id",
    "authored_changed_feature",
    "reveal_of_question_id",
    "reveal",
)
CASE_COLUMNS = CASE_META + CASE_STIMULUS + LINK_COLUMNS


def _case_sql(question_key: str, case: dict[str, Any]) -> str:
    values = [q(question_id(question_key))]
    for column in CASE_COLUMNS:
        value = case.get(column)
        if column == "baseline_question_id":
            key = case.get("baseline_question_key")
            values.append(q(question_id(str(key))) if key else "null")
        elif column == "reveal_of_question_id":
            key = case.get("reveal_of_question_key")
            values.append(q(question_id(str(key))) if key else "null")
        elif column in ("related_transactions", "context"):
            values.append(_jsonb(value))
        elif column == "reveal":
            values.append(_jsonb(value) if value is not None else "null")
        else:
            values.append(q(value))
    updates = ", ".join(f"{c} = excluded.{c}" for c in CASE_COLUMNS)
    return (
        "insert into public.discovery_transaction_cases (question_id, "
        + ", ".join(CASE_COLUMNS)
        + ") values ("
        + ", ".join(values)
        + ") on conflict (question_id) do update set "
        + updates
        + ", updated_at = now();"
    )


def build_sql(bank: Bank, retire: bool = False) -> str:
    """One transaction's worth of statements. Every insert is an upsert on a stable id."""
    cases, case_problems = materialise_cases(bank)
    if case_problems:
        raise ValueError("; ".join(case_problems))
    lines: list[str] = []
    for section in bank.sections.values():
        sid = section_id(section.slug)
        lines.append(
            "insert into public.discovery_sections "
            "(id, slug, title, description, is_core, estimated_minutes) values "
            f"({q(sid)}, {q(section.slug)}, {q(section.title)}, {q(section.description)}, "
            f"{q(section.is_core)}, {q(section.estimated_minutes)}) "
            "on conflict (id) do update set title = excluded.title, "
            "description = excluded.description, is_core = excluded.is_core, "
            "estimated_minutes = excluded.estimated_minutes, updated_at = now();"
        )
        # Reordering can collide on unique (section_id, order) mid-way, so existing rows are
        # first moved out of the way, then every question is upserted at its real position. The
        # shift is one more than the section's current maximum, not a constant: a question the
        # fixture dropped but did not retire keeps its shifted position, and a constant shift
        # walked the next run's rows straight into it (the isolation check's unexplained
        # transient). Above the maximum, no target can equal a row not yet moved.
        lines.append(
            f'update public.discovery_questions set "order" = "order" + '
            f'(select coalesce(max("order"), 0) + 1 from public.discovery_questions '
            f"where section_id = {q(sid)}) where section_id = {q(sid)};"
        )
        # A retired section's questions are written retired and stay retired; a live section's
        # are written live, which is also how a question the fixture names again comes back.
        retired_at = (
            "coalesce(public.discovery_questions.retired_at, now())" if section.retired else "null"
        )
        for x in section.questions:
            lines.append(
                "insert into public.discovery_questions (id, section_id, question_key, "
                "question_text, helper_text, question_type, options, scenario_brief, "
                'required_core, priority, sensitivity, "order", compare_key, data_key, '
                "branch_condition, retired_at) values "
                f"({q(question_id(x.question_key))}, {q(sid)}, {q(x.question_key)}, "
                f"{q(x.question_text)}, {q(x.helper_text)}, {q(x.question_type)}, "
                f"{_jsonb(x.options)}, "
                f"{_jsonb(x.scenario_brief) if x.scenario_brief is not None else 'null'}, "
                f"{q(x.required_core)}, {x.priority}, {q(x.sensitivity)}, {x.order}, "
                f"{q(x.compare_key)}, {q(x.data_key)}, "
                f"{_jsonb(x.branch_condition) if x.branch_condition is not None else 'null'}, "
                f"{'now()' if section.retired else 'null'}) "
                "on conflict (id) do update set "
                # A question moved to another section moves; if any answer references it the
                # composite key (question_id, section_id) refuses the update and the whole
                # transaction fails loudly, which beats silently leaving it where it was.
                "section_id = excluded.section_id, "
                # Changed wording bumps the version in place; responses keep theirs.
                "question_version = case when public.discovery_questions.question_text "
                "is distinct from excluded.question_text "
                "then public.discovery_questions.question_version + 1 "
                "else public.discovery_questions.question_version end, "
                "question_text = excluded.question_text, helper_text = excluded.helper_text, "
                "question_type = excluded.question_type, options = excluded.options, "
                "scenario_brief = excluded.scenario_brief, required_core = excluded.required_core, "
                "priority = excluded.priority, sensitivity = excluded.sensitivity, "
                '"order" = excluded."order", compare_key = excluded.compare_key, '
                "data_key = excluded.data_key, branch_condition = excluded.branch_condition, "
                f"retired_at = {retired_at}, updated_at = now();"
            )
            if x.question_type == "transaction_calibration":
                lines.append(_case_sql(x.question_key, cases[x.question_key]))
        keys = ", ".join(q(x.question_key) for x in section.questions)
        if retire:
            lines.append(
                "update public.discovery_questions set retired_at = coalesce(retired_at, now()) "
                f"where section_id = {q(sid)} and question_key not in ({keys});"
            )
        else:
            # Left alone, and named, so the operator knows what --retire would do.
            lines.append(
                "do $$ declare n int; begin select count(*) into n from public.discovery_questions "
                f"where section_id = {q(sid)} and question_key not in ({keys}) "
                "and retired_at is null; if n > 0 then raise notice "
                f"'{section.slug}: % question(s) not in the fixture; "
                "pass --retire to retire them', n; end if; end $$;"
            )
    for t in bank.templates:
        tid = template_id(t.slug)
        lines.append(
            "insert into public.discovery_templates (id, slug, title, subtitle, estimated_minutes) "
            f"values ({q(tid)}, {q(t.slug)}, {q(t.title)}, {q(t.subtitle)}, "
            f"{q(t.estimated_minutes)}) on conflict (id) do update set title = excluded.title, "
            "subtitle = excluded.subtitle, estimated_minutes = excluded.estimated_minutes, "
            "updated_at = now();"
        )
        wanted = ", ".join(q(section_id(s)) for s in t.sections)
        # A link the fixture dropped is removed only where nothing answered through it: the
        # join row cascades to responses, and an answer is never the loader's to delete.
        lines.append(
            "delete from public.discovery_template_sections ts "
            f"where ts.template_id = {q(tid)} and ts.section_id not in ({wanted}) "
            "and not exists (select 1 from public.discovery_responses r "
            "where r.template_id = ts.template_id and r.section_id = ts.section_id);"
        )
        lines.append(
            f'update public.discovery_template_sections set "order" = "order" + '
            f'(select coalesce(max("order"), 0) + 1 from public.discovery_template_sections '
            f"where template_id = {q(tid)}) where template_id = {q(tid)};"
        )
        for position, slug in enumerate(t.sections):
            lines.append(
                "insert into public.discovery_template_sections "
                f'(template_id, section_id, "order") values '
                f"({q(tid)}, {q(section_id(slug))}, {position}) "
                'on conflict (template_id, section_id) do update set "order" = excluded."order";'
            )
    lines.append("notify pgrst, 'reload schema';")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------- main --
def main(argv: list[str]) -> int:
    import os
    import subprocess
    import tempfile

    check = "--check" in argv
    retire = "--retire" in argv
    root = FIXTURES
    if "--fixtures" in argv:
        root = Path(argv[argv.index("--fixtures") + 1])
    try:
        bank = load_bank(root)
    except (FixtureError, json.JSONDecodeError, OSError) as exc:
        print(f"fixture error: {exc}")
        return 1
    problems = validate(bank)
    if problems:
        print(f"{len(problems)} problem(s) in {root}:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    questions = sum(len(s.questions) for s in bank.sections.values())
    cases = sum(
        1
        for s in bank.sections.values()
        for x in s.questions
        if x.question_type == "transaction_calibration"
    )
    print(
        f"fixtures valid: {len(bank.templates)} template(s), {len(bank.sections)} section(s), "
        f"{questions} question(s), {cases} transaction case(s)"
    )
    if check:
        return 0
    try:
        script = build_sql(bank, retire=retire)
    except ValueError as exc:  # a literal containing the quoting tag
        print(f"fixture error: {exc}")
        return 1
    url = database_url()
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as handle:
        handle.write(script)
        temp_path = handle.name
    try:
        result = subprocess.run(
            [
                "psql",
                url,
                "-X",
                "-q",
                "-v",
                "ON_ERROR_STOP=1",
                "--single-transaction",
                "-f",
                temp_path,
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PGCONNECT_TIMEOUT": "15"},
        )
    finally:
        os.unlink(temp_path)
    for line in result.stderr.splitlines():
        if "NOTICE" in line:
            print(line.strip())
    if result.returncode != 0:
        # Never silent: a failed run names the exit status even when psql said nothing.
        print(f"psql exited with status {result.returncode}; nothing was applied")
        print(result.stderr.strip()[-2000:] or result.stdout.strip()[-2000:])
        return 1
    print(f"seeded {questions} question(s) in {script.count(chr(10))} statements, one transaction")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
