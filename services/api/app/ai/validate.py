"""Four checks, run in order, reported separately.

Structured JSON is a format, not a proof, so "the JSON parsed" is never allowed to stand in for
"this is right". The layers exist because they fail independently:

* **schema** — is this the shape we asked for?
* **evidence** — does every reference exist in what the model was actually given?
* **numbers** — does every figure equal the value it points at, and is every amount in the
  prose one the fact sheet contains?
* **support** — does the sentence say what that fact supports: the right direction, no cause
  invented, a statement balance not described as money in the bank today?

The consequences differ on purpose. A fabricated reference rejects the whole answer, because an
output that invents one reference has not earned trust on its other claims. A wrong number or an
unsupported phrasing withholds that one claim and says so, because the rest of the answer can
still be checked.

None of this makes a claim true. A claim that passes every layer is a claim whose figures match
the owner's own data and whose wording does not overstate what the data shows; whether it is a
useful reading of the business is a separate question, and the interface says so.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.ai.signals import FactSheet, Signal, render_money

MAX_CLAIMS = 8

# Lexicons. Small and explicit, so a failure can be explained to the person it affects.
UP_WORDS = frozenset(
    {"up", "rose", "risen", "increased", "increase", "higher", "grew", "grown", "climbed", "more"}
)
DOWN_WORDS = frozenset(
    {
        "down",
        "fell",
        "fallen",
        "decreased",
        "decrease",
        "lower",
        "dropped",
        "declined",
        "less",
        "reduced",
        "smaller",
    }
)
CAUSAL_PHRASES = (
    "because",
    "due to",
    "caused by",
    "driven by",
    "drove",
    "as a result of",
    "resulted in",
    "led to",
    "owing to",
    "thanks to",
    "contributed to",
    "explains",
    "explained by",
    # Bare "reason" too: the first real brief wrote "is a big reason August ended positive",
    # which a list holding only "the reason" let straight through.
    "reason",
)
RECEIPTS_WORDS = frozenset(
    {
        "sales",
        "revenue",
        "customers",
        "customer",
        "receipts",
        "income",
        "earned",
        "trading",
        "turnover",
    }
)
FINANCING_WORDS = frozenset(
    {
        "loan",
        "loans",
        "financing",
        "finance",
        "credit",
        "borrowing",
        "borrowed",
        "borrow",
        "advance",
        "lender",
        "facility",
        "repayment",
        "drawdown",
    }
)
LIVE_BALANCE_PHRASES = (
    "in the bank now",
    "in the bank today",
    "currently in the bank",
    "today's balance",
    "right now",
    "available now",
    "you have now",
)
COMPARISON_PHRASES = ("compared with", "compared to", "versus", " vs ", "against last")
# Which way the money went, for facts that carry a direction. Phrases rather than single words,
# because "paid" alone appears on both sides of a payment.
INFLOW_PHRASES = (
    "paid in",
    "came in",
    "arrived",
    "received",
    "landed",
    "was credited",
    "deposited",
    "lifted",
)
OUTFLOW_PHRASES = ("paid out", "went out", "was spent", "repaid", "was debited", "left the")
ESTIMATE_WORDS = frozenset(
    {"expected", "estimated", "estimate", "projected", "projection", "around", "about", "roughly"}
)

_MONEY = re.compile(r"[-−]?\$\s?\d[\d,]*(?:\.\d{1,2})?")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_WORDS = re.compile(r"[a-z']+")

PERCENT_TOLERANCE = 0.5


# ----------------------------------------------------------------- layer one --
class Figure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(min_length=1, max_length=80)
    amount_minor: int


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str = Field(min_length=1, max_length=60)
    text: str = Field(min_length=1, max_length=240)
    figures: list[Figure] = Field(default_factory=list, max_length=4)
    evidence: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("evidence", mode="after")
    @classmethod
    def _bare_transaction_ids(cls, value: list[str]) -> list[str]:
        """Accept a transaction cited either bare or with its `tx:` prefix.

        A fact sheet lists a signal's evidence as bare ids, but the conversation panel hands
        the model whole transactions whose own id is written `tx:<uuid>` — so the natural thing
        to cite is the spelling it was shown, and rejecting that is the contract disagreeing
        with itself rather than the answer being wrong. The same reasoning as `field_of`
        accepting both spellings of a figure reference. Normalised once here, so validation and
        the evidence link the interface opens are always the same id.
        """
        return [item.removeprefix("tx:") for item in value]


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str = Field(min_length=1, max_length=60)
    text: str = Field(min_length=1, max_length=160)


class BriefOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str = Field(min_length=1, max_length=160)
    claims: list[Claim] = Field(min_length=1, max_length=MAX_CLAIMS)
    questions: list[Question] = Field(default_factory=list, max_length=4)
    caveats: list[str] = Field(default_factory=list, max_length=3)


# --------------------------------------------------------------- the verdict --
@dataclass
class Layer:
    ok: bool = True
    messages: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.ok = False
        self.messages.append(message)

    def record(self) -> dict[str, Any]:
        return {"ok": self.ok, "messages": list(self.messages)}


@dataclass
class Validation:
    schema: Layer = field(default_factory=Layer)
    evidence: Layer = field(default_factory=Layer)
    numbers: Layer = field(default_factory=Layer)
    support: Layer = field(default_factory=Layer)
    claims_withheld: int = 0
    questions_dropped: int = 0
    caveats_dropped: int = 0
    withheld_reasons: list[str] = field(default_factory=list)
    coverage_gaps: list[str] = field(default_factory=list)
    headline_replaced: bool = False
    rejected: bool = False

    @property
    def problems(self) -> list[str]:
        """What a repair attempt is told, and nothing else."""
        return self.schema.messages + self.evidence.messages

    def record(self) -> dict[str, Any]:
        return {
            "schema": self.schema.record(),
            "evidence": self.evidence.record(),
            "numbers": self.numbers.record(),
            "support": self.support.record(),
            "claims_withheld": self.claims_withheld,
            "questions_dropped": self.questions_dropped,
            "caveats_dropped": self.caveats_dropped,
            "withheld_reasons": list(self.withheld_reasons),
            "coverage_gaps": list(self.coverage_gaps),
            "headline_replaced": self.headline_replaced,
            "rejected": self.rejected,
        }


class DisplayFigure(BaseModel):
    ref: str
    amount_minor: int
    rendered: str


class DisplayClaim(BaseModel):
    signal_id: str
    signal_label: str
    text: str
    figures: list[DisplayFigure]
    evidence: list[str]
    evidence_total: int
    evidence_truncated: bool


class ValidatedBrief(BaseModel):
    headline: str
    claims: list[DisplayClaim]
    questions: list[Question]
    caveats: list[str]
    claims_withheld: int
    withheld_reasons: list[str]


def _words(text: str) -> set[str]:
    return set(_WORDS.findall(text.lower()))


def _normalise_money(text: str) -> str:
    return text.replace(",", "").replace(" ", "").replace("−", "-")


def _money_in(text: str) -> list[str]:
    return _MONEY.findall(text)


def field_of(ref: str, signal_id: str) -> str | None:
    """The value name a figure reference points at, or None if it is not this fact's at all.

    Both spellings are accepted: `chg:suppliers.change_minor` and the longer
    `chg:suppliers.values.change_minor`. The second is not a looser reference — it is the
    literal path into the object the model was handed, since every value sits under `values`
    in the fact sheet — and it resolves to exactly the same field and the same equality check.
    The first live call quoted the longer form for every figure it cited, which says the
    ambiguity was in the contract rather than in the answer.
    """
    if not ref.startswith(signal_id + "."):
        return None
    name = ref[len(signal_id) + 1 :]
    if name.startswith("values."):
        name = name[len("values.") :]
    return name or None


def resolve_figure(ref: str, sheet: FactSheet, signal_id: str) -> tuple[Signal, str] | None:
    """The fact and field a figure reference points at, anywhere in the sheet.

    The claim's own fact is tried first, so the ordinary case is unchanged. What is new is that
    a figure may cite a DIFFERENT fact, provided the reference names it in full.

    This is not a loosening. Three things decide whether a number is grounded: the reference
    must name a fact that exists, the field must exist on it, and the integer the claim carries
    must equal that field exactly. All three still hold — `_check_numbers` compares against
    whichever fact the reference resolves to, not against a laxer rule.

    What it removes is an inconsistency. The prose check already accepts any amount from any
    fact in the sheet, so a sentence could always say "$2,500 against $3,000 of starting cash";
    only the structured `figures` array was confined to one fact, which meant the number the
    interface links to could not be cited even though the same number was allowed in the
    sentence beside it. Both live failures on this contract were exactly that: a claim about
    one fact quoting a real, correct figure belonging to another. Widening the array makes the
    citation MORE traceable, because the figure list is what the interface renders.
    """
    own = field_of(ref, signal_id)
    known = sheet.by_id()
    if own is not None and signal_id in known and own in known[signal_id].values:
        return known[signal_id], own
    for other_id, signal in known.items():
        name = field_of(ref, other_id)
        if name is not None and name in signal.values:
            return signal, name
    return None


def _fallback_headline(sheet: FactSheet) -> str:
    """A headline written by code, used when the model's own quotes a figure we cannot match."""
    for signal in sheet.signals:
        if signal.kind == "balance":
            live = "Your bank balance" if signal.is_live else "Your latest statement balance"
            return f"{live} is {signal.rendered['amount']} as of {sheet.as_of}."
    return f"Your cash position as of {sheet.as_of}."


def _check_evidence(output: BriefOutput, sheet: FactSheet, layer: Layer) -> None:
    known = sheet.by_id()
    for index, claim in enumerate(output.claims, start=1):
        signal = known.get(claim.signal_id)
        if signal is None:
            layer.fail(
                f"claim {index} cites '{claim.signal_id}', which is not a fact you were given. "
                f"The ids you may cite are: {', '.join(sorted(known))}."
            )
            continue
        for figure in claim.figures:
            if resolve_figure(figure.ref, sheet, claim.signal_id) is None:
                own = ", ".join(sorted(signal.values)) or "no values"
                layer.fail(
                    f"claim {index} quotes '{figure.ref}', which is not a value of any fact "
                    f"you were given. Write '<fact id>.<field>'. {claim.signal_id} carries: "
                    f"{own}."
                )
        allowed_ids = set(signal.evidence)
        for reference in claim.evidence:
            if reference not in allowed_ids:
                layer.fail(
                    f"claim {index} attaches a transaction that is not evidence for "
                    f"{claim.signal_id}"
                )
    for index, question in enumerate(output.questions, start=1):
        if question.signal_id not in known:
            layer.fail(
                f"question {index} cites '{question.signal_id}', which is not a fact you were "
                f"given. The ids you may cite are: {', '.join(sorted(known))}."
            )


def _check_numbers(claim: Claim, signal: Signal, sheet: FactSheet) -> str | None:
    """Every figure exact, every amount in the prose one the fact sheet contains."""
    for figure in claim.figures:
        found = resolve_figure(figure.ref, sheet, claim.signal_id)
        # The equality is exact and is made against the fact the reference names, which may be
        # a different one from the claim's own. Grounding is the reference resolving and the
        # integer matching; which fact it belongs to does not change either.
        actual = found[0].values.get(found[1]) if found else None
        if actual != figure.amount_minor:
            label = (found[0] if found else signal).label.lower()
            return f"a figure did not match your own {label}"
    allowed = {_normalise_money(a) for a in sheet.allowed_numbers()}
    for found in _money_in(claim.text):
        if _normalise_money(found) not in allowed:
            return "an amount in the sentence is not one of your figures"
    # Magnitudes, not signed values, exactly as money is compared. The pattern above never
    # captures a leading minus, so "receipts fall by 15%" and "a change of -15%" both arrive
    # here as 15 while the fact holds -15 — and every sentence quoting the owner's own typed
    # percentage back to them was rejected as a number that is not one of their figures. What
    # stops a percentage being described the wrong way round is the direction check, not the
    # sign of the digits, which is the same reasoning `_money_forms` already records.
    percentages = [abs(p) for p in sheet.allowed_percentages()]
    for found in _PERCENT.findall(claim.text):
        value = abs(float(found))
        if not any(abs(value - allowed_pct) <= PERCENT_TOLERANCE for allowed_pct in percentages):
            return "a percentage in the sentence is not one of your figures"
    return None


def _reads_as_machinery(text: str, sheet: FactSheet) -> bool:
    """Whether a displayed string carries a fact id from this sheet."""
    return any(known_id in text for known_id in sheet.by_id())


def _check_readable(claim: Claim, sheet: FactSheet) -> str | None:
    """Is the sentence written for the owner, or leaking the machinery it was built from?

    The first real brief passed every other check and was still unfit to show: it wrote
    "money in month:2026-08.money_in_minor 4697408 ($46,974.08)" — the fact id, the raw minor
    units and the rendered amount all in one sentence. Every figure was correct. None of it was
    readable. The references belong in the `figures` array, which the interface renders
    separately; the prose is for a person.

    Both checks are exact rather than heuristic: a known fact id appearing verbatim, and the
    integer minor-unit value of a figure this claim itself cites. Neither can fire on ordinary
    writing about money.

    The minor-unit check has to match on a whole number, not a substring, and this was wrong
    for a long time. A plain `in` test asks whether the digits appear anywhere at all, so a
    figure of $5.00 (500) fired on the perfectly readable "$5,500.00", and a figure of exactly
    zero fired on almost every sentence ever written, because "0" is inside "$0.00", "30 days"
    and "2026". A scenario whose ending cash did not move has difference figures of zero, so
    the check rejected four of five claims in the one case the feature exists for — and it read
    like the model failing rather than the validator misfiring.

    Zero is skipped outright: there is no distinctive raw form of it, so "0" in prose is not
    machinery. Everything else must appear with no digit, comma or currency symbol against it
    on either side, which is what tells "4056159 ($40,561.59)" — the real leak this was written
    for — apart from a rendered amount that merely contains the same digits.
    """
    for known_id in sheet.by_id():
        if known_id in claim.text:
            return "the sentence contains an internal reference instead of plain words"
    for figure in claim.figures:
        found = resolve_figure(figure.ref, sheet, claim.signal_id)
        # Money only. This check is about minor units leaking into prose, and a value that is
        # not money has no minor-unit form to leak: `delay_days` of 18 is eighteen days, and
        # "arrives 18 days late" is the correct sentence, not a raw integer. Testing every
        # figure withheld exactly that claim on the delayed-payment case — the one fact the
        # owner actually changed.
        if found is None or found[1] not in found[0].money_fields:
            continue
        raw = abs(figure.amount_minor)
        if raw and _quotes_raw_number(claim.text, raw):
            return "the sentence quotes a raw internal number instead of a dollar amount"
    return None


def _quotes_raw_number(text: str, value: int) -> bool:
    """Does this integer stand alone in the sentence, rather than sit inside a written amount?

    Bounded on both sides by "not part of a number": a digit, a comma, a full stop or a
    currency symbol beside it means the digits belong to something else that is being read
    normally, such as $5,500.00 or a date.
    """
    return re.search(rf"(?<![\d.,$]){re.escape(str(value))}(?![\d.,])", text) is not None


def _check_support(claim: Claim, signal: Signal) -> str | None:
    """Does the sentence say what this fact supports? Deterministic checks only."""
    text = claim.text.lower()
    words = _words(claim.text)

    if signal.direction_word:
        if "-$" in claim.text or "\u2212$" in claim.text:
            return "a change is described with a direction word and a negative sign at once"
        up, down = bool(words & UP_WORDS), bool(words & DOWN_WORDS)
        if signal.direction_word == "up" and (down or not up):
            return "the sentence describes the change in the wrong direction"
        if signal.direction_word == "down" and (up or not down):
            return "the sentence describes the change in the wrong direction"
        if signal.direction_word == "flat" and (up or down):
            return "the sentence describes a change where the figures show none"

    if not signal.id.startswith("data:") and not signal.causal_allowed:
        for phrase in CAUSAL_PHRASES:
            if phrase in text:
                return "the sentence gives a reason your bank data cannot show"

    if signal.kind == "financing":
        if words & RECEIPTS_WORDS:
            return "borrowing is described as trading income"
        if not (words & FINANCING_WORDS):
            return "a financing entry is not described as borrowing"
        # Borrowing and repaying are both financing and both sound alike in a sentence, so the
        # vocabulary check alone lets "a loan of $2,500 was paid in" stand for money going out.
        # The direction is on the fact; the sentence has to agree with it.
        direction = signal.values.get("direction")
        if direction == "outflow" and any(phrase in text for phrase in INFLOW_PHRASES):
            return "money going out is described as money coming in"
        if direction == "inflow" and any(phrase in text for phrase in OUTFLOW_PHRASES):
            return "money coming in is described as money going out"

    if signal.kind == "balance" and signal.is_live is False:
        for phrase in LIVE_BALANCE_PHRASES:
            if phrase in text:
                return "a statement closing balance is described as money in the bank today"

    if signal.complete is False:
        for phrase in COMPARISON_PHRASES:
            if phrase in text:
                return "an unfinished month is compared with a full one"

    if signal.estimated and not (words & ESTIMATE_WORDS):
        return "an estimate is stated as though it were certain"

    return None


def _check_coverage(kept: list[Claim], sheet: FactSheet) -> list[str]:
    """Facts a brief was expected to address. A gap is recorded, never hidden, never fatal."""
    bound = {claim.signal_id for claim in kept}
    gaps = []
    for signal in sheet.signals:
        required = (
            signal.required
            or signal.kind in ("financing", "data_change")
            or signal.id == "fc:baseline"
        )
        if required and signal.id not in bound:
            gaps.append(signal.id)
    return gaps


def validate(
    text: str, sheet: FactSheet, output_model: type[BriefOutput] = BriefOutput
) -> tuple[Validation, ValidatedBrief | None]:
    """Run the four layers in order and return what, if anything, may be shown.

    `output_model` exists so a capability can widen the shape without a second validator. The
    conversation panel answers in sentences rather than in a headline, and may legitimately
    have nothing to claim; every layer after the schema is identical, which is the point.
    """
    result = Validation()

    try:
        parsed = json.loads(text)
    except ValueError:
        result.schema.fail("the answer was not valid JSON")
        result.rejected = True
        return result, None
    try:
        output = output_model.model_validate(parsed)
    except ValidationError as exc:
        for error in exc.errors()[:6]:
            location = ".".join(str(part) for part in error["loc"])
            result.schema.fail(f"{location}: {error['msg']}")
        result.rejected = True
        return result, None

    _check_evidence(output, sheet, result.evidence)
    if not result.evidence.ok:
        result.rejected = True
        return result, None

    known = sheet.by_id()
    kept: list[Claim] = []
    for claim in output.claims:
        signal = known[claim.signal_id]
        problem = _check_numbers(claim, signal, sheet)
        if problem:
            result.numbers.fail(problem)
            result.claims_withheld += 1
            result.withheld_reasons.append(problem)
            continue
        problem = _check_readable(claim, sheet) or _check_support(claim, signal)
        if problem:
            result.support.fail(problem)
            result.claims_withheld += 1
            result.withheld_reasons.append(problem)
            continue
        kept.append(claim)

    headline = output.headline
    allowed = {_normalise_money(a) for a in sheet.allowed_numbers()}
    if any(_normalise_money(found) not in allowed for found in _money_in(headline)):
        headline = _fallback_headline(sheet)
        result.headline_replaced = True
        result.numbers.fail("the headline quoted an amount that is not one of your figures")

    # A question on a fact that takes none is dropped, not fatal. The fact is real and was
    # given; only its flag says a question would not be answerable from it. That is a different
    # thing from inventing a reference, which is what whole-output rejection is reserved for.
    askable = [
        q
        for q in output.questions
        if known[q.signal_id].question_allowed and not _reads_as_machinery(q.text, sheet)
    ]
    result.questions_dropped = len(output.questions) - len(askable)
    # Caveats are displayed to the owner as well, and were the strings most likely to be
    # written in schema terms — their whole job is managing trust, so machinery there costs
    # the most. A caveat that reads like a debug line is dropped rather than shown.
    readable_caveats = [c for c in output.caveats if not _reads_as_machinery(c, sheet)]
    result.caveats_dropped = len(output.caveats) - len(readable_caveats)

    result.coverage_gaps = _check_coverage(kept, sheet)

    brief = ValidatedBrief(
        headline=headline,
        claims=[
            DisplayClaim(
                signal_id=claim.signal_id,
                signal_label=known[claim.signal_id].label,
                text=claim.text,
                figures=[
                    DisplayFigure(
                        ref=figure.ref,
                        amount_minor=figure.amount_minor,
                        rendered=_rendered_for(sheet, claim.signal_id, figure.ref),
                    )
                    for figure in claim.figures
                ],
                evidence=list(claim.evidence),
                evidence_total=known[claim.signal_id].evidence_total,
                evidence_truncated=known[claim.signal_id].evidence_truncated,
            )
            for claim in kept
        ],
        questions=askable,
        caveats=readable_caveats,
        claims_withheld=result.claims_withheld,
        withheld_reasons=sorted(set(result.withheld_reasons)),
    )
    return result, brief


def _rendered_for(sheet: FactSheet, signal_id: str, ref: str) -> str:
    found = resolve_figure(ref, sheet, signal_id)
    value = found[0].values.get(found[1]) if found else None
    return render_money(value) if isinstance(value, int) else str(value)
