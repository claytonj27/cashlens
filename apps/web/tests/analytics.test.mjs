/**
 * The analytics rules that keep the Discovery portal out of every event.
 *
 * Loads `src/lib/analytics.ts` through TypeScript's own transpiler, with the tag configured and
 * consent granted, so what is asserted is the real module's behaviour: a page view on a portal
 * path pushes nothing, an event fired from a portal path pushes nothing, a section slug never
 * survives route reduction, and nothing that does get pushed carries a portal word.
 *
 *     npm test
 */

import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { readFileSync } from "node:fs";
import process from "node:process";
import { test } from "node:test";
import ts from "typescript";

// Configured and consented, so every guard but the exclusion is open.
process.env.NEXT_PUBLIC_ANALYTICS_ENABLED = "true";
process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID = "G-TEST0000";

const window = {
  location: { origin: "https://example.test", pathname: "/" },
  dataLayer: [],
  localStorage: { getItem: () => "granted", setItem: () => undefined },
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
  dispatchEvent: () => undefined,
};
globalThis.window = window;

const source = readFileSync(new URL("../src/lib/analytics.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const analytics = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

const PORTAL_WORDS = ["discovery", "phase2", "core-test", "restaurant", "approve", "customer"];

function pushed() {
  const out = window.dataLayer.splice(0);
  return out;
}

test("every portal path is excluded, and nothing else newly is", () => {
  for (const path of ["/discovery", "/discovery/", "/discovery/phase2-core-test", "/discovery/a/b"]) {
    assert.equal(analytics.isExcludedPath(path), true, path);
  }
  for (const path of ["/", "/workspace", "/workspace/analysis", "/demo/juniper-kitchen", "/discoveryx"]) {
    assert.equal(analytics.isExcludedPath(path), false, path);
  }
  // The existing exclusions still hold.
  for (const path of ["/admin/ai-usage", "/login", "/workspace/imports/abc"]) {
    assert.equal(analytics.isExcludedPath(path), true, path);
  }
});

test("a section slug never survives route reduction", () => {
  assert.equal(analytics.routeLabel("/discovery"), "/discovery");
  assert.equal(analytics.routeLabel("/discovery/phase2-core-test"), "/discovery/[section]");
  assert.equal(analytics.routeLabel("/discovery/phase2-core-test?x=1#y"), "/discovery/[section]");
  assert.equal(analytics.sectionLabel("/discovery/phase2-core-test"), "discovery");
});

test("a page view on a portal path pushes nothing", () => {
  pushed();
  analytics.trackPageView("/discovery");
  analytics.trackPageView("/discovery/phase2-core-test");
  assert.deepEqual(pushed(), []);
});

test("an event fired while on a portal path pushes nothing, whatever it carries", () => {
  window.location.pathname = "/discovery/phase2-core-test";
  pushed();
  analytics.track("analysis_viewed", { section: "phase2-core-test" });
  analytics.track("ai_question_submitted", { question_text: "Would you approve this applicant?" });
  assert.deepEqual(pushed(), []);
});

test("recognisable sentinels in a portal path or its events never reach the data layer", () => {
  window.location.pathname = "/discovery/SECRET_SECTION_SENTINEL";
  pushed();
  analytics.trackPageView("/discovery/SECRET_SECTION_SENTINEL");
  analytics.track("analysis_viewed", { section: "PRIVATE_ANSWER_SENTINEL" });
  analytics.track("ai_question_submitted", { question_text: "PRIVATE_ANSWER_SENTINEL", answer: "approve" });
  assert.deepEqual(pushed(), []);
  assert.equal(analytics.routeLabel("/discovery/SECRET_SECTION_SENTINEL").includes("SENTINEL"), false);
  assert.equal(analytics.sectionLabel("/discovery/SECRET_SECTION_SENTINEL"), "discovery");
  assert.equal(analytics.isExcludedPath("/discovery/SECRET_SECTION_SENTINEL"), true);
});

test("the Discovery pages and components import nothing from analytics", () => {
  const files = [
    "../src/components/discovery-shell.tsx",
    "../src/components/discovery-overview.tsx",
    "../src/components/discovery-section.tsx",
    "../src/components/discovery-progress.tsx",
    "../src/components/question-field.tsx",
    "../src/components/scenario-field.tsx",
    "../src/lib/discovery-api.ts",
    "../src/lib/discovery-rules.ts",
    "../src/app/discovery/page.tsx",
    "../src/app/discovery/[sectionSlug]/page.tsx",
  ];
  for (const file of files) {
    const text = readFileSync(new URL(file, import.meta.url), "utf8");
    assert.equal(/analytics|gtag|dataLayer|console\./.test(text), false, file);
  }
});

test("elsewhere the harness is live, and still carries no portal word or unlisted field", () => {
  window.location.pathname = "/workspace";
  pushed();
  analytics.trackPageView("/workspace/analysis");
  analytics.track("analysis_viewed", {
    section: "analysis",
    question_text: "Would you approve this applicant?",
    answer: "approve",
    section_slug: "phase2-core-test",
  });
  const events = pushed();
  assert.equal(events.length, 2, "one page view and one event");
  const serialised = JSON.stringify(events).toLowerCase();
  for (const word of PORTAL_WORDS) assert.equal(serialised.includes(word), false, word);
  const [, eventArgs] = events;
  assert.deepEqual(eventArgs, ["event", "analysis_viewed", { section: "analysis" }]);
});
