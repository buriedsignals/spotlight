// The Wiki changes view: Engine's staged wiki writes, confirmed or discarded
// through a fake `bsig` that keeps Engine's nonce and digest rules.
import assert from "node:assert/strict";
import { chmodSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { createWikiBridge, safeChildEnvironment } from "../case-bridge.mjs";
import { authed, buildCase, tempRoot } from "./helpers.mjs";

const HOSTILE_DIFF = '--- a/notes/a.md\n+++ b/notes/a.md\n@@ -1 +1 @@\n-old\n+</pre><script>alert(1)</script><img src=x onerror=alert(2)>';

// Engine's verbs as the fake implements them: a nonce binds one id and the
// digest at issue time, travels on stdin, and a refusal leaves it unspent.
const FAKE_BSIG = `#!/usr/bin/env node
const { appendFileSync, readFileSync, writeFileSync } = require("node:fs");
const { randomBytes } = require("node:crypto");
const statePath = process.env.FAKE_BSIG_STATE;
const state = JSON.parse(readFileSync(statePath, "utf8"));
const argv = process.argv.slice(2);
let stdin = "";
try { stdin = readFileSync(0, "utf8"); } catch {}
appendFileSync(statePath + ".log", JSON.stringify({ argv, stdin }) + "\\n");
const out = (event, code = 0) => { process.stdout.write(JSON.stringify(event) + "\\n"); writeFileSync(statePath, JSON.stringify(state)); process.exit(code); };
const [, , verb, id] = argv;
if (state.old || !verb.startsWith("staged")) out({ event: "error", message: "usage: bsig wiki <search|read>" }, 4);
if (argv.some((arg) => arg.startsWith("--nonce"))) out({ event: "error", message: "usage" }, 4);
if (verb === "staged") out({ event: "result", data: { tool: verb, result: state.changes } });
const change = state.changes.find((item) => item.id === id);
if (!change) out({ event: "error", message: "wiki: not found" }, 1);
if (verb === "staged-nonce") {
  const nonce = randomBytes(32).toString("hex");
  state.nonces[nonce] = { id, digest: change.digest, used: false };
  out({ event: "result", data: { tool: verb, result: { nonce } } });
}
const refuse = (refusal) => out({ event: "error", message: "wiki: refused " + refusal, data: { refusal } }, 1);
const nonce = JSON.parse(stdin).nonce;
const issued = state.nonces[nonce];
const digest = argv.find((arg) => arg.startsWith("--digest=")).slice(9);
if (state.refuseOnce) { const reason = state.refuseOnce; delete state.refuseOnce; refuse(reason); }
if (!issued) refuse("nonce_unknown");
if (issued.used) refuse("nonce_used");
if (issued.id !== id) refuse("nonce_other_change");
if (digest !== change.digest || issued.digest !== change.digest) refuse("digest_changed");
issued.used = true;
state.changes = state.changes.filter((item) => item.id !== id);
out({ event: "result", data: { tool: verb, result: verb === "staged-apply" ? { op: change.op, applied: true, paths: change.paths } : { id, discarded: true } } });
`;

function change(id, digestChar, diff = "--- a/notes/a.md\n+++ b/notes/a.md\n@@ -1 +1 @@\n-old\n+new") {
  return { id, op: "edit", summary: `Edit ${id}`, created: "2026-10-23T10:00:00Z", paths: ["notes/a.md"], diff, digest: digestChar.repeat(64), stale: false };
}

function fakeBsig(root, state) {
  const script = join(root, "bsig");
  const statePath = join(root, "bsig-state.json");
  writeFileSync(script, FAKE_BSIG);
  chmodSync(script, 0o755);
  writeFileSync(statePath, JSON.stringify({ nonces: {}, ...state }));
  return {
    statePath,
    wiki: createWikiBridge({ env: { ...safeChildEnvironment(), SPOTLIGHT_BSIG: script, FAKE_BSIG_STATE: statePath } }),
    edit: (fn) => {
      const next = JSON.parse(readFileSync(statePath, "utf8"));
      fn(next);
      writeFileSync(statePath, JSON.stringify(next));
    },
    calls: () => readFileSync(`${statePath}.log`, "utf8").trim().split("\n").map((line) => JSON.parse(line)),
    issued: () => Object.keys(JSON.parse(readFileSync(statePath, "utf8")).nonces),
  };
}

test("apply sends Engine's nonce on stdin only and never returns it to the page", async (t) => {
  const root = tempRoot(t);
  const fake = fakeBsig(root, { changes: [change("c1", "a"), change("c2", "b")] });
  const { call } = await authed(t, buildCase(root, "case", "methodology"), { wiki: fake.wiki });
  const responses = [await call("/api/wiki-changes")];
  const listed = JSON.parse(responses[0].text).changes;
  assert.deepEqual(listed.map((item) => item.id), ["c1", "c2"]);
  responses.push(await call("/api/wiki-changes/apply", { id: "c1", digest: listed[0].digest }));
  responses.push(await call("/api/wiki-changes/discard", { id: "c2", digest: listed[1].digest }));
  assert.deepEqual(responses.map((r) => r.status), [200, 200, 200]);
  assert.deepEqual(JSON.parse(responses[2].text).changes, []);

  const nonces = fake.issued();
  assert.equal(nonces.length, 2);
  for (const nonce of nonces) for (const { text } of responses) assert.ok(!text.includes(nonce), "a nonce reached the page");
  const confirms = fake.calls().filter(({ argv }) => argv[2] === "staged-apply" || argv[2] === "staged-discard");
  assert.equal(confirms.length, 2);
  for (const { argv, stdin } of confirms) {
    assert.ok(nonces.includes(JSON.parse(stdin).nonce));
    assert.ok(nonces.every((nonce) => !argv.some((arg) => arg.includes(nonce))), "a nonce reached argv");
  }
});

test("a change edited since the page loaded answers 409 with the fresh list, then confirms on a new nonce", async (t) => {
  const root = tempRoot(t);
  const fake = fakeBsig(root, { changes: [change("c1", "a")] });
  const { json } = await authed(t, buildCase(root, "case", "methodology"), { wiki: fake.wiki });
  const loaded = (await json("/api/wiki-changes")).body.changes[0];
  // Engine refuses the first nonce, then the change is restaged.
  fake.edit((state) => { state.refuseOnce = "nonce_used"; });
  const refused = await json("/api/wiki-changes/apply", { id: "c1", digest: loaded.digest });
  assert.equal(refused.status, 422);
  assert.equal(refused.body.code, "refused");
  fake.edit((state) => { state.changes[0].digest = "c".repeat(64); });

  const stale = await json("/api/wiki-changes/apply", { id: "c1", digest: loaded.digest });
  assert.equal(stale.status, 409);
  assert.equal(stale.body.code, "digest-changed");
  assert.equal(stale.body.changes[0].digest, "c".repeat(64));

  const applied = await json("/api/wiki-changes/apply", { id: "c1", digest: stale.body.changes[0].digest });
  assert.equal(applied.status, 200);
  assert.deepEqual(applied.body.changes, []);
  // A fresh nonce after each refusal: the refused ones were dropped, not retried.
  assert.equal(fake.issued().length, 3);
});

test("diff text containing HTML reaches the page as inert JSON data", async (t) => {
  const root = tempRoot(t);
  const fake = fakeBsig(root, { changes: [change("c1", "a", HOSTILE_DIFF)] });
  const { call } = await authed(t, buildCase(root, "case", "methodology"), { wiki: fake.wiki });
  const { status, text } = await call("/api/wiki-changes");
  assert.equal(status, 200);
  assert.ok(!/[<>]/.test(text), "raw markup in the JSON response");
  assert.equal(JSON.parse(text).changes[0].diff, HOSTILE_DIFF);
});

test("without Engine, or with a bsig that lacks the verb, the list is empty and confirming is refused", async (t) => {
  const root = tempRoot(t);
  const old = fakeBsig(root, { old: true, changes: [change("c1", "a")] });
  const missing = createWikiBridge({ env: { ...safeChildEnvironment(), SPOTLIGHT_BSIG: join(root, "no-bsig") } });
  const caseDir = buildCase(root, "case", "methodology");
  for (const wiki of [old.wiki, missing, null]) {
    const { json } = await authed(t, caseDir, { wiki });
    assert.deepEqual(await json("/api/wiki-changes"), { status: 200, body: { changes: [] } });
    const confirm = await json("/api/wiki-changes/apply", { id: "c1", digest: "a".repeat(64) });
    assert.ok([400, 422].includes(confirm.status));
  }
});
