"""The Discovery admin surface: the session question, and the read-only analysis views.

Router-level dependencies are the deployment guards only. `require_role("admin")`, as the
`DiscoveryAdmin` dependency, is a **per-route** guard that every privileged operation names in
its own signature. It is never a router-level dependency, because `/session` answers a boolean
to any signed-in caller so the interface knows whether to draw a link, and a router-level guard
would refuse the one caller that route exists for. Hiding a link is never the control; the
guard on each privileged route is.

This is the shape `services/api/app/admin.py` already uses: its router carries the deployment
guards, `admin_session()` takes `CurrentUser` and answers, and `ai_usage()` takes `AdminUser`.
Reading a handler's signature tells you whether it is guarded. The test suite derives every
discovery admin operation from `app.openapi()` and asserts that exactly one, this session
route, is unguarded by role.

`/session` still sits behind `require_discovery` and `refuse_on_public_demo`, so a deployment
without the portal answers 404 to everyone, admin included. Unguarded means unguarded by role,
not by deployment.

The analysis routes are GET only. There is no route here that writes a response, and none that
takes a respondent, assignment or user id from the caller: the dataset is every assignment on a
real template, read with the service role, and nothing the client sends narrows or widens it.
Stakeholder answers are read here and nowhere else outside the stakeholder's own routes.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.auth import refuse_on_public_demo, require_client_header
from app.discovery import analysis as an
from app.discovery import taxonomy as tx
from app.discovery.auth import CurrentRole, DiscoveryAdmin, Supabase, require_discovery
from app.discovery.routes import (
    CALIBRATION_FIELDS,
    QUESTION_FIELDS,
    RESPONSE_FIELDS,
    SCENARIO_FIELDS,
)
from app.supabase import SupabaseClient

router = APIRouter(
    prefix="/api/admin/discovery",
    tags=["discovery-admin"],
    dependencies=[
        Depends(require_client_header),
        Depends(refuse_on_public_demo),
        Depends(require_discovery),
    ],
)

# The case columns the analysis reads: the stimulus plus the authored relationships that the
# respondent path deliberately never selects.
ADMIN_CASE_FIELDS = (
    "question_id,direction,amount_minor,descriptor,business_vertical,business_description,"
    "account_type,merchant,counterparty,payment_rail,posted_date,day_of_week,time_of_day,memo,"
    "frequency_30d,frequency_90d,average_amount_minor,amount_min_minor,amount_max_minor,"
    "recurrence_pattern,related_transactions,context,case_family,variant,changed_feature,"
    "baseline_question_id,authored_changed_feature,reveal_of_question_id,reveal"
)
REAL_TEMPLATE_PATTERN = "ilike.discovery-*"


@router.get("/session")
def discovery_session(role: CurrentRole) -> dict[str, bool]:
    """Whether this signed-in caller may read the discovery admin views.

    Never 403: it is the question, not a protected answer. 401 without a session, because an
    anonymous caller has no role to report. The role comes from `public.profiles`, not from the
    `ADMIN_USER_IDS` allowlist that gates the AI usage dashboard; the two are separate until a
    later phase converges them deliberately.
    """
    return {"is_discovery_admin": role == "admin"}


# ----------------------------------------------------------------------------- loader --
def _in(ids: list[str]) -> str:
    return f"in.({','.join(ids)})"


def load_dataset(supabase: SupabaseClient, caller_id: str) -> an.Dataset:
    """Everything the analysis reads, in eleven queries, none keyed by anything the client sent.

    Respondents are every assignment on a real (`discovery-*`) template except the caller's own
    and any held by an admin profile. A person holding more than one real template is a
    reviewer and is listed as such; the comparisons use the stakeholder on each template.
    """
    ds = an.Dataset()
    templates = supabase.rest(
        "GET",
        "discovery_templates",
        params={"slug": REAL_TEMPLATE_PATTERN, "select": "id,slug,title"},
    )
    ds.templates = {str(t["slug"]): t for t in templates}
    if not templates:
        return ds
    template_ids = [str(t["id"]) for t in templates]
    slug_of = {str(t["id"]): str(t["slug"]) for t in templates}
    links = supabase.rest(
        "GET",
        "discovery_template_sections",
        params={"template_id": _in(template_ids), "select": "template_id,section_id,order"},
    )
    for link in sorted(links, key=lambda x: int(x["order"])):
        ds.template_sections.setdefault(slug_of[str(link["template_id"])], []).append(
            str(link["section_id"])
        )
    section_ids = sorted({str(link["section_id"]) for link in links})
    if section_ids:
        for s in supabase.rest(
            "GET",
            "discovery_sections",
            params={
                "id": _in(section_ids),
                "select": "id,slug,title,description,is_core,estimated_minutes",
            },
        ):
            ds.sections[str(s["id"])] = {
                **s,
                "id": str(s["id"]),
                "template_slugs": [
                    slug for slug, ids in ds.template_sections.items() if str(s["id"]) in ids
                ],
            }
        for q in supabase.rest(
            "GET",
            "discovery_questions",
            params={"section_id": _in(section_ids), "select": QUESTION_FIELDS},
        ):
            section = ds.sections.get(str(q["section_id"]))
            if section is None:
                continue
            ds.questions[str(q["id"])] = {
                **q,
                "id": str(q["id"]),
                "section_id": str(q["section_id"]),
                "section_slug": section["slug"],
                "meta": tx.meta_for(str(q["question_key"])),
            }
    calibration_ids = [
        qid
        for qid, q in ds.questions.items()
        if q.get("question_type") == "transaction_calibration"
    ]
    if calibration_ids:
        for c in supabase.rest(
            "GET",
            "discovery_transaction_cases",
            params={"question_id": _in(calibration_ids), "select": ADMIN_CASE_FIELDS},
        ):
            q = ds.questions.get(str(c["question_id"]))
            if q is not None:
                q["transaction_case"] = {
                    **c,
                    "baseline_question_id": str(c["baseline_question_id"])
                    if c.get("baseline_question_id")
                    else None,
                    "reveal_of_question_id": str(c["reveal_of_question_id"])
                    if c.get("reveal_of_question_id")
                    else None,
                }
    assignments = supabase.rest(
        "GET",
        "discovery_assignments",
        params={
            "template_id": _in(template_ids),
            "select": (
                "id,user_id,template_id,status,started_at,core_completed_at,completed_at,created_at"
            ),
        },
    )
    user_ids = sorted({str(a["user_id"]) for a in assignments})
    profiles = (
        {
            str(p["user_id"]): p
            for p in supabase.rest(
                "GET",
                "profiles",
                params={"user_id": _in(user_ids), "select": "user_id,role,first_name,display_name"},
            )
        }
        if user_ids
        else {}
    )
    held: dict[str, set[str]] = {}
    for a in assignments:
        held.setdefault(str(a["user_id"]), set()).add(slug_of[str(a["template_id"])])
    respondents: list[an.Respondent] = []
    for a in sorted(assignments, key=lambda x: str(x.get("created_at") or ""), reverse=True):
        uid = str(a["user_id"])
        profile = profiles.get(uid) or {}
        if uid == caller_id or profile.get("role") == "admin":
            continue
        slug = slug_of[str(a["template_id"])]
        respondents.append(
            an.Respondent(
                user_id=uid,
                assignment_id=str(a["id"]),
                template_slug=slug,
                name=str(
                    profile.get("display_name")
                    or profile.get("first_name")
                    or an.LABEL_BY_TEMPLATE.get(slug, slug)
                ),
                label=an.LABEL_BY_TEMPLATE.get(slug, slug),
                kind="reviewer" if len(held[uid]) > 1 else "stakeholder",
                status=str(a.get("status") or "not_started"),
                created_at=a.get("created_at"),
            )
        )
    # Stakeholders first, then reviewers, each in template order (Lead, Partner, Engineer). Within
    # one template the newest assignment comes first, and it is the one the comparisons use:
    # `respondent_for` takes the first stakeholder on a template.
    order = {"discovery-lead": 0, "discovery-partner": 1, "discovery-engineer": 2}
    respondents.sort(
        key=lambda r: (r.kind != "stakeholder", order.get(r.template_slug, 9), r.template_slug)
    )
    ds.respondents = respondents
    assignment_ids = [r.assignment_id for r in respondents]
    if not assignment_ids:
        return ds
    responses = supabase.rest(
        "GET",
        "discovery_responses",
        params={"assignment_id": _in(assignment_ids), "select": RESPONSE_FIELDS + ",created_at"},
    )
    by_response_id: dict[str, an.Row] = {}
    for r in responses:
        row = {**r, "id": str(r["id"])}
        ds.responses[(str(r["assignment_id"]), str(r["question_id"]))] = row
        by_response_id[row["id"]] = row
    for s in supabase.rest(
        "GET",
        "discovery_scenario_responses",
        params={"assignment_id": _in(assignment_ids), "select": SCENARIO_FIELDS},
    ):
        row = by_response_id.get(str(s["response_id"]))
        if row is not None:
            row["scenario"] = {k: v for k, v in s.items() if k != "response_id"}
    for c in supabase.rest(
        "GET",
        "discovery_transaction_calibration_responses",
        params={"assignment_id": _in(assignment_ids), "select": CALIBRATION_FIELDS},
    ):
        row = by_response_id.get(str(c["response_id"]))
        if row is not None:
            row["calibration"] = {k: v for k, v in c.items() if k != "response_id"}
    # A typed answer without its typed row is not an answer, as on the respondent path.
    for key, row in list(ds.responses.items()):
        kind = row.get("question_type")
        if row.get("status") == "answered" and (
            (kind == "scenario_decision" and "scenario" not in row)
            or (kind == "transaction_calibration" and "calibration" not in row)
        ):
            del ds.responses[key]
    for s in supabase.rest(
        "GET",
        "discovery_sessions",
        params={
            "assignment_id": _in(assignment_ids),
            "select": "id,assignment_id,started_at,last_seen_at,ended_at,saves",
        },
    ):
        ds.sessions.setdefault(str(s["assignment_id"]), []).append(s)
    return ds


# ------------------------------------------------------------------------------ views --
@router.get("/respondents")
def respondents(admin: DiscoveryAdmin, supabase: Supabase) -> dict[str, Any]:
    ds = load_dataset(supabase, admin)
    return {
        "respondents": [an.respondent_card(ds, r) for r in ds.respondents],
        "templates": sorted(ds.templates),
    }


@router.get("/overview")
def overview(admin: DiscoveryAdmin, supabase: Supabase) -> dict[str, Any]:
    return an.overview(load_dataset(supabase, admin))


@router.get("/dataset")
def dataset(admin: DiscoveryAdmin, supabase: Supabase) -> dict[str, Any]:
    """Every question of every real template with every respondent's current answer: the read
    the search box, the filters and the detail drawer work from, and the JSON export."""
    ds = load_dataset(supabase, admin)
    questions = [
        an.question_view(ds, q)
        for q in sorted(ds.questions.values(), key=lambda x: (x["section_slug"], int(x["order"])))
    ]
    answers: dict[str, dict[str, Any]] = {}
    for r in ds.respondents:
        answers[r.assignment_id] = {
            qid: an.response_view(ds.questions[qid], row)
            for qid, row in ds.responses_of(r).items()
            if qid in ds.questions
        }
    return {
        # The engagement view is the respondent card plus topic breakdown: what a respondent
        # header shows, in one read.
        "respondents": an.engagement(ds),
        "sections": [
            {
                "id": s["id"],
                "slug": s["slug"],
                "title": s["title"],
                "is_core": s["is_core"],
                "group": tx.group_of(s["slug"]),
                "level": tx.level_of(s["slug"]),
                "owner": tx.owner_of(s["slug"]),
                "templates": s["template_slugs"],
                # Where the section sits in each template, so a respondent's questions can be
                # listed in the order they were asked.
                "positions": {
                    slug: ids.index(s["id"])
                    for slug, ids in ds.template_sections.items()
                    if s["id"] in ids
                },
            }
            for s in ds.sections.values()
        ],
        "questions": questions,
        "answers": answers,
    }


@router.get("/history/{assignment_id}/{question_id}")
def answer_history(
    admin: DiscoveryAdmin, supabase: Supabase, assignment_id: str, question_id: str
) -> dict[str, Any]:
    """Every earlier value of one answer, as the triggers recorded it, newest first.

    The assignment must be one of the dataset's respondents (a real template, not the caller's
    own, not an admin's): anything else is 404, so the route cannot be used to read history the
    dashboard itself would not show. Plain answers come from the generic history table; scenario
    and calibration answers from their typed history tables, which carry the whole row.
    """
    ds = load_dataset(supabase, admin)
    respondent = next((r for r in ds.respondents if r.assignment_id == assignment_id), None)
    question = ds.questions.get(question_id)
    if respondent is None or question is None:
        raise HTTPException(status_code=404, detail="not found")
    kind = question.get("question_type")
    if kind == "scenario_decision":
        table, fields = "discovery_scenario_response_history", SCENARIO_FIELDS
    elif kind == "transaction_calibration":
        table, fields = "discovery_transaction_calibration_response_history", CALIBRATION_FIELDS
    else:
        table, fields = "discovery_response_history", "status,answer,question_version"
    rows = supabase.rest(
        "GET",
        table,
        params={
            "assignment_id": f"eq.{assignment_id}",
            "question_id": f"eq.{question_id}",
            "select": "change,valid_from,valid_to," + fields.replace("response_id,", ""),
            "order": "valid_to.desc",
        },
    )
    entries = []
    for h in rows:
        if kind == "scenario_decision":
            shaped = {"status": "answered", "scenario": h}
        elif kind == "transaction_calibration":
            shaped = {"status": "answered", "calibration": h}
        else:
            shaped = {"status": h.get("status"), "answer": h.get("answer")}
        entries.append(
            {
                "change": h.get("change"),
                "valid_from": h.get("valid_from"),
                "valid_to": h.get("valid_to"),
                "display": an.display(question, shaped),
                "status": shaped.get("status"),
                "answer": shaped.get("answer"),
                "scenario": {
                    k: v for k, v in h.items() if k not in ("change", "valid_from", "valid_to")
                }
                if kind == "scenario_decision"
                else None,
                "calibration": {
                    k: v for k, v in h.items() if k not in ("change", "valid_from", "valid_to")
                }
                if kind == "transaction_calibration"
                else None,
            }
        )
    current = ds.response(respondent, question_id)
    return {
        "assignment_id": assignment_id,
        "question_id": question_id,
        "respondent": respondent.label,
        "current": an.response_view(question, current),
        "entries": entries,
    }


@router.get("/comparisons")
def comparisons(admin: DiscoveryAdmin, supabase: Supabase) -> dict[str, Any]:
    ds = load_dataset(supabase, admin)
    _, _, labels = an.founder_pair(ds)
    rows = an.founder_rows(ds)
    return {
        "labels": list(labels),
        "assignments": an.founder_assignments(ds),
        "summary": an.founder_summary(rows, labels),
        "rows": rows,
        "tolerance": an.TOLERANCE,
    }


@router.get("/business")
def business(admin: DiscoveryAdmin, supabase: Supabase) -> dict[str, Any]:
    ds = load_dataset(supabase, admin)
    return an.business_view(ds, an.founder_rows(ds))


@router.get("/underwriting")
def underwriting(admin: DiscoveryAdmin, supabase: Supabase) -> dict[str, Any]:
    ds = load_dataset(supabase, admin)
    return an.underwriting_view(
        ds, an.founder_rows(ds), an.calibration_rows(ds), an.scenario_rows(ds)
    )


@router.get("/calibration")
def calibration(admin: DiscoveryAdmin, supabase: Supabase) -> dict[str, Any]:
    ds = load_dataset(supabase, admin)
    _, _, labels = an.founder_pair(ds)
    rows = an.calibration_rows(ds)
    return {
        "labels": list(labels),
        "assignments": an.founder_assignments(ds),
        "summary": an.calibration_summary(rows, labels),
        "rows": rows,
        "controlled_pairs": an.controlled_pairs(ds, rows),
        "reveals": an.reveals(ds, rows),
    }


@router.get("/scenarios")
def scenarios(admin: DiscoveryAdmin, supabase: Supabase) -> dict[str, Any]:
    ds = load_dataset(supabase, admin)
    _, _, labels = an.founder_pair(ds)
    rows = an.scenario_rows(ds)
    return {
        "labels": list(labels),
        "assignments": an.founder_assignments(ds),
        "summary": an.scenario_summary(rows, labels),
        "rows": rows,
    }


@router.get("/engineering")
def engineering(admin: DiscoveryAdmin, supabase: Supabase) -> dict[str, Any]:
    return an.engineering_view(load_dataset(supabase, admin))


@router.get("/engagement")
def engagement(admin: DiscoveryAdmin, supabase: Supabase) -> dict[str, Any]:
    return {"respondents": an.engagement(load_dataset(supabase, admin))}


@router.get("/open-questions")
def open_questions(admin: DiscoveryAdmin, supabase: Supabase) -> dict[str, Any]:
    ds = load_dataset(supabase, admin)
    rows, cal, scen = an.founder_rows(ds), an.calibration_rows(ds), an.scenario_rows(ds)
    queue = an.open_questions(ds, rows, cal, scen, an.engineering_view(ds))
    return {"count": len(queue), "items": queue}


EXPORTS = ("founder-comparison", "calibration", "scenarios", "progress")


@router.get("/export/{name}", response_class=PlainTextResponse)
def export_csv(admin: DiscoveryAdmin, supabase: Supabase, name: str) -> PlainTextResponse:
    """A CSV of one comparison table. Answers only; no email, id or token of anyone."""
    if name not in EXPORTS:
        raise HTTPException(status_code=404, detail="not found")
    ds = load_dataset(supabase, admin)
    if name == "founder-comparison":
        text = an.founder_csv(an.founder_rows(ds))
    elif name == "calibration":
        text = an.calibration_csv(an.calibration_rows(ds))
    elif name == "scenarios":
        text = an.scenario_csv(an.scenario_rows(ds))
    else:
        text = an.progress_csv([an.respondent_card(ds, r) for r in ds.respondents])
    return PlainTextResponse(
        text,
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="discovery-{name}.csv"'},
    )
