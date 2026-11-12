"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  fetchSection,
  minutesLabel,
  saveAnswer,
  skipQuestion,
  touchSession,
  type Question,
  type Response,
  type SaveRequest,
  type SaveResult,
  type Section,
} from "@/lib/discovery-api";
import {
  calibrationFromStored,
  calibrationRequest,
  emptyCalibration,
  moneyInput,
  parseMoney,
  type CalibrationDraft,
} from "@/lib/discovery-rules";
import { CalibrationField } from "@/components/calibration-field";
import { DiscoveryProgress } from "@/components/discovery-progress";
import { ReviewPanel, useReviewMode } from "@/components/discovery-review";
import { QuestionField, type Draft } from "@/components/question-field";
import { ScenarioField, emptyScenario, type ScenarioDraft } from "@/components/scenario-field";
import { buttonClass, Card, Chip, Icon, Notice, PageHeader, quietButtonClass, secondaryButtonClass } from "@/components/ui";

type AnyDraft = Draft | ScenarioDraft | CalibrationDraft;
type Drafts = Record<string, AnyDraft>;
type Mode = "continue" | "exit";

/**
 * One section, one question at a time.
 *
 * The server owns the truth: which questions are visible, what was answered, how far along the
 * person is. This component holds only the drafts of what is being typed, and after every save
 * it re-reads the section, so a question a branch has just revealed appears and a resume shows
 * exactly what was stored. Nothing is kept in the browser between visits.
 */
export function DiscoverySection({
  assignmentId,
  slug,
  token,
  previewing = null,
}: {
  assignmentId: string;
  slug: string;
  token: string;
  /** In reviewer mode, the persona whose template this section belongs to; drawn, never sent. */
  previewing?: string | null;
}) {
  const router = useRouter();
  const [section, setSection] = useState<Section | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [index, setIndex] = useState(0);
  const [drafts, setDrafts] = useState<Drafts>({});
  const [saving, setSaving] = useState<Mode | "skip" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  // Local-only reviewer notes about the question; never an answer, never sent anywhere.
  const review = useReviewMode();

  useEffect(() => {
    let active = true;
    touchSession(assignmentId, token);
    const load = async () => {
      const fresh = await fetchSection(assignmentId, slug, token);
      if (!active) return;
      setSection(fresh);
      setDrafts(hydrateAll(fresh, {}, null));
      // Resume at the first question without an answer; a finished section opens at the top.
      const first = fresh.questions.findIndex((q) => q.response === null);
      setIndex(first === -1 ? 0 : first);
    };
    load().catch((caught: unknown) => {
      if (!active) return;
      if (caught instanceof ApiError && caught.status === 404) setMissing(true);
      else setError(caught instanceof ApiError ? caught.describe() : "Could not load this section.");
    });
    return () => {
      active = false;
    };
  }, [assignmentId, slug, token]);

  if (missing)
    return (
      <>
        <Notice tone="error">That section is not part of your Discovery.</Notice>
        <div>
          <Link href="/discovery" className={buttonClass}>
            Back to Discovery
          </Link>
        </div>
      </>
    );
  if (error)
    return (
      <>
        <Notice tone="error">{error}</Notice>
        <div>
          <Link href="/discovery" className={secondaryButtonClass}>
            Back to Discovery
          </Link>
        </div>
      </>
    );
  if (!section) return <p className="text-ink-muted">Loading…</p>;

  const questions = section.questions;
  const question = questions[index] ?? null;
  const last = index >= questions.length - 1;
  const estimate = minutesLabel(section.estimated_minutes);

  /**
   * After a save: re-read the section so visibility and progress are the server's, keep what
   * is being typed elsewhere, then either leave or step to the next question.
   */
  async function apply(result: SaveResult, questionId: string, mode: Mode) {
    setSaved(questionId);
    if (mode === "exit") {
      router.push("/discovery");
      return;
    }
    const fresh = await fetchSection(assignmentId, slug, token);
    setSection(fresh);
    setDrafts((previous) => hydrateAll(fresh, previous, questionId));
    const at = fresh.questions.findIndex((q) => q.id === questionId);
    if (at === -1 || at + 1 >= fresh.questions.length) router.push("/discovery");
    else setIndex(at + 1);
    void result;
  }

  async function save(mode: Mode) {
    if (!question) return;
    setProblem(null);
    const request = toRequest(question, drafts[question.id]);
    if ("problem" in request) {
      setProblem(request.problem);
      return;
    }
    if (request.body === null) {
      if (question.required_core) {
        setProblem("This question is part of the core set and needs an answer before you move on.");
        return;
      }
      await skip(mode);
      return;
    }
    setSaving(mode);
    try {
      const result = await saveAnswer(assignmentId, question.id, request.body, token);
      await apply(result, question.id, mode);
    } catch (caught: unknown) {
      setProblem(caught instanceof ApiError ? caught.describe() : "Could not save that answer.");
    } finally {
      setSaving(null);
    }
  }

  async function skip(mode: Mode) {
    if (!question || question.required_core) return;
    setProblem(null);
    setSaving("skip");
    try {
      const result = await skipQuestion(assignmentId, question.id, token);
      await apply(result, question.id, mode);
    } catch (caught: unknown) {
      setProblem(caught instanceof ApiError ? caught.describe() : "Could not skip that question.");
    } finally {
      setSaving(null);
    }
  }

  const busy = saving !== null;

  return (
    <>
      <PageHeader
        eyebrow={`Discovery${previewing ? ` · Previewing ${previewing}` : ""} · Section ${section.position} of ${section.section_count}`}
        title={section.title}
        lead={section.description ?? undefined}
        actions={
          <Link href="/discovery" className={quietButtonClass}>
            All sections
          </Link>
        }
      >
        <p className="mt-2 text-xs text-ink-faint">
          {section.is_core ? "Core section. " : ""}
          {estimate ? `Takes ${estimate}. ` : ""}
          Each answer is saved on its own; leave any time.
        </p>
      </PageHeader>

      <DiscoveryProgress progress={section.progress} label="Questions answered in this section" showCore={section.is_core} />

      {questions.length === 0 ? (
        <Card>
          <p className="text-sm text-ink-muted">There is nothing to answer in this section right now.</p>
        </Card>
      ) : question ? (
        <>
        <Card key={question.id}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs font-medium text-ink-muted">
              Question {index + 1} of {questions.length}
            </p>
            <div className="flex flex-wrap items-center gap-1.5">
              {question.required_core ? <Chip tone="accent">Core</Chip> : <Chip tone="muted">Optional</Chip>}
              {question.sensitivity === "high" ? <Chip tone="warn">Sensitive · you may skip</Chip> : null}
              {question.response ? (
                <Chip tone={question.response.status === "answered" ? "up" : "muted"} icon={question.response.status === "answered" ? "check" : undefined}>
                  {question.response.status === "answered" ? "Saved" : "Skipped"}
                </Chip>
              ) : null}
            </div>
          </div>
          <h2 className="mt-3 whitespace-pre-line text-lg font-semibold leading-snug text-ink">{question.question_text}</h2>
          {question.helper_text ? <p className="mt-1 text-sm text-ink-muted">{question.helper_text}</p> : null}

          <div className="mt-5">
            {question.question_type === "scenario_decision" ? (
              <ScenarioField
                question={question}
                draft={asScenario(drafts[question.id])}
                onChange={(draft) => setDrafts((prev) => ({ ...prev, [question.id]: draft }))}
                disabled={busy}
              />
            ) : question.question_type === "transaction_calibration" ? (
              <CalibrationField
                question={question}
                draft={asCalibration(drafts[question.id])}
                onChange={(draft) => setDrafts((prev) => ({ ...prev, [question.id]: draft }))}
                disabled={busy}
              />
            ) : (
              <QuestionField
                question={question}
                draft={asDraft(drafts[question.id])}
                onChange={(draft) => setDrafts((prev) => ({ ...prev, [question.id]: draft }))}
                disabled={busy}
              />
            )}
          </div>

          {problem ? (
            <div className="mt-4">
              <Notice tone="error">{problem}</Notice>
            </div>
          ) : saved === question.id && question.response ? (
            <div className="mt-4">
              <Notice tone="success" compact>
                Saved.
              </Notice>
            </div>
          ) : null}

          <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
            <div className="flex flex-wrap gap-2">
              {index > 0 ? (
                <button type="button" onClick={() => { setProblem(null); setIndex(index - 1); }} disabled={busy} className={secondaryButtonClass}>
                  Previous
                </button>
              ) : (
                <Link href="/discovery" className={secondaryButtonClass}>
                  Previous
                </Link>
              )}
              {!question.required_core ? (
                <button type="button" onClick={() => skip("continue")} disabled={busy} className={quietButtonClass}>
                  {saving === "skip" ? "Skipping…" : "Skip this question"}
                </button>
              ) : null}
            </div>
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={() => save("exit")} disabled={busy} className={secondaryButtonClass}>
                {saving === "exit" ? "Saving…" : "Save & exit"}
              </button>
              <button type="button" onClick={() => save("continue")} disabled={busy} className={buttonClass}>
                {saving === "continue" ? "Saving…" : last ? "Save & finish section" : "Save & continue"}
                <Icon name="arrow" className="size-4" />
              </button>
            </div>
          </div>
        </Card>
        {review ? <ReviewPanel key={`review-${question.id}`} question={question} section={section.slug} /> : null}
        </>
      ) : null}
    </>
  );
}

// ------------------------------------------------------------- drafts --
/**
 * Drafts for a freshly read section. Every question takes what the server holds, except that
 * unsaved typing on questions other than `savedId` survives, so saving one answer never loses
 * another that was being written.
 */
function hydrateAll(fresh: Section, previous: Drafts, savedId: string | null): Drafts {
  const drafts: Drafts = {};
  for (const question of fresh.questions) {
    const unsaved = previous[question.id];
    drafts[question.id] =
      savedId !== null && question.id !== savedId && unsaved !== undefined ? unsaved : hydrate(question, question.response);
  }
  return drafts;
}

function isObjectDraft(value: AnyDraft | undefined): value is ScenarioDraft | CalibrationDraft {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asDraft(value: AnyDraft | undefined): Draft {
  if (value === undefined || isObjectDraft(value)) return null;
  return value;
}

function asScenario(value: AnyDraft | undefined): ScenarioDraft {
  return isObjectDraft(value) && "decision" in value ? value : emptyScenario;
}

function asCalibration(value: AnyDraft | undefined): CalibrationDraft {
  return isObjectDraft(value) && "classification_category" in value ? value : emptyCalibration;
}

/** A stored answer turned back into what the input shows, so a resume looks like a return. */
function hydrate(question: Question, response: Response | null): AnyDraft {
  if (question.question_type === "transaction_calibration") {
    return calibrationFromStored(response?.status === "answered" ? response.calibration : null);
  }
  if (question.question_type === "scenario_decision") {
    const s = response?.scenario;
    if (!s) return emptyScenario;
    return {
      decision: s.decision,
      confidence: String(s.confidence),
      reason_codes: s.reason_codes,
      maximum_exposure: moneyInput(s.maximum_exposure_minor),
      additional_information_needed: s.additional_information_needed ?? "",
      what_would_change_my_decision: s.what_would_change_my_decision ?? "",
      notes: s.notes ?? "",
    };
  }
  if (!response || response.status !== "answered") return null;
  const answer = response.answer;
  switch (question.question_type) {
    case "boolean":
      return typeof answer === "boolean" ? answer : null;
    case "multi_select":
    case "ranking":
      return Array.isArray(answer) ? answer.map(String) : [];
    case "currency":
      return typeof answer === "number" ? moneyInput(answer) : "";
    case "number":
    case "percentage":
    case "scale":
      return typeof answer === "number" ? String(answer) : "";
    default:
      return typeof answer === "string" ? answer : "";
  }
}

type Prepared = { body: SaveRequest | null } | { problem: string };

/**
 * A draft turned into a request, or null when the person has entered nothing, or a problem
 * worth saying before a round trip. The server validates again; this only stops the obvious.
 */
function toRequest(question: Question, draft: AnyDraft | undefined): Prepared {
  if (question.question_type === "transaction_calibration") {
    return calibrationRequest(asCalibration(draft));
  }
  if (question.question_type === "scenario_decision") {
    const s = asScenario(draft);
    const untouched =
      s.decision === null &&
      s.confidence === "" &&
      s.reason_codes.length === 0 &&
      !s.maximum_exposure.trim() &&
      !s.additional_information_needed.trim() &&
      !s.what_would_change_my_decision.trim() &&
      !s.notes.trim();
    if (untouched) return { body: null };
    if (s.decision === null) return { problem: "Choose approve, manual review or deny." };
    if (s.confidence === "") return { problem: "Say how confident you are, from 1 to 5." };
    let exposure: number | null = null;
    if (s.maximum_exposure.trim()) {
      exposure = parseMoney(s.maximum_exposure);
      if (exposure === null) return { problem: "The maximum exposure needs to be an amount like 25,000.00." };
    }
    return {
      body: {
        scenario: {
          decision: s.decision,
          confidence: Number(s.confidence),
          reason_codes: s.reason_codes,
          maximum_exposure_minor: exposure,
          additional_information_needed: s.additional_information_needed.trim() || null,
          what_would_change_my_decision: s.what_would_change_my_decision.trim() || null,
          notes: s.notes.trim() || null,
        },
      },
    };
  }
  const value = asDraft(draft);
  switch (question.question_type) {
    case "short_text":
    case "long_text": {
      const text = typeof value === "string" ? value.trim() : "";
      return { body: text ? { answer: text } : null };
    }
    case "boolean":
      return { body: typeof value === "boolean" ? { answer: value } : null };
    case "single_select":
      return { body: typeof value === "string" && value ? { answer: value } : null };
    case "multi_select":
    case "ranking":
      return { body: Array.isArray(value) && value.length > 0 ? { answer: value } : null };
    case "currency": {
      const text = typeof value === "string" ? value.trim() : "";
      if (!text) return { body: null };
      const minor = parseMoney(text);
      return minor === null ? { problem: "Enter an amount like 1,250.00." } : { body: { answer: minor } };
    }
    case "number":
    case "percentage":
    case "scale": {
      const text = typeof value === "string" ? value.trim() : "";
      if (!text) return { body: null };
      const number = Number(text);
      if (!Number.isFinite(number)) return { problem: "Enter a number." };
      if (question.question_type === "scale" && !Number.isInteger(number)) return { problem: "Enter a whole number from 0 to 10." };
      return { body: { answer: number } };
    }
    default:
      return { body: null };
  }
}
