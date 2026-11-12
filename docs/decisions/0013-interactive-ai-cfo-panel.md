# ADR 0013: The interactive AI CFO panel is an M10 deliverable

- Status: accepted
- Date: 2026-09-14
- Deciders: project author
- Amends: [ADR 0011](0011-grounded-ai-and-provider-boundary.md), which recorded "there is
  no tool loop" and resolved the flows document's tool loop as a conflict

## Context

The original plan committed to a conversational assistant. The blueprint's product contract
names "a constrained financial assistant" beside the brief and the calculator
(`docs/planning/AI-CFO-Engineering-Blueprint.md` §1); its architecture diagram routes the
owner's question into validated tools (§2); its interface section requires "assistant access"
and chat state isolated per business (§7); its acceptance test is "ask a question, run a
delayed-receipt scenario, inspect trace" (§10). The flows document sequences that exact turn,
"What if receipts arrive three days late?", through scoped facts, allowed tool schemas,
validation, the calculator and a validated explanation with sources
(`docs/planning/AI-CFO-Flows-and-Data-Model.md` §4). The playbook's interface milestone lists
"scenario comparison, and assistant" (`docs/planning/AI-CFO-Playbook.md` §9). The concept
mockup shows a **Your AI CFO** panel with a suggested question, an answer, source chips and an
"Ask about your business…" input (`docs/mockups/cfo-overview-concept.png`).

The M10 design kept the brief, reviewable classification and a scenario *explanation*, and
narrowed the assistant to a one-shot scenario text box: "no tool loop and no other tool"
(design §5.2), "no tools, no conversation state" (§6.1), "a tool-calling loop" listed as out
of scope (§15), and ADR 0011's "Conflicts with earlier documents, resolved" entry. Free-form
questions, follow-ups and suggested questions were not re-planned elsewhere. That was a
narrowing of a stated commitment, not a documented decision to drop it. The owner's
correction is that the request was for *more* than chat, never for *no* chat.

## Decision

The interactive panel is an explicit M10 deliverable, specified in design section 5A. Its
shape is chosen so that every grounding property the first three capabilities established
carries over unchanged:

- **A bounded loop over four allowlisted tools**, each a typed wrapper over code that exists
  (`signals.build`, `build_bundle`, `forecast.build`, `classification_suggestions`). At most
  three model rounds, four tool calls and one scenario computation per turn, under one
  reservation. No generic agent framework, no SQL, no code execution, no browsing.
- **The model computes nothing and cites nothing it was not given.** The answer is validated
  by the same four layers as a brief, against a fact sheet assembled from this turn's tool
  results alone.
- **An unsupported scenario is clarified or declined, never translated.** "All receipts three
  days late" is not one of the three controls; the panel offers a single expected receipt it
  can schedule, or asks for an amount and a date, and says what it cannot do. The control
  that would answer the mockup's question directly (`receipts_delay_days`, `forecast-2`) is
  recorded as follow-up work, not silently approximated.
- **A proposed category is an open suggestion.** The existing review card is the only path
  that writes an override; owner confirmation stays mandatory.
- **Scope comes from the session.** No tool argument names a workspace or business.
- **Context is conversational, not memory.** The last six validated answers and the agreed
  scenario fragments of one conversation in one workspace, bounded by the input allowance;
  nothing persists across workspaces and nothing adjusts a model.
- **Data versions travel with every turn.** A changed fingerprint is announced and agreed
  assumptions are re-shown before reuse; switching workspace switches conversation.
- **OpenRouter's synthetic-only rule holds for the whole turn**, because tools run in Python
  and the only model calls go through `routing.resolve`.
- **Deletion and retention include the new rows**: `request_business_deletion` removes turns
  and conversations before runs; the visitor sweep removes them by workspace cascade.

Superseded in ADR 0011: the sentence "There is no tool loop" and the resolved-conflict entry
about the flows document. Kept from ADR 0011: everything else, including "no streaming" and
"no automatic routing".

## Consequences

- Migration 0023 (`ai_conversations`, `ai_turns`, the `ask` capability) and one new route
  family, `ai/ask` and `ai/ask/suggestions`.
- The dashboard's top band becomes two columns on desktop with the panel on the right; the
  existing cards are reused, not redrawn.
- The evaluation plan gains `ask` cases: tool selection, clarification behaviour, invented
  numbers, instruction following from transaction text, and refusal of unsupported requests.
- The first implementation is one vertical slice through the acceptance journey in design
  §5A.1, with its own live-call allowance inside the approved $25.

## Alternatives considered

- **Leave the assistant to M12 polish.** Rejected: it is a product commitment with its own
  acceptance test, not a finish.
- **A general agent with open tool use.** Rejected: unverifiable by construction, and it
  would put the model in a position to widen scope or compute.
- **Approximate "all receipts late" with one delayed payment.** Rejected: a different
  question answered as if it were the one asked.
