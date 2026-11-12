"""The stakeholder's own view of the Discovery portal: what to answer, and what was answered.

Every assignment-scoped route depends on `Mine`, which resolves the assignment in the path
through the caller's own user id and gives a stranger, an admin, a guessed id and a malformed
id the same 404. Nothing here takes a user id from the browser. The role is read for the shell,
never trusted from the request.

Questions leave this module only after branch visibility is applied server-side. A hidden
question is absent from the payload, not flagged in it, so the browser cannot show what the
person is not meant to see.

A save is one `PUT` per question, idempotent: the same answer twice is the same row. History is
written by the database triggers on update and delete, never here. A scenario answer is two
rows, generic and typed; the read path counts it as answered only when both exist, so a save
that got half way can never be reported as complete.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.auth import CurrentUser, refuse_on_public_demo, require_client_header, require_features
from app.discovery import progress as prog
from app.discovery.answers import CATEGORIES_BY_DIRECTION, TYPED_TYPES, AnswerError, normalise
from app.discovery.auth import AssignmentScope, Mine, Supabase, require_discovery, role_of
from app.discovery.models import (
    AssignmentSummary,
    AssignmentView,
    CalibrationAnswerView,
    HistoryEntry,
    HistoryView,
    MeView,
    OptionView,
    ProgressView,
    QuestionView,
    ResponseView,
    SaveRequest,
    SaveResult,
    ScenarioAnswerView,
    SectionSummary,
    SectionView,
    SessionView,
    TransactionCaseView,
)
from app.supabase import SupabaseClient, SupabaseError
from app.workspaces import _as_uuid

router = APIRouter(
    prefix="/api/discovery",
    tags=["discovery"],
    dependencies=[
        Depends(require_features),
        Depends(refuse_on_public_demo),
        Depends(require_discovery),
    ],
)

logger = logging.getLogger(__name__)

QUESTION_FIELDS = (
    "id,section_id,question_key,question_version,question_text,helper_text,question_type,"
    "options,scenario_brief,required_core,priority,sensitivity,order,branch_condition,"
    "retired_at"
)
ASSIGNMENT_ROW_FIELDS = (
    "id,title_override,current_section_id,status,started_at,core_completed_at,completed_at"
)
RESPONSE_FIELDS = (
    "id,assignment_id,question_id,section_id,question_version,question_type,status,answer,note,"
    "updated_at"
)
SCENARIO_FIELDS = (
    "response_id,decision,confidence,reason_codes,maximum_exposure_minor,"
    "additional_information_needed,what_would_change_my_decision,notes"
)
# The stimulus only. case_family, variant and changed_feature are never read on this path, so
# nothing served to a respondent can link two cases as versions of each other.
CASE_FIELDS = (
    "question_id,direction,amount_minor,descriptor,business_vertical,business_description,"
    "account_type,merchant,counterparty,payment_rail,posted_date,day_of_week,time_of_day,memo,"
    "frequency_30d,frequency_90d,average_amount_minor,amount_min_minor,amount_max_minor,"
    "recurrence_pattern,related_transactions,context"
)
CALIBRATION_FIELDS = (
    "response_id,direction,classification_category,classification_confidence,"
    "underwriting_materiality,underwriting_signal,decision_impact,reason_codes,"
    "needs_more_context,context_needed,reasoning,handling_recommendation"
)
TYPED_SAVE = "save_discovery_typed_response"


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------- reads --
@dataclass(frozen=True)
class Bank:
    """One assignment's template, fully read: sections in order, live questions per section,
    the caller's responses, and the typed scenario rows. Read once per request."""

    template: dict[str, Any]
    sections: list[dict[str, Any]]  # each carries "order" from the join row
    questions_by_section: dict[str, list[dict[str, Any]]]
    responses_by_question: dict[str, dict[str, Any]]
    scenarios_by_response: dict[str, dict[str, Any]]
    calibrations_by_response: dict[str, dict[str, Any]]


def _read_bank(supabase: SupabaseClient, scope: AssignmentScope) -> Bank:
    templates = supabase.rest(
        "GET",
        "discovery_templates",
        params={
            "id": f"eq.{scope.template_id}",
            "select": "id,slug,title,subtitle,estimated_minutes",
        },
    )
    if not templates:
        raise _not_found()
    links = supabase.rest(
        "GET",
        "discovery_template_sections",
        params={"template_id": f"eq.{scope.template_id}", "select": "section_id,order"},
    )
    section_ids = [str(link["section_id"]) for link in links]
    sections: list[dict[str, Any]] = []
    if section_ids:
        rows = supabase.rest(
            "GET",
            "discovery_sections",
            params={
                "id": f"in.({','.join(section_ids)})",
                "select": "id,slug,title,description,is_core,estimated_minutes",
            },
        )
        by_id = {str(r["id"]): r for r in rows}
        for link in sorted(links, key=lambda x: int(x["order"])):
            section = by_id.get(str(link["section_id"]))
            if section is not None:
                sections.append({**section, "order": int(link["order"])})
    questions_by_section: dict[str, list[dict[str, Any]]] = {s["id"]: [] for s in sections}
    if section_ids:
        for question in supabase.rest(
            "GET",
            "discovery_questions",
            params={
                "section_id": f"in.({','.join(section_ids)})",
                "retired_at": "is.null",
                "select": QUESTION_FIELDS,
            },
        ):
            questions_by_section.setdefault(str(question["section_id"]), []).append(question)
    for bucket in questions_by_section.values():
        bucket.sort(key=lambda x: int(x["order"]))
    # A calibration question carries its case; the case's direction decides which categories
    # an answer may use, so it rides on the question dict for the validator.
    calibration_ids = [
        str(x["id"])
        for bucket in questions_by_section.values()
        for x in bucket
        if x.get("question_type") == "transaction_calibration"
    ]
    if calibration_ids:
        cases = {
            str(c["question_id"]): c
            for c in supabase.rest(
                "GET",
                "discovery_transaction_cases",
                params={"question_id": f"in.({','.join(calibration_ids)})", "select": CASE_FIELDS},
            )
        }
        for bucket in questions_by_section.values():
            for x in bucket:
                if x.get("question_type") == "transaction_calibration":
                    x["transaction_case"] = cases.get(str(x["id"]))
    responses = supabase.rest(
        "GET",
        "discovery_responses",
        params={"assignment_id": f"eq.{scope.assignment_id}", "select": RESPONSE_FIELDS},
    )
    responses_by_question = {str(r["question_id"]): r for r in responses}
    scenarios_by_response: dict[str, dict[str, Any]] = {}
    response_ids = [
        str(r["id"]) for r in responses if r.get("question_type") == "scenario_decision"
    ]
    if response_ids:
        for row in supabase.rest(
            "GET",
            "discovery_scenario_responses",
            params={"response_id": f"in.({','.join(response_ids)})", "select": SCENARIO_FIELDS},
        ):
            scenarios_by_response[str(row["response_id"])] = row
    calibrations_by_response: dict[str, dict[str, Any]] = {}
    calibration_response_ids = [
        str(r["id"]) for r in responses if r.get("question_type") == "transaction_calibration"
    ]
    if calibration_response_ids:
        for row in supabase.rest(
            "GET",
            "discovery_transaction_calibration_responses",
            params={
                "response_id": f"in.({','.join(calibration_response_ids)})",
                "select": CALIBRATION_FIELDS,
            },
        ):
            calibrations_by_response[str(row["response_id"])] = row
    # A typed answer whose typed row is missing is not an answer. The database write function
    # makes that state unreachable through the API; this keeps the read honest regardless.
    typed_rows = {
        "scenario_decision": scenarios_by_response,
        "transaction_calibration": calibrations_by_response,
    }
    for question_id, response in list(responses_by_question.items()):
        kind = str(response.get("question_type"))
        if (
            kind in typed_rows
            and response.get("status") == "answered"
            and str(response["id"]) not in typed_rows[kind]
        ):
            del responses_by_question[question_id]
    return Bank(
        template=templates[0],
        sections=sections,
        questions_by_section=questions_by_section,
        responses_by_question=responses_by_question,
        scenarios_by_response=scenarios_by_response,
        calibrations_by_response=calibrations_by_response,
    )


def _progress_view(p: prog.Progress) -> ProgressView:
    return ProgressView(
        total=p.total,
        answered=p.answered,
        core_total=p.core_total,
        core_answered=p.core_answered,
        core_complete=p.core_complete and p.core_total > 0,
        complete=p.complete and p.total > 0,
        percent=p.percent,
    )


def _section_progress(bank: Bank, section: dict[str, Any]) -> prog.Progress:
    return prog.section_progress(
        bank.questions_by_section.get(str(section["id"]), []), bank.responses_by_question
    )


def _assignment_progress(bank: Bank) -> prog.Progress:
    total = prog.EMPTY
    for section in bank.sections:
        total = total + _section_progress(bank, section)
    return total


def _calibration_view(row: dict[str, Any]) -> CalibrationAnswerView:
    return CalibrationAnswerView(**{k: row.get(k) for k in CalibrationAnswerView.model_fields})


def _response_view(bank: Bank, response: dict[str, Any]) -> ResponseView:
    scenario = bank.scenarios_by_response.get(str(response["id"]))
    calibration = bank.calibrations_by_response.get(str(response["id"]))
    return ResponseView(
        question_id=str(response["question_id"]),
        status=str(response["status"]),
        answer=response.get("answer"),
        scenario=ScenarioAnswerView(**{k: scenario[k] for k in ScenarioAnswerView.model_fields})
        if scenario
        else None,
        calibration=_calibration_view(calibration) if calibration else None,
        note=response.get("note"),
        question_version=int(response["question_version"]),
        updated_at=response.get("updated_at"),
    )


def _case_view(case: dict[str, Any] | None) -> TransactionCaseView | None:
    if not case:
        return None
    direction = str(case.get("direction"))
    fields = {k: case.get(k) for k in TransactionCaseView.model_fields if k != "categories"}
    fields["related_transactions"] = case.get("related_transactions") or []
    fields["context"] = case.get("context") or {}
    return TransactionCaseView(
        **fields, categories=list(CATEGORIES_BY_DIRECTION.get(direction, ()))
    )


def _question_view(bank: Bank, question: dict[str, Any]) -> QuestionView:
    response = bank.responses_by_question.get(str(question["id"]))
    return QuestionView(
        id=str(question["id"]),
        question_key=str(question["question_key"]),
        question_version=int(question["question_version"]),
        question_text=str(question["question_text"]),
        helper_text=question.get("helper_text"),
        question_type=str(question["question_type"]),
        options=[
            OptionView(value=str(o.get("value")), label=str(o.get("label")))
            for o in (question.get("options") or [])
            if isinstance(o, dict)
        ],
        scenario_brief=question.get("scenario_brief"),
        transaction_case=_case_view(question.get("transaction_case")),
        required_core=bool(question.get("required_core")),
        sensitivity=str(question.get("sensitivity") or "low"),
        order=int(question["order"]),
        response=_response_view(bank, response) if response else None,
    )


def _assignment_view(
    bank: Bank, scope: AssignmentScope, assignment: dict[str, Any]
) -> AssignmentView:
    summaries: list[SectionSummary] = []
    for section in bank.sections:
        p = _section_progress(bank, section)
        summaries.append(
            SectionSummary(
                id=str(section["id"]),
                slug=str(section["slug"]),
                title=str(section["title"]),
                description=section.get("description"),
                is_core=bool(section.get("is_core")),
                estimated_minutes=section.get("estimated_minutes"),
                order=int(section["order"]),
                progress=_progress_view(p),
            )
        )
    current = assignment.get("current_section_id")
    current_slug = (
        next((s.slug for s in summaries if s.id == str(current)), None) if current else None
    )
    # Resume where the person was, unless that section is finished; then the first with work left.
    resume = current_slug
    if (
        resume is None
        or next((s for s in summaries if s.slug == resume), None) is None
        or (next(s for s in summaries if s.slug == resume).progress.complete)
    ):
        resume = next((s.slug for s in summaries if not s.progress.complete), None)
    return AssignmentView(
        assignment_id=scope.assignment_id,
        template_id=scope.template_id,
        template_slug=str(bank.template["slug"]),
        title=str(assignment.get("title_override") or bank.template["title"]),
        subtitle=bank.template.get("subtitle"),
        estimated_minutes=bank.template.get("estimated_minutes"),
        status=str(assignment.get("status") or "not_started"),
        current_section_slug=current_slug,
        resume_section_slug=resume,
        sections=summaries,
        progress=_progress_view(_assignment_progress(bank)),
    )


def _assignment_row(supabase: SupabaseClient, scope: AssignmentScope) -> dict[str, Any]:
    rows = supabase.rest(
        "GET",
        "discovery_assignments",
        params={
            "id": f"eq.{scope.assignment_id}",
            "user_id": f"eq.{scope.user_id}",
            "select": ASSIGNMENT_ROW_FIELDS,
        },
    )
    if not rows:
        raise _not_found()
    return rows[0]


# ---------------------------------------------------------------- routes --
@router.get("/me", response_model=MeView)
def discovery_me(user: CurrentUser, supabase: Supabase) -> MeView:
    """The caller's role and, if they hold an assignment, its overview. A member gets the role
    and null: this route answers everyone signed in, and names no template to anyone without
    one."""
    role = role_of(supabase, user.id)
    profiles = supabase.rest(
        "GET", "profiles", params={"user_id": f"eq.{user.id}", "select": "first_name"}
    )
    first_name = profiles[0].get("first_name") if profiles else None
    # Every assignment the caller holds, newest first: the newest is what the portal opens, so a
    # person moved from one template to another sees the new one without the old one having to
    # be removed; the rest are listed by name so a reviewer holding several templates can choose
    # between their own. Nothing here is anyone else's: the filter is the session's user id.
    rows = supabase.rest(
        "GET",
        "discovery_assignments",
        params={
            "user_id": f"eq.{user.id}",
            "select": "id,user_id,template_id,title_override",
            "order": "created_at.desc",
        },
    )
    if not rows:
        return MeView(role=role, first_name=first_name, assignment=None, assignments=[])
    template_ids = sorted({str(r["template_id"]) for r in rows})
    templates = {
        str(t["id"]): t
        for t in supabase.rest(
            "GET",
            "discovery_templates",
            params={"id": f"in.({','.join(template_ids)})", "select": "id,slug,title"},
        )
    }
    summaries = [
        AssignmentSummary(
            assignment_id=str(r["id"]),
            template_slug=str(templates.get(str(r["template_id"]), {}).get("slug") or ""),
            title=str(
                r.get("title_override")
                or templates.get(str(r["template_id"]), {}).get("title")
                or "Discovery"
            ),
        )
        for r in rows
    ]
    scope = AssignmentScope(
        assignment_id=str(rows[0]["id"]),
        user_id=str(rows[0]["user_id"]),
        template_id=str(rows[0]["template_id"]),
        role=role,
    )
    bank = _read_bank(supabase, scope)
    return MeView(
        role=role,
        first_name=first_name,
        assignment=_assignment_view(bank, scope, _assignment_row(supabase, scope)),
        assignments=summaries,
    )


@router.get("/assignments/{assignment_id}", response_model=AssignmentView)
def assignment_overview(scope: Mine, supabase: Supabase) -> AssignmentView:
    bank = _read_bank(supabase, scope)
    return _assignment_view(bank, scope, _assignment_row(supabase, scope))


def _section_by_slug(bank: Bank, slug: str) -> dict[str, Any]:
    section = next((s for s in bank.sections if s["slug"] == slug), None)
    if section is None:
        raise _not_found()
    return section


def _section_view(bank: Bank, section: dict[str, Any]) -> SectionView:
    index = bank.sections.index(section)
    questions = prog.visible_questions(
        bank.questions_by_section.get(str(section["id"]), []), bank.responses_by_question
    )
    return SectionView(
        id=str(section["id"]),
        slug=str(section["slug"]),
        title=str(section["title"]),
        description=section.get("description"),
        is_core=bool(section.get("is_core")),
        estimated_minutes=section.get("estimated_minutes"),
        order=int(section["order"]),
        position=index + 1,
        section_count=len(bank.sections),
        next_section_slug=(
            str(bank.sections[index + 1]["slug"]) if index + 1 < len(bank.sections) else None
        ),
        previous_section_slug=str(bank.sections[index - 1]["slug"]) if index > 0 else None,
        questions=[_question_view(bank, x) for x in questions],
        progress=_progress_view(_section_progress(bank, section)),
    )


@router.get("/assignments/{assignment_id}/sections/{slug}", response_model=SectionView)
def section_questions(scope: Mine, supabase: Supabase, slug: str) -> SectionView:
    """The section's visible questions, each with the caller's current answer. A question a
    branch condition currently hides is not in the payload at all."""
    bank = _read_bank(supabase, scope)
    return _section_view(bank, _section_by_slug(bank, slug))


@router.get("/assignments/{assignment_id}/responses", response_model=list[ResponseView])
def responses(scope: Mine, supabase: Supabase) -> list[ResponseView]:
    bank = _read_bank(supabase, scope)
    return [_response_view(bank, r) for r in bank.responses_by_question.values()]


def _find_question(bank: Bank, question_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The question and its section, from the bank the caller can see. A question in another
    template, a retired one, or one a branch currently hides is 404 alike."""
    qid = _as_uuid(question_id)
    if qid is None:
        raise _not_found()
    for section in bank.sections:
        shown = prog.visible_questions(
            bank.questions_by_section.get(str(section["id"]), []), bank.responses_by_question
        )
        for question in shown:
            if str(question["id"]) == qid:
                return question, section
    raise _not_found()


def _write_assignment_state(
    supabase: SupabaseClient,
    scope: AssignmentScope,
    assignment: dict[str, Any],
    bank: Bank,
    section_id: str,
) -> str:
    """Refresh the cached status and the resume pointer after a save. The arithmetic in
    progress.py is the authority; this is the cheap copy the listing reads. The milestone
    timestamps are written once, the first time each milestone is reached."""
    p = _assignment_progress(bank)
    status = prog.status_of(p, any_saved=True)
    patch: dict[str, Any] = {
        "current_section_id": section_id,
        "status": status,
        "updated_at": _now(),
    }
    if not assignment.get("started_at"):
        patch["started_at"] = _now()
    if status in ("core_complete", "complete") and not assignment.get("core_completed_at"):
        patch["core_completed_at"] = _now()
    if status == "complete" and not assignment.get("completed_at"):
        patch["completed_at"] = _now()
    supabase.rest(
        "PATCH",
        "discovery_assignments",
        params={"id": f"eq.{scope.assignment_id}", "user_id": f"eq.{scope.user_id}"},
        json_body=patch,
        prefer="return=minimal",
    )
    _count_save(supabase, scope)
    return status


def _count_save(supabase: SupabaseClient, scope: AssignmentScope) -> None:
    """Add one save to the open visit, if there is one. Best effort: the answer is already
    stored by the time this runs, and a visit count is not worth failing a save over."""
    try:
        open_rows = supabase.rest(
            "GET",
            "discovery_sessions",
            params={
                "assignment_id": f"eq.{scope.assignment_id}",
                "user_id": f"eq.{scope.user_id}",
                "ended_at": "is.null",
                "select": "id,saves",
                "order": "started_at.desc",
                "limit": "1",
            },
        )
        if not open_rows:
            return
        supabase.rest(
            "PATCH",
            "discovery_sessions",
            params={"id": f"eq.{open_rows[0]['id']}", "user_id": f"eq.{scope.user_id}"},
            json_body={"saves": int(open_rows[0].get("saves") or 0) + 1, "last_seen_at": _now()},
            prefer="return=minimal",
        )
    except SupabaseError as exc:
        logger.warning(
            "discovery.session_count_failed",
            extra={"assignment_id": scope.assignment_id, "upstream_status": exc.status},
        )


@router.put(
    "/assignments/{assignment_id}/responses/{question_id}",
    response_model=SaveResult,
    dependencies=[Depends(require_client_header)],
)
def save_response(
    scope: Mine, supabase: Supabase, question_id: str, body: SaveRequest
) -> SaveResult:
    """Save one answer. Idempotent: the same answer twice is one row, and history is the
    trigger's business.

    A typed answer (a scenario or a calibration) is a generic row and a typed row, written
    together by one database function so they commit together or not at all. The function takes
    the scope `Mine` already resolved; it decides nothing about ownership, and a calibration's
    direction is read from the case inside it, never from this request.
    """
    bank = _read_bank(supabase, scope)
    question, section = _find_question(bank, question_id)
    try:
        normalised = normalise(question, body.model_dump())
    except AnswerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    note = body.note.strip() if body.note else None
    typed = normalised.typed_row()
    if typed is not None:
        supabase.rpc(TYPED_SAVE, _typed_call(scope, section, question, "answered", note, typed))
    else:
        existing = bank.responses_by_question.get(str(question["id"]))
        if existing is None:
            supabase.rest(
                "POST",
                "discovery_responses",
                json_body={
                    "assignment_id": scope.assignment_id,
                    "user_id": scope.user_id,
                    "template_id": scope.template_id,
                    "section_id": str(section["id"]),
                    "question_id": str(question["id"]),
                    "question_version": int(question["question_version"]),
                    "question_type": str(question["question_type"]),
                    "status": "answered",
                    "answer": normalised.answer,
                    "note": note,
                },
                prefer="return=minimal",
            )
        else:
            supabase.rest(
                "PATCH",
                "discovery_responses",
                params={"id": f"eq.{existing['id']}", "user_id": f"eq.{scope.user_id}"},
                json_body={
                    "status": "answered",
                    "answer": normalised.answer,
                    "note": note,
                    "question_version": int(question["question_version"]),
                },
                prefer="return=minimal",
            )
    bank = _read_bank(supabase, scope)
    status = _write_assignment_state(
        supabase, scope, _assignment_row(supabase, scope), bank, str(section["id"])
    )
    saved = bank.responses_by_question.get(str(question["id"]))
    if saved is None:
        raise HTTPException(status_code=502, detail="the answer was not stored; try again")
    return SaveResult(
        response=_response_view(bank, saved),
        section_progress=_progress_view(_section_progress(bank, section)),
        assignment_progress=_progress_view(_assignment_progress(bank)),
        status=status,
    )


@router.post(
    "/assignments/{assignment_id}/responses/{question_id}/skip",
    response_model=SaveResult,
    dependencies=[Depends(require_client_header)],
)
def skip_response(scope: Mine, supabase: Supabase, question_id: str) -> SaveResult:
    """Explicitly skip an optional question. A core question cannot be skipped, and says so."""
    bank = _read_bank(supabase, scope)
    question, section = _find_question(bank, question_id)
    if question.get("required_core"):
        raise HTTPException(
            status_code=422, detail="this question is part of the core and cannot be skipped"
        )
    if str(question["question_type"]) in TYPED_TYPES:
        # The typed row goes with the same function that wrote it; the trigger keeps what it said.
        supabase.rpc(TYPED_SAVE, _typed_call(scope, section, question, "skipped", None, None))
    else:
        existing = supabase.rest(
            "GET",
            "discovery_responses",
            params={
                "assignment_id": f"eq.{scope.assignment_id}",
                "question_id": f"eq.{question['id']}",
                "select": RESPONSE_FIELDS,
            },
        )
        if existing:
            supabase.rest(
                "PATCH",
                "discovery_responses",
                params={"id": f"eq.{existing[0]['id']}", "user_id": f"eq.{scope.user_id}"},
                json_body={"status": "skipped", "answer": None, "note": None},
                prefer="return=minimal",
            )
        else:
            supabase.rest(
                "POST",
                "discovery_responses",
                json_body={
                    "assignment_id": scope.assignment_id,
                    "user_id": scope.user_id,
                    "template_id": scope.template_id,
                    "section_id": str(section["id"]),
                    "question_id": str(question["id"]),
                    "question_version": int(question["question_version"]),
                    "question_type": str(question["question_type"]),
                    "status": "skipped",
                    "answer": None,
                },
                prefer="return=minimal",
            )
    bank = _read_bank(supabase, scope)
    status = _write_assignment_state(
        supabase, scope, _assignment_row(supabase, scope), bank, str(section["id"])
    )
    saved = bank.responses_by_question[str(question["id"])]
    return SaveResult(
        response=_response_view(bank, saved),
        section_progress=_progress_view(_section_progress(bank, section)),
        assignment_progress=_progress_view(_assignment_progress(bank)),
        status=status,
    )


@router.get(
    "/assignments/{assignment_id}/responses/{question_id}/history", response_model=HistoryView
)
def response_history(scope: Mine, supabase: Supabase, question_id: str) -> HistoryView:
    """The caller's own change history for one question, as the triggers recorded it."""
    qid = _as_uuid(question_id)
    if qid is None:
        raise _not_found()
    generic = supabase.rest(
        "GET",
        "discovery_response_history",
        params={
            "assignment_id": f"eq.{scope.assignment_id}",
            "question_id": f"eq.{qid}",
            "select": "change,valid_from,valid_to,status,answer,question_version",
            "order": "valid_to.desc",
        },
    )
    typed = supabase.rest(
        "GET",
        "discovery_scenario_response_history",
        params={
            "assignment_id": f"eq.{scope.assignment_id}",
            "question_id": f"eq.{qid}",
            "select": "change,valid_from,valid_to," + SCENARIO_FIELDS.replace("response_id,", ""),
            "order": "valid_to.desc",
        },
    )
    calibrated = supabase.rest(
        "GET",
        "discovery_transaction_calibration_response_history",
        params={
            "assignment_id": f"eq.{scope.assignment_id}",
            "question_id": f"eq.{qid}",
            "select": "change,valid_from,valid_to,"
            + CALIBRATION_FIELDS.replace("response_id,", ""),
            "order": "valid_to.desc",
        },
    )
    entries = (
        [
            HistoryEntry(
                change=str(h["change"]),
                valid_from=str(h["valid_from"]),
                valid_to=str(h["valid_to"]),
                status=h.get("status"),
                answer=h.get("answer"),
                question_version=h.get("question_version"),
            )
            for h in generic
        ]
        + [
            HistoryEntry(
                change=str(h["change"]),
                valid_from=str(h["valid_from"]),
                valid_to=str(h["valid_to"]),
                scenario=ScenarioAnswerView(
                    **{k: h.get(k) for k in ScenarioAnswerView.model_fields}
                ),
            )
            for h in typed
        ]
        + [
            HistoryEntry(
                change=str(h["change"]),
                valid_from=str(h["valid_from"]),
                valid_to=str(h["valid_to"]),
                calibration=_calibration_view(h),
            )
            for h in calibrated
        ]
    )
    entries.sort(key=lambda e: e.valid_to, reverse=True)
    return HistoryView(question_id=qid, entries=entries)


def _typed_call(
    scope: AssignmentScope,
    section: dict[str, Any],
    question: dict[str, Any],
    status: str,
    note: str | None,
    typed: dict[str, Any] | None,
) -> dict[str, Any]:
    """The arguments of the one database function that writes a typed answer (E7)."""
    return {
        "p_assignment_id": scope.assignment_id,
        "p_user_id": scope.user_id,
        "p_template_id": scope.template_id,
        "p_section_id": str(section["id"]),
        "p_question_id": str(question["id"]),
        "p_question_version": int(question["question_version"]),
        "p_question_type": str(question["question_type"]),
        "p_status": status,
        "p_note": note,
        "p_typed": typed,
    }


@router.post(
    "/assignments/{assignment_id}/sessions",
    response_model=SessionView,
    dependencies=[Depends(require_client_header)],
)
def touch_session(scope: Mine, supabase: Supabase) -> SessionView:
    """Open a visit, or touch the open one. The browser fires this and forgets it; nothing about
    saving depends on it, so a failure here is a missing timing and not a lost answer."""
    open_rows = supabase.rest(
        "GET",
        "discovery_sessions",
        params={
            "assignment_id": f"eq.{scope.assignment_id}",
            "user_id": f"eq.{scope.user_id}",
            "ended_at": "is.null",
            "select": "id,started_at,saves",
            "order": "started_at.desc",
            "limit": "1",
        },
    )
    if open_rows:
        row = open_rows[0]
        supabase.rest(
            "PATCH",
            "discovery_sessions",
            params={"id": f"eq.{row['id']}"},
            json_body={"last_seen_at": _now()},
            prefer="return=minimal",
        )
        return SessionView(
            session_id=str(row["id"]), started_at=str(row["started_at"]), saves=int(row["saves"])
        )
    created = supabase.rest(
        "POST",
        "discovery_sessions",
        json_body={"assignment_id": scope.assignment_id, "user_id": scope.user_id},
        prefer="return=representation",
    )
    row = created[0]
    return SessionView(
        session_id=str(row["id"]),
        started_at=str(row.get("started_at") or _now()),
        saves=int(row.get("saves") or 0),
    )
