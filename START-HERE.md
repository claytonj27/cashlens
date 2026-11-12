# CashLens starter package

Open the project folder in VS Code. Setup was intentionally limited to local preparation before the first commit and push. Milestone 1 added a runnable Next.js frontend and FastAPI backend; the README local setup section has the commands.

The archive has one cashlens/ root. Avoid nesting it twice. It contains the four current planning documents, 11 diagrams as PNG and SVG (seven labeled baseline and four current), editable Mermaid flow/relationship files, one earlier concept mockup, a working README with relative images and a current-diagram renderer. No app implementation, credentials, real financial files, or Git history are included.

Read the statement import plan first. The prior blueprint/playbook remain useful for detailed features and milestones, but their dated four-day estimate and anonymous/synthetic-only schema need the import extension. Baseline diagrams are historical synthetic-mode designs, not the full private-upload model. Use the current diagram guide for scope.

A first working deployment should precede feature expansion. Keep the direct OpenAI path first and optional OpenRouter behind statement-upload completion. Scanned PDFs and real bank connections are later work. README links to live screenshots or deployments must only be added after they exist.

Repository decision, September 12, 2026: the code is one private GitHub monorepo deployed separately to Vercel, with reviewer access granted on request; see [docs/decisions/0002-private-repository-and-deployment.md](docs/decisions/0002-private-repository-and-deployment.md). Earlier public-repository wording in the planning documents was updated in place, and the playbook's asset paths were corrected to the shipped docs/mockups and docs/diagrams layout.
