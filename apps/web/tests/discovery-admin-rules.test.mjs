/**
 * The admin dashboard's pure rules: how a status reads, how the filters and the search box
 * narrow a table. Loaded through TypeScript's transpiler like the other rules tests.
 *
 *     npm test
 */

import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/lib/discovery-admin-rules.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const rules = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

const question = (over = {}) => ({
  id: "q-1",
  question_key: "sf.uw.hard-stop-nsf",
  question_text: "Is a repeated NSF a hard stop?",
  helper_text: null,
  question_type: "boolean",
  options: [],
  required_core: true,
  priority: 90,
  sensitivity: "low",
  section_slug: "founder-core-underwriting",
  section_title: "Core · Underwriting",
  level: "core",
  group: "core",
  topic: "Underwriting",
  owner: "founder",
  retired: false,
  meta: { title: null },
  scenario_brief: null,
  transaction_case: null,
  ...over,
});
const row = (status, over = {}) => ({ question: question(over), status, detail: "", extra: {}, lead: null, partner: null, business_fact: false, underwriting: true });

test("a comparison status reads as a person would say it", () => {
  const { statusLabel, statusTone } = rules;
  assert.equal(statusLabel("AGREE"), "Agree");
  assert.equal(statusLabel("MATERIAL_DISAGREEMENT"), "Material disagreement");
  assert.equal(statusLabel("ONLY_LEAD_ANSWERED"), "Only Lead answered");
  assert.equal(statusLabel("ONLY_PARTNER_ANSWERED"), "Only Partner answered");
  assert.equal(statusLabel("NEEDS_RECONCILIATION"), "Needs reconciliation");
  assert.equal(statusLabel("SOMETHING_NEW"), "something new");
  assert.equal(statusTone("AGREE"), "good");
  assert.equal(statusTone("DIFFERENT"), "warn");
  assert.equal(statusTone("MATERIAL_DISAGREEMENT"), "bad");
  assert.equal(statusTone("NEITHER_ANSWERED"), "muted");
});

test("the comparison filters narrow by status, level and topic", () => {
  const { matchesComparisonFilter, COMPARISON_FILTERS, isAwaiting } = rules;
  assert.deepEqual(
    COMPARISON_FILTERS.map((f) => f.key),
    ["all", "both", "agree", "disagree", "material", "awaiting", "core", "business", "underwriting", "risk", "operations", "role"],
  );
  assert.equal(matchesComparisonFilter(row("AGREE"), "both"), true);
  assert.equal(matchesComparisonFilter(row("ONLY_PARTNER_ANSWERED"), "both"), false);
  assert.equal(matchesComparisonFilter(row("AGREE"), "agree"), true);
  assert.equal(matchesComparisonFilter(row("ROUGHLY_ALIGNED"), "agree"), true);
  assert.equal(matchesComparisonFilter(row("DIFFERENT"), "agree"), false);
  assert.equal(matchesComparisonFilter(row("DIFFERENT"), "disagree"), true);
  assert.equal(matchesComparisonFilter(row("MATERIAL_DISAGREEMENT"), "material"), true);
  assert.equal(matchesComparisonFilter(row("DIFFERENT"), "material"), false);
  assert.equal(matchesComparisonFilter(row("ONLY_PARTNER_ANSWERED"), "awaiting"), true);
  assert.equal(matchesComparisonFilter(row("NEITHER_ANSWERED"), "awaiting"), true);
  assert.equal(matchesComparisonFilter(row("AGREE"), "awaiting"), false);
  assert.equal(matchesComparisonFilter(row("AGREE", { required_core: false }), "core"), false);
  assert.equal(matchesComparisonFilter(row("AGREE"), "underwriting"), true);
  assert.equal(matchesComparisonFilter(row("AGREE", { topic: "Risk" }), "underwriting"), false);
  assert.equal(matchesComparisonFilter(row("AGREE", { topic: "Role" }), "role"), true);
  assert.equal(isAwaiting("ONLY_LEAD_ANSWERED"), true);
  assert.equal(isAwaiting("NOT_COMPARABLE"), false);
});

test("search matches text, key, id and section, case-insensitively, and blank matches all", () => {
  const { matchesSearch } = rules;
  assert.equal(matchesSearch(question(), ""), true);
  assert.equal(matchesSearch(question(), "   "), true);
  assert.equal(matchesSearch(question(), "repeated nsf"), true);
  assert.equal(matchesSearch(question(), "SF.UW.HARD"), true);
  assert.equal(matchesSearch(question(), "q-1"), true);
  assert.equal(matchesSearch(question(), "core-underwriting"), true);
  assert.equal(matchesSearch(question(), "payroll"), false);
  assert.equal(matchesSearch(question({ meta: { title: "Large ACH credit" } }), "ach credit"), true);
});

test("the dataset filters combine respondent, level, answered state, section and type", () => {
  const { matchesDatasetFilters, EMPTY_DATASET_FILTERS } = rules;
  const answered = new Set(["a-1:q-1"]);
  const by = (aid, qid) => answered.has(`${aid}:${qid}`);
  const ids = ["a-1", "a-2"];
  assert.equal(matchesDatasetFilters(question(), EMPTY_DATASET_FILTERS, by, ids), true);
  assert.equal(matchesDatasetFilters(question(), { ...EMPTY_DATASET_FILTERS, level: "recommended" }, by, ids), false);
  assert.equal(matchesDatasetFilters(question(), { ...EMPTY_DATASET_FILTERS, section: "founder-core-underwriting" }, by, ids), true);
  assert.equal(matchesDatasetFilters(question(), { ...EMPTY_DATASET_FILTERS, type: "number" }, by, ids), false);
  assert.equal(matchesDatasetFilters(question(), { ...EMPTY_DATASET_FILTERS, answered: "answered" }, by, ids), true);
  assert.equal(matchesDatasetFilters(question(), { ...EMPTY_DATASET_FILTERS, answered: "unanswered" }, by, ids), false);
  assert.equal(matchesDatasetFilters(question(), { ...EMPTY_DATASET_FILTERS, answered: "answered", respondent: "a-2" }, by, ids), false);
  assert.equal(matchesDatasetFilters(question(), { ...EMPTY_DATASET_FILTERS, answered: "unanswered", respondent: "a-2" }, by, ids), true);
});

test("the responses view: status, filters, comment search, ordering, importance, links", () => {
  const { responseStatus, matchesResponsesFilter, writtenComment, matchesAnswerSearch, respondentQuestions, importantResponses, analysisLinks, levelLabel, agoLabel, hrefFor } = rules;
  const answered = { status: "answered", answer: "5_10", display: "5–10", note: null, scenario: null, calibration: null, question_version: 1, created_at: null, updated_at: null };
  const prefer = { ...answered, display: "Prefer not to answer" };
  const skipped = { ...answered, status: "skipped", display: "(skipped)" };
  assert.equal(responseStatus(null), "unanswered");
  assert.equal(responseStatus(answered), "answered");
  assert.equal(responseStatus(prefer), "prefer_not");
  assert.equal(responseStatus(skipped), "skipped");
  const core = question();
  const rec = question({ level: "recommended", required_core: false, group: "recommended" });
  const cal = question({ level: "core", required_core: true, group: "calibration", question_type: "transaction_calibration" });
  assert.equal(matchesResponsesFilter(core, answered, "answered"), true);
  assert.equal(matchesResponsesFilter(core, prefer, "answered"), true);
  assert.equal(matchesResponsesFilter(core, skipped, "unanswered"), true);
  assert.equal(matchesResponsesFilter(core, null, "unanswered"), true);
  assert.equal(matchesResponsesFilter(rec, answered, "recommended"), true);
  assert.equal(matchesResponsesFilter(cal, answered, "recommended"), false);
  assert.equal(matchesResponsesFilter(cal, answered, "cases"), true);
  assert.equal(matchesResponsesFilter(core, answered, "core"), true);
  // written comments come from whichever field the type keeps them in
  assert.equal(writtenComment(core, answered), null);
  assert.equal(writtenComment(core, { ...answered, note: "a note" }), "a note");
  assert.equal(writtenComment(question({ question_type: "long_text" }), { ...answered, answer: "long story" }), "long story");
  assert.equal(writtenComment(cal, { ...answered, calibration: { reasoning: "looks personal" } }), "looks personal");
  assert.equal(writtenComment(question({ question_type: "scenario_decision" }), { ...answered, scenario: { notes: "four funders" } }), "four funders");
  assert.equal(matchesAnswerSearch(core, { ...answered, note: "Stripe payouts" }, "stripe"), true);
  assert.equal(matchesAnswerSearch(core, answered, "5–10"), true);
  assert.equal(matchesAnswerSearch(core, answered, "zzz"), false);
  // template order: section position, then question order
  const sections = [
    { id: "s2", slug: "founder-core-underwriting", positions: { "discovery-lead": 1 } },
    { id: "s1", slug: "founder-core-business", positions: { "discovery-lead": 0, "discovery-partner": 0 } },
    { id: "s3", slug: "engineer-core-system", positions: { "discovery-engineer": 0 } },
  ];
  const qs = [
    question({ id: "b", section_slug: "founder-core-underwriting", order: 0 }),
    question({ id: "a2", section_slug: "founder-core-business", order: 2 }),
    question({ id: "a1", section_slug: "founder-core-business", order: 1 }),
    question({ id: "d", section_slug: "engineer-core-system", order: 0 }),
    question({ id: "r", section_slug: "founder-core-business", order: 0, retired: true }),
  ];
  assert.deepEqual(respondentQuestions(qs, sections, "discovery-lead").map((q) => q.id), ["a1", "a2", "b"]);
  assert.deepEqual(respondentQuestions(qs, sections, "discovery-partner").map((q) => q.id), ["a1", "a2"]);
  // importance: one reason per question, ordered
  const items = [
    question({ id: "i1", required_core: true }),
    question({ id: "i2", required_core: false, priority: 95, question_type: "long_text" }),
    question({ id: "i3", sensitivity: "high", required_core: false }),
    question({ id: "i4", question_key: "en.own.keep", required_core: false }),
    question({ id: "i5", meta: { title: null, semantic_class: "UNRESOLVED_DISAGREEMENT" }, required_core: false }),
    question({ id: "i6", required_core: false, question_type: "transaction_calibration" }),
    question({ id: "i7", required_core: false }),
  ];
  const ans = {
    i1: answered,
    i2: { ...answered, answer: "x".repeat(100) },
    i3: answered,
    i4: answered,
    i5: answered,
    i6: { ...answered, calibration: { underwriting_materiality: "critical" } },
  };
  const imp = importantResponses(items, (id) => ans[id], (id) => (id === "i1" ? "MATERIAL_DISAGREEMENT" : undefined));
  assert.deepEqual(imp.map((i) => [i.question.id, i.reason]), [
    ["i1", "differs_from_other_founder"],
    ["i6", "material_case"],
    ["i5", "unresolved_disagreement"],
    ["i3", "high_sensitivity"],
    ["i4", "ownership"],
    ["i2", "substantive"],
    ["i1", "core_answered"],
  ]);
  // a decision that also differs is found under both headings
  const both = importantResponses([question({ id: "s", required_core: false, question_type: "scenario_decision" })], () => answered, () => "MATERIAL_DISAGREEMENT");
  assert.deepEqual(both.map((i) => i.reason), ["differs_from_other_founder", "decision"]);
  assert.deepEqual(analysisLinks(question({ owner: "founder" }), true, true).map((l) => l.tab), ["comparison", "business", "underwriting"]);
  assert.deepEqual(analysisLinks(question({ owner: "founder", group: "calibration" }), false, false).map((l) => l.tab), ["calibration"]);
  assert.deepEqual(analysisLinks(question({ owner: "engineer" }), false, false).map((l) => l.tab), ["engineering"]);
  assert.equal(levelLabel(core), "Core");
  assert.equal(levelLabel(rec), "Recommended");
  assert.equal(levelLabel(cal), "Calibration");
  const now = Date.parse("2026-09-21T12:00:00Z");
  assert.equal(agoLabel("2026-09-21T11:52:00Z", now), "8 minutes ago");
  assert.equal(agoLabel("2026-09-21T11:59:50Z", now), "just now");
  assert.equal(agoLabel("2026-09-21T09:00:00Z", now), "3 hours ago");
  assert.equal(agoLabel("2026-09-18T12:00:00Z", now), "3 days ago");
  assert.equal(agoLabel(null, now), "never");
  assert.equal(hrefFor("responses", { respondent: "a-1", open: undefined }), "/admin/discovery?tab=responses&respondent=a-1");
});

test("tabs and labels", () => {
  const { tabFromParam, TABS, whenLabel, percentLabel, OPEN_KIND_LABEL } = rules;
  assert.equal(tabFromParam(null), "overview");
  assert.equal(tabFromParam("nope"), "overview");
  assert.equal(tabFromParam("browse"), "responses");
  assert.equal(tabFromParam("calibration"), "calibration");
  assert.equal(TABS[1].key, "responses");
  assert.equal(TABS.length, 10);
  assert.equal(whenLabel(null), "never");
  assert.equal(whenLabel("not a date"), "not a date");
  assert.match(whenLabel("2026-09-21T10:00:00Z"), /Sep/);
  assert.equal(percentLabel(1, 4), "25%");
  assert.equal(percentLabel(0, 0), "—");
  assert.equal(OPEN_KIND_LABEL.approve_vs_deny, "Approve vs deny");
});
