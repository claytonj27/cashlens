# Diagram guide

Current architecture: 08-current-system.svg. Upload functions: 09-statement-upload.svg. Private records: 10-private-data-model.svg. Processing/AI relationships: 11-import-jobs-and-ai.svg. Matching PNG files are included.

The baseline folder preserves the earlier system, bank-to-brief, scenario, A/B, financial database, workspace and trace drawings. Those diagrams describe synthetic demo mode. For private ownership, manual-source provenance and optional AI-run relationships, the current import specification supersedes them. Do not present the baseline bank/workspace diagrams as the complete current schema.

The current drawings are selected logical relationships, not exhaustive migration definitions. The planning documents contain constraints and additional tables. Mermaid files are editable logical sources; SVG/PNG drawings use the included Python layout script, not an automatic Mermaid export. Run scripts/render_current_diagrams.py with cairosvg installed to reproduce current pictures.

Update, September 12, 2026: baseline drawing 01-system and its Mermaid source editable-flow-02.mmd were relabeled from a public to a private code repository, and the PNG was re-rendered locally from the edited SVG. No other baseline content changed. A public demo URL is a deployment matter and does not make the repository public.
