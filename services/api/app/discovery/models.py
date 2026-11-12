"""The shapes the stakeholder routes speak. Nothing here reads a table."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProgressView(BaseModel):
    total: int
    answered: int
    core_total: int
    core_answered: int
    core_complete: bool
    complete: bool
    percent: int


class SectionSummary(BaseModel):
    id: str
    slug: str
    title: str
    description: str | None
    is_core: bool
    estimated_minutes: int | None
    order: int
    progress: ProgressView


class AssignmentView(BaseModel):
    assignment_id: str
    template_id: str
    template_slug: str
    title: str
    subtitle: str | None
    estimated_minutes: int | None
    status: str
    current_section_slug: str | None
    # Where Resume should land: the current section, else the first with something left.
    resume_section_slug: str | None
    sections: list[SectionSummary]
    progress: ProgressView


class AssignmentSummary(BaseModel):
    """One of the caller's own assignments, by name. Only ever the caller's: the list is read
    for the session's user id and nobody else's, so a reviewer holding several templates can
    choose between them and a stakeholder holding one sees one."""

    assignment_id: str
    template_slug: str
    title: str


class MeView(BaseModel):
    """What the portal shell and the header need. A member gets a role and nothing else."""

    role: str
    first_name: str | None
    # The newest assignment, fully read; what the portal opens by default.
    assignment: AssignmentView | None
    # Every assignment the caller holds, newest first, the one above included.
    assignments: list[AssignmentSummary] = []


class OptionView(BaseModel):
    value: str
    label: str


class ScenarioAnswerView(BaseModel):
    decision: str
    confidence: int
    reason_codes: list[str]
    maximum_exposure_minor: int | None
    additional_information_needed: str | None
    what_would_change_my_decision: str | None
    notes: str | None


class CalibrationAnswerView(BaseModel):
    direction: str
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


class TransactionCaseView(BaseModel):
    """The synthetic transaction a calibration question asks about, as the respondent sees it.

    Deliberately without `case_family`, `variant` or `changed_feature`: the comparison between
    variants happens in analysis, not in the respondent's head, so nothing served here lets two
    cases be recognised as versions of each other.
    """

    direction: str
    amount_minor: int
    descriptor: str
    # Which of the eleven buckets are legal for this direction, from the classifier's own list.
    categories: list[str]
    business_vertical: str | None = None
    business_description: str | None = None
    account_type: str | None = None
    merchant: str | None = None
    counterparty: str | None = None
    payment_rail: str | None = None
    posted_date: str | None = None
    day_of_week: int | None = None
    time_of_day: str | None = None
    memo: str | None = None
    frequency_30d: int | None = None
    frequency_90d: int | None = None
    average_amount_minor: int | None = None
    amount_min_minor: int | None = None
    amount_max_minor: int | None = None
    recurrence_pattern: str | None = None
    related_transactions: list[Any] = []
    context: dict[str, Any] = {}


class ResponseView(BaseModel):
    question_id: str
    status: str  # answered | skipped
    answer: Any = None
    scenario: ScenarioAnswerView | None = None
    calibration: CalibrationAnswerView | None = None
    note: str | None = None
    question_version: int
    updated_at: str | None = None


class QuestionView(BaseModel):
    id: str
    question_key: str
    question_version: int
    question_text: str
    helper_text: str | None
    question_type: str
    options: list[OptionView]
    scenario_brief: dict[str, Any] | None
    # The synthetic transaction, for a transaction_calibration question only.
    transaction_case: TransactionCaseView | None = None
    required_core: bool
    sensitivity: str
    order: int
    # The caller's current answer, hydrated server-side so a resume shows what was saved.
    response: ResponseView | None


class SectionView(BaseModel):
    id: str
    slug: str
    title: str
    description: str | None
    is_core: bool
    estimated_minutes: int | None
    order: int
    position: int  # 1-based, of section_count
    section_count: int
    next_section_slug: str | None
    previous_section_slug: str | None
    questions: list[QuestionView]
    progress: ProgressView


class ScenarioAnswer(BaseModel):
    # A field this model does not name is refused, not ignored: a `user_id` or an unknown
    # column in a save body is a mistake or an attempt, and either deserves a 422. Strict, so
    # "3" is not a confidence and "10" is not an amount: the JSON type has to be the right one.
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: str
    confidence: int
    reason_codes: list[str] = []
    maximum_exposure_minor: int | None = None
    additional_information_needed: str | None = None
    what_would_change_my_decision: str | None = None
    notes: str | None = None


class CalibrationAnswer(BaseModel):
    """A transaction calibration answer as the browser sends it. No `direction`: the database
    copies that from the case, so a caller cannot choose which categories are legal."""

    model_config = ConfigDict(extra="forbid", strict=True)

    classification_category: str
    classification_confidence: int
    underwriting_materiality: str
    underwriting_signal: str
    decision_impact: str | None = None
    reason_codes: list[str] = []
    needs_more_context: bool = False
    context_needed: str | None = None
    reasoning: str | None = None
    handling_recommendation: str | None = None


class SaveRequest(BaseModel):
    """One answer. `answer` for every plain type; `scenario` for scenario_decision;
    `calibration` for transaction_calibration."""

    model_config = ConfigDict(extra="forbid", strict=True)

    answer: Any = None
    scenario: ScenarioAnswer | None = None
    calibration: CalibrationAnswer | None = None
    note: str | None = Field(default=None, max_length=2000)


class SaveResult(BaseModel):
    response: ResponseView
    section_progress: ProgressView
    assignment_progress: ProgressView
    status: str


class HistoryEntry(BaseModel):
    change: str
    valid_from: str
    valid_to: str
    status: str | None = None
    answer: Any = None
    scenario: ScenarioAnswerView | None = None
    calibration: CalibrationAnswerView | None = None
    question_version: int | None = None


class HistoryView(BaseModel):
    question_id: str
    entries: list[HistoryEntry]


class SessionView(BaseModel):
    session_id: str
    started_at: str
    saves: int
