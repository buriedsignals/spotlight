// Page edits and decisions through the real server and orchestration seam.
import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { createCaseBridge } from "../case-bridge.mjs";
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
  const unnamed = await json("/api/decide", { decision: "declined", digest: view.digest });
  assert.equal(unnamed.status, 422);
  assert.equal(unnamed.body.code, "reviewer-required");
  view = (await json("/api/edit", { edit: { kind: "reviewer", value: "A. Reviewer" } })).body;
  view = (await json("/api/decide", { decision: "declined", digest: view.digest })).body;
  assert.equal(view.receipt.decision, "declined");
  assert.equal(view.receipt.approved_by, "A. Reviewer");

  // The same session follows the resolver to the next pending gate.
  view = (await json("/api/view")).body;
  assert.equal(view.gate, "ingest");
  assert.deepEqual(view.content.batch, { state: "empty", records: [] });
  view = (await json("/api/edit", { edit: { kind: "reviewer", value: "B. Reviewer" } })).body;
  const decided = await json("/api/decide", { decision: "declined", digest: view.digest });
  assert.equal(decided.status, 200);
  assert.equal(decided.body.receipt.approved_by, "B. Reviewer");
  assert.equal(seamStatus(caseDir).phase, "complete");
});

test("the Investigation gate page requests a follow-up with a note and the member's name", async (t) => {
  const root = tempRoot(t);
  // Blocking failures and an open flag do not prevent a follow-up: it is the answer to them.
  const caseDir = buildCase(root, "case", "investigation_gate");
  const { json } = await authed(t, caseDir);
  let view = (await json("/api/view")).body;
  assert.ok(view.blocking.length > 0);
  const unnamed = await json("/api/follow-up", { instructions: "Find a second filing.", digest: view.digest });
  assert.equal(unnamed.status, 422);
  assert.equal(unnamed.body.code, "reviewer-required");
  view = (await json("/api/edit", { edit: { kind: "reviewer", value: "A. Reviewer" } })).body;
  const empty = await json("/api/follow-up", { instructions: "  ", digest: view.digest });
  assert.equal(empty.status, 400);
  assert.equal(seamStatus(caseDir).phase, "investigation_gate_approval");

  const requested = await json("/api/follow-up", { instructions: "Find a second filing.", digest: view.digest });
  assert.equal(requested.status, 200, JSON.stringify(requested.body));
  assert.equal(requested.body.receipt.decision, "follow_up");
  assert.equal(requested.body.receipt.approved_by, "A. Reviewer");
  assert.equal(requested.body.receipt.channel, "page");
  const status = seamStatus(caseDir);
  assert.equal(status.phase, "investigation");
  assert.deepEqual(status.resume, { instructions: "Find a second filing." });
});

test("the reopened Investigation gate after a follow-up starts with no decider's name", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "investigation_gate");
  const { json } = await authed(t, caseDir);
  let view = (await json("/api/edit", { edit: { kind: "reviewer", value: "A. Reviewer" } })).body;
  const requested = await json("/api/follow-up", { instructions: "Find a second filing.", digest: view.digest });
  assert.equal(requested.status, 200, JSON.stringify(requested.body));

  // The investigator and the fact-checker answer the follow-up; the gate reopens.
  for (const [name, change] of [
    ["findings.json", (doc) => { doc.findings[0].evidence = "A second filing confirms the payment."; }],
    ["fact-check.json", (doc) => { doc.claims[0].notes = "Rechecked after the follow-up."; }],
  ]) {
    const path = join(caseDir, "data", name);
    const doc = JSON.parse(readFileSync(path, "utf8"));
    change(doc);
    writeFileSync(path, JSON.stringify(doc));
  }
  assert.equal(seamStatus(caseDir).phase, "investigation_gate_approval");

  const reopened = await authed(t, caseDir);
  view = (await reopened.json("/api/view")).body;
  assert.equal(view.reviewer, "", "the previous decider's name is not offered as this decision's name");
  assert.equal(view.previous_reviewer, "A. Reviewer", "the previous decider is shown as history");
  const unnamed = await reopened.json("/api/follow-up", { instructions: "Check again.", digest: view.digest });
  assert.equal(unnamed.status, 422);
  assert.equal(unnamed.body.code, "reviewer-required");
  assert.equal(seamStatus(caseDir).phase, "investigation_gate_approval");

  view = (await reopened.json("/api/edit", { edit: { kind: "reviewer", value: "B. Reviewer" } })).body;
  assert.equal(view.reviewer, "B. Reviewer");
  const second = await reopened.json("/api/follow-up", { instructions: "Check again.", digest: view.digest });
  assert.equal(second.status, 200, JSON.stringify(second.body));
  assert.equal(second.body.receipt.approved_by, "B. Reviewer");
});

test("the Report page shows each finding's fact-check verdict and confidence", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "report");
  writeFileSync(join(caseDir, "data", "report-draft.json"), JSON.stringify({
    title: "Draft",
    finding_order: ["F1", "F9"],
    finding_treatments: [
      { finding_id: "F1", headline: "The payment", summary: "One payment." },
      { finding_id: "F9", headline: "Not in the record", summary: "No such finding." },
    ],
  }));
  const { json } = await authed(t, caseDir);
  const view = (await json("/api/view")).body;
  assert.equal(view.gate, "report");
  const [first, unknown] = view.content.treatments;
  assert.deepEqual(
    { id: first.finding_id, verdict: first.verdict, confidence: first.confidence, verified: first.verified },
    { id: "F1", verdict: "unverified", confidence: "low", verified: false },
  );
  assert.deepEqual(
    { id: unknown.finding_id, verdict: unknown.verdict, confidence: unknown.confidence, verified: unknown.verified },
    { id: "F9", verdict: "not checked", confidence: "", verified: false },
  );
});

test("a page nonce that starts with a dash is redeemed", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  const real = createCaseBridge({ caseDir });
  // URL-safe tokens start with "-" about one time in 64; the seam must not read
  // such a nonce as an option.
  const bridge = {
    ...real,
    async issueNonce(gate) {
      await real.issueNonce(gate);
      const nonce = `-${randomBytes(24).toString("base64url")}`;
      const ledgerPath = join(caseDir, "data", "review-nonces.json");
      const ledger = JSON.parse(readFileSync(ledgerPath, "utf8"));
      ledger.nonces[createHash("sha256").update(nonce).digest("hex")] = { gate, channel: "page", used: false };
      writeFileSync(ledgerPath, JSON.stringify(ledger));
      return nonce;
    },
  };
  const { json } = await authed(t, caseDir, { bridge });
  await json("/api/edit", { edit: { kind: "reviewer", value: "A. Reviewer" } });
  const view = (await json("/api/view")).body;
  const accepted = await json("/api/decide", { decision: "approved", digest: view.digest });
  assert.equal(accepted.status, 200, JSON.stringify(accepted.body));
  assert.equal(seamStatus(caseDir).phase, "investigation");
});
