"""Which questions a person can see, which they have answered, and whether the core is done.

Pure functions over plain dicts, in the manner of `app.analysis.metrics`: nothing here reads a
table, so the arithmetic that decides the soft gate is testable with a handful of literals.

The rules, as the schema document states them:

    visible(question)   branch_condition is null, or it holds against an answer in the SAME
                        section. A hidden question is absent from what the API serves.
    answered(question)  a response with status answered, or skipped where the question is not
                        required_core. A skip satisfies an optional question and nothing else.
    core_complete       every visible required_core question is answered.

`discovery_assignments.status` is a cache of these, written after each save. This is the
authority.
"""

from dataclasses import dataclass
from typing import Any

Response = dict[str, Any]
Question = dict[str, Any]


def is_live(question: Question) -> bool:
    return question.get("retired_at") is None


def holds(condition: dict[str, Any] | None, answers_by_key: dict[str, Any]) -> bool:
    """Whether a branch condition is satisfied by the answers given so far in its section."""
    if not condition:
        return True
    key = str(condition.get("question_key", ""))
    if key not in answers_by_key:
        return False
    given = answers_by_key[key]
    if "equals" in condition:
        return given == condition["equals"]
    if "in" in condition:
        options = condition["in"]
        return isinstance(options, list) and given in options
    return False


def visible_questions(
    questions: list[Question], responses_by_question: dict[str, Response]
) -> list[Question]:
    """The live questions of one section that the person should currently be shown, in order.

    Evaluated in order, so a condition may lean on any earlier question in the same section,
    which is the only kind of reference the seed loader admits.
    """
    answers_by_key: dict[str, Any] = {}
    out: list[Question] = []
    for question in sorted((x for x in questions if is_live(x)), key=lambda x: x["order"]):
        if not holds(question.get("branch_condition"), answers_by_key):
            continue
        out.append(question)
        response = responses_by_question.get(str(question["id"]))
        if response is not None and response.get("status") == "answered":
            answers_by_key[str(question["question_key"])] = response.get("answer")
    return out


def is_answered(question: Question, response: Response | None) -> bool:
    if response is None:
        return False
    status = response.get("status")
    if status == "answered":
        return True
    return status == "skipped" and not bool(question.get("required_core"))


@dataclass(frozen=True)
class Progress:
    total: int
    answered: int
    core_total: int
    core_answered: int

    @property
    def core_complete(self) -> bool:
        return self.core_answered >= self.core_total

    @property
    def complete(self) -> bool:
        return self.answered >= self.total

    @property
    def percent(self) -> int:
        return round(100 * self.answered / self.total) if self.total else 0

    def __add__(self, other: "Progress") -> "Progress":
        return Progress(
            self.total + other.total,
            self.answered + other.answered,
            self.core_total + other.core_total,
            self.core_answered + other.core_answered,
        )


EMPTY = Progress(0, 0, 0, 0)


def section_progress(
    questions: list[Question], responses_by_question: dict[str, Response]
) -> Progress:
    """Counts over the questions the person can see, which is the honest denominator."""
    shown = visible_questions(questions, responses_by_question)
    total = len(shown)
    answered = sum(1 for x in shown if is_answered(x, responses_by_question.get(str(x["id"]))))
    core = [x for x in shown if x.get("required_core")]
    core_answered = sum(1 for x in core if is_answered(x, responses_by_question.get(str(x["id"]))))
    return Progress(total, answered, len(core), core_answered)


def status_of(progress: Progress, any_saved: bool) -> str:
    """The cached status the assignment carries, from the arithmetic that is its authority."""
    if progress.complete and progress.total > 0:
        return "complete"
    if progress.core_complete and progress.core_total > 0:
        return "core_complete"
    return "in_progress" if any_saved else "not_started"
