/**
 * The portal's pure rules: where sign-in lands, when the link is drawn, and how typed money
 * becomes minor units. Loaded through TypeScript's transpiler like the analytics test.
 *
 *     npm test
 */

import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/lib/discovery-rules.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const rules = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

test("sign-in lands on the portal only for a stakeholder with core work left", () => {
  const { destinationFor } = rules;
  assert.equal(destinationFor({ role: "stakeholder", discovery: { core_complete: false } }), "/discovery");
  assert.equal(destinationFor({ role: "stakeholder", discovery: { core_complete: true } }), "/workspace");
  assert.equal(destinationFor({ role: "stakeholder", discovery: null }), "/workspace");
  // Every deployment without the portal reports member, so the rule is inert there.
  assert.equal(destinationFor({ role: "member", discovery: null }), "/workspace");
  assert.equal(destinationFor({ role: "member", discovery: { core_complete: false } }), "/workspace");
  assert.equal(destinationFor({ role: "admin", discovery: { core_complete: false } }), "/workspace");
  assert.equal(destinationFor(null), "/workspace");
});

test("an invite or recovery link lands on the set-password page; anything else on the workspace", () => {
  const { landingAfterEmailLink } = rules;
  assert.equal(landingAfterEmailLink("invite"), "/auth/reset-password");
  assert.equal(landingAfterEmailLink("recovery"), "/auth/reset-password");
  assert.equal(landingAfterEmailLink("magiclink"), "/workspace");
  assert.equal(landingAfterEmailLink("signup"), "/workspace");
  assert.equal(landingAfterEmailLink(null), "/workspace");
  assert.equal(landingAfterEmailLink(undefined), "/workspace");
});

test("the header link is drawn for the two portal roles and nobody else", () => {
  const { showsDiscoveryLink } = rules;
  assert.equal(showsDiscoveryLink("stakeholder"), true);
  assert.equal(showsDiscoveryLink("admin"), true);
  assert.equal(showsDiscoveryLink("member"), false);
  assert.equal(showsDiscoveryLink(undefined), false);
  assert.equal(showsDiscoveryLink(null), false);
});

test("a review persona resolves only to one of the caller's own assignments", () => {
  const { resolveReviewAssignment, personaLabel, calibrationGroupTitle, REVIEW_PERSONAS } = rules;
  const lead = { assignment_id: "a-j", template_slug: "discovery-lead" };
  const partner = { assignment_id: "a-a", template_slug: "discovery-partner" };
  const me = { assignment: lead, assignments: [lead, partner] };
  // The chosen persona, when held.
  assert.deepEqual(resolveReviewAssignment(me, "discovery-partner"), partner);
  // Not held: the current assignment, never anything invented.
  assert.deepEqual(resolveReviewAssignment(me, "discovery-engineer"), lead);
  // Nothing chosen: the current assignment.
  assert.deepEqual(resolveReviewAssignment(me, null), lead);
  // A stakeholder holding one assignment resolves to it whatever is stored.
  const one = { assignment: partner, assignments: [partner] };
  assert.deepEqual(resolveReviewAssignment(one, "discovery-lead"), partner);
  // No assignment at all is null, not a crash.
  assert.equal(resolveReviewAssignment({ assignment: null, assignments: [] }, "discovery-lead"), null);
  assert.deepEqual(
    REVIEW_PERSONAS.map((p) => p.slug),
    ["discovery-lead", "discovery-partner", "discovery-engineer"],
  );
  assert.equal(personaLabel("discovery-engineer"), "Engineer");
  assert.equal(personaLabel("phase2-discovery-test"), null);
  assert.equal(personaLabel(null), null);
  assert.equal(calibrationGroupTitle("discovery-engineer"), "Technical scenarios");
  assert.equal(calibrationGroupTitle("discovery-lead"), "Calibration and scenarios");
});

test("a section's landing-page group is read from its slug, with the core flag as the fallback", () => {
  const { sectionGroup, SECTION_GROUPS } = rules;
  assert.equal(sectionGroup("founder-core-business", true), "core");
  assert.equal(sectionGroup("founder-rec-economics", false), "recommended");
  assert.equal(sectionGroup("engineer-deep-runtime", false), "deep_dive");
  assert.equal(sectionGroup("founder-cal-core", true), "calibration");
  assert.equal(sectionGroup("founder-scen-rec", false), "calibration");
  assert.equal(sectionGroup("engineer-scen-deep", false), "calibration");
  // The proof fixtures have no group in their slug.
  assert.equal(sectionGroup("phase2-core-test", true), "core");
  assert.equal(sectionGroup("phase35-calibration-proof", false), "deep_dive");
  assert.deepEqual(
    SECTION_GROUPS.map((g) => g.key),
    ["core", "recommended", "deep_dive", "calibration"],
  );
});

test("a calibration draft becomes a request only once the four required fields are there", () => {
  const { calibrationRequest, emptyCalibration } = rules;
  assert.deepEqual(calibrationRequest(emptyCalibration), { body: null });
  const partial = { ...emptyCalibration, classification_category: "Personal expense" };
  assert.deepEqual(calibrationRequest(partial), { problem: "Say how confident you are, from 1 to 5." });
  assert.deepEqual(calibrationRequest({ ...partial, classification_confidence: "4" }), {
    problem: "Say how much this would matter to underwriting.",
  });
  assert.deepEqual(calibrationRequest({ ...partial, classification_confidence: "4", underwriting_materiality: "low" }), {
    problem: "Say which way it points: positive, neutral, negative or unknown.",
  });
  const full = {
    ...partial,
    classification_confidence: "4",
    underwriting_materiality: "low",
    underwriting_signal: "negative",
    reason_codes: ["merchant_ambiguous"],
    context_needed: "  who was driving  ",
    reasoning: " looks personal ",
  };
  const prepared = calibrationRequest(full);
  assert.ok("body" in prepared && prepared.body);
  assert.deepEqual(prepared.body.calibration, {
    classification_category: "Personal expense",
    classification_confidence: 4,
    underwriting_materiality: "low",
    underwriting_signal: "negative",
    decision_impact: null,
    reason_codes: ["merchant_ambiguous"],
    needs_more_context: true, // set by the text, so the server's coherence rule holds
    context_needed: "who was driving",
    reasoning: "looks personal",
    handling_recommendation: null,
  });
  // Nothing in a request names a direction: the server copies it from the case.
  assert.equal("direction" in prepared.body.calibration, false);
  // A draft with only a signal chosen is not untouched, so it is a problem, not a skip.
  assert.deepEqual(calibrationRequest({ ...emptyCalibration, underwriting_signal: "unknown" }), {
    problem: "Say what this transaction is.",
  });
});

test("a stored calibration hydrates back into the draft the inputs show", () => {
  const { calibrationFromStored, emptyCalibration, calibrationRequest } = rules;
  assert.deepEqual(calibrationFromStored(null), emptyCalibration);
  const stored = {
    direction: "outflow",
    classification_category: "Operating expense",
    classification_confidence: 3,
    underwriting_materiality: "medium",
    underwriting_signal: "neutral",
    decision_impact: "manual_review",
    reason_codes: ["timing_atypical"],
    needs_more_context: false,
    context_needed: null,
    reasoning: null,
    handling_recommendation: "human_review",
  };
  const draft = calibrationFromStored(stored);
  assert.equal(draft.classification_confidence, "3");
  assert.equal(draft.context_needed, "");
  const again = calibrationRequest(draft);
  assert.ok("body" in again && again.body);
  const withoutDirection = { ...stored };
  delete withoutDirection.direction;
  assert.deepEqual(again.body.calibration, withoutDirection);
});

test("timing reads as a person would say it", () => {
  const { timingLabel } = rules;
  assert.equal(timingLabel(6, "22:41:00"), "Saturday 22:41");
  assert.equal(timingLabel(2, null), "Tuesday");
  assert.equal(timingLabel(null, "09:15"), "09:15");
  assert.equal(timingLabel(null, null), "");
});

test("typed money becomes integer minor units or nothing", () => {
  const { parseMoney, moneyInput } = rules;
  assert.equal(parseMoney("1,250.00"), 125000);
  assert.equal(parseMoney("$1,250.5"), 125050);
  assert.equal(parseMoney("70000"), 7000000);
  assert.equal(parseMoney("-12.34"), -1234);
  assert.equal(parseMoney("0"), 0);
  for (const bad of ["", "abc", "1.234", "12.", ".5", "1e3", "1,2,3.4.5"]) {
    assert.equal(parseMoney(bad), null, JSON.stringify(bad));
  }
  assert.equal(moneyInput(125050), "1250.50");
  assert.equal(moneyInput(-5), "-0.05");
  assert.equal(moneyInput(null), "");
  assert.equal(parseMoney(moneyInput(7000000)), 7000000);
});
