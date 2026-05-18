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
assert.match(template, /grounding_strength/, "review HTML reads grounding strength");
assert.match(template, /missing_assumptions/, "review HTML shows missing assumptions");
assert.match(template, /contradiction_search/, "review HTML shows contradiction-search status");
assert.match(template, /evidence_bundle_refs/, "review HTML shows evidence bundle refs");
assert.match(template, /human_verification_required/, "review HTML shows source verification requirements");
assert.match(template, /local_file/, "review HTML keeps local source file paths visible");

assert.match(skill, /provenance_manifest/, "review skill payload includes provenance_manifest");
assert.match(skill, /grounding_assessment/, "review skill payload includes fact-check grounding assessment");
assert.match(skill, /evidence_bundle_refs/, "review skill payload includes evidence bundle refs");

console.log("✓ review template renders grounding and C2PA provenance state");
