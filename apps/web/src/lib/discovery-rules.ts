/**
 * The Discovery portal's small pure rules, kept free of imports so a plain Node test can load
 * them: where to send a person after sign-in, whether to draw the link, how typed money becomes
 * minor units, and how a transaction calibration draft becomes a request. Nothing here talks to
 * a server.
 */

export type MeRoles = {
  role: string;
  discovery: { core_complete: boolean } | null;
};

/**
 * Where sign-in lands. A stakeholder with an assignment whose core is not finished goes to the
 * portal; everyone else goes to the workspace, exactly as before the portal existed. An account
 * the server reports as `member`, which is what every deployment without the portal reports,
 * takes the second branch, so the rule is inert wherever the feature is off.
 */
export function destinationFor(me: MeRoles | null): "/discovery" | "/workspace" {
  if (me && me.role === "stakeholder" && me.discovery && !me.discovery.core_complete) {
    return "/discovery";
  }
  return "/workspace";
}

/**
 * Where an email link lands once the auth client has read its session. A recovery link and an
 * invite both arrive without a usable password, so both go to the set-password page; anything
 * else is an ordinary sign-in and goes to the workspace, from where the portal redirect applies.
 */
export function landingAfterEmailLink(linkType: string | null | undefined): "/auth/reset-password" | "/workspace" {
  return linkType === "recovery" || linkType === "invite" ? "/auth/reset-password" : "/workspace";
}

/** Whether the header shows a Discovery link: the two roles the portal exists for. */
export function showsDiscoveryLink(role: string | null | undefined): boolean {
  return role === "stakeholder" || role === "admin";
}

// ------------------------------------------------------------ review persona --
/**
 * The three stakeholder templates a reviewer may inspect, by template slug. A persona is only
 * ever one of the REVIEWER'S OWN assignments on that template: the API lists the caller's
 * assignments and nobody else's, and every route is scoped to the caller, so choosing a persona
 * changes which of one's own assignments is rendered and can never reach another person's
 * answers. Nothing here is an identity.
 */
export const REVIEW_PERSONAS: { slug: string; label: string }[] = [
  { slug: "discovery-lead", label: "Lead" },
  { slug: "discovery-partner", label: "Partner" },
  { slug: "discovery-engineer", label: "Engineer" },
];

export function personaLabel(templateSlug: string | null | undefined): string | null {
  return REVIEW_PERSONAS.find((p) => p.slug === templateSlug)?.label ?? null;
}

type OwnAssignment = { assignment_id: string; template_slug: string };

/**
 * Which of the caller's own assignments a chosen persona maps to. The current (newest)
 * assignment when nothing is chosen or the caller holds no assignment on that template; null
 * only when the caller holds none at all.
 */
export function resolveReviewAssignment(
  me: { assignment: OwnAssignment | null; assignments: OwnAssignment[] },
  persona: string | null,
): OwnAssignment | null {
  if (persona) {
    const held = me.assignments.find((a) => a.template_slug === persona);
    if (held) return { assignment_id: held.assignment_id, template_slug: held.template_slug };
  }
  return me.assignment ? { assignment_id: me.assignment.assignment_id, template_slug: me.assignment.template_slug } : null;
}

/** The engineering template's scenarios are technical, not underwriting; the group says so. */
export function calibrationGroupTitle(templateSlug: string | null | undefined): string {
  return templateSlug === "discovery-engineer" ? "Technical scenarios" : "Calibration and scenarios";
}

// ---------------------------------------------------------------- sections --
export type SectionGroup = "core" | "recommended" | "deep_dive" | "calibration";

/** The four groups the landing page draws, in order, with the line under each heading. */
export const SECTION_GROUPS: { key: SectionGroup; title: string; blurb: string }[] = [
  { key: "core", title: "Core", blurb: "The highest-value questions. This is what we ask for." },
  { key: "recommended", title: "Recommended", blurb: "Additional questions that improve the analysis. Optional." },
  { key: "deep_dive", title: "Deep dive", blurb: "Optional detailed discovery, in any order." },
  { key: "calibration", title: "Calibration and scenarios", blurb: "Transactions, applicants and situations to judge. As many as you have time for." },
];

/**
 * Which group a section belongs to, read from its slug. The fixture builder names every
 * section `<owner>-<group>-<theme>`, where the group is `core`, `rec`, `deep`, `cal` or `scen`.
 * A slug in any other shape falls back on the section's own core flag, which is what the
 * proof fixtures carry.
 */
export function sectionGroup(slug: string, isCore: boolean): SectionGroup {
  const kind = slug.split("-")[1];
  if (kind === "cal" || kind === "scen") return "calibration";
  if (kind === "core") return "core";
  if (kind === "rec") return "recommended";
  if (kind === "deep") return "deep_dive";
  return isCore ? "core" : "deep_dive";
}

/**
 * Typed money as integer minor units, or null when it is not money.
 *
 * "1,250" and "$1,250.5" are 125000 and 125050. Three decimals, letters and an empty string are
 * refused rather than rounded, because a stored amount must be something the person typed.
 */
export function parseMoney(text: string): number | null {
  const cleaned = text.replace(/[$,\s]/g, "");
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(cleaned);
  if (!match) return null;
  const [, sign, whole, fraction = ""] = match;
  const cents = Number(whole) * 100 + Number((fraction + "00").slice(0, 2));
  if (!Number.isSafeInteger(cents)) return null;
  return sign === "-" ? -cents : cents;
}

/** Minor units back into the input's own format: "1250.50", no symbol, no separators. */
export function moneyInput(minor: number | null | undefined): string {
  if (minor === null || minor === undefined) return "";
  const sign = minor < 0 ? "-" : "";
  const abs = Math.abs(minor);
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}

// ------------------------------------------------------------ calibration --
/** The draft of a transaction calibration answer, in the inputs' own shapes. */
export type CalibrationDraft = {
  classification_category: string | null;
  classification_confidence: string; // "" or "1".."5"
  underwriting_materiality: string | null;
  underwriting_signal: string | null;
  decision_impact: string | null;
  reason_codes: string[];
  needs_more_context: boolean;
  context_needed: string;
  reasoning: string;
  handling_recommendation: string | null;
};

/** The stored shape, as the API sends it back. `direction` is the server's, never the draft's. */
export type CalibrationStored = {
  classification_category: string;
  classification_confidence: number;
  underwriting_materiality: string;
  underwriting_signal: string;
  decision_impact: string | null;
  reason_codes: string[];
  needs_more_context: boolean;
  context_needed: string | null;
  reasoning: string | null;
  handling_recommendation: string | null;
};

export const emptyCalibration: CalibrationDraft = {
  classification_category: null,
  classification_confidence: "",
  underwriting_materiality: null,
  underwriting_signal: null,
  decision_impact: null,
  reason_codes: [],
  needs_more_context: false,
  context_needed: "",
  reasoning: "",
  handling_recommendation: null,
};

export const MATERIALITIES = ["immaterial", "low", "medium", "high", "critical"] as const;
export const SIGNALS = ["positive", "neutral", "negative", "unknown"] as const;
export const IMPACTS = ["none", "contributes_positive", "contributes_negative", "manual_review", "potential_hard_decline"] as const;
export const HANDLINGS = ["auto_classify", "ai_review", "human_review", "ask_borrower"] as const;

/** A stored calibration turned back into what the inputs show. */
export function calibrationFromStored(stored: CalibrationStored | null | undefined): CalibrationDraft {
  if (!stored) return emptyCalibration;
  return {
    classification_category: stored.classification_category,
    classification_confidence: String(stored.classification_confidence),
    underwriting_materiality: stored.underwriting_materiality,
    underwriting_signal: stored.underwriting_signal,
    decision_impact: stored.decision_impact,
    reason_codes: stored.reason_codes,
    needs_more_context: stored.needs_more_context,
    context_needed: stored.context_needed ?? "",
    reasoning: stored.reasoning ?? "",
    handling_recommendation: stored.handling_recommendation,
  };
}

export type CalibrationPrepared = { body: { calibration: CalibrationStored } | null } | { problem: string };

/**
 * A draft turned into a request: null when nothing has been entered, a problem worth saying
 * before a round trip when a required field is missing, otherwise the body. The four required
 * fields are the category, the confidence, the materiality and the signal. Naming context that
 * is needed sets the flag, because the server refuses the one without the other. The server
 * validates everything again against the case; this only stops the obvious.
 */
export function calibrationRequest(draft: CalibrationDraft): CalibrationPrepared {
  const contextNeeded = draft.context_needed.trim();
  const untouched =
    draft.classification_category === null &&
    draft.classification_confidence === "" &&
    draft.underwriting_materiality === null &&
    draft.underwriting_signal === null &&
    draft.decision_impact === null &&
    draft.reason_codes.length === 0 &&
    !draft.needs_more_context &&
    !contextNeeded &&
    !draft.reasoning.trim() &&
    draft.handling_recommendation === null;
  if (untouched) return { body: null };
  if (draft.classification_category === null) return { problem: "Say what this transaction is." };
  if (draft.classification_confidence === "") return { problem: "Say how confident you are, from 1 to 5." };
  if (draft.underwriting_materiality === null) return { problem: "Say how much this would matter to underwriting." };
  if (draft.underwriting_signal === null) return { problem: "Say which way it points: positive, neutral, negative or unknown." };
  return {
    body: {
      calibration: {
        classification_category: draft.classification_category,
        classification_confidence: Number(draft.classification_confidence),
        underwriting_materiality: draft.underwriting_materiality,
        underwriting_signal: draft.underwriting_signal,
        decision_impact: draft.decision_impact,
        reason_codes: draft.reason_codes,
        needs_more_context: draft.needs_more_context || contextNeeded.length > 0,
        context_needed: contextNeeded || null,
        reasoning: draft.reasoning.trim() || null,
        handling_recommendation: draft.handling_recommendation,
      },
    },
  };
}

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

/** "Saturday 22:41", "Tuesday", "22:41" or "", from what the case carries. */
export function timingLabel(dayOfWeek: number | null | undefined, timeOfDay: string | null | undefined): string {
  const day = dayOfWeek !== null && dayOfWeek !== undefined && DAYS[dayOfWeek] ? DAYS[dayOfWeek] : "";
  const time = timeOfDay ? timeOfDay.slice(0, 5) : "";
  return [day, time].filter(Boolean).join(" ");
}
