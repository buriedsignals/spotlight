#!/usr/bin/env node
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const template = fs.readFileSync(
  path.join(ROOT, "skills", "review", "references", "template.html"),
  "utf8"
);
const skill = fs.readFileSync(
  path.join(ROOT, "skills", "review", "SKILL.md"),
  "utf8"
);
const feedbackSchema = fs.readFileSync(
  path.join(ROOT, "skills", "review", "references", "feedback-schema.md"),
  "utf8"
);

const scriptBlocks = [...template.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)];
const mainScript = scriptBlocks
  .map((match) => match[1])
  .find((body) => body.includes("function renderProvenance"));

assert.ok(mainScript, "review HTML includes the main render script");
assert.doesNotThrow(() => new Function(mainScript), "review HTML inline script is syntactically valid");
assert.equal(
  (template.match(/\/\*INVESTIGATION_DATA\*\//g) || []).length,
  1,
  "review HTML has exactly one data injection marker"
);

assert.match(template, /id="provenance-block"/, "review HTML exposes a provenance panel");
assert.match(template, /Provenance \/ C2PA/, "review HTML labels C2PA provenance state");
assert.match(template, /function renderProvenance/, "review HTML renders provenance manifests");
assert.match(template, /function renderGrounding/, "review HTML renders grounding detail");
assert.match(template, /support_type/, "review HTML reads support type");
assert.match(template, /missing_assumptions/, "review HTML shows missing assumptions");
assert.match(template, /confidence_cap/, "review HTML shows confidence cap");
assert.match(template, /evidence_bundle_refs/, "review HTML shows evidence bundle refs");
assert.match(template, /human_verification_required/, "review HTML shows source verification requirements");
assert.match(template, /local_file/, "review HTML keeps local source file paths visible");
assert.match(template, /function renderSourceExpressions/, "review HTML renders source-expression chains");
assert.match(template, /Source expression → finding → verdict/, "review HTML labels the complete audit chain");
assert.match(template, /DATA\.source_expressions/, "review HTML joins the source-expression collection");
assert.match(template, /finding_links/, "review HTML joins expressions by authoritative finding link");
assert.match(template, /relation-supports/, "review HTML distinguishes supporting expressions");
assert.match(template, /relation-contradicts/, "review HTML distinguishes contradicting expressions");
assert.match(template, /lifecycle-superseded/, "review HTML styles superseded expression history");
assert.match(template, /original_evidence_bundle_id/, "review HTML shows the source evidence identity");
assert.match(template, /expressionLocator/, "review HTML shows the canonical source locator");
assert.match(template, /expression_fingerprint/, "review HTML shows expression integrity hashes");
assert.match(template, /expression\.attribution/, "review HTML shows printed attribution");
assert.match(template, /expression\.language/, "review HTML shows source language");
assert.match(template, /quote\.textContent = expression\.text/, "exact expression text is inserted as inert text");
assert.doesNotMatch(template, /innerHTML\s*=\s*expression\.text/, "expression text is never assigned directly to innerHTML");

const hostileText = '</script><script>globalThis.__spotlightPwned = true</script>';
const serializedPayload = JSON.stringify({
  project: "hostile-expression-fixture",
  findings: [],
  source_expressions: [{ id: "SX-hostile", text: hostileText, finding_links: [] }],
})
  .replace(/</g, "\\u003c")
  .replace(/\u2028/g, "\\u2028")
  .replace(/\u2029/g, "\\u2029");
const hostileArtifact = template.replace("/*INVESTIGATION_DATA*/", serializedPayload);
assert.doesNotMatch(hostileArtifact, /<script>globalThis\.__spotlightPwned/, "hostile expression text cannot open an executable script");
const injectedPayload = hostileArtifact.match(/<script id="investigation-data" type="application\/json">\s*([\s\S]*?)\s*<\/script>/)[1];
assert.equal(JSON.parse(injectedPayload).source_expressions[0].text, hostileText, "script-safe serialization preserves exact expression text");

for (const category of [
  "omitted_context",
  "attribution_error",
  "wrong_relation",
  "mistranscription",
  "bad_locator",
  "stale_source",
  "other",
]) {
  assert.match(template, new RegExp(category), `review HTML offers ${category} feedback`);
  assert.match(feedbackSchema, new RegExp(category), `feedback contract accepts ${category}`);
}
assert.match(template, /expression_id: group\.dataset\.expressionId/, "feedback records the affected expression ID");
assert.match(template, /finding_id: group\.dataset\.findingId/, "feedback records the affected finding ID");

assert.match(skill, /provenance_manifest/, "review skill payload includes provenance_manifest");
assert.match(skill, /grounding_assessment/, "review skill payload includes fact-check grounding assessment");
assert.match(skill, /evidence_bundle_refs/, "review skill payload includes evidence bundle refs");
assert.match(skill, /source-expressions\.json/, "review skill reads the source-expression artifact");
assert.match(skill, /active, superseded, and withdrawn/, "review skill preserves historical expressions");
assert.match(skill, /\\u003c/, "review skill requires script-safe JSON serialization");
assert.match(skill, /validate-case\.py/, "expression feedback passes case validation");
assert.match(skill, /only actor in this loop that may change a verdict/, "only independent fact-check may change verdicts");
assert.match(feedbackSchema, /dangling expression or finding target/, "dangling expression feedback is skipped explicitly");
assert.match(feedbackSchema, /findings_feedback/, "legacy finding-level feedback remains supported");

console.log("✓ review template renders provenance, grounding, and safe source-expression feedback chains");
