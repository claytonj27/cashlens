# ADR 0014: an internal customer workspace inside CashLens, named only behind authentication

- Status: accepted
- Date: 2026-09-20
- Deciders: project author
- Relates to: [ADR 0001](0001-project-scope.md) (project scope),
  [ADR 0002](0002-private-repository-and-deployment.md) (private repository, public demo URL)

## Context

`CLAUDE.md` carries a standing instruction: *"Use the name CashLens consistently. No the customer
affiliation is implied."* That instruction exists because CashLens is an independent concept
built on synthetic data, and a public surface that implied a commercial relationship with a real
company would be a false claim about the project.

CashLens is now also being used as a working prototype in a discovery engagement with the customer.
Three named the customer stakeholders need individual accounts, each with a discovery questionnaire
assigned to them, alongside the ability to exercise the prototype itself: upload statements, run
classification, read the analysis.

That feature has to be called something, and the thing it is about is the customer. So the
instruction and the feature collide. `CLAUDE.md` is explicit about what to do when documents or
instructions disagree: *"record the conflict in a decision record instead of silently picking
one."* This is that record.

## Decision

**CashLens is the product name on every public surface, without exception.** The landing page,
the README, the repository description, the login and signup screens, the page title, the site
header, the demo pages, the privacy notice and anything else an unauthenticated visitor can
reach name CashLens and say nothing about the customer.

**The name Discovery is used only inside authenticated, the customer-specific
functionality**: the `/discovery` portal and the `/admin/discovery` views, both of which require
a signed-in account holding the `stakeholder` or `admin` role. A visitor, an ordinary
signed-up `member` account, and the public synthetic demo never see the name.

**The feature is an internal customer-specific workspace inside CashLens, not a product.** It is
not on the roadmap, not offered for sale, not advertised, and not generalised. It exists to run
one discovery engagement. If a second engagement ever wants the same thing, that is a new
decision, not an assumed extension.

Concretely:

| Surface | Names the customer? |
|---|---|
| Landing page, README, repository description | No |
| Login, signup, password reset, auth callback | No |
| Public synthetic demo (`/demo/...`) | No |
| Site header, for a visitor or a `member` account | No |
| Site header, for `stakeholder` or `admin` | Yes, one link reading "Discovery" |
| `/discovery` and below, signed in and assigned | Yes |
| `/admin/discovery` and below, admin only | Yes |
| Page `<title>` | Always "CashLens" |
| Analytics events and page views | No. `/discovery` is excluded from collection entirely |
| Commit messages, decision records, code comments | Yes, where accurate |

The deployment switch `DISCOVERY_ENABLED` is off by default, so a deployment carries the portal
only when it is opted in. The public synthetic-only deployment, which sets `PUBLIC_DEMO_ONLY`,
refuses every discovery route regardless.

## Consequences

The standing instruction in `CLAUDE.md` is narrowed, not overturned: it now reads as a rule
about public surfaces rather than about the whole repository. `CLAUDE.md` should be updated to
say so and to point here, so a future reader does not find an instruction that the code appears
to violate.

The repository itself contains the name, in this file, in three design documents, in the
`app/discovery/` package and in the question fixtures. The repository is private
([ADR 0002](0002-private-repository-and-deployment.md)), and a reviewer given access is given it
deliberately. That is acceptable. It also means that **if the repository is ever made public,
this decision has to be revisited before that happens**, which is recorded as a risk in the
implementation plan rather than left implicit.

Nothing about the naming decision affects authorization. The portal is gated by role, by the
deployment switch and by the public-demo refusal, and would be equally gated if it were called
something else. The name is a disclosure question, not a security one.

## Alternatives considered

**Call it something neutral, such as "Discovery" or "Stakeholder Portal", everywhere.** This was
close to being chosen. It was rejected because the personalised titles, the question content and
the admin comparison views are all about the customer specifically, so a neutral name inside the
authenticated surface would be a euphemism rather than a simplification, and would make the
code harder to read for no gain. The neutral name **is** used in the one place it matters, the
header link, which reads "Discovery".

**Build it as a separate application.** Rejected. It would need its own authentication, its own
deployment, its own authorization model and its own tests, and the stakeholders need to use the
CashLens prototype anyway, so they would have to hold two accounts. The whole value of putting
it here is that the account, the session, the authorization choke point and the prototype are
already in one place.

**Keep no record and just build it.** Rejected, because a future reader finding `app/discovery/`
full of the customer references while `CLAUDE.md` says the opposite would reasonably conclude that
one of them is wrong, and would have no way to tell which.
