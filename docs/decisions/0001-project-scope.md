# ADR 0001: CashLens project scope, stack and boundaries

- Status: accepted
- Date: 2026-09-12
- Deciders: project author
- Supersedes: none
- Amended by: [ADR 0002](0002-private-repository-and-deployment.md) on repository visibility

## Context

CashLens is an independent portfolio prototype of an AI CFO for small
businesses, inspired by a commercial rent-financing team's stated interest in
the idea. The planning set consists of an engineering blueprint, a flows and
data-model companion, a development playbook and a statement import plan dated
September 12, 2026. The first three described a synthetic-only demo with
anonymous visitor ownership. The import plan extends scope to authenticated
CSV and text-PDF bank statement imports for private businesses and supersedes
the earlier assumptions where they conflict.

This record captures the scope and stack decisions taken at repository
preparation time so implementation starts from one agreed baseline. No code,
deployment, database or credentials exist at the time of writing.

## Options considered

Scope
1. Synthetic demos only, as in the original blueprint.
2. Synthetic demos plus authenticated statement imports (chosen).
3. Live bank connections from the start.

Ownership model
1. Anonymous browser visitors for everything.
2. Anonymous visitors for demos, authenticated memberships for private data (chosen).

Model access
1. Direct OpenAI only.
2. Direct OpenAI for private data, optional OpenRouter for synthetic evaluations later (chosen).
3. Multi-provider routing from the start.

Hosting
1. Vite frontend on GitHub Pages with a Render backend (earlier direction).
2. Next.js and FastAPI on Vercel with Supabase (chosen).

## Decision

- Name the project CashLens. It is a descriptive working name, not a
  trademark claim, and implies no affiliation with any other company.
- Deliver three fictional demo businesses on synthetic Plaid-like fixtures
  and, alongside them, authenticated imports of CSV exports and text-based PDF
  statements for USD checking and savings accounts.
- Feed every source through one canonical transaction contract so synthetic
  fixtures, uploads and any future bank connector reach identical
  deterministic calculations and AI capabilities.
- Own private data through Supabase Auth users and business memberships;
  never under a browser visitor identifier alone. Demo visitors keep isolated
  synthetic workspaces. Demo seeds stay immutable.
- Use Next.js/TypeScript for presentation, Python/FastAPI for all
  authoritative calculations and AI orchestration, Supabase Postgres and
  private Storage, and one GitHub monorepo deployed separately to Vercel.
  Repository visibility is decided in ADR 0002: private.
- Use direct server-side OpenAI for real statement processing. Optional
  OpenRouter evaluations run only on synthetic fixtures after the import
  workflow is complete.
- Prove deployment feasibility before feature development, and put private
  ownership in place before import development.
- Keep the layout A/B experiment restricted to synthetic demo mode.
- Defer scanned or image-only PDFs, password-protected documents,
  credit-card statements, invoices, spreadsheets, multi-currency, live bank
  connections, payment execution and model training.
- Keep genuine development history: commits reflect work as it is completed,
  with actual verification results and no backdating.

## Consequences

Positive
- Reviewers can bring a real statement through the same review, calculation
  and AI path as the demos, which demonstrates ingestion separated from
  analysis and a credible path to a bank connector.
- One contract keeps calculations testable with synthetic known answers.
- Server-side ownership checks plus RLS and Storage policies give a defensible
  privacy story before any real upload.

Negative and accepted
- Scope is larger than the original four-day estimate; the timeline must be
  re-estimated after the deployment spike.
- Vercel function duration limits may not fit PDF parsing plus a model call.
  A durable worker would be a separate decision.
- Scheduled cleanup and retention promises depend on a mechanism that has not
  yet been verified.
- Supporting real PDFs means honest, narrow coverage claims rather than broad
  bank support.

## Open questions

These are unresolved and must not be reported as validated:

1. Whether bounded CSV/PDF processing fits Vercel function limits.
2. Which scheduled cleanup mechanism is verified to run.
3. Two-project Vercel routing, streaming and cookie behavior.
4. Node and Python versions, package manager, migration tool and formatters.
5. OpenAI model selection and cost profile.
6. Final demo limits for file size, pages, rows and concurrent jobs.
7. Sequencing of deterministic financial tools relative to the CSV vertical
   slice, since generating a brief requires metrics.
8. Repository visibility. Resolved the same day by
   [ADR 0002](0002-private-repository-and-deployment.md): private, with reviewer
   access shared on request.

The implementation checklist tracks these items and future decision records
will resolve them individually.

## References

- docs/planning/CashLens-Statement-Import-Plan.md
- docs/planning/AI-CFO-Engineering-Blueprint.md
- docs/planning/AI-CFO-Flows-and-Data-Model.md
- docs/planning/AI-CFO-Playbook.md
- docs/diagrams/README.md
