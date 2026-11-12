"""What the respondents actually said, compared deterministically.

Pure functions over one `Dataset` of plain dicts, in the manner of `app.discovery.progress`:
nothing here reads a table or calls a model. Every comparison status comes from a rule in this
file with a configured tolerance, and free text is never judged, only placed side by side. A
"policy signal" is an observed response pattern and is phrased as one; nothing here becomes a
rule anywhere else.
"""

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.discovery import progress as prog
from app.discovery import taxonomy as tx

# ------------------------------------------------------------------------------- shapes --
Row = dict[str, Any]


@dataclass(frozen=True)
class Respondent:
    user_id: str
    assignment_id: str
    template_slug: str
    name: str
    label: str  # Lead | Partner | Engineer, from the template
    kind: str  # stakeholder | reviewer
    status: str
    created_at: str | None


@dataclass
class Dataset:
    templates: dict[str, Row] = field(default_factory=dict)  # by slug
    sections: dict[str, Row] = field(default_factory=dict)  # by id; carries slug, template_slugs
    questions: dict[str, Row] = field(default_factory=dict)  # by id; carries section_slug, meta
    respondents: list[Respondent] = field(default_factory=list)
    responses: dict[tuple[str, str], Row] = field(default_factory=dict)  # (assignment, question)
    sessions: dict[str, list[Row]] = field(default_factory=dict)  # by assignment
    template_sections: dict[str, list[str]] = field(default_factory=dict)  # slug -> section ids

    def respondent_for(self, template_slug: str) -> Respondent | None:
        for r in self.respondents:
            if r.template_slug == template_slug and r.kind == "stakeholder":
                return r
        return None

    def questions_in(self, section_id: str) -> list[Row]:
        return sorted(
            (q for q in self.questions.values() if q["section_id"] == section_id),
            key=lambda q: int(q["order"]),
        )

    def response(self, r: Respondent | None, question_id: str) -> Row | None:
        return self.responses.get((r.assignment_id, question_id)) if r else None

    def responses_of(self, r: Respondent) -> dict[str, Row]:
        return {q: row for (a, q), row in self.responses.items() if a == r.assignment_id}


LABEL_BY_TEMPLATE = {
    "discovery-lead": "Lead",
    "discovery-partner": "Partner",
    "discovery-engineer": "Engineer",
}

AGREE, ROUGH, DIFFERENT, MATERIAL = "AGREE", "ROUGHLY_ALIGNED", "DIFFERENT", "MATERIAL_DISAGREEMENT"
NEITHER, NOT_COMPARABLE = "NEITHER_ANSWERED", "NOT_COMPARABLE"
DISAGREEMENTS = (DIFFERENT, MATERIAL)

TOLERANCE = {
    "relative_rough": 0.10,  # numbers within 10% of the larger are roughly aligned
    "relative_different": 0.50,  # within 50% is different; beyond is material
    "percentage_points_rough": 5,
    "percentage_points_different": 20,
    "scale_rough": 1,  # one step apart on a scale
    "scale_different": 2,
    "multi_select_rough": 0.5,  # Jaccard overlap at or above this is roughly aligned
    "ordinal_rough_steps": 1,  # adjacent bands are roughly aligned
}
MATERIALITY_RANK = {"immaterial": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
ESCAPE_WORDS = (
    "prefer not",
    "don't track",
    "do not track",
    "varies too much",
    "not sure",
    "don't know",
    "not applicable",
)
DECISION_WORDS = {
    "approve": "approve",
    "decline": "deny",
    "deny": "deny",
    "manual": "manual_review",
    "review": "manual_review",
}


def only_answered(label: str) -> str:
    return f"ONLY_{label.upper()}_ANSWERED"


# ---------------------------------------------------------------------------- answers --
def option_label(question: Row, value: Any) -> str:
    for o in question.get("options") or []:
        if o.get("value") == value:
            return str(o.get("label", value))
    return str(value)


def _money(minor: Any) -> str:
    try:
        return f"${int(minor) / 100:,.2f}"
    except (TypeError, ValueError):
        return str(minor)


def display(question: Row, response: Row | None) -> str | None:
    """A person-readable rendering of an answer, for tables and CSV. None when unanswered."""
    if response is None:
        return None
    if response.get("status") == "skipped":
        return "(skipped)"
    kind = question.get("question_type")
    if kind == "scenario_decision":
        s = response.get("scenario") or {}
        bits = [str(s.get("decision", "")).replace("_", " "), f"confidence {s.get('confidence')}"]
        if s.get("maximum_exposure_minor") is not None:
            bits.append(f"max exposure {_money(s['maximum_exposure_minor'])}")
        return " · ".join(b for b in bits if b)
    if kind == "transaction_calibration":
        c = response.get("calibration") or {}
        return " · ".join(
            str(x)
            for x in (
                c.get("classification_category"),
                f"confidence {c.get('classification_confidence')}",
                c.get("underwriting_materiality"),
                c.get("underwriting_signal"),
            )
        )
    answer = response.get("answer")
    if kind in ("single_select",):
        return option_label(question, answer)
    if kind in ("multi_select", "ranking"):
        items = answer if isinstance(answer, list) else []
        joined = [option_label(question, v) for v in items]
        return (" > " if kind == "ranking" else ", ").join(joined) if joined else "(none chosen)"
    if kind == "currency":
        return _money(answer)
    if kind == "percentage":
        return f"{answer}%"
    if kind == "boolean":
        return "Yes" if answer else "No"
    return str(answer) if answer is not None else None


def is_answered(response: Row | None) -> bool:
    return bool(response) and response.get("status") == "answered"


def prefers_not(question: Row, response: Row | None) -> bool:
    if not is_answered(response) or question.get("question_type") != "single_select":
        return False
    return "prefer not" in option_label(question, response.get("answer")).lower()


# ------------------------------------------------------------------------- comparison --
def _ordinal_options(question: Row) -> list[str] | None:
    """The option values in order when the options read as bands, else None.

    A band has a digit, a currency sign or a comparative word in its label; escape options
    ("Prefer not to answer", "We don't track it") are left out of the order.
    """
    options = question.get("options") or []
    if len(options) < 3:
        return None
    ranked = [
        o for o in options if not any(w in str(o.get("label", "")).lower() for w in ESCAPE_WORDS)
    ]
    banded = [
        o
        for o in ranked
        if any(ch.isdigit() for ch in str(o.get("label", "")))
        or any(
            w in str(o.get("label", "")).lower()
            for w in ("under", "over", "more than", "fewer", "less than", "none", "all")
        )
    ]
    if len(banded) >= max(3, int(0.6 * len(ranked))):
        return [str(o["value"]) for o in ranked]
    return None


def _decision_of(question: Row, value: Any) -> str | None:
    label = option_label(question, value).lower()
    for word, decision in DECISION_WORDS.items():
        if word in label:
            return decision
    return None


def _jaccard(a: list[Any], b: list[Any]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def _compare_values(question: Row, a: Any, b: Any) -> tuple[str, str]:
    """(status, detail) for two answered values of one plain-typed question."""
    kind = question.get("question_type")
    if kind == "boolean":
        return (AGREE, "same answer") if a == b else (MATERIAL, "yes against no")
    if kind == "single_select":
        if a == b:
            return AGREE, "same option"
        da, db = _decision_of(question, a), _decision_of(question, b)
        if da and db and {da, db} == {"approve", "deny"}:
            return MATERIAL, "approve against deny"
        order = _ordinal_options(question)
        if order and a in order and b in order:
            steps = abs(order.index(a) - order.index(b))
            if steps <= TOLERANCE["ordinal_rough_steps"]:
                return ROUGH, f"adjacent bands ({steps} step)"
            return (DIFFERENT if steps == 2 else MATERIAL), f"{steps} bands apart"
        if order and (a in order) != (b in order):
            return DIFFERENT, "one gave a band, the other an escape option"
        return DIFFERENT, "different options"
    if kind == "multi_select":
        la, lb = (a if isinstance(a, list) else []), (b if isinstance(b, list) else [])
        j = _jaccard(la, lb)
        shared, only_a, only_b = (
            len(set(la) & set(lb)),
            len(set(la) - set(lb)),
            len(set(lb) - set(la)),
        )
        detail = f"{shared} shared · {only_a} only first · {only_b} only second"
        if j == 1.0:
            return AGREE, detail
        if j >= TOLERANCE["multi_select_rough"]:
            return ROUGH, detail
        if j > 0:
            return DIFFERENT, detail
        return MATERIAL, "no option in common"
    if kind == "ranking":
        la, lb = (a if isinstance(a, list) else []), (b if isinstance(b, list) else [])
        if la == lb:
            return AGREE, "same order"
        if la and lb and la[0] == lb[0]:
            return ROUGH, "same first choice"
        top_a, top_b = set(la[:3]), set(lb[:3])
        if la and lb and (la[0] in top_b or lb[0] in top_a):
            return DIFFERENT, "each first choice is in the other's top three"
        return MATERIAL, "different first choices, neither in the other's top three"
    if kind in ("number", "currency", "percentage", "scale"):
        try:
            fa, fb = float(a), float(b)
        except (TypeError, ValueError):
            return NOT_COMPARABLE, "not numeric"
        if fa == fb:
            return AGREE, "same value"
        if kind == "scale":
            steps = abs(fa - fb)
            if steps <= TOLERANCE["scale_rough"]:
                return ROUGH, f"{steps:g} step apart"
            return (
                DIFFERENT if steps <= TOLERANCE["scale_different"] else MATERIAL
            ), f"{steps:g} steps apart"
        if kind == "percentage":
            points = abs(fa - fb)
            if points <= TOLERANCE["percentage_points_rough"]:
                return ROUGH, f"{points:g} points apart"
            return (
                DIFFERENT if points <= TOLERANCE["percentage_points_different"] else MATERIAL
            ), f"{points:g} points apart"
        rel = abs(fa - fb) / max(abs(fa), abs(fb))
        if rel <= TOLERANCE["relative_rough"]:
            return ROUGH, f"{rel:.0%} apart"
        return (
            DIFFERENT if rel <= TOLERANCE["relative_different"] else MATERIAL
        ), f"{rel:.0%} apart"
    return NOT_COMPARABLE, "free text: read side by side"


def compare_scenarios(a: Row, b: Row) -> tuple[str, str, dict[str, Any]]:
    da, db = a.get("decision"), b.get("decision")
    ea, eb = a.get("maximum_exposure_minor"), b.get("maximum_exposure_minor")
    exposure_delta = (int(ea) - int(eb)) if ea is not None and eb is not None else None
    reasons = _jaccard(a.get("reason_codes") or [], b.get("reason_codes") or [])
    extra = {
        "decision_match": da == db,
        "exposure_delta_minor": exposure_delta,
        "reason_overlap": round(reasons, 2),
        "info_overlap": bool(a.get("additional_information_needed"))
        and bool(b.get("additional_information_needed")),
    }
    if da == db:
        if (
            exposure_delta
            and max(abs(int(ea)), abs(int(eb)))
            and abs(exposure_delta) / max(abs(int(ea)), abs(int(eb)))
            > TOLERANCE["relative_different"]
        ):
            return ROUGH, "same decision, exposure differs by more than half", extra
        return AGREE, "same decision", extra
    if {da, db} == {"approve", "deny"}:
        return MATERIAL, "approve against deny", extra
    return DIFFERENT, f"{da} against {db}".replace("_", " "), extra


def compare_calibrations(a: Row, b: Row) -> tuple[str, str, dict[str, Any]]:
    same_class = a.get("classification_category") == b.get("classification_category")
    ma, mb = (
        MATERIALITY_RANK.get(str(a.get("underwriting_materiality")), 0),
        MATERIALITY_RANK.get(str(b.get("underwriting_materiality")), 0),
    )
    same_signal = a.get("underwriting_signal") == b.get("underwriting_signal")
    conf_delta = int(a.get("classification_confidence") or 0) - int(
        b.get("classification_confidence") or 0
    )
    extra = {
        "classification_match": same_class,
        "confidence_delta": conf_delta,
        "materiality_match": ma == mb,
        "materiality_steps": abs(ma - mb),
        "signal_match": same_signal,
    }
    if {a.get("underwriting_signal"), b.get("underwriting_signal")} == {"positive", "negative"}:
        return MATERIAL, "opposite risk signal", extra
    if abs(ma - mb) >= 2:
        return MATERIAL, f"materiality {abs(ma - mb)} steps apart", extra
    if not same_class:
        return DIFFERENT, "different classification", extra
    if not same_signal or abs(ma - mb) == 1:
        return ROUGH, "same classification; signal or materiality one step apart", extra
    return AGREE, "same classification, materiality and signal", extra


def compare(
    question: Row, ra: Row | None, rb: Row | None, labels: tuple[str, str]
) -> dict[str, Any]:
    """One row of a two-respondent comparison. Skips count as unanswered."""
    a_ok, b_ok = is_answered(ra), is_answered(rb)
    out: dict[str, Any] = {"a": display(question, ra), "b": display(question, rb), "extra": {}}
    if not a_ok and not b_ok:
        out.update(status=NEITHER, detail="")
        return out
    if a_ok != b_ok:
        out.update(status=only_answered(labels[0] if a_ok else labels[1]), detail="")
        return out
    assert ra is not None and rb is not None
    kind = question.get("question_type")
    if kind == "scenario_decision":
        status, detail, extra = compare_scenarios(
            ra.get("scenario") or {}, rb.get("scenario") or {}
        )
    elif kind == "transaction_calibration":
        status, detail, extra = compare_calibrations(
            ra.get("calibration") or {}, rb.get("calibration") or {}
        )
    else:
        status, detail = _compare_values(question, ra.get("answer"), rb.get("answer"))
        extra = {}
    out.update(status=status, detail=detail, extra=extra)
    return out


# --------------------------------------------------------------------------- progress --
def _sections_for(ds: Dataset, r: Respondent) -> list[Row]:
    return [
        ds.sections[sid]
        for sid in ds.template_sections.get(r.template_slug, [])
        if sid in ds.sections
    ]


def _visible(ds: Dataset, r: Respondent, section: Row) -> list[Row]:
    return prog.visible_questions(ds.questions_in(section["id"]), ds.responses_of(r))


def _last_activity(ds: Dataset, r: Respondent) -> str | None:
    stamps = [
        str(row.get("updated_at") or row.get("created_at") or "")
        for row in ds.responses_of(r).values()
    ]
    stamps += [
        str(s.get("last_seen_at") or s.get("started_at") or "")
        for s in ds.sessions.get(r.assignment_id, [])
    ]
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


def respondent_card(ds: Dataset, r: Respondent) -> dict[str, Any]:
    mine = ds.responses_of(r)
    by_group: dict[str, dict[str, int]] = defaultdict(
        lambda: {"available": 0, "answered": 0, "skipped": 0}
    )
    core_total = core_answered = skips = prefer = total = answered = 0
    for section in _sections_for(ds, r):
        group = tx.group_of(section["slug"])
        for q in _visible(ds, r, section):
            resp = mine.get(str(q["id"]))
            g = by_group[group]
            g["available"] += 1
            total += 1
            if is_answered(resp):
                g["answered"] += 1
                answered += 1
            elif resp and resp.get("status") == "skipped":
                g["skipped"] += 1
                skips += 1
            if q.get("required_core"):
                core_total += 1
                core_answered += 1 if is_answered(resp) else 0
            if prefers_not(q, resp):
                prefer += 1
    sessions = ds.sessions.get(r.assignment_id, [])
    return {
        "user_id": r.user_id,
        "assignment_id": r.assignment_id,
        "template_slug": r.template_slug,
        "label": r.label,
        "name": r.name,
        "kind": r.kind,
        "status": r.status,
        "last_activity": _last_activity(ds, r),
        "sessions": len(sessions),
        "core_total": core_total,
        "core_answered": core_answered,
        "core_percent": round(100 * core_answered / core_total) if core_total else 0,
        "total": total,
        "answered": answered,
        "skips": skips,
        "prefer_not_to_answer": prefer,
        "by_group": {k: dict(v) for k, v in by_group.items()},
    }


# --------------------------------------------------------------------------- questions --
def question_view(ds: Dataset, q: Row) -> dict[str, Any]:
    slug = q["section_slug"]
    meta = q.get("meta") or {}
    return {
        "id": str(q["id"]),
        "question_key": q["question_key"],
        "question_text": q["question_text"],
        "helper_text": q.get("helper_text"),
        "question_type": q["question_type"],
        "options": q.get("options") or [],
        "required_core": bool(q.get("required_core")),
        "order": int(q.get("order") or 0),
        "priority": int(q.get("priority") or 0),
        "sensitivity": q.get("sensitivity"),
        "section_slug": slug,
        "section_title": ds.sections[q["section_id"]]["title"],
        "level": tx.level_of(slug),
        "group": tx.group_of(slug),
        "topic": tx.topic_of(q["question_key"], slug),
        "owner": tx.owner_of(slug),
        "retired": q.get("retired_at") is not None,
        "meta": {
            k: meta.get(k)
            for k in (
                "system_use",
                "semantic_class",
                "ask_stage",
                "why",
                "tells",
                "tests",
                "teaches",
                "title",
            )
        },
        "scenario_brief": q.get("scenario_brief"),
        "transaction_case": q.get("transaction_case"),
    }


def response_view(q: Row, resp: Row | None) -> dict[str, Any] | None:
    if resp is None:
        return None
    return {
        "status": resp.get("status"),
        "answer": resp.get("answer"),
        "display": display(q, resp),
        "note": resp.get("note"),
        "scenario": resp.get("scenario"),
        "calibration": resp.get("calibration"),
        "question_version": resp.get("question_version"),
        "created_at": resp.get("created_at"),
        "updated_at": resp.get("updated_at"),
    }


def shared_founder_questions(ds: Dataset) -> list[Row]:
    """The question items both founders were asked: founder-* sections of the three levels."""
    return [
        q
        for q in sorted(ds.questions.values(), key=lambda x: (x["section_slug"], int(x["order"])))
        if tx.owner_of(q["section_slug"]) == "founder"
        and tx.group_of(q["section_slug"]) in ("core", "recommended", "deep_dive")
        and q.get("retired_at") is None
    ]


def founder_pair(ds: Dataset) -> tuple[Respondent | None, Respondent | None, tuple[str, str]]:
    j, a = ds.respondent_for("discovery-lead"), ds.respondent_for("discovery-partner")
    return j, a, (j.label if j else "Lead", a.label if a else "Partner")


def founder_assignments(ds: Dataset) -> dict[str, str | None]:
    """The two founders' assignment ids by label, for the drawer's history reads."""
    j, a, labels = founder_pair(ds)
    return {labels[0]: j.assignment_id if j else None, labels[1]: a.assignment_id if a else None}


def founder_rows(ds: Dataset) -> list[dict[str, Any]]:
    j, a, labels = founder_pair(ds)
    rows = []
    for q in shared_founder_questions(ds):
        cmp = compare(q, ds.response(j, str(q["id"])), ds.response(a, str(q["id"])), labels)
        view = question_view(ds, q)
        rows.append(
            {
                "question": view,
                "status": cmp["status"],
                "detail": cmp["detail"],
                "extra": cmp["extra"],
                "lead": response_view(q, ds.response(j, str(q["id"]))),
                "partner": response_view(q, ds.response(a, str(q["id"]))),
                "business_fact": tx.is_business_fact(q["question_key"]),
                "underwriting": tx.is_underwriting(q["question_key"], q["section_slug"]),
            }
        )
    return rows


def founder_summary(rows: list[dict[str, Any]], labels: tuple[str, str]) -> dict[str, int]:
    return {
        "total": len(rows),
        "both": sum(
            1
            for r in rows
            if r["status"] not in (NEITHER, only_answered(labels[0]), only_answered(labels[1]))
        ),
        "only_lead": sum(1 for r in rows if r["status"] == only_answered(labels[0])),
        "only_partner": sum(1 for r in rows if r["status"] == only_answered(labels[1])),
        "neither": sum(1 for r in rows if r["status"] == NEITHER),
        "agree": sum(1 for r in rows if r["status"] == AGREE),
        "roughly": sum(1 for r in rows if r["status"] == ROUGH),
        "different": sum(1 for r in rows if r["status"] == DIFFERENT),
        "material": sum(1 for r in rows if r["status"] == MATERIAL),
        "not_comparable": sum(1 for r in rows if r["status"] == NOT_COMPARABLE),
    }


# ------------------------------------------------------------------------ calibration --
def calibration_rows(ds: Dataset) -> list[dict[str, Any]]:
    j, a, labels = founder_pair(ds)
    rows = []
    for q in sorted(ds.questions.values(), key=lambda x: (x["section_slug"], int(x["order"]))):
        if (
            q.get("question_type") != "transaction_calibration"
            or tx.owner_of(q["section_slug"]) != "founder"
            or q.get("retired_at")
        ):
            continue
        case = q.get("transaction_case") or {}
        rj, ra = ds.response(j, str(q["id"])), ds.response(a, str(q["id"]))
        cmp = compare(q, rj, ra, labels)
        meta = q.get("meta") or {}
        rows.append(
            {
                "question": question_view(ds, q),
                "case_key": q["question_key"],
                "title": meta.get("title"),
                "case_family": case.get("case_family"),
                "variant": case.get("variant"),
                "changed_feature": case.get("changed_feature")
                or case.get("authored_changed_feature"),
                "baseline_question_id": case.get("baseline_question_id"),
                "reveal_of_question_id": case.get("reveal_of_question_id"),
                "is_variant": bool(case.get("baseline_question_id")),
                "is_reveal": bool(case.get("reveal_of_question_id")),
                "amount_minor": case.get("amount_minor"),
                "direction": case.get("direction"),
                "business_vertical": case.get("business_vertical"),
                "high_dollar": (int(case.get("amount_minor") or 0)) >= 1_000_000,
                "status": cmp["status"],
                "detail": cmp["detail"],
                "extra": cmp["extra"],
                "lead": response_view(q, rj),
                "partner": response_view(q, ra),
            }
        )
    return rows


def _changes(before: Row | None, after: Row | None) -> dict[str, Any] | None:
    if not (is_answered(before) and is_answered(after)):
        return None
    b, a = before.get("calibration") or {}, after.get("calibration") or {}  # type: ignore[union-attr]
    return {
        "classification": b.get("classification_category") != a.get("classification_category"),
        "confidence": b.get("classification_confidence") != a.get("classification_confidence"),
        "materiality": b.get("underwriting_materiality") != a.get("underwriting_materiality"),
        "signal": b.get("underwriting_signal") != a.get("underwriting_signal"),
    }


def controlled_pairs(ds: Dataset, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {r["question"]["id"]: r for r in rows}
    j, a, _ = founder_pair(ds)
    out = []
    for r in rows:
        base = by_id.get(str(r["baseline_question_id"])) if r["is_variant"] else None
        if base is None:
            continue
        qb, qv = ds.questions[base["question"]["id"]], ds.questions[r["question"]["id"]]
        out.append(
            {
                "case_family": r["case_family"],
                "changed_feature": r["changed_feature"],
                "baseline": base,
                "variant": r,
                "lead_changed": _changes(
                    ds.response(j, str(qb["id"])), ds.response(j, str(qv["id"]))
                ),
                "partner_changed": _changes(
                    ds.response(a, str(qb["id"])), ds.response(a, str(qv["id"]))
                ),
            }
        )
    return out


def reveals(ds: Dataset, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {r["question"]["id"]: r for r in rows}
    j, a, _ = founder_pair(ds)
    out = []
    for r in rows:
        original = by_id.get(str(r["reveal_of_question_id"])) if r["is_reveal"] else None
        if original is None:
            continue
        qo, qr = ds.questions[original["question"]["id"]], ds.questions[r["question"]["id"]]
        case = qr.get("transaction_case") or {}
        out.append(
            {
                "case_key": original["case_key"],
                "title": original["title"],
                "reveal": (case.get("reveal") or {}).get("fact")
                if isinstance(case.get("reveal"), dict)
                else (qr.get("meta") or {}).get("reveal_fact"),
                "before": original,
                "after": r,
                "lead_changed": _changes(
                    ds.response(j, str(qo["id"])), ds.response(j, str(qr["id"]))
                ),
                "partner_changed": _changes(
                    ds.response(a, str(qo["id"])), ds.response(a, str(qr["id"]))
                ),
            }
        )
    return out


def calibration_summary(rows: list[dict[str, Any]], labels: tuple[str, str]) -> dict[str, int]:
    both = [
        r
        for r in rows
        if r["status"] not in (NEITHER, only_answered(labels[0]), only_answered(labels[1]))
    ]
    return {
        "cases": len(rows),
        "both": len(both),
        "agreements": sum(1 for r in both if r["status"] in (AGREE, ROUGH)),
        "material": sum(1 for r in both if r["status"] == MATERIAL),
        "classification_disagreements": sum(
            1 for r in both if not r["extra"].get("classification_match", True)
        ),
        "awaiting_both": sum(1 for r in rows if r["status"] == NEITHER),
        "awaiting_one": sum(
            1 for r in rows if r["status"] in (only_answered(labels[0]), only_answered(labels[1]))
        ),
    }


# -------------------------------------------------------------------------- scenarios --
def scenario_rows(ds: Dataset) -> list[dict[str, Any]]:
    j, a, labels = founder_pair(ds)
    rows = []
    for q in sorted(ds.questions.values(), key=lambda x: (x["section_slug"], int(x["order"]))):
        if (
            q.get("question_type") != "scenario_decision"
            or tx.owner_of(q["section_slug"]) != "founder"
            or q.get("retired_at")
        ):
            continue
        rj, ra = ds.response(j, str(q["id"])), ds.response(a, str(q["id"]))
        cmp = compare(q, rj, ra, labels)
        rows.append(
            {
                "question": question_view(ds, q),
                "scenario_key": q["question_key"],
                "title": (q.get("meta") or {}).get("title"),
                "status": cmp["status"],
                "detail": cmp["detail"],
                "extra": cmp["extra"],
                "approve_vs_deny": cmp["status"] == MATERIAL,
                "lead": response_view(q, rj),
                "partner": response_view(q, ra),
            }
        )
    return rows


def scenario_summary(rows: list[dict[str, Any]], labels: tuple[str, str]) -> dict[str, int]:
    both = [
        r
        for r in rows
        if r["status"] not in (NEITHER, only_answered(labels[0]), only_answered(labels[1]))
    ]
    return {
        "scenarios": len(rows),
        "both": len(both),
        "decision_agreements": sum(1 for r in both if r["extra"].get("decision_match")),
        "decision_disagreements": sum(1 for r in both if not r["extra"].get("decision_match")),
        "approve_vs_deny": sum(1 for r in both if r["approve_vs_deny"]),
        "awaiting_both": sum(1 for r in rows if r["status"] == NEITHER),
    }


# ------------------------------------------------------------------------ engineering --
def engineering_view(ds: Dataset) -> dict[str, Any]:
    d = ds.respondent_for("discovery-engineer")
    items = []
    for q in sorted(
        ds.questions.values(),
        key=lambda x: (
            0 if tx.level_of(x["section_slug"]) == "core" else 1,
            x["section_slug"],
            int(x["order"]),
        ),
    ):
        if tx.owner_of(q["section_slug"]) != "engineer" or q.get("retired_at"):
            continue
        resp = ds.response(d, str(q["id"]))
        items.append(
            {
                "question": question_view(ds, q),
                "category": tx.engineer_category_of(q["question_key"], q["section_slug"]),
                "buckets": tx.engineering_buckets_of(q["question_key"]),
                "response": response_view(q, resp),
            }
        )
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        by_category[it["category"]].append(it)
    buckets = []
    for bucket, rule, _keys, _frags in tx.ENGINEERING_BUCKETS:
        members = [it for it in items if bucket in it["buckets"]]
        buckets.append(
            {
                "bucket": bucket,
                "rule": rule,
                "answered": sum(
                    1 for it in members if is_answered(ds.response(d, it["question"]["id"]))
                ),
                "total": len(members),
                "items": members,
            }
        )
    constraints = [
        it
        for it in items
        if "RISK_SIGNAL" in (it["question"]["meta"].get("system_use") or [])
        and is_answered(ds.response(d, it["question"]["id"]))
    ]
    return {
        "respondent": d.label if d else None,
        "assignment_id": d.assignment_id if d else None,
        "categories": [
            {"category": c, "items": by_category[c]}
            for c in tx.ENGINEER_CATEGORY_ORDER
            if by_category.get(c)
        ],
        "buckets": buckets,
        "stated_constraints": sorted(constraints, key=lambda it: -it["question"]["priority"])[:12],
        "answered": sum(
            1 for it in items if it["response"] and it["response"]["status"] == "answered"
        ),
        "total": len(items),
    }


# ------------------------------------------------------------------------- engagement --
def engagement(ds: Dataset) -> list[dict[str, Any]]:
    out = []
    for r in ds.respondents:
        card = respondent_card(ds, r)
        mine = ds.responses_of(r)
        topics: dict[str, dict[str, int]] = defaultdict(lambda: {"available": 0, "answered": 0})
        explanations = answered_optional = text_chars = text_count = 0
        for section in _sections_for(ds, r):
            for q in _visible(ds, r, section):
                resp = mine.get(str(q["id"]))
                t = topics[tx.topic_of(q["question_key"], q["section_slug"])]
                t["available"] += 1
                if not is_answered(resp):
                    continue
                t["answered"] += 1
                assert resp is not None
                kind = q.get("question_type")
                if kind == "transaction_calibration":
                    answered_optional += 1
                    explanations += 1 if (resp.get("calibration") or {}).get("reasoning") else 0
                elif kind == "scenario_decision":
                    answered_optional += 1
                    s = resp.get("scenario") or {}
                    explanations += (
                        1 if (s.get("notes") or s.get("what_would_change_my_decision")) else 0
                    )
                else:
                    answered_optional += 1
                    explanations += 1 if resp.get("note") else 0
                if kind in ("short_text", "long_text") and isinstance(resp.get("answer"), str):
                    text_chars += len(resp["answer"])
                    text_count += 1
        out.append(
            {
                **card,
                "total_percent": round(100 * card["answered"] / card["total"])
                if card["total"]
                else 0,
                "explanation_rate": round(100 * explanations / answered_optional)
                if answered_optional
                else 0,
                "average_text_length": round(text_chars / text_count) if text_count else None,
                "topics": [
                    {
                        "topic": k,
                        **v,
                        "percent": round(100 * v["answered"] / v["available"])
                        if v["available"]
                        else 0,
                    }
                    for k, v in sorted(topics.items())
                ],
            }
        )
    return out


# ---------------------------------------------------------------- business / underwriting --
def business_view(ds: Dataset, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Business facts from the shared bank plus each founder's own bank, grouped and flagged."""
    j, a, _ = founder_pair(ds)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not row["business_fact"]:
            continue
        q = row["question"]
        grouped[tx.business_group_of(q["question_key"], q["section_slug"])].append(
            {
                **row,
                "needs_reconciliation": row["status"] in DISAGREEMENTS,
            }
        )
    for q in sorted(ds.questions.values(), key=lambda x: (x["section_slug"], int(x["order"]))):
        owner = tx.owner_of(q["section_slug"])
        if (
            owner not in ("lead", "partner")
            or q.get("retired_at")
            or not tx.is_business_fact(q["question_key"])
        ):
            continue
        r = j if owner == "lead" else a
        resp = ds.response(r, str(q["id"]))
        grouped[tx.business_group_of(q["question_key"], q["section_slug"])].append(
            {
                "question": question_view(ds, q),
                "status": "OWN_BANK",
                "detail": f"{owner} only",
                "extra": {},
                "lead": response_view(q, resp) if owner == "lead" else None,
                "partner": response_view(q, resp) if owner == "partner" else None,
                "business_fact": True,
                "underwriting": False,
                "needs_reconciliation": False,
            }
        )
    return {
        "groups": [
            {"group": g, "items": grouped[g]} for g in tx.BUSINESS_GROUP_ORDER if grouped.get(g)
        ],
        "facts_collected": sum(
            1 for items in grouped.values() for it in items if it["lead"] or it["partner"]
        ),
        "needs_reconciliation": sum(
            1 for items in grouped.values() for it in items if it["needs_reconciliation"]
        ),
    }


def policy_signals(
    rows: list[dict[str, Any]],
    calibration: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    labels: tuple[str, str],
) -> list[dict[str, Any]]:
    """Observed response patterns on underwriting questions. Never a rule; phrased as observed."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if not row["underwriting"]:
            continue
        q = row["question"]
        if row["status"] in (AGREE, ROUGH) and row["lead"]:
            out.append(
                {
                    "kind": "aligned",
                    "question_id": q["id"],
                    "question_key": q["question_key"],
                    "text": f"Both founders: {q['question_text']} → {row['lead']['display']}"
                    + (f" / {row['partner']['display']}" if row["status"] == ROUGH else ""),
                }
            )
        elif row["status"] == MATERIAL:
            out.append(
                {
                    "kind": "split",
                    "question_id": q["id"],
                    "question_key": q["question_key"],
                    "text": (
                        f"{labels[0]}: {row['lead']['display']}; "
                        f"{labels[1]}: {row['partner']['display']} — {q['question_text']}"
                    ),
                }
            )
    for r in calibration:
        cj, ca = (r["lead"] or {}).get("calibration"), (r["partner"] or {}).get("calibration")
        if (
            cj
            and ca
            and cj.get("underwriting_materiality") == ca.get("underwriting_materiality")
            and cj["underwriting_materiality"] in ("high", "critical")
            and cj.get("underwriting_signal") == ca.get("underwriting_signal")
        ):
            out.append(
                {
                    "kind": "aligned",
                    "question_id": r["question"]["id"],
                    "question_key": r["case_key"],
                    "text": (
                        f"Both founders rate {r['title'] or r['case_key']} "
                        f"{cj['underwriting_materiality']} materiality, "
                        f"{cj['underwriting_signal']} signal"
                    ),
                }
            )
    for r in scenarios:
        if r["approve_vs_deny"]:
            sj, sa = r["lead"]["scenario"], r["partner"]["scenario"]
            out.append(
                {
                    "kind": "split",
                    "question_id": r["question"]["id"],
                    "question_key": r["scenario_key"],
                    "text": (
                        f"{r['title'] or r['scenario_key']}: {labels[0]} would "
                        f"{sj['decision']}, {labels[1]} would {sa['decision']}"
                    ),
                }
            )
    return out


def underwriting_view(
    ds: Dataset,
    rows: list[dict[str, Any]],
    calibration: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
) -> dict[str, Any]:
    _, _, labels = founder_pair(ds)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["underwriting"]:
            grouped[tx.underwriting_group_of(row["question"]["question_key"])].append(row)
    order = [name for name, _ in tx.UNDERWRITING_GROUPS] + ["Other underwriting"]
    return {
        "groups": [{"group": g, "items": grouped[g]} for g in order if grouped.get(g)],
        "signals": policy_signals(rows, calibration, scenarios, labels),
    }


# ---------------------------------------------------------------------- open questions --
def open_questions(
    ds: Dataset,
    rows: list[dict[str, Any]],
    calibration: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    engineering: dict[str, Any],
) -> list[dict[str, Any]]:
    j, a, labels = founder_pair(ds)
    d = ds.respondent_for("discovery-engineer")
    queue: list[dict[str, Any]] = []

    def why_of(q: dict[str, Any]) -> str | None:
        return q["meta"].get("why") or q["meta"].get("tells") or q["meta"].get("tests")

    for row in rows:
        q = row["question"]
        if row["status"] == MATERIAL:
            queue.append(
                {
                    "kind": "founder_material_disagreement",
                    "rank": 0,
                    "question": q,
                    "why": why_of(q),
                    "lead": row["lead"],
                    "partner": row["partner"],
                    "detail": row["detail"],
                }
            )
        elif row["status"] == DIFFERENT:
            queue.append(
                {
                    "kind": "founder_disagreement",
                    "rank": 2,
                    "question": q,
                    "why": why_of(q),
                    "lead": row["lead"],
                    "partner": row["partner"],
                    "detail": row["detail"],
                }
            )
        elif (
            row["status"] in (NEITHER, only_answered(labels[0]), only_answered(labels[1]))
            and q["required_core"]
        ):
            missing = [
                lbl
                for lbl, r in ((labels[0], row["lead"]), (labels[1], row["partner"]))
                if not (r and r["status"] == "answered")
            ]
            queue.append(
                {
                    "kind": "unanswered_core",
                    "rank": 1 if q["priority"] >= 90 else 3,
                    "question": q,
                    "why": why_of(q),
                    "lead": row["lead"],
                    "partner": row["partner"],
                    "detail": f"awaiting {', '.join(missing)}",
                }
            )
        if q["meta"].get("semantic_class") == "UNRESOLVED_DISAGREEMENT" and row["status"] not in (
            MATERIAL,
            DIFFERENT,
        ):
            queue.append(
                {
                    "kind": "unresolved_disagreement_marker",
                    "rank": 2,
                    "question": q,
                    "why": why_of(q),
                    "lead": row["lead"],
                    "partner": row["partner"],
                    "detail": "authored as a known point of disagreement",
                }
            )
    for r in scenarios:
        if r["approve_vs_deny"]:
            queue.append(
                {
                    "kind": "approve_vs_deny",
                    "rank": 0,
                    "question": r["question"],
                    "why": r["question"]["meta"].get("tests"),
                    "lead": r["lead"],
                    "partner": r["partner"],
                    "detail": r["detail"],
                }
            )
    for r in calibration:
        if r["status"] in DISAGREEMENTS:
            queue.append(
                {
                    "kind": "calibration_disagreement",
                    "rank": 1 if r["status"] == MATERIAL else 3,
                    "question": r["question"],
                    "why": r["question"]["meta"].get("tests"),
                    "lead": r["lead"],
                    "partner": r["partner"],
                    "detail": r["detail"],
                }
            )
    for cat in engineering["categories"]:
        for it in cat["items"]:
            q = it["question"]
            if q["required_core"] and not (
                it["response"] and it["response"]["status"] == "answered"
            ):
                queue.append(
                    {
                        "kind": "engineer_unanswered_core",
                        "rank": 1 if q["priority"] >= 90 else 3,
                        "question": q,
                        "why": why_of(q),
                        "engineer": it["response"],
                        "detail": f"awaiting {d.label if d else 'Engineer'}",
                    }
                )
    queue.sort(key=lambda x: (x["rank"], -x["question"]["priority"], x["question"]["question_key"]))
    return queue


# ------------------------------------------------------------------------- overview --
def overview(ds: Dataset) -> dict[str, Any]:
    _, _, labels = founder_pair(ds)
    rows = founder_rows(ds)
    cal = calibration_rows(ds)
    scen = scenario_rows(ds)
    eng = engineering_view(ds)
    biz = business_view(ds, rows)
    uw = underwriting_view(ds, rows, cal, scen)
    queue = open_questions(ds, rows, cal, scen, eng)
    cards = [respondent_card(ds, r) for r in ds.respondents]
    recent = sorted(
        (
            {
                "respondent": r.label,
                "kind": r.kind,
                "assignment_id": r.assignment_id,
                "question_id": qid,
                "question_key": ds.questions[qid]["question_key"],
                "question_text": ds.questions[qid]["question_text"],
                "level": tx.level_of(ds.questions[qid]["section_slug"]),
                "group": tx.group_of(ds.questions[qid]["section_slug"]),
                "status": row.get("status"),
                "display": display(ds.questions[qid], row),
                "at": row.get("updated_at") or row.get("created_at"),
            }
            for r in ds.respondents
            for qid, row in ds.responses_of(r).items()
            if qid in ds.questions
        ),
        key=lambda x: str(x["at"] or ""),
        reverse=True,
    )[:15]
    engineer = next((c for c in cards if c["template_slug"] == "discovery-engineer"), None)
    return {
        "respondents": cards,
        "answers_received": {
            c["label"]: c["answered"] for c in cards if c["kind"] == "stakeholder"
        },
        "engineer_core": {
            "answered": engineer["core_answered"] if engineer else 0,
            "total": engineer["core_total"] if engineer else 0,
        },
        "open_high_priority": sum(1 for x in queue if x["rank"] <= 1),
        "founder": founder_summary(rows, labels),
        "calibration": calibration_summary(cal, labels),
        "scenarios": scenario_summary(scen, labels),
        "recent_activity": recent,
        "top_disagreements": [
            x
            for x in queue
            if x["kind"]
            in ("founder_material_disagreement", "founder_disagreement", "approve_vs_deny")
        ][:8],
        "top_unanswered": [
            x for x in queue if x["kind"] in ("unanswered_core", "engineer_unanswered_core")
        ][:8],
        "facts_collected": biz["facts_collected"],
        "facts_needing_reconciliation": biz["needs_reconciliation"],
        "policy_signals": uw["signals"][:8],
        "engineer_constraints": [
            {"question": it["question"], "response": it["response"]}
            for it in eng["stated_constraints"][:6]
        ],
        "unknowns": [
            x for x in queue if x["kind"] in ("unanswered_core", "unresolved_disagreement_marker")
        ][:8],
        "open_questions": len(queue),
    }


# --------------------------------------------------------------------------- exports --
def _csv(header: list[str], lines: list[list[Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(lines)
    return buf.getvalue()


def founder_csv(rows: list[dict[str, Any]]) -> str:
    return _csv(
        [
            "question_key",
            "section",
            "level",
            "topic",
            "type",
            "question",
            "lead",
            "partner",
            "status",
            "detail",
        ],
        [
            [
                r["question"]["question_key"],
                r["question"]["section_slug"],
                r["question"]["level"],
                r["question"]["topic"],
                r["question"]["question_type"],
                r["question"]["question_text"],
                (r["lead"] or {}).get("display") or "",
                (r["partner"] or {}).get("display") or "",
                r["status"],
                r["detail"],
            ]
            for r in rows
        ],
    )


def calibration_csv(rows: list[dict[str, Any]]) -> str:
    def cal(x: dict[str, Any] | None) -> list[Any]:
        c = (x or {}).get("calibration") or {}
        return [
            c.get("classification_category", ""),
            c.get("classification_confidence", ""),
            c.get("underwriting_materiality", ""),
            c.get("underwriting_signal", ""),
        ]

    return _csv(
        [
            "case",
            "title",
            "family",
            "variant",
            "changed_feature",
            "direction",
            "amount_minor",
            "vertical",
            "level",
            "lead_class",
            "lead_conf",
            "lead_materiality",
            "lead_signal",
            "partner_class",
            "partner_conf",
            "partner_materiality",
            "partner_signal",
            "status",
            "detail",
        ],
        [
            [
                r["case_key"],
                r["title"] or "",
                r["case_family"] or "",
                r["variant"] or "",
                r["changed_feature"] or "",
                r["direction"] or "",
                r["amount_minor"] if r["amount_minor"] is not None else "",
                r["business_vertical"] or "",
                r["question"]["level"],
                *cal(r["lead"]),
                *cal(r["partner"]),
                r["status"],
                r["detail"],
            ]
            for r in rows
        ],
    )


def scenario_csv(rows: list[dict[str, Any]]) -> str:
    def sc(x: dict[str, Any] | None) -> list[Any]:
        s = (x or {}).get("scenario") or {}
        return [
            s.get("decision", ""),
            s.get("confidence", ""),
            s.get("maximum_exposure_minor", ""),
            ";".join(s.get("reason_codes") or []),
        ]

    return _csv(
        [
            "scenario",
            "title",
            "level",
            "lead_decision",
            "lead_conf",
            "lead_exposure_minor",
            "lead_reasons",
            "partner_decision",
            "partner_conf",
            "partner_exposure_minor",
            "partner_reasons",
            "status",
            "detail",
        ],
        [
            [
                r["scenario_key"],
                r["title"] or "",
                r["question"]["level"],
                *sc(r["lead"]),
                *sc(r["partner"]),
                r["status"],
                r["detail"],
            ]
            for r in rows
        ],
    )


def progress_csv(cards: list[dict[str, Any]]) -> str:
    groups = ("core", "recommended", "deep_dive", "calibration", "scenarios")
    return _csv(
        [
            "respondent",
            "kind",
            "status",
            "last_activity",
            "sessions",
            "core_answered",
            "core_total",
            "core_percent",
            "answered",
            "total",
            "skips",
            "prefer_not_to_answer",
            *[f"{g}_answered" for g in groups],
            *[f"{g}_available" for g in groups],
        ],
        [
            [
                c["label"],
                c["kind"],
                c["status"],
                c["last_activity"] or "",
                c["sessions"],
                c["core_answered"],
                c["core_total"],
                c["core_percent"],
                c["answered"],
                c["total"],
                c["skips"],
                c["prefer_not_to_answer"],
                *[c["by_group"].get(g, {}).get("answered", 0) for g in groups],
                *[c["by_group"].get(g, {}).get("available", 0) for g in groups],
            ]
            for c in cards
        ],
    )
