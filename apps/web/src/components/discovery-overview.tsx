"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import { fetchAssignment, minutesLabel, touchSession, type Assignment, type DiscoveryMe, type SectionSummary } from "@/lib/discovery-api";
import { REVIEW_PERSONAS, SECTION_GROUPS, calibrationGroupTitle, personaLabel, resolveReviewAssignment, sectionGroup, type SectionGroup } from "@/lib/discovery-rules";
import { ReviewExport, ReviewPersonaSwitch, useReviewMode, useReviewPersona } from "@/components/discovery-review";
import { buttonClass, Card, CardTitle, Chip, Icon, Notice, PageHeader, ProgressBar, quietButtonClass, secondaryButtonClass } from "@/components/ui";

/**
 * The landing page: who this is for, how the core is going, and the sections in four groups.
 *
 * Core is the only thing measured against a total, because it is the only thing asked for.
 * Everything after it is offered, never counted down: the page says "Core complete" and then
 * "additional answers help refine the analysis", and never draws one bar over the whole bank.
 * "Resume" goes to the section the server says is next; "Open CashLens" goes to the workspace,
 * which was never closed.
 *
 * In reviewer mode, and only then, a "Review as" control chooses which of the reviewer's OWN
 * assignments to render. The API lists the caller's assignments and nobody else's, and every
 * read below is scoped to the caller, so a persona is a choice between one's own templates and
 * never a view of another person's answers.
 */
export function DiscoveryOverview({ me, token }: { me: DiscoveryMe; token: string }) {
  const review = useReviewMode();
  const [persona, setPersona] = useReviewPersona();
  const target = review ? resolveReviewAssignment(me, persona) : null;
  const current = me.assignment!;
  // The other assignment to render, if the persona is not the newest one. Kept keyed by its id,
  // so a stale load for a previous persona is never shown and nothing has to be reset in an
  // effect.
  const targetId = target && target.assignment_id !== current.assignment_id ? target.assignment_id : null;
  const [loaded, setLoaded] = useState<{ id: string; assignment: Assignment } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (!targetId) return;
    let active = true;
    fetchAssignment(targetId, token)
      .then((fetched) => {
        if (active) setLoaded({ id: targetId, assignment: fetched });
      })
      .catch((caught: unknown) => {
        if (active) setProblem(caught instanceof ApiError ? caught.describe() : "Could not load that template.");
      });
    return () => {
      active = false;
    };
  }, [targetId, token]);

  const assignment = targetId ? (loaded && loaded.id === targetId ? loaded.assignment : null) : current;
  const assignmentId = assignment?.assignment_id ?? null;

  useEffect(() => {
    if (assignmentId) touchSession(assignmentId, token);
  }, [assignmentId, token]);

  const held = new Set(me.assignments.map((a) => a.template_slug));
  const previewing = review ? personaLabel(assignment?.template_slug ?? target?.template_slug) : null;
  const choose = (slug: string) => {
    setProblem(null);
    setPersona(slug);
  };

  const reviewBar = review ? (
    <div className="flex flex-col gap-2">
      <ReviewExport />
      <ReviewPersonaSwitch personas={REVIEW_PERSONAS} held={held} current={assignment?.template_slug ?? target?.template_slug ?? null} onChange={choose} />
    </div>
  ) : null;

  if (!assignment) {
    return (
      <>
        {reviewBar}
        {problem ? <Notice tone="error">{problem}</Notice> : <p className="text-ink-muted">Loading…</p>}
      </>
    );
  }

  const resume = assignment.resume_section_slug ?? assignment.sections[0]?.slug ?? null;
  const core = assignment.progress;
  const coreDone = core.core_complete;

  const grouped = new Map<SectionGroup, SectionSummary[]>();
  for (const section of [...assignment.sections].sort((a, b) => a.order - b.order)) {
    const key = sectionGroup(section.slug, section.is_core);
    grouped.set(key, [...(grouped.get(key) ?? []), section]);
  }
  const coreMinutes = minutesLabel(assignment.estimated_minutes);

  return (
    <>
      {reviewBar}
      <PageHeader
        eyebrow={previewing ? `Discovery · Previewing ${previewing}` : "Discovery"}
        title={me.first_name ? `Welcome, ${me.first_name}` : "Welcome"}
        lead={
          <>
            {assignment.title}
            {assignment.subtitle ? ` — ${assignment.subtitle}` : ""}
          </>
        }
        actions={
          <>
            {resume ? (
              <Link href={`/discovery/${encodeURIComponent(resume)}`} className={buttonClass}>
                {coreDone ? "Continue" : core.answered > 0 ? "Resume" : "Start"}
                <Icon name="arrow" className="size-4" />
              </Link>
            ) : null}
            <Link href="/workspace" className={secondaryButtonClass}>
              Open CashLens
            </Link>
          </>
        }
      />

      <Card>
        <CardTitle
          title={coreDone ? "Core complete" : "Core"}
          hint={
            coreDone
              ? "Additional answers help refine the analysis. Everything below is optional, in any order, and you can revisit any answer; earlier answers are kept."
              : `Answers save one at a time, so you can leave whenever you like and pick up where you stopped.${coreMinutes ? ` The core takes ${coreMinutes}.` : ""}`
          }
          icon="review"
        />
        <div className="mt-4">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-xs">
            <span className="font-medium text-ink">
              Core: {core.core_answered} of {core.core_total} answered
            </span>
            {coreDone ? (
              <Chip tone="up" icon="check">
                Core complete
              </Chip>
            ) : null}
          </div>
          <ProgressBar value={core.core_answered} max={core.core_total} label="Core questions answered" className="mt-1.5" />
        </div>
      </Card>

      {SECTION_GROUPS.map((group) => {
        const sections = grouped.get(group.key) ?? [];
        if (sections.length === 0) return null;
        const answered = sections.reduce((n, s) => n + s.progress.answered, 0);
        const total = sections.reduce((n, s) => n + s.progress.total, 0);
        const title = group.key === "calibration" ? calibrationGroupTitle(assignment.template_slug) : group.title;
        return (
          <div key={group.key} className="flex flex-col gap-3">
            <div className="flex flex-wrap items-baseline justify-between gap-x-4">
              <div>
                <h2 className="t-section">{title}</h2>
                <p className="text-xs text-ink-muted">
                  {group.blurb}
                  {answered > 0 ? ` ${answered} of ${total} answered so far.` : ""}
                </p>
              </div>
              {review ? (
                <Chip tone="muted">
                  {sections.length} {sections.length === 1 ? "section" : "sections"} · {total} items
                </Chip>
              ) : null}
            </div>
            {sections.map((section) => (
              <SectionRow key={section.id} section={section} />
            ))}
          </div>
        );
      })}

      <Notice tone="info">
        Discovery is separate from your CashLens workspace. Nothing you answer here changes an
        upload, a classification or a figure there, and you can use both in any order.
      </Notice>
    </>
  );
}

function SectionRow({ section }: { section: SectionSummary }) {
  const estimate = minutesLabel(section.estimated_minutes);
  const state = section.progress.complete ? "Complete" : section.progress.answered > 0 ? "In progress" : "Not started";
  return (
    <Card as="article" padding="tight">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-ink">{section.title}</h3>
            {section.is_core ? <Chip tone="accent">Core</Chip> : null}
            <Chip tone={section.progress.complete ? "up" : "muted"}>{state}</Chip>
          </div>
          {section.description ? <p className="mt-0.5 text-xs text-ink-muted">{section.description}</p> : null}
          <p className="mt-1 text-xs text-ink-faint">
            {section.progress.answered} of {section.progress.total} answered
            {estimate ? ` · ${estimate}` : ""}
          </p>
        </div>
        <Link href={`/discovery/${encodeURIComponent(section.slug)}`} className={quietButtonClass}>
          {section.progress.complete ? "Review" : section.progress.answered > 0 ? "Continue" : "Open"}
          <Icon name="arrow" className="size-3.5" />
        </Link>
      </div>
    </Card>
  );
}
