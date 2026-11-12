# ADR 0012: A routed second model on synthetic data only, and what a figure may cite

- Status: accepted
- Date: 2026-09-14
- Deciders: project author
- Extends: [ADR 0011](0011-grounded-ai-and-provider-boundary.md), which set the
  provider boundary and the four validity layers

## Context

Milestone 10 needed a second model family to compare against direct OpenAI.
OpenRouter reaches many models behind one credential and fits the existing
`ModelProvider` boundary without changing any grounding rule, so it is the
obvious way in.

It also introduces something the direct provider does not have. OpenRouter is a
router: the upstream provider that serves a request is chosen per request, from
a set that can differ between calls and price differently. The published
response shape does not promise to name which upstream served a given call.

Separately, running the four development cases against a real provider for the
first time showed that three of them failed on the *contract* rather than on the
model, and two of those failures were the same rule.

## Decision

### The routed model is available on synthetic demo data only

OpenRouter is not a value of `AI_PROVIDER`. Making it one would let a deployment
point every business at it. It is a second route, chosen per request, and
`app/ai/routing.py` refuses it for any business whose `data_mode` is not `demo`.
The mode comes from the session-derived scope, never from the request.

Three things follow, all deliberate:

- **No fallback.** A request that asks for the routed model and cannot have it is
  refused. It is never served by the direct provider instead, because a
  comparison whose result quietly came from the other model is worse than no
  result.
- **No rerouting.** A private business is never moved onto the router for any
  reason, including the direct provider being unavailable.
- **Nothing offered that would be refused.** A private workspace is told only
  about the direct route. A disabled control implying that real bank data could
  be routed somewhere is worse than no control, because the honest answer is
  that there is no way to enable it.

Every request carries `allow_fallbacks: false`, `require_parameters: true`,
`data_collection: "deny"`, and a `max_price` ceiling taken from this
deployment's own dated price table. The ceiling is what makes the existing
reservation a real bound on a variable route: when no upstream is at or below
it, OpenRouter answers 404 and nothing is billed.

What comes back is recorded as it arrives. The upstream is stored only when the
response names one, and explicitly as absent when it does not. Filling it in
from the model slug would claim a pinned backend that routing never confirmed.

### A reuse key includes the configuration that produced the answer

A stored explanation was keyed by the data and the owner's assumptions. Asking a
second model to explain the same scenario would have returned the first model's
answer and reported it as current — a comparison between two models that was
quietly one model against itself. Provider and model are now part of that key.

### A figure may cite any fact in the sheet, named in full

Previously a claim's figures had to belong to the claim's own fact. The prose
check has always accepted any amount from any fact, so a sentence could say
"$2,500 against $3,000 of starting cash" while the figures array beside it could
not cite the second number. Both live rejections were exactly that shape.

A figure reference now resolves anywhere in the sheet. Grounding is unchanged:
the reference must name a fact that exists, the field must exist on it, and the
integer must equal that field exactly, compared against whichever fact the
reference names. The figures array is what the interface renders as the evidence
link, so being able to cite the number makes the citation more traceable.

### Validation rules are scoped to the kind of value they are about

Two rules were firing on values they were never meant to judge, and both cost
true sentences on the delayed-payment case:

- the raw-minor-unit check tested every figure, so `delay_days` of 18 made
  "arrives 18 days late" read as leaked storage. It now applies only to fields a
  fact declares as money, which is the only place a minor-unit form exists.
- the percentage check recognised one field name and compared signed values,
  while the pattern that finds a percentage in prose never captures a minus. A
  fact holding -15 could not be quoted in any natural sentence. Quotable
  percentages are now declared per fact the way money is, and compared by
  magnitude — direction is the direction check's job.

A cause may be stated only where the cause is the owner's own typed assumption.
Bank history records what happened and never why, but in a scenario "the late
payment is why the low point is deeper" restates the assumption rather than
inventing anything. Every projected fact stays strict.

### A checked answer is not necessarily a complete one

An explanation can pass all four layers, lose most of its sentences, and arrive
as one true remark that never mentions the trough. That is now reported as
`partial`, with the missing fact named in the owner's words. The run is still
recorded as succeeded, because the call did succeed; reuse returns a partial
answer still marked partial.

## Consequences

- A reviewer can compare two model families on the demo businesses through the
  existing controls, and every result says which provider and model produced it.
- Real bank statements stay on direct OpenAI with no configuration that can
  change that, and the refusal is proven by test and against a running server.
- The price table now has to carry entries for routed models, and those entries
  must be the worst case across a slug's serving endpoints, because the ceiling
  sent to the router is taken from them.
- Widening figure references means a claim's figures may name facts other than
  its own. Anything reading `figures[].ref` must resolve it against the whole
  sheet rather than assuming the claim's own id.
- `partial` is a new status the interface has to render. Treating it as success
  or as failure would both be wrong.

## Alternatives considered

- **OpenRouter as the deployment provider.** Rejected: one setting would then
  decide whether real statements go to a variable set of upstreams.
- **Allowing the routed model on private data with a stricter data policy.**
  Rejected for this milestone. `data_collection: "deny"` is a routing preference
  about retention, not a guarantee about who serves a request, and it is not
  treated as one.
- **Relaxing the estimate and causal rules generally** so more claims survive.
  Rejected: the claims that were being lost were lost to rules firing on the
  wrong kind of value, and scoping those rules keeps every check that was
  actually doing work.
