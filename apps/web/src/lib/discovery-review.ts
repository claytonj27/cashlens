/**
 * Reviewer mode: local-only notes ABOUT a Discovery question or calibration case, for judging
 * the question bank while using the portal. Nothing here is an answer. Nothing here reaches the
 * API, the database, a training label or analytics; it lives in this browser's localStorage,
 * keyed by the question's stable id, until it is exported as JSON.
 *
 * Import-free so a plain Node test can load it.
 */

export const REVIEW_STORAGE_KEY = "cashlens.discovery-review.v1";
export const REVIEW_FLAG_KEY = "cashlens.discovery-review.on";

export const REVIEW_VERDICTS = ["keep", "change", "delete"] as const;
export const REVIEW_PLACEMENTS = ["core", "deep_dive", "ask_later", "dont_ask"] as const;
export const REVIEW_TRAINING_VALUES = ["yes", "somewhat", "no"] as const;
export const REVIEW_ISSUES = [
  "unclear",
  "too_obvious",
  "redundant",
  "missing_context",
  "too_leading",
  "wrong_answer_choices",
  "weak_underwriting_value",
  "weak_classification_value",
  "other",
] as const;

export type ReviewVerdict = (typeof REVIEW_VERDICTS)[number];
export type ReviewPlacement = (typeof REVIEW_PLACEMENTS)[number];
export type ReviewTrainingValue = (typeof REVIEW_TRAINING_VALUES)[number];
export type ReviewIssue = (typeof REVIEW_ISSUES)[number];

export type QuestionReview = {
  question_id: string;
  section: string;
  question_type: string;
  title: string;
  verdict: ReviewVerdict | null;
  usefulness: number | null; // 1..5
  placement: ReviewPlacement | null;
  training_value: ReviewTrainingValue | null;
  issues: ReviewIssue[];
  comment: string;
  suggested_rewrite: string;
  updated_at: string;
};

export type ReviewStore = Record<string, QuestionReview>;

/**
 * Whether reviewer controls show. `?review=1` switches it on and `?review=0` off, and the
 * choice is remembered locally so it survives the portal's own navigation. Never on a
 * production build unless `NEXT_PUBLIC_DISCOVERY_REVIEW_MODE=true` was set for that build,
 * which nothing committed does.
 */
export function isReviewActive(
  search: string,
  stored: string | null,
  nodeEnv: string | undefined,
  envFlag: string | undefined,
): boolean {
  const param = new URLSearchParams(search).get("review");
  if (param === "0") return false;
  const enabled = envFlag === "true" || nodeEnv !== "production";
  if (!enabled) return false;
  return param === "1" || stored === "1";
}

export function emptyReview(question_id: string, section: string, question_type: string, title: string): QuestionReview {
  return {
    question_id,
    section,
    question_type,
    title,
    verdict: null,
    usefulness: null,
    placement: null,
    training_value: null,
    issues: [],
    comment: "",
    suggested_rewrite: "",
    updated_at: "",
  };
}

/** Anything at all recorded, so a blank panel does not count as a review. */
export function isReviewed(review: QuestionReview): boolean {
  return (
    review.verdict !== null ||
    review.usefulness !== null ||
    review.placement !== null ||
    review.training_value !== null ||
    review.issues.length > 0 ||
    review.comment.trim() !== "" ||
    review.suggested_rewrite.trim() !== ""
  );
}

/** "✓ Keep", "~ Change", "× Delete", or null when no verdict has been given. */
export function reviewMark(review: QuestionReview | null | undefined): string | null {
  if (!review || !review.verdict) return null;
  return { keep: "✓ Keep", change: "~ Change", delete: "× Delete" }[review.verdict];
}

/** Stored text back into a store; anything unreadable is an empty store, never a crash. */
export function parseReviews(text: string | null): ReviewStore {
  if (!text) return {};
  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
    const out: ReviewStore = {};
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof value === "object" && value !== null && typeof (value as QuestionReview).question_id === "string") {
        const r = value as QuestionReview;
        out[key] = { ...emptyReview(r.question_id, r.section ?? "", r.question_type ?? "", r.title ?? ""), ...r, issues: Array.isArray(r.issues) ? r.issues : [] };
      }
    }
    return out;
  } catch {
    return {};
  }
}

export type ReviewExport = {
  exported_at: string;
  note: string;
  summary: {
    total_reviewed: number;
    keep: number;
    change: number;
    delete: number;
    average_usefulness: number | null;
  };
  reviews: Omit<QuestionReview, "updated_at">[];
};

/** Every recorded review plus the counts, in a stable order, ready to be saved or pasted. */
export function buildExport(store: ReviewStore, now: string): ReviewExport {
  const reviews = Object.values(store)
    .filter(isReviewed)
    .sort((a, b) => a.section.localeCompare(b.section) || a.title.localeCompare(b.title));
  const rated = reviews.map((r) => r.usefulness).filter((u): u is number => typeof u === "number");
  const average = rated.length ? Math.round((rated.reduce((s, u) => s + u, 0) / rated.length) * 100) / 100 : null;
  return {
    exported_at: now,
    note: "Reviewer feedback about Discovery questions. Local-only metadata; not answers, not labels.",
    summary: {
      total_reviewed: reviews.length,
      keep: reviews.filter((r) => r.verdict === "keep").length,
      change: reviews.filter((r) => r.verdict === "change").length,
      delete: reviews.filter((r) => r.verdict === "delete").length,
      average_usefulness: average,
    },
    reviews: reviews.map((r) => ({
      question_id: r.question_id,
      section: r.section,
      question_type: r.question_type,
      title: r.title,
      verdict: r.verdict,
      usefulness: r.usefulness,
      placement: r.placement,
      training_value: r.training_value,
      issues: r.issues,
      comment: r.comment,
      suggested_rewrite: r.suggested_rewrite,
    })),
  };
}
