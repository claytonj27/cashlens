/**
 * Reviewer mode's pure rules: when it is active, what counts as a review, and what the export
 * contains. Loaded through TypeScript's transpiler like the other tests.
 *
 *     node --test tests/discovery-review.test.mjs
 */

import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/lib/discovery-review.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const review = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

test("reviewer mode is a local switch and never on in a production build", () => {
  const { isReviewActive } = review;
  assert.equal(isReviewActive("?review=1", null, "development", undefined), true);
  assert.equal(isReviewActive("", "1", "development", undefined), true); // remembered
  assert.equal(isReviewActive("", null, "development", undefined), false);
  assert.equal(isReviewActive("?review=0", "1", "development", undefined), false); // forgets
  assert.equal(isReviewActive("?review=1", "1", "production", undefined), false);
  assert.equal(isReviewActive("?review=1", null, "production", "true"), true); // explicit local build only
  assert.equal(isReviewActive("?review=1", null, "production", "false"), false);
  assert.equal(isReviewActive("?other=1", null, "development", undefined), false);
});

test("a blank panel is not a review; any field makes it one", () => {
  const { emptyReview, isReviewed, reviewMark } = review;
  const blank = emptyReview("q1", "sec", "short_text", "A question?");
  assert.equal(isReviewed(blank), false);
  assert.equal(reviewMark(blank), null);
  assert.equal(isReviewed({ ...blank, comment: "  hmm " }), true);
  assert.equal(isReviewed({ ...blank, issues: ["unclear"] }), true);
  assert.equal(reviewMark({ ...blank, verdict: "keep" }), "✓ Keep");
  assert.equal(reviewMark({ ...blank, verdict: "change" }), "~ Change");
  assert.equal(reviewMark({ ...blank, verdict: "delete" }), "× Delete");
});

test("the export carries every reviewed question and an honest summary", () => {
  const { emptyReview, buildExport, parseReviews } = review;
  const store = {
    a: { ...emptyReview("a", "s1", "short_text", "First?"), verdict: "keep", usefulness: 5, issues: ["too_obvious"] },
    b: { ...emptyReview("b", "s1", "boolean", "Second?"), verdict: "change", usefulness: 3, suggested_rewrite: "Ask it differently." },
    c: { ...emptyReview("c", "s2", "transaction_calibration", "UBER *TRIP (outflow 18.40)"), verdict: "delete", placement: "dont_ask" },
    d: emptyReview("d", "s2", "currency", "Untouched?"),
  };
  const out = buildExport(store, "2026-09-20T00:00:00Z");
  assert.equal(out.exported_at, "2026-09-20T00:00:00Z");
  assert.deepEqual(out.summary, { total_reviewed: 3, keep: 1, change: 1, delete: 1, average_usefulness: 4 });
  assert.deepEqual(out.reviews.map((r) => r.question_id), ["a", "b", "c"]);
  for (const r of out.reviews) {
    for (const key of ["question_id", "section", "question_type", "title", "verdict", "usefulness", "placement", "training_value", "issues", "comment", "suggested_rewrite"]) {
      assert.ok(key in r, key);
    }
    assert.equal("updated_at" in r, false);
  }
  // Round trip through storage text; garbage never crashes.
  assert.deepEqual(Object.keys(parseReviews(JSON.stringify(store))), ["a", "b", "c", "d"]);
  assert.deepEqual(parseReviews("not json"), {});
  assert.deepEqual(parseReviews("[1,2]"), {});
  assert.deepEqual(buildExport({}, "x").summary, { total_reviewed: 0, keep: 0, change: 0, delete: 0, average_usefulness: null });
});
