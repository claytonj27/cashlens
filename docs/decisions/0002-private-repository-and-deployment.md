# ADR 0002: Private repository, separate Vercel deployment and reviewer access

- Status: accepted
- Date: 2026-09-12
- Deciders: project author
- Amends: [ADR 0001](0001-project-scope.md), which assumed one public GitHub repository

## Context

The engineering blueprint, playbook and flows document all planned one public
GitHub repository so reviewers could read the code directly, with the
application deployed from it to Vercel. On September 12, 2026 the author
created https://github.com/claytonj27/cashlens and decided that the code
repository should be private. At the time of this decision the remote
repository was empty and the local project had no commits, so this is the
first recorded choice about visibility, not a reversal of published code.

Repository visibility and demo visibility are separate questions. A deployed
demo can have a public URL while the code that produced it stays private.

## Options considered

1. One public GitHub repository, as originally planned. Simplest for
   portfolio discovery, but exposes work in progress and any accidental leak
   to everyone immediately.
2. One private GitHub monorepo with access granted to named reviewers when
   the author asks (chosen). Keeps control over who reads the code while the
   deployed demo can still be shown.
3. A private working repository plus a curated public mirror. Doubles the
   maintenance and risks the two drifting apart.

## Decision

- The code lives in one private GitHub monorepo at
  https://github.com/claytonj27/cashlens containing apps/web, services/api
  and docs. There is no separate hosting repository.
- The application is deployed separately to Vercel from this repository.
  Whether the demo URL is public is decided in the deployment milestone and
  never makes the repository public.
- Repository access is shared with reviewers only when the author requests
  it. No collaborators are added as part of this decision.
- Credentials, real bank statements, extracted financial records, private
  model payloads and local upload files stay excluded from Git regardless of
  visibility. The .gitignore rules and the project's privacy rules stand.
- Documentation keeps a public-safe standard because reviewers will read it
  and the demo may be public.

## Consequences

- Vercel must be connected to a private repository through its GitHub
  integration. Confirm that access and preview deployments work during the
  deployment feasibility spike.
- The interview code tour happens through granted access or screen sharing
  rather than a public link.
- On 2026-09-12 the wording "one public GitHub repository" was updated in
  place in the engineering blueprint, the playbook, the flows document and
  ADR 0001, and the baseline system diagram label and its Mermaid source
  were relabeled. The rest of those documents is unchanged.
- Making the repository public later would need a new decision record and a
  review of the full history for anything unsuitable for publication.

## References

- docs/planning/AI-CFO-Engineering-Blueprint.md, sections 3 and 11
- docs/planning/AI-CFO-Playbook.md, intended outcome and initial milestone brief
- docs/planning/AI-CFO-Flows-and-Data-Model.md, section 2
- docs/diagrams/baseline/editable-flow-02.mmd and 01-system.svg

## Amendment, 2026-11-12: a public snapshot

The working repository stays private and is now named `claytonj27/cashlens-private`; it remains
the only source of deployments. A second repository, `https://github.com/claytonj27/cashlens`,
is a public snapshot of it: rebuilt from the private `main` as a single commit with no history,
with the discovery engagement's content removed (the authored question bank, its fixtures and
metadata, the design documents' customer references, and the scripts that create or invite a
named stakeholder) and the feature's customer-specific identifiers renamed to generic ones. The
snapshot is produced by `scripts/build_public_snapshot.py`, which excludes itself from the
export, and before each publication the tree and the full private history are scanned for secrets, personal addresses, project
identifiers and deployment URLs. The snapshot is not deployed and does not run against the
private database without the corresponding renames.

## Amendment, 2026-11-12: a public snapshot

The working repository stays private, as above. A second repository,
`https://github.com/claytonj27/cashlens`, is a public snapshot of it, rebuilt from the private
`main` with a single commit and no history. The snapshot removes the discovery engagement's
content: the authored question bank, its fixtures and metadata, the design documents' customer
references and the scripts that create or invite a named stakeholder. The feature's
customer-specific identifiers are renamed to generic ones (`stakeholder` for the role,
`discovery-lead`, `discovery-partner` and `discovery-engineer` for the templates), so the code
here is the same shape as the private code but does not run against the private database
without the corresponding renames. Every secret, personal address, project identifier and
deployment URL was checked absent before publication, by scanning both the tree and the full
private history. The private repository is the only source of deployments.
