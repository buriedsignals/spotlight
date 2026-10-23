// Page edits and decisions through the real server and orchestration seam.
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { authed, buildCase, seamStatus, tempRoot } from "./helpers.mjs";

const HOSTILE = '</script><script type="module">fetch("/api/decide",{method:"POST"})</script>';

test("a decision whose digest changed since load is refused and the page gets the reloaded record", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  const { json } = await authed(t, caseDir);
  await json("/api/edit", { edit: { kind: "reviewer", value: "A. Reviewer" } });
  const loaded = (await json("/api/view")).body;

  const methodologyPath = join(caseDir, "data", "methodology.json");
  const plan = JSON.parse(readFileSync(methodologyPath, "utf8"));
  plan.limitations = ["Edited after the page loaded."];
  writeFileSync(methodologyPath, JSON.stringify(plan));

  const refused = await json("/api/decide", { decision: "approved", digest: loaded.digest });
  assert.equal(refused.status, 409);
  assert.equal(refused.body.code, "digest-changed");
  assert.notEqual(refused.body.view.digest, loaded.digest);
  assert.deepEqual(refused.body.view.content.limitations, ["Edited after the page loaded."]);
  assert.equal(seamStatus(caseDir).phase, "methodology_approval");

  const accepted = await json("/api/decide", { decision: "approved", digest: refused.body.view.digest });
  assert.equal(accepted.status, 200);
  assert.equal(accepted.body.receipt.review_digest, refused.body.view.digest);
  assert.equal(accepted.body.receipt.approved_by, "A. Reviewer");
  assert.equal(seamStatus(caseDir).phase, "investigation");

  // Read-only after approval: no further edits or decisions.
  assert.equal((await json("/api/edit", { edit: { kind: "reviewer", value: "B" } })).status, 409);
  assert.equal((await json("/api/decide", { decision: "approved", digest: accepted.body.digest })).status, 409);
});

test("approval is refused while a blocking failure is open", async (t) => {
  const root = tempRoot(t);
  // The unclean fixture's log fails the case validator.
  const caseDir = buildCase(root, "case", "investigation_gate");
  const { json } = await authed(t, caseDir);
  await json("/api/edit", { edit: { kind: "reviewer", value: "A. Reviewer" } });
  const view = (await json("/api/edit", { edit: { kind: "mark", key: "disposition:F1", value: "keep" } })).body;
  assert.ok(view.blocking.length > 0);
  assert.equal(view.open_required, 0);
  const refused = await json("/api/decide", { decision: "approved", digest: view.digest });
  assert.equal(refused.status, 422);
  assert.match(refused.body.message, /blocking failures/);
  assert.equal(seamStatus(caseDir).phase, "investigation_gate_approval");
});

test("an approval needs the member's name", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  const { json } = await authed(t, caseDir);
  const view = (await json("/api/view")).body;
  const refused = await json("/api/decide", { decision: "approved", digest: view.digest });
  assert.equal(refused.status, 422);
  assert.equal(refused.body.code, "reviewer-required");
});

test("case text reaches the page only as escaped JSON data", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "investigation_gate", "--excerpt", HOSTILE);
  const { origin, call } = await authed(t, caseDir);
  const shell = await (await fetch(`${origin}/`)).text();
  assert.ok(!shell.includes("fetch(\"/api/decide\""), "the shell carries no case text");

  const { status, text } = await call("/api/view");
  assert.equal(status, 200);
  assert.ok(!/[<>&]/.test(text), "no raw <, > or & in the JSON body");
  const view = JSON.parse(text);
  assert.equal(view.content.findings[0].evidence, HOSTILE);
});

test("report and ingest decisions from the page complete the case", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "report");
  const { json } = await authed(t, caseDir);
  let view = (await json("/api/view")).body;
  assert.equal(view.gate, "report");
  assert.equal(view.content.draft_ready, false);
  view = (await json("/api/decide", { decision: "declined", digest: view.digest })).body;
  assert.equal(view.receipt.decision, "declined");

  // The same session follows the resolver to the next pending gate.
  view = (await json("/api/view")).body;
  assert.equal(view.gate, "ingest");
  assert.deepEqual(view.content.batch, { state: "empty", records: [] });
  const decided = await json("/api/decide", { decision: "declined", digest: view.digest });
  assert.equal(decided.status, 200);
  assert.equal(seamStatus(caseDir).phase, "complete");
});
