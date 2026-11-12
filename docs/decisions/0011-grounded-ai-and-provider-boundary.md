# 0011: grounded AI capabilities and the provider boundary

Status: accepted, 2026-09-14. *Amended 2026-09-14: the original status line read "Design only;
nothing is implemented and no model has been called." That is no longer true. The capabilities
described here are implemented on `feat/m10-ai-insights` and paid models have been called against
the development database; the pre-registered comparison and the held-out evaluation in this record
remain unrun. See the [implementation checklist](../planning/IMPLEMENTATION-CHECKLIST.md#interview-demo-release).*
The full design is
[CashLens M10 AI design](../planning/CashLens-M10-AI-Design.md). Supersedes the M10 rows of
the data contract's deferred-table list and the tool-loop shape in the flows document, as
recorded at the end.

## Context

Milestones 1 to 9 produced a deterministic, versioned financial engine whose every category
and recurring row carries transaction ids. M10 adds the three model capabilities the project
exists to demonstrate: contextual classification, a proactive brief and plain-language
scenarios. It is the milestone an interviewer will judge, so the design has to be defensible
on evidence rather than on the model's fluency.

Measured before deciding: the held-out labels in `fixtures/evaluation/` have never been read
by any code; `rules-1` agrees with them on named counterparties and guesses on bare
instrument words, counting the planted ambiguous deposits ($3,500, $6,800) and two
unresolvable credits as customer receipts; the only model client is the operator probe; no
usage control exists; demo rows are immutable; the web proxy imposes no timeout of its own.

## Decisions

**Code computes, the model explains, and the explanation is validated before display.** The
model receives a fact sheet built from the typed `CashAnalysis` and nothing else, and returns
claims bound to fact ids. Four checks run in order and are reported separately: schema
(shape), evidence (every reference exists in the fact sheet), numerical consistency (every
figure equals the referenced value; every number in prose is in the allowed set), and factual
support (direction words match the code-decided direction; causal language only on
data-change facts; a model grader and an author sample on evaluation sets only). "Valid JSON"
is never treated as proof of anything but shape.

**Evidence failure rejects the whole output; numerical and support failures withhold the
claim.** A fabricated reference means the output cannot be trusted on its other claims. A
wrong number in one sentence does not, so that sentence is withheld and counted, visibly.

**Rules stay deterministic and gain a counterparty tier (`rules-2`).** A description with no
word left after removing digits and instrument words is never placed by an instrument keyword.
This moves the planted ambiguous rows from receipts to uncategorised, which is what the
methodology already promised, and keeps `ACH CREDIT HARBOR POINT HOA` a named receipt. The
tier is decided by the presence of a counterparty, not by the word, because the same word is
generic in one row and specific in the next. Contractors, repairs and maintenance, and owner
contributions are added; `equipment lease` is matched before `equipment`. Every change is
versioned and the tests that asserted `rules-1` placements are updated.

**The model is asked only about what the rules could not place.** Rules win clear cases;
candidates are the uncategorised rows, in bounded batches, with computed context (recurrence,
transfer hints, category counts, the owner's earlier decisions as facts). Suggestions are
stored and never applied.

**Unresolved is a first-class outcome.** A suggestion must carry evidence; otherwise the item
is unresolved with a question and, where possible, alternatives. Resolving an unresolvable
held-out item disqualifies a model from being the runtime choice.

**Corrections are overrides with provenance, and nothing is "learned".** An accepted
suggestion or an owner's correction is an append-only, workspace-scoped row recording source,
decider (opaque id), time, the suggestion and run it answered, and the transaction revision it
was made against. The analysis applies current overrides on read (`analysis-2`, `cash-2`).
Later classification runs see earlier decisions as facts in the fact sheet. The interface says
"set by you" and "saved", never "learned" or "trained", because no prompt, model or rule
changes when a row is stored.

**Invalidation is one mechanism: the input fingerprint.** A hash over the versions, the
analysis date, every counted transaction with its revision, every balance and every current
override, computed on each read from rows already loaded. Stored results carry the fingerprint
they were written against; a read compares and labels stale. Imports, removals, corrections
and rules changes are not special-cased, and nothing is regenerated without a request.

**No automatic regeneration.** A confirmation or a correction never triggers a paid call. An
unattended call spends money on someone else's action and would often be stale before it was
read. "Update brief" is a button.

**Change detection is code; the brief explains changes it was told about.** Signals
(balances, month totals, top category changes with a code-decided direction word, recurring
items, one-off large rows, financing rows, forecast summary, coverage, classification status,
and data changes since the previous brief) are computed from the analysis and the previous
run's stored input summary. The model is told what changed, in numbers, and asked what it means
and what to ask; the reason for a change is not knowable from a statement and belongs in a
question.

**Scenarios map onto the three existing controls or are declined.** The model proposes
parameters in the M9 `ScenarioRequest` shape; every number must come from the request text or
a cited recurring fact; dates must be in the window; values outside the constraints are
rejected rather than clamped; a delayed payment whose amount or date the owner has not stated
is confirmed first, because bank history is not evidence of an unpaid invoice. Python computes.
There is no tool loop: the request maps onto one computation or it does not. *Amended by [ADR 0013](0013-interactive-ai-cfo-panel.md), 2026-09-14: the conversation panel runs a bounded, allowlisted loop; this sentence no longer holds for the `ask` capability.*

**One synchronous bounded call per capability, no job table.** The proxy has no timeout of its
own, the function limit is 300 s, and extraction already runs 60 s inside it; a 30 s call with
one repair attempt fits. A job with leases exists for extraction because it has resumable
sub-steps and a child process; a model call has neither, and a job table would add state
without adding a guarantee. The run row is persisted before the call, so nothing is untracked.

**A run record with the fact sheet, business-scoped with a nullable workspace.** `ai_runs`
stores what the model was given and the validated output, because the reviewer view has to
show both, and never raw text. Rows are reachable only through the workspace's scoped routes,
are deleted by `request_business_deletion` before the transactions, cascade with a visitor's
workspace, and demo cache rows are swept by age and prompt version. Logs carry ids, counts and
durations only.

**Reserve before paying, in the database, under locks.** `usage_reservations` is keyed by the
spending identity (user or visitor). The reservation is inserted in the same transaction that
counts, under a global then an identity advisory lock, with per-identity and global rolling
daily cost caps, one run in flight per identity, an output-token cap and a provider timeout.
Migration 0017's lesson is applied before the first paid call rather than after a race.

**One provider interface, three adapters, and OpenRouter refused for private data in code.**
`ModelProvider.complete` is one synchronous call with no transport retries; retries belong to
the capability so they are visible in attempts and usage. Direct OpenAI is the runtime
provider. OpenRouter goes through the same interface with no fallback list and no routing
preference, is used on synthetic fixtures for the comparison, and a request for a private
business through it answers 503 with a test asserting it, until someone reads the providers'
data-handling terms and records a decision. Replay serves tests and CI and is always labelled.

**A controlled comparison with a pre-registered decision rule, not a router.** Four
configurations on the same sealed cases, same prompt version, recording quality, latency and
cost; eligibility requires no invented reference, no followed injection and no resolved
unresolvable; then lowest amount-weighted error and fewest withheld claims, with cost and p95
latency as tie-breakers. The rule is committed before the run. Automatic routing was rejected
because no benefit was identified that a chosen model does not already give.

**Development and held-out cases are separate, and the seal is a record.** The held-out set is
written with the development set and not run until prompt versions are frozen; `evals/SEAL.md`
records the commit and versions; a prompt changed after a held-out run turns that set into a
development set for the new prompt and the report says so. No accuracy threshold is promised
before a run; only the decision rule is fixed in advance.

**Deterministic checks first; a model grades only factual support, only in evaluation.**
Numbers, references, directions, causes, coverage, parameters, refusals and injection markers
are all string and integer checks. The grader's disagreement with the author's sample is
reported next to its score.

**Failure leaves the figures alone.** Provider failure, timeout, invalid output, insufficient
data, a mid-run data change and budget exhaustion each have a status, a one-line message and a
usage rule, and none of them changes a number on the dashboard.

## Consequences

- `rules-2` changes demo figures: in Juniper Kitchen $3,775, in Northline Services $6,800 plus
  four `CHECK DEPOSIT` rows, and in Willow Studio $450 move from customer receipts to
  uncategorised until someone decides. This is the methodology applied, and it is stated in
  the methodology.
- The analysis, metrics and rules versions become `analysis-2`, `cash-2`, `rules-2`;
  `forecast-1` is unchanged in code.
- Migration 0018 adds four tables, three usage functions, a replacement deletion function and
  a cache sweep rule, and `unique (id, business_id)` on `workspaces` for composite keys.
- The data contract's deferred rows for M10 resolve as: `classifications` becomes
  `classification_suggestions` (rules are recomputed, not stored); `overrides` becomes
  `category_overrides`; `insights`, `insight_transactions`, `financial_snapshots`, `scenarios`
  and `tool_calls` are not created, because results live on `ai_runs` with validated evidence
  ids and the analysis is recomputed live; `assumptions` stays deferred; `usage_reservations`
  is keyed by identity rather than by visitor alone.
- No release gate closes. Production keeps no AI variable.

## Alternatives considered

**Let the model compute or restate totals.** Rejected: unverifiable by construction.

**Trust structured output as validation.** Rejected: it proves shape. The four layers exist
because numbers, references and support fail independently, and the evaluation reports them
separately.

**Store every rule classification per transaction.** Rejected: rules are deterministic and
versioned, so a stored copy is either redundant or stale; the fingerprint carries the version.

**Regenerate the brief automatically after a confirmation.** Rejected: unattended spend, and
often stale before it is read.

**A leased job for model calls.** Rejected: no resumable sub-steps; the run row gives the
tracking without the state machine.

**Automatic model routing through OpenRouter.** Rejected: no concrete benefit identified; a
chosen model with a recorded comparison is simpler and defensible.

**A per-workspace reservation key.** Rejected: the person spends, and one person can hold
several workspaces.

**Streaming tokens to the browser.** Deferred: it complicates validation-before-display, which
is the property this milestone is built around.

## Conflicts with earlier documents, resolved

- Flows document section 4 sequences a tool loop with `tool_calls`; this design has one
  validated parameter proposal and one computation, recorded on the run. *Superseded by ADR 0013, which restores a bounded loop for the conversation panel.*
- The data contract listed `insights`, `financial_snapshots` and `scenarios` for M9/M10;
  M9 computed live and M10 stores results on `ai_runs`, so they are not created.
- The blueprint's `usage_reservations` keyed by visitor becomes keyed by identity, since
  private users spend too.
