"""What counts as an answer to each kind of question.

Strict on purpose. The database's check constraint proves the JSON shape (a string for a text
question, a number for a currency one); this layer proves the content: an option that exists,
an integer where money is expected, a decision the enum knows, a confidence in range. Anything
else is 422 before a row is touched, so the table never holds an answer the question cannot
mean.

Money is integer minor units, and a float is refused rather than rounded: 12.5 is not a number
of cents, and the one place the rule was relaxed would be the one place it was broken.
"""

import math
from dataclasses import dataclass
from typing import Any

from app.ai.prompts import CASHFLOW_INFLOW_CATEGORIES, CASHFLOW_OUTFLOW_CATEGORIES

DECISIONS = frozenset({"approve", "deny", "manual_review"})
CONFIDENCE_MIN, CONFIDENCE_MAX = 1, 5
SCALE_MIN, SCALE_MAX = 0, 10
TEXT_MAX = {"short_text": 500, "long_text": 10_000}
NOTE_MAX = 2000
REASON_CODES_MAX = 20
MONEY_ABS_MAX = 10_000_000_000_000  # matches the import row bound elsewhere in this API
NUMBER_ABS_MAX = 1e15  # a plain number answer; nothing a questionnaire asks is larger

# The two question types whose detail lives in a typed table beside the generic row.
TYPED_TYPES = frozenset({"scenario_decision", "transaction_calibration"})

# Transaction calibration vocabularies. The categories are the classifier's own eleven buckets,
# by direction, imported rather than copied: a calibration label is the string the classifier
# emits, so no comparison ever goes through a mapping table. The rest are Discovery's own.
CATEGORIES_BY_DIRECTION: dict[str, tuple[str, ...]] = {
    "inflow": tuple(CASHFLOW_INFLOW_CATEGORIES),
    "outflow": tuple(CASHFLOW_OUTFLOW_CATEGORIES),
}
MATERIALITIES = ("immaterial", "low", "medium", "high", "critical")
SIGNALS = ("positive", "neutral", "negative", "unknown")
IMPACTS = (
    "none",
    "contributes_positive",
    "contributes_negative",
    "manual_review",
    "potential_hard_decline",
)
HANDLINGS = ("auto_classify", "ai_review", "human_review", "ask_borrower")


class AnswerError(ValueError):
    """The answer does not fit the question. The message is safe to show to the person."""


@dataclass(frozen=True)
class Scenario:
    decision: str
    confidence: int
    reason_codes: list[str]
    maximum_exposure_minor: int | None
    additional_information_needed: str | None
    what_would_change_my_decision: str | None
    notes: str | None

    def row(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "confidence": self.confidence,
            "reason_codes": self.reason_codes,
            "maximum_exposure_minor": self.maximum_exposure_minor,
            "additional_information_needed": self.additional_information_needed,
            "what_would_change_my_decision": self.what_would_change_my_decision,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class Calibration:
    """One reading of one transaction: what it is, how sure, how much it matters, which way.

    `direction` is deliberately absent. It is copied from the case by the database write
    function, never taken from a caller; here it is only used to decide which categories are
    legal, and it comes from the question's own case row.
    """

    classification_category: str
    classification_confidence: int
    underwriting_materiality: str
    underwriting_signal: str
    decision_impact: str | None
    reason_codes: list[str]
    needs_more_context: bool
    context_needed: str | None
    reasoning: str | None
    handling_recommendation: str | None

    def row(self) -> dict[str, Any]:
        return {
            "classification_category": self.classification_category,
            "classification_confidence": self.classification_confidence,
            "underwriting_materiality": self.underwriting_materiality,
            "underwriting_signal": self.underwriting_signal,
            "decision_impact": self.decision_impact,
            "reason_codes": self.reason_codes,
            "needs_more_context": self.needs_more_context,
            "context_needed": self.context_needed,
            "reasoning": self.reasoning,
            "handling_recommendation": self.handling_recommendation,
        }


@dataclass(frozen=True)
class Normalised:
    """What gets stored: the JSON `answer` for the generic row, and the typed detail if any."""

    answer: Any
    scenario: Scenario | None = None
    calibration: Calibration | None = None

    def typed_row(self) -> dict[str, Any] | None:
        """The typed detail as the database write function takes it, or None for a plain answer."""
        if self.scenario is not None:
            return self.scenario.row()
        if self.calibration is not None:
            return self.calibration.row()
        return None


def option_values(question: dict[str, Any]) -> list[str]:
    return [str(o.get("value")) for o in (question.get("options") or []) if isinstance(o, dict)]


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    """A finite JSON number: an int or a float that is neither NaN nor infinite, never a bool."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _text(value: Any, limit: int, what: str, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise AnswerError(f"{what} needs some text")
        return None
    if not isinstance(value, str):
        raise AnswerError(f"{what} must be text")
    text = value.strip()
    if not text:
        if required:
            raise AnswerError(f"{what} needs some text")
        return None
    if len(text) > limit:
        raise AnswerError(f"{what} is longer than {limit} characters")
    return text


def _minor(value: Any, what: str) -> int:
    if not _is_int(value):
        raise AnswerError(f"{what} must be a whole number of minor units")
    if abs(value) > MONEY_ABS_MAX:
        raise AnswerError(f"{what} is out of range")
    return value


def normalise(question: dict[str, Any], body: dict[str, Any]) -> Normalised:
    """The stored form of `body` for `question`, or AnswerError.

    `body` is the request: `{"answer": ...}` for every type but `scenario_decision`, which
    carries `{"scenario": {...}}` instead and stores a null generic answer.
    """
    kind = str(question.get("question_type"))
    if kind == "scenario_decision":
        return Normalised(answer=None, scenario=_scenario(question, body.get("scenario")))
    if kind == "transaction_calibration":
        return Normalised(answer=None, calibration=_calibration(question, body.get("calibration")))
    if "answer" not in body:
        raise AnswerError("an answer is required")
    value = body["answer"]
    if kind in TEXT_MAX:
        return Normalised(answer=_text(value, TEXT_MAX[kind], "the answer", required=True))
    if kind == "boolean":
        if not isinstance(value, bool):
            raise AnswerError("the answer must be yes or no")
        return Normalised(answer=value)
    if kind == "single_select":
        allowed = option_values(question)
        if not isinstance(value, str) or value not in allowed:
            raise AnswerError("the answer must be one of the offered options")
        return Normalised(answer=value)
    if kind == "multi_select":
        allowed = option_values(question)
        if not isinstance(value, list) or not value:
            raise AnswerError("choose at least one option")
        chosen = [str(v) for v in value]
        if len(set(chosen)) != len(chosen) or any(v not in allowed for v in chosen):
            raise AnswerError("every choice must be one of the offered options, once")
        return Normalised(answer=chosen)
    if kind == "ranking":
        # A ranking orders the options that apply, most important first, and leaves the rest
        # out: the bank's ranking questions say "rank the ones that are genuinely constraints",
        # and forcing every option into the order would manufacture an answer nobody gave.
        allowed = option_values(question)
        if not isinstance(value, list) or not value:
            raise AnswerError("rank at least one option")
        chosen = [str(v) for v in value]
        if len(set(chosen)) != len(chosen) or any(v not in allowed for v in chosen):
            raise AnswerError("a ranking orders offered options, each at most once")
        return Normalised(answer=chosen)
    if kind == "currency":
        return Normalised(answer=_minor(value, "the amount"))
    if kind == "number":
        # JSON parsing admits NaN and Infinity, which jsonb does not; refuse them here rather
        # than as a 502 from the database, and bound the magnitude as money is bounded.
        if not _is_number(value) or abs(value) > NUMBER_ABS_MAX:
            raise AnswerError("the answer must be a number")
        return Normalised(answer=value)
    if kind == "percentage":
        if not _is_number(value) or not 0 <= value <= 100:
            raise AnswerError("the answer must be a percentage from 0 to 100")
        return Normalised(answer=value)
    if kind == "scale":
        if not _is_int(value) or not SCALE_MIN <= value <= SCALE_MAX:
            raise AnswerError(f"the answer must be a whole number from {SCALE_MIN} to {SCALE_MAX}")
        return Normalised(answer=value)
    raise AnswerError(f"unknown question type {kind!r}")


def _scenario(question: dict[str, Any], raw: Any) -> Scenario:
    if not isinstance(raw, dict):
        raise AnswerError("a scenario answer needs a decision, a confidence and reason codes")
    decision = raw.get("decision")
    if decision not in DECISIONS:
        raise AnswerError("the decision must be approve, deny or manual_review")
    confidence = raw.get("confidence")
    if not _is_int(confidence) or not CONFIDENCE_MIN <= confidence <= CONFIDENCE_MAX:
        raise AnswerError(
            f"confidence must be a whole number from {CONFIDENCE_MIN} to {CONFIDENCE_MAX}"
        )
    chosen = _reason_codes(question, raw.get("reason_codes", []))
    exposure = raw.get("maximum_exposure_minor")
    return Scenario(
        decision=str(decision),
        confidence=confidence,
        reason_codes=chosen,
        maximum_exposure_minor=(
            None if exposure is None else _minor(exposure, "the maximum exposure")
        ),
        additional_information_needed=_text(
            raw.get("additional_information_needed"),
            NOTE_MAX,
            "the information needed",
            required=False,
        ),
        what_would_change_my_decision=_text(
            raw.get("what_would_change_my_decision"),
            NOTE_MAX,
            "what would change the decision",
            required=False,
        ),
        notes=_text(raw.get("notes"), NOTE_MAX, "the notes", required=False),
    )


def _reason_codes(question: dict[str, Any], codes: Any) -> list[str]:
    """Reason codes as stored: each one the question offers, each at most once, at most 20."""
    allowed = option_values(question)
    if not isinstance(codes, list):
        raise AnswerError("reason codes must be a list")
    chosen = [str(c) for c in codes]
    if len(chosen) > REASON_CODES_MAX:
        raise AnswerError(f"at most {REASON_CODES_MAX} reason codes")
    if len(set(chosen)) != len(chosen) or any(c not in allowed for c in chosen):
        raise AnswerError("every reason code must be one this question offers, once")
    return chosen


def _one_of(value: Any, allowed: tuple[str, ...], what: str, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise AnswerError(f"{what} is required")
        return None
    if not isinstance(value, str) or value not in allowed:
        raise AnswerError(f"{what} must be one of {', '.join(allowed)}")
    return value


def _calibration(question: dict[str, Any], raw: Any) -> Calibration:
    """A transaction calibration answer against its question's own case.

    Four fields are required: the category, the confidence, the materiality and the signal. The
    category must be one of the eleven and legal for the case's direction, which is read from the
    question's case row here and copied onto the stored answer by the database, so a caller cannot
    send a direction of its own. Everything else is optional and bounded.
    """
    if not isinstance(raw, dict):
        raise AnswerError(
            "a calibration answer needs a category, a confidence, a materiality and a signal"
        )
    case = question.get("transaction_case")
    if not isinstance(case, dict) or case.get("direction") not in CATEGORIES_BY_DIRECTION:
        raise AnswerError("this question has no transaction case to calibrate against")
    direction = str(case["direction"])
    category = raw.get("classification_category")
    legal = CATEGORIES_BY_DIRECTION[direction]
    if not isinstance(category, str) or category not in legal:
        raise AnswerError(
            f"the category must be one of the {direction} buckets: {', '.join(legal)}"
        )
    confidence = raw.get("classification_confidence")
    if not _is_int(confidence) or not CONFIDENCE_MIN <= confidence <= CONFIDENCE_MAX:
        raise AnswerError(
            f"confidence must be a whole number from {CONFIDENCE_MIN} to {CONFIDENCE_MAX}"
        )
    needs_more = raw.get("needs_more_context", False)
    if not isinstance(needs_more, bool):
        raise AnswerError("needs_more_context must be true or false")
    context_needed = _text(
        raw.get("context_needed"), NOTE_MAX, "the context needed", required=False
    )
    if context_needed is not None and not needs_more:
        raise AnswerError("naming the context needed means needs_more_context must be true")
    return Calibration(
        classification_category=category,
        classification_confidence=confidence,
        underwriting_materiality=str(
            _one_of(
                raw.get("underwriting_materiality"), MATERIALITIES, "materiality", required=True
            )
        ),
        underwriting_signal=str(
            _one_of(raw.get("underwriting_signal"), SIGNALS, "the signal", required=True)
        ),
        decision_impact=_one_of(
            raw.get("decision_impact"), IMPACTS, "the decision impact", required=False
        ),
        reason_codes=_reason_codes(question, raw.get("reason_codes", [])),
        needs_more_context=needs_more,
        context_needed=context_needed,
        reasoning=_text(raw.get("reasoning"), NOTE_MAX, "the reasoning", required=False),
        handling_recommendation=_one_of(
            raw.get("handling_recommendation"), HANDLINGS, "the handling", required=False
        ),
    )
