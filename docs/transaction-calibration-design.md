# Discovery: transaction calibration

Status: **revision 1, approved; phase 3.5A implemented on 2026-09-20.** The data model, the
one-call typed write (E7), the loader rules, a six-case synthetic proof fixture and the
respondent's card exist and are verified; see the
[phase 3.5A handoff](discovery-phase3-5-handoff.md), which also records where the
approved decisions amended this document (`underwriting_signal` is required). Sections 7–11
(consensus, prediction comparison, rule candidates, metrics) and the real bank remain design.
Phase 3's verdict is [ready for phase 4](discovery-phase3-security-gate.md); this
document inserted **phase 3.5** in front of it so the bank phase 4 seeds includes transaction
calibration from the start rather than acquiring it later.

Companion: [schema proposal](transaction-calibration-schema-proposal.md), which holds
the DDL, the migration ordering and the loader rules.

Existing design: [ADR 0014](decisions/0014-discovery-portal.md),
[architecture](discovery-architecture.md), [schema](discovery-schema.md),
[implementation plan](discovery-implementation-plan.md), and the three handoffs
([1](discovery-phase1-handoff.md), [2](discovery-phase2-handoff.md),
[3](discovery-phase3-security-gate.md)).

---

## 1. Problem definition

CashLens sorts every row of an uploaded statement into one of eleven cash-flow buckets
(`services/api/app/ai/prompts.py`, migration `0024`). It is useful and it is roughly right. What
separates roughly right from decision-grade is a set of edge cases that a model cannot resolve
from the row alone, because resolving them requires knowing how an experienced lender reads the
row:

- an Uber inflow for a restaurant is delivery revenue; an Uber outflow for the same restaurant on
  a Saturday night is probably operating, and on a Tuesday morning probably personal;
- a Safeway outflow for a restaurant may be supplies, and for a consultancy is personal;
- a recurring Zelle to the same counterparty twice a month is payroll in everything but name;
- a transfer to a counterparty sharing the applicant's surname is a family transfer, not payroll,
  and the difference changes the cash-flow picture;
- a same-amount credit two days after a debit is a reversal, whether or not the descriptor says
  so;
- an NSF fee is immaterial as money and highly material as a signal.

Lead used exactly these rows as calibration exercises in the discovery conversations. The judgement he
was applying is not written down anywhere, and today it exists only in two people's heads.

**What this capability does:** it presents one transaction (or one transaction pattern) at a
time, synthetic, and asks two separate questions about it.

1. **Classification.** What is this, in the same eleven-bucket vocabulary the classifier uses?
2. **Underwriting significance.** Do we care about it when deciding whether to lend, how much,
   and in which direction?

**What it produces:** structured, queryable, attributable labels that can later inform
deterministic rules, weighted scoring, counterparty memory, AI context, human-review priorities,
offline evaluation and eventual supervised learning.

**What it does not produce:** rules. Nothing a stakeholder answers here becomes production
behaviour without a human promotion step and its own decision record. That boundary is section 10
and it is the most important sentence in this document.

---

## 2. Transaction calibration is not an underwriting scenario

The portal already has `scenario_decision`: a synthetic borrower, a brief, and the question
"approve, deny or manually review?". That stays exactly as it is. The two layers ask different
questions of different objects, and conflating them would destroy both.

| | Transaction calibration | Underwriting scenario (`scenario_decision`) |
|---|---|---|
| Object | One transaction, or one pattern of transactions | One borrower, one application |
| Question | "What is this line, and how much does it matter?" | "Would you make this loan?" |
| Output | A category, a confidence, a materiality, a signal | approve / deny / manual_review |
| Vocabulary | The eleven cash-flow buckets, shared with the classifier | `underwriting_decision` enum |
| Volume | Tens of cases per founder; each 20–40 seconds | A handful per founder; each several minutes |
| Feeds | The classifier, routing thresholds, counterparty memory | Underwriting policy, the scenario comparison views |
| Controlled variants | Central: the whole point is which feature flips the label | Not applicable; each scenario is whole |

Two consequences follow, and the design holds both:

- **Do not widen `scenario_decision`.** Adding `classification_category` and `materiality` to the
  scenario response would mean every borrower scenario carries seven null columns and every
  calibration case carries an `approve/deny/manual_review` that means nothing. The check
  constraints that make each shape honest could not survive it.
- **Do not express calibration as a scenario with a small brief.** The brief is display-only
  jsonb. Calibration's stimulus has to be *queried* — by merchant, by vertical, by amount band,
  by case family — because the analysis in section 7 is arithmetic over those fields.

---

## 3. Recommended response schema

One typed row per (assignment, calibration question), alongside the generic
`discovery_responses` parent, in exactly the shape `discovery_scenario_responses` already uses.
Field list, and why each one exists. Anything not named here was considered and rejected; the
rejections are at the end of this section, because "we thought about it and said no" is worth
recording.

| Field | Required | Type | Why it exists |
|---|---|---|---|
| `classification_category` | **yes** | text, one of the eleven, valid for the case's direction | The label. Everything downstream is a comparison against it. |
| `classification_confidence` | **yes** | smallint 1–5 | A 2 and a 5 are not the same evidence. Gates label candidacy (section 7) and is the only way to measure whether human confidence is itself calibrated. |
| `underwriting_materiality` | **yes** | enum: immaterial, low, medium, high, critical | Separates "what is it" from "does it matter". An NSF fee is $35 and critical; a $900 supplier payment is routine. Weights accuracy (section 11) and orders human review. |
| `underwriting_signal` | optional | enum: positive, neutral, negative, unknown | The direction of the effect on the lending decision. Needed because materiality alone is unsigned: "high materiality" does not say whether it helps or hurts. |
| `decision_impact` | optional | enum: none, contributes_positive, contributes_negative, manual_review, potential_hard_decline | What it would actually *do*. This is where hard declines are discovered: if either founder marks a case `potential_hard_decline`, that is a finding regardless of agreement elsewhere. |
| `reason_codes` | optional | text[], ≤ 20, each ∈ the question's options | Why, in a form a query can group by. The feature names that recur here are the candidate inputs to a rule. |
| `needs_more_context` | optional, default false | boolean | Expert-level ambiguity, which is different from system-level ambiguity. A case both founders mark this way can never be classified by any model from the row alone, and belongs in "ask the borrower", not in the training set. |
| `context_needed` | optional | text ≤ 2000 | Names the missing data. This is a direct product backlog: what CashLens should fetch, derive or ask. |
| `reasoning` | optional | text ≤ 2000 | The sentence that becomes a rule, a prompt example, or the resolution of a disagreement. The single most useful field for the human reading the results, and useless to a query, which is why it is text and not codes. |
| `handling_recommendation` | optional | enum: auto_classify, ai_review, human_review, ask_borrower | Evidence for routing thresholds: at what confidence and materiality should the system decide for itself? The only source for a human-recommended review rate and AI escalation rate. |
| `direction` | derived, written by the API | text: inflow, outflow | Copied from the case at write time so "the category is valid for this direction" is a **row-level check constraint** rather than a matter of the API's care. Bound back to the case by a composite key, so the copy cannot be spoofed. Exactly the trick `discovery_responses.question_type` already uses. |

**On the three routing booleans.** The brief proposed `would_auto_classify`,
`would_send_to_ai`, `would_send_to_human_review`. Recommendation: **one nullable enum instead**,
`handling_recommendation`. Three booleans admit eight states, of which several are incoherent
(auto-classify *and* send to human), and they cost three interactions per case across sixty
cases and two people. The single enum answers the actual question — what should the system do
with a line like this — in one interaction. The nuance the booleans could express and the enum
cannot ("auto-classify but sample for audit") is a policy decision about sampling rates, not a
judgement only Lead and Partner can supply.

The more valuable routing artefact needs no field at all: a proposed policy can be **derived**
from category × confidence × materiality × agreement state across the whole set. Asking directly
is a cross-check on the derived policy, which is why the field is optional and worth asking on
perhaps a third of cases rather than all of them.

**Required versus optional matters here.** Three required fields (category, confidence,
materiality) keep a case at roughly twenty seconds. Everything else is progressive: the interface
reveals signal, impact, reason codes and notes when the respondent signals the case is
interesting (materiality above `low`, or `needs_more_context`). Sixty cases × two founders is the
budget; every mandatory field spends about two minutes of it. Required-ness inside the answer is
enforced by `answers.py`, not by `discovery_questions.required_core`, which governs whether the
*question* may be skipped.

**Fields considered and rejected**

| Rejected | Why |
|---|---|
| Separate `would_auto_classify` / `would_send_to_ai` / `would_send_to_human_review` | Contradictory states; three interactions for one decision. Replaced by `handling_recommendation`. |
| `elapsed_ms` per case | A weak proxy for difficulty, it reads as surveillance, and it needs client plumbing that can be wrong. Revisit only if confidence turns out to be badly calibrated and we need a second difficulty signal. |
| A probability (0–100) instead of 1–5 confidence | People are unreliable at stating probabilities and reliable at a five-point scale. The existing scenario answer already uses 1–5; two confidence scales in one portal would be a translation problem forever. |
| Confidence on materiality as well as on classification | Doubles the interactions for a second-order quantity. Disagreement on materiality is already visible without it. |
| A "suggested rule" free-text field | Invites writing policy inside a questionnaire. Rule drafting is the promotion step in section 10, done by a human looking at the whole set, not case by case. |
| `approve/deny/manual_review` on a transaction case | That is the scenario layer. Keeping them apart is the point of section 2. |
| Per-case monetary threshold ("at what amount would this change?") | Attractive, and it is what the controlled variants measure empirically without asking. Ask the variants, not the respondent. |

---

## 4. Recommended case (stimulus) schema

One row per calibration question, keyed by `question_id`, in its own table. It is not
`scenario_brief`: that column is display-only jsonb and the check constraint in `0025` restricts
it to `scenario_decision`, correctly.

Typed columns are for everything the analysis groups by or filters on; jsonb is for everything
that is only ever rendered.

**Identity and controlled-variant metadata** (section 6 explains the use)

| Field | Type | Note |
|---|---|---|
| `case_family` | text, required, indexed | `uber-restaurant`, `zelle-payroll`, `grocery-vertical` |
| `variant` | text, required, unique within the family | `baseline`, `saturday-night`, `amount-145` |
| `changed_feature` | text, nullable, from a fixed list | Null marks the family's baseline. Names the *one* column that differs from the baseline. |
| `synthetic` | boolean, not null, default true | The loader refuses anything else (section 15). |

**The business context**

| Field | Type | Note |
|---|---|---|
| `business_vertical` | text | `restaurant`, `consulting`, `retail`, `trucking`. Metrics are reported per vertical; this must be a column. |
| `business_description` | text | One line of prose the respondent reads. |
| `account_type` | text | `checking`, `savings`. Changes what a transfer means. |

**The transaction**

| Field | Type | Note |
|---|---|---|
| `direction` | text: inflow, outflow | Deterministic from the amount sign, as in `0024`. Never a judgement. |
| `amount_minor` | bigint, required | Signed integer minor units. Money rule, no exceptions. |
| `descriptor` | text, required | The raw bank string, as it would appear. |
| `merchant` | text | Normalised where a normalisation exists. Metrics are reported per merchant family. |
| `counterparty` | text | For transfers and Zelle: the name on the other side. |
| `payment_rail` | text | `ach`, `card`, `zelle`, `wire`, `check`, `internal`. |
| `posted_date` | date | Synthetic. |
| `day_of_week` | smallint 0–6 | Stored, not derived, so a case can vary the day without moving the date. |
| `time_of_day` | time | Nullable; only some rails carry it. |
| `memo` | text | Where the rail has one. |

**The pattern** — the part a single row cannot carry, and where most of the interesting judgement
lives

| Field | Type | Note |
|---|---|---|
| `frequency_30d`, `frequency_90d` | integer | How often a line like this appears. |
| `average_amount_minor` | bigint | Across those occurrences. |
| `amount_min_minor`, `amount_max_minor` | bigint | The spread; a fixed amount and a variable one mean different things. |
| `recurrence_pattern` | text | `none`, `weekly`, `biweekly`, `semimonthly`, `monthly`, `irregular`. Semimonthly on a person's name is payroll far more often than not. |
| `related_transactions` | jsonb | A short list of rows shown alongside (the debit a credit reverses, the three prior Zelles). Display-only; no query groups by it. |
| `context` | jsonb | Anything else display-only: relationship hints such as "counterparty shares the applicant's surname", prior-period notes. |

Not every case populates every field, and the interface renders only what is present. A case is
allowed to be a single line with nothing else; that minimalism is itself a variable worth testing.

---

## 5. Category model

**Use the classifier's own vocabulary, unchanged.** It is defined today in
`services/api/app/ai/prompts.py` as two tuples of display strings:

```
INFLOW    Organic revenue · Internal transfers · Loans income · Personal income ·
          Overdraft/Reversals · Unclassified
OUTFLOW   Operating expense · Transfer out · Loan payments · Personal expense ·
          Payroll · Unclassified
```

Eleven distinct values, `Unclassified` shared by both directions. Direction is arithmetic, decided
from the amount before anyone sees the row, and a respondent classifies within the six categories
valid for their case's direction.

Three decisions follow.

1. **One vocabulary, not two.** The calibration label must be the same string the classifier
   emits, or every comparison in sections 9 and 11 becomes a mapping table that will drift. The
   calibration constraint and `prompts.py` must be the same list, which is testable: a real-database
   check reads the constraint definition and asserts it equals the Python tuples. Drift fails a
   test rather than shipping.
2. **Text with a check constraint, not a Postgres enum.** The values contain spaces and a slash,
   they belong to the classifier rather than to Discovery, and a check constraint can be widened
   in one statement whereas an enum value cannot be added and used in the same migration
   (section 14 of the schema proposal). The database gains the validation it does not currently
   have: `import_rows.cashflow` is unvalidated jsonb today.
3. **`Unclassified` is a real answer, and it is ambiguous.** Combined with `needs_more_context` it
   distinguishes two very different findings: "the system cannot tell but an expert can" (a
   training opportunity) from "an expert cannot tell either" (a question for the borrower, and a
   case that must be excluded from any accuracy denominator).

---

## 6. Controlled variants

The purpose is not to collect labels. It is to find out **which feature changes the label**.

A family is a baseline case plus variants, each differing from the baseline in exactly one named
feature:

```
case_family: uber-restaurant
  baseline        changed_feature: null    restaurant, Uber, $18, Saturday 22:40
  tuesday-morning changed_feature: day_of_week      … Tuesday 09:15
  amount-145      changed_feature: amount_minor     … $145, Saturday 22:40
  vertical-consult changed_feature: business_vertical  consulting, $18, Saturday 22:40
```

`changed_feature` is drawn from a fixed list of the case columns that are worth varying:
`amount_minor`, `day_of_week`, `time_of_day`, `business_vertical`, `merchant`, `counterparty`,
`payment_rail`, `recurrence_pattern`, `frequency_30d`, `descriptor`, `account_type`,
`related_transactions`, `direction`.

**The loader makes "controlled" structural.** A variant that claims `changed_feature:
day_of_week` must differ from its baseline in `day_of_week` **and in nothing else**. The loader
compares the two rows column by column and refuses the fixture otherwise. This turns a property
that would otherwise depend on the fixture author's care into one the fixture cannot violate —
the same move the composite foreign keys make for ownership.

Three further rules, each with a reason:

- **Exactly one baseline per family.** At most one is enforced by a partial unique index; at
  least one by the loader.
- **Variants are spaced apart in presentation order.** If the Saturday and Tuesday versions of
  the same row are adjacent, the second answer is anchored on the first and the comparison
  measures consistency rather than judgement. Recommend the loader enforce a minimum gap (three
  questions) between members of a family. Open decision 6 in section 19 — adjacency is faster and
  makes the contrast explicit, so this is a genuine trade-off, not an oversight.
- **Baselines are core, variants are optional.** Fifty mandatory calibration cases would put the
  soft gate out of reach. Marking baselines `required_core` and variants optional keeps the gate
  achievable and makes the variants a "keep going" surface. The cost is that a family with a
  skipped variant yields no feature-effect row, which the admin view must show as *incomplete
  family*, never as *no effect*.

---

## 7. Lead and Partner: comparison logic

Both founders answer the **same question row**, because the calibration section is a shared
section referenced by both founder templates. That is the mechanism phase 3 proved: one
`question_id`, two responses, isolated from each other, compared by equality on a primary key
rather than by matching text. Neither can see the other's answer at any point; nothing in the
portal serves it, and the comparison exists only on the admin surface.

Two orthogonal axes, deliberately not collapsed into one score.

**Classification state**

| State | Condition |
|---|---|
| `agree_strong` | Same `classification_category`, both confidences ≥ 4 |
| `agree_weak` | Same category, at least one confidence ≤ 3 |
| `split` | Different categories |
| `incomplete` | Fewer than two answers |

**Significance state** (computed only when both answered)

| State | Condition |
|---|---|
| `aligned` | Materiality distance ≤ 1 and no polarity conflict |
| `drifted` | Materiality distance ≥ 2, no polarity conflict |
| `conflicted` | Polarity conflict: `positive` vs `negative` signal, or `contributes_positive` vs `contributes_negative` impact, or one says `potential_hard_decline` and the other says `none` |

Materiality is ordinal (`immaterial < low < medium < high < critical`), so "distance" is the
difference in position, and "one step apart" is genuine near-agreement rather than a coincidence
of labels. Signal and impact are nominal, so only the *polarity* conflicts are named — `neutral`
against `unknown` is not a disagreement worth a flag.

Then:

- **Label candidate** = `agree_strong` **and** `aligned`. These are the rows worth building
  anything on.
- **Calibration gap** = `split` **or** `conflicted`. These go on the discussion list, highest
  priority first: a conflict on `potential_hard_decline` outranks a category split, which outranks
  a signal flip.
- Everything else is evidence with a weight, and is neither promoted nor discussed.

Reason codes are compared as a set overlap (Jaccard) rather than for equality: citing two of the
same three reasons is agreement about the mechanism even where the labels differ, and that is
often the most informative row in the set.

**The feature sensitivity matrix** is the headline output. For each family and each
`changed_feature`, compare the variant's labels against the baseline's for both founders:

| Outcome | Reading |
|---|---|
| `flips_both` | The feature is decision-relevant. A rule may use it. |
| `flips_one` | A calibration gap **on that feature**. The most valuable discussion item in the whole exercise: the two experts weigh it differently. |
| `flips_neither` | Not decision-relevant at this magnitude. The classifier can ignore it, which is as useful as knowing it matters. |
| `materiality_shift_*` | Same three outcomes computed on materiality rather than category: a feature can change how much a line matters without changing what it is. Amount usually does exactly this. |
| `incomplete` | A variant went unanswered. Never reported as an effect. |

---

## 8. Label lifecycle

Three layers, three different kinds of truth, never joined as if they were one. This is the
distinction the brief asks for, and it is enforced by keeping them in separate tables with
separate names and by never letting a lower layer write an upper one.

```
   EVIDENCE                  CONSENSUS                  POLICY
   one person's answer       derived, recomputable      a human decision

   discovery_transaction_    calibration_consensus      classifier rules /
   calibration_responses     (phase 5, derived)         prompts / thresholds
   + history (trigger)                                  (outside Discovery)

   "Lead says Personal     "both agree, strongly"     "CashLens does this"
    expense, confidence 4"
        ↓                          ↓                          ↓
   never overwritten          drop and recompute         changed only by a
   silently; every            at will; carries the       human, with a record
   revision kept              response ids and
                              versions it used
```

**Evidence** is what a stakeholder wrote. It is attributable, versioned against the question
wording it answered, and preserved by the same trigger machinery the rest of the portal uses: a
changed calibration label keeps its predecessor, and a deleted one is recorded as deleted. A
founder's answer is *evidence*, not ground truth, and the vocabulary in every view must say so.

**Consensus** is arithmetic over evidence, computed by section 7's rules. It is derived: it can
be deleted and rebuilt, and it records which response ids and `question_version`s produced it, so
a consensus computed before a rewording is distinguishable from one computed after. When the two
founders disagree, **neither answer silently wins**: the row's state is `split`, there is no
consensus category, and any metric that needs one excludes the case from its denominator and says
how many it excluded. When they agree, the artefact is a *high-confidence human label*, which is
not the same claim as *correct*.

**Policy** is what CashLens does. It lives in the classifier's own domain — rules, prompts,
thresholds — and nothing in Discovery may write it. The path from consensus to policy is section
10, and it is a human one.

A fourth object exists and belongs to none of these layers: **the model's prediction**, section 9.

---

## 9. Comparing against the CashLens prediction

A calibration case is **not a transaction in the ledger**. It is synthetic, it belongs to no
business, and there is no `import_rows` row to look up. So "what did CashLens say?" cannot be a
join; it has to be a run.

The design is an **offline evaluation harness** (phase 5, not now): feed each case's stimulus
through the same classifier code path the product uses, and record what came back next to the
case, one row per run.

```
case stimulus ──▶ the product's own classify path ──▶ predicted category + confidence
                                                       │
                    consensus label (section 8) ────────┼──▶ agreement, per category,
                                                       │    per vertical, per merchant,
                    each founder's label ──────────────┘    weighted by materiality
```

What the prediction row must carry, because a measurement without provenance is not a
measurement: the classifier or prompt version (`CLASSIFY_PROMPT_VERSION` and the cash-flow
prompt's equivalent), the model id, the mode (live, cached, replay — the existing distinction),
the run id, and the timestamp. `CLAUDE.md` requires pinned prompt and fixture versions for any
reported accuracy, and this is where that is satisfied.

Two facts about the current implementation that this design has to account for, neither of which
is a Discovery problem and neither of which blocks anything:

- **The model's confidence is three-level** (`high`, `medium`, `low`) while the human's is 1–5.
  Confidence-calibration metrics therefore compare within each scale, and any cross-scale mapping
  is an explicit, documented choice rather than an implicit one.
- **A human correction overwrites the model's category in place.** `import_rows.cashflow` keeps
  `category` and `source` in one object, so after a correction the original prediction is gone.
  That makes "human override rate" measurable *going forward* only if the correction path is
  changed to preserve what it replaced. Recommendation: record it in the classifier's own domain
  when that metric is wanted; do not let Discovery reach into `import_rows`. Noted as open
  decision 9.

**Anchoring rule, and it is not optional:** the respondent must never see the model's prediction,
and must never see the other founder's answer. A label produced after seeing either is not
independent evidence, and an evaluation scored against contaminated labels measures nothing. The
portal serves neither, which is already true of the current implementation and must stay true.

---

## 10. Rule-candidate lifecycle

```
label candidate (section 7)
      │  a human reads the family, the reason codes and the reasoning text
      ▼
draft rule or threshold          ← written by a person, in the classifier's language
      │  backtested offline against the whole calibration set and, separately,
      │  against real reviewed imports
      ▼
candidate with measured effect   ← "would have changed N rows, M of them for the better"
      │  approval, recorded
      ▼
production change                ← a normal code or prompt change, reviewed, versioned
```

Four properties this has to keep:

1. **No automatic generation and no automatic deployment.** Nothing in Discovery emits a rule.
   The admin surface lists candidates; a person writes the rule.
2. **A candidate names its evidence.** Which family, which cases, which responses, which
   consensus state, which measured effect. A rule whose provenance is not recorded cannot be
   re-examined when it misfires.
3. **Backtesting is against both sets.** The calibration set says whether the rule matches expert
   judgement; real reviewed imports say whether it fires often enough to matter and rarely enough
   to be safe. A rule that is right and never fires is not worth the complexity.
4. **Agreement among three people is a signal, not a policy.** The existing admin design already
   takes this position for scenario rule candidates and labels them as candidates in the
   interface; calibration follows it exactly.

---

## 11. Metrics this data enables

Every metric names its reference. There is no bare "accuracy" here, because there is no ground
truth — only agreement with a named party.

| Metric | Computation | Needs |
|---|---|---|
| Raw classification agreement | prediction = consensus category, over cases with a consensus | consensus (§8), prediction (§9) |
| Material-weighted agreement | the same, weighted by consensus materiality (immaterial 0 … critical 4) | materiality on the consensus |
| Agreement with Lead / with Partner | prediction = that person's category, reported separately, never averaged | per-respondent evidence |
| Inter-founder agreement | `agree_strong + agree_weak` over answered pairs | §7 |
| Human confidence calibration | agreement rate grouped by the respondent's own 1–5 confidence; a well-calibrated 5 should agree far more often than a 2 | `classification_confidence` |
| Model confidence calibration | agreement rate grouped by the model's high/medium/low | prediction confidence |
| False personal-expense rate | predicted `Personal expense`, consensus something else — the costly error, because it understates business cash flow | confusion matrix |
| False payroll rate | predicted `Payroll` where consensus says a family transfer, and the reverse | confusion matrix |
| False organic-revenue rate | predicted `Organic revenue` where consensus says a loan, a transfer or personal income — the error that inflates revenue and therefore the advance | confusion matrix |
| Review rate | share of cases where the humans said `human_review` | `handling_recommendation` |
| AI escalation rate | share where they said `ai_review` | `handling_recommendation` |
| Ask-the-borrower rate | share with `needs_more_context` or `ask_borrower` | both fields |
| Human override rate (real imports) | corrections over classifications, in the product, not here | a classifier-side change (§9) |
| Performance by vertical | any of the above grouped by `business_vertical` | typed column |
| Performance by merchant family | grouped by `merchant` | typed column |
| Feature sensitivity | §7's matrix, per feature, across families | `case_family`, `changed_feature` |
| Unclassifiable-by-experts rate | both founders `Unclassified` and `needs_more_context` | excluded from every denominator above, and reported separately |

Every one of these is a bounded query over indexed or small tables. None scans a jsonb blob,
which is why sections 3 and 4 put the analysis keys in columns.

---

## 12. API additions eventually required

**Stakeholder surface: none.** This is worth stating plainly, because it is the design's main
structural claim. A calibration answer travels on the existing route,
`PUT /api/discovery/assignments/{id}/responses/{question_id}`, carrying a `calibration` object
where a scenario carries `scenario`. The section payload gains a `transaction_case` on the
question alongside the existing `scenario_brief`. Same scope resolution (`Mine`), same client
header, same idempotency, same history, same progress. **The authorization surface phase 3 proved
does not grow.**

Changes inside existing modules, all additive:

| Module | Change |
|---|---|
| `app/discovery/answers.py` | A `calibration` branch in `normalise`, validating the eleven categories against the case's direction, confidence 1–5, the three enums, reason codes against the question's options, the two text bounds. |
| `app/discovery/models.py` | A `CalibrationAnswer` model, `strict`, `extra="forbid"`, and a `calibration` field on `SaveRequest`; a `TransactionCaseView` on `QuestionView`. |
| `app/discovery/routes.py` | `_read_bank` reads the case rows and the calibration responses; the "a typed answer needs both rows" rule extends to calibration; the save path writes the typed row the same way. |
| `app/discovery/seed.py` | Loads `transaction_case` blocks, validates families and variants (§6), expands section-level reason codes onto each question's options, and refuses a branch condition that targets a typed question (§18). |

`app/discovery/progress.py` needs no change: branch evaluation reads the generic answer, which is
null for a typed question, and the loader rule above stops such a condition existing. Worth
noting that today a condition of the form `{"equals": null}` against an answered scenario
question would hold, because the evaluator stores the null answer under the question's key. No
fixture does this and the new loader rule makes it unreachable for both typed types.

Admin surface (phase 5, guarded per route by `DiscoveryAdmin`, bounded and paginated like every
existing admin read):

| Route | Purpose |
|---|---|
| `GET /api/admin/discovery/calibration/families` | Every family, its variants, and the agreement state per case |
| `GET /api/admin/discovery/calibration/disagreements` | Calibration gaps, ordered by severity |
| `GET /api/admin/discovery/calibration/candidates` | Label candidates |
| `GET /api/admin/discovery/calibration/features` | The feature sensitivity matrix |
| `GET /api/admin/discovery/calibration/evaluation` | Consensus against prediction, by category, vertical and merchant |

Each appears automatically in the OpenAPI-derived enumeration the phase 3 suite already runs, so
any one of them shipping without its per-route guard fails a test rather than shipping.

---

## 13. UI concept

One case at a time, at `/discovery/[sectionSlug]`, using the existing shell, card and progress
primitives. A new `calibration-field.tsx` sits beside `scenario-field.tsx` and
`question-field.tsx` dispatches to it on the question type.

```
┌─────────────────────────────────────────────────────────────┐
│ Discovery · Section 3 of 5              Question 12 of 40   │
│ Synthetic case · Restaurant                                 │
├─────────────────────────────────────────────────────────────┤
│  THE ROW                                                    │
│  UBER *EATS 8N2K4        Sat 14 Mar · 22:41                 │
│  −$18.40                            card · checking         │
│                                                             │
│  seen 11× in 30 days · $9–$24 · avg $16.80 · irregular      │
│  ▸ related rows (3)                                         │
├─────────────────────────────────────────────────────────────┤
│  What is it?                                                │
│  [1 Operating expense] [2 Transfer out] [3 Loan payments]   │
│  [4 Personal expense]  [5 Payroll]      [6 Unclassified]    │
│                                                             │
│  How sure?        1  2  3 (4) 5                             │
│  Does it matter?  immaterial (low) medium  high  critical   │
│                                                             │
│  ▸ More: signal, impact, why, what you'd need to know       │
├─────────────────────────────────────────────────────────────┤
│  [Skip]                     [Save & exit]  [Save & next →]  │
└─────────────────────────────────────────────────────────────┘
```

Deliberate choices:

- **The row is rendered like a bank statement line**, not like a form. The respondent is reading a
  transaction, which is the thing they are expert at; a labelled field grid would change the task.
- **Keyboard first.** Digits pick a category and a confidence, Enter saves and advances. Forty
  cases at four clicks each is 160 clicks; at two keystrokes each it is 80 keystrokes.
- **Progressive disclosure.** Signal, impact, reason codes, context and reasoning live behind one
  disclosure, opened automatically when materiality is `medium` or above — the cases where the
  extra detail is worth the time.
- **No prediction, no other founder, no "most people said".** Section 9's anchoring rule, in the
  interface where it is actually enforced.
- **Variants are not visually linked.** No "this is the Tuesday version of the case you just did".
  The comparison happens in analysis, not in the respondent's head.
- **Saving is per case**, as everywhere else in the portal: leave at any point, resume where you
  stopped, previous answers hydrated from the database.
- Only the fields present on the case are rendered; a bare one-line case is a legitimate case.

---

## 14. Schema and migration additions

In full, with DDL, constraint text, migration ordering and the loader rules, in the
[schema proposal](transaction-calibration-schema-proposal.md). In summary: **two
migrations**, `0026` and `0027`, because one adds a value to an existing enum type and the other
uses it, and `db/migrate.py` runs each migration file in its own transaction. Three new tables
(`discovery_transaction_cases`, `discovery_transaction_calibration_responses`,
`discovery_transaction_calibration_response_history`), four new enum types, one history trigger,
and two check constraints widened on tables migration `0025` created. Everything else in `0025`
is untouched.

---

## 15. Security impact

**Nothing phase 3 proved is weakened, and the proof surface barely moves.** Specifically:

| Property | Effect of this design |
|---|---|
| FastAPI is the only data interface | Unchanged. No new client path, no new grant. |
| Service-role-only tables, RLS with no policies | The three new tables get exactly the same treatment: `revoke all` from `anon` and `authenticated`, the four verbs to `service_role`, RLS enabled, no policies. The `discovery_rls_check.py` table list grows from eleven to fourteen. |
| No direct PostgREST access for stakeholders | Unchanged, and it matters more here: the case table is question-bank data, so a read grant would leak cases a respondent has not been served, exactly as it would leak branch-hidden questions. |
| Assignment isolation | Unchanged. Calibration responses hang off the same `(assignment_id, user_id)` composite key as scenario responses. |
| Shared questions, isolated answers | **This is now the central case**, not an edge case: Lead and Partner answer the same calibration `question_id` by design. Phase 3 proved the isolation for exactly this shape. |
| Server-side branching | Unchanged, with one narrowing: a calibration question cannot be a branch *target* (§18). |
| History preservation | Extended: a third history table and a third trigger, same `before update or delete` pattern, including the cascade from a deleted parent. |
| Strict validation | Extended: the calibration answer is validated in three places, as the phase 3 report describes — Pydantic for the envelope, `answers.py` for the content against the question, check constraints as the second lock. |
| No analytics on Discovery content | Unchanged. `/discovery` is excluded; a merchant name or an amount cannot travel. |

Two new considerations that are genuinely new, both about the fixtures rather than the code:

1. **Cases must be synthetic, and the loader must enforce it.** `CLAUDE.md` forbids committing
   real statements or extracted financial records, and a calibration case is a transaction row.
   The rule: `synthetic` defaults true, and **the fixture loader refuses any case that is not
   synthetic**, in `--check` as well as on a real run. If authorised real cases are ever wanted,
   that is a separate path loading from `/local-data`, with its own decision record, and it is not
   in scope here.
2. **Counterparty names invite real people.** The family-transfer cases need a surname that
   matches the applicant's. Those names must be invented, and the review checklist for the phase 4
   bank must say so, because no validator can tell an invented surname from a real one.

**Finding E7** (the two-write scenario save) is discussed in section 17.

---

## 16. Testing requirements

The phase 3 harness covers new routes automatically; what needs writing is the calibration
equivalent of everything phase 3 wrote for scenarios. Nothing here is optional, and by the same
standard phase 3 set: a property is proved by a test or a real-database check, never asserted
from the code.

**Unit, `tests/test_discovery_gate.py` and a new `test_discovery_calibration.py`**

- The full malformed-answer matrix: every enum out of range, confidence 0/6/3.5/"4", a category
  from the wrong direction, a category not in the eleven, reason codes not in the question's
  options, 21 reason codes, over-long `context_needed` and `reasoning`, unknown fields, the
  `calibration` object sent to a scenario question and the reverse. All 422, nothing written.
- The required trio enforced: a calibration answer without category, confidence or materiality is
  refused.
- Atomicity, both directions, as phase 3 does for scenarios: typed insert fails → nothing stored;
  typed update fails → the previous coherent pair stands; both writes fail → the orphan is
  invisible to every route and repaired on retry.
- History through **every** calibration field, one parameterised case each, plus the delete.
- Two founders on the same shared calibration question: two rows, distinct ids, neither visible to
  the other, independent progress — the phase 3 shared-question tests extended to the typed table.
- A calibration question hidden by a branch is absent from the payload and unwritable.
- A calibration question cannot be a branch target (loader refuses the fixture).
- The authorization matrix and the deployment-switch matrix pick up any new admin route
  automatically; the assertion that exactly one admin operation is role-unprotected still holds.

**Seed loader, `tests/test_discovery_seed.py`**

- A variant differing from its baseline in more than the named feature is refused.
- A variant naming a `changed_feature` it does not actually change is refused.
- A family with no baseline, or two baselines, is refused.
- A non-synthetic case is refused, in `--check`.
- A calibration question with no reason-code options is refused; the section-level expansion
  produces them.
- Variant spacing, if adopted (open decision 6).

**Real database**

- `discovery_rls_check.py`: the three new tables in the table list, the counts moved off the
  literal `11` to `len(TABLES)`, and the same privilege, RLS-and-no-policy and stray-grant
  assertions applied to them.
- The category check constraint matches `prompts.CASHFLOW_INFLOW_CATEGORIES` and
  `CASHFLOW_OUTFLOW_CATEGORIES` exactly — the drift test from section 5.
- The database refuses an inflow category on an outflow case, through the copied-direction
  constraint, and refuses a calibration response whose `direction` does not match its case,
  through the composite key.
- `discovery_isolation_check.py`: the cross-stakeholder attack set extended to a calibration
  case, and the trigger history verified field by field on the real tables.

---

## 17. Implementation sequence

**Phase 3.5 (implementation), after this design is approved**

1. Migrations `0026` and `0027`, applied and recorded.
2. The shared write RPC (below) and the routes moved onto it.
3. `answers.py`, `models.py`, `routes.py`, `seed.py` additions.
4. `calibration-field.tsx` and the `question-field.tsx` dispatch.
5. A **proof fixture only**: two families, three variants each, synthetic, marked as phase 3.5
   test data and disposable in the same way the phase 2 template is. Enough to prove the model;
   not the question bank.
6. The test suite in section 16, plus the real-database checks re-run.
7. A handoff document, and the phase 2 and 3.5 test fixtures cleaned up when phase 4 seeds the
   real bank.

**Phase 4** adds the real calibration bank alongside the founder and engineering sections, and the
three accounts. Nothing in phase 4 changes the model.

**Phase 5** adds the admin views, the consensus computation and the evaluation harness.

**Phase 6+** is the rule-candidate promotion path, under its own decision record.

**Finding E7, the single-transaction write.** Phase 3 accepted that a typed answer is two
PostgREST writes with a compensating delete, because the failure is invisible to every route and
self-healing on retry. Calibration does not change the blast radius of one incident, but it
multiplies the number of two-write saves by ten or twenty: forty cases per founder instead of two
or three scenarios. **Recommendation: do it in phase 3.5**, as one
`save_discovery_typed_response` database function shared by `scenario_decision` and
`transaction_calibration`, for three reasons: the write path is being touched anyway; retrofitting
it after phase 4 means migrating a path that holds real stakeholder answers; and it deletes the
compensating-delete branch, which is the only place in the portal where correctness depends on a
second call succeeding. It is not a blocker — the mitigation holds at any volume — so if phase 3.5
runs long it can slip to phase 5 without risk. It must not slip past the point where real answers
exist.

The RPC is not an authorization surface: it is called by the API with the service role, after
`Mine` has resolved the scope, and it is revoked from `anon` and `authenticated` like the three
existing trigger functions. It re-asserts nothing about ownership that the composite foreign keys
do not already enforce.

---

## 18. Can the existing question system support this cleanly?

**Yes — with one new question type, three new tables and two widened check constraints.** Nothing
about authorization, assignment scope, progress, resume, sessions, the soft gate, the routes or
the frontend shell changes.

**Recommendation: option A, a new `question_type`, named `transaction_calibration`.**

The alternative was option B: keep the eleven existing types and express calibration as, say, a
`single_select` whose answer is the category, with everything else in a side table. It needs no
enum change and it is the wrong answer. The reason codes would not be in `options` (the categories
would be), so the existing reason-code validation could not be reused; `answers.py` dispatches on
the type, so every branch would need "unless this single_select is really a calibration
question"; the admin comparison queries would have to carry the same exception; and the schema's
central property — that the type tells you what the row is — would stop being true. The design
documents rejected a euphemism for the feature's name on similar grounds; this is the same
mistake one layer down.

On the name: the brief proposed `transaction_classification`. Recommend **`transaction_calibration`**
instead, because the answer carries two judgements and "classification" names only the first,
and because "classification" already means something specific in this codebase (the classifier's
own output). Cheap to change now, impossible after the bank is seeded.

**The three friction points, stated plainly rather than discovered later**

1. **The enum value needs its own migration.** `ALTER TYPE ... ADD VALUE` cannot be used in the
   same transaction that adds it, and `db/migrate.py` runs one transaction per file — so `0026`
   adds the value and `0027` uses it. Both apply in a single `apply` run. It is a two-file
   constraint, not a two-day one.
2. **Two constraints on `0025`'s tables must be widened.** `options_match_type` must accept
   calibration as a type that requires options (its reason codes), and
   `response_answer_matches_type` must accept a null `answer` for a calibration parent, exactly as
   it does for `scenario_decision`. This makes `0026`/`0027` the first migration pair that alters
   an object `0025` created. Both are widenings, both are `drop constraint` / `add constraint` on
   small tables, and `scenario_brief_matches_type` is deliberately left alone: a calibration
   question has no `scenario_brief`, and its stimulus lives in its own table.
3. **A calibration question cannot be a branch target.** Branch conditions compare against
   `discovery_responses.answer`, which is null for a typed answer — true of `scenario_decision`
   today, and the loader does not currently forbid it. Add that validation for both types while
   this is being touched. Making the category readable to a branch is possible later; it is not
   worth it now.

---

## 19. Unresolved product decisions

These need Clayton, not an engineer. Each has a recommended default so they can be settled by
exception.

1. **Name.** `transaction_calibration` (recommended) or `transaction_classification`.
2. **`handling_recommendation`**: one optional enum (recommended) or the three separate booleans
   the brief proposed.
3. **Scope of the first real bank.** How many families and variants for Lead and Partner?
   Recommended: 12–15 families, 2–3 variants each, 30–45 cases, 20–30 minutes per founder.
4. **Which families first.** The brief lists seventeen. Recommended first six, by expected
   information per minute: Zelle/Venmo payroll, family transfer versus payroll, Uber inflow versus
   outflow, delivery-platform inflows, reversals without a descriptor, NSF and overdraft
   behaviour.
5. **Core versus optional.** Baselines core, variants optional (recommended), or all core.
6. **Variant spacing.** Loader-enforced gap of three questions (recommended) or deliberate
   adjacency for speed and explicit contrast.
7. **Does Engineer answer any of this?** Recommended: no. The engineering template asks what the
   system should do with these signals, not what the transaction is. Trivially changed later,
   since sections are shared by reference.
8. **Real cases, ever.** Recommended: never in the repository. If authorised the customer rows are
   wanted, that is a `/local-data` path with its own decision record.
9. **Preserving the model's category on correction** so "human override rate" is measurable in the
   product. Recommended: yes, but as a classifier-side change in its own milestone, not part of
   this work.
10. **Confidence scales.** Leave the human at 1–5 and the model at high/medium/low (recommended),
    or unify. Unifying touches the classifier's prompt and its stored output, which is a larger
    change than it looks.
11. **The E7 RPC.** Phase 3.5 (recommended), or phase 5 at the latest, but before real answers
    exist.
12. **What the founders are told.** These are synthetic cases used to calibrate a classifier, and
    their answers are evidence, not policy. Worth a sentence at the top of the section, and worth
    agreeing that sentence now.

---

## Decisions Clayton must approve before implementation

1. **Option A**: a new question type `transaction_calibration`, rather than overloading an
   existing type. (§18)
2. **The name** `transaction_calibration`. (§19.1)
3. **The response schema**: three required fields (category, confidence 1–5, materiality) and
   seven optional ones, with `handling_recommendation` as a single enum rather than three
   booleans. (§3, §19.2)
4. **The category vocabulary is the classifier's own eleven strings**, stored as text with a check
   constraint kept in step with `prompts.py` by a real-database test. (§5)
5. **A separate case table with typed columns**, not `scenario_brief` jsonb, because the analysis
   groups by merchant, vertical, amount and family. (§4)
6. **Controlled variants are structural**: the loader refuses a variant that differs from its
   baseline in anything but its named feature. (§6)
7. **Two migrations, `0026` and `0027`**, and the acceptance that two `0025` check constraints are
   widened. (§18, schema proposal)
8. **Evidence, consensus and policy stay in three separate layers**, and no Discovery answer ever
   becomes production behaviour without a human promotion step. (§8, §10)
9. **The anchoring rule**: respondents never see the model's prediction or each other's answers.
   (§9, §13)
10. **Fixtures are synthetic only**, enforced by the loader; real cases would need their own
    decision record. (§15)
11. **The E7 single-transaction RPC lands in phase 3.5**, shared by scenario and calibration. (§17)
12. **Phase 3.5 ships a proof fixture only** — two families, three variants each — and the real
    bank waits for phase 4. (§17)

Once these are settled, phase 3.5 implementation is migration `0026`/`0027`, five touched modules,
one new component, one test file and a handoff. No new routes, no new authorization surface, and
nothing phase 3 proved is reopened.
