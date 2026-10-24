// Shared fixtures: real offline cases built by the Python fixture builder and a
// real review server talking to the real orchestration seam.
import { execFileSync } from "node:child_process";
import { chmodSync, copyFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { CHECKOUT_ROOT, createCaseBridge, createWikiBridge, safeChildEnvironment, which } from "../case-bridge.mjs";
import { startReviewServer } from "../server.mjs";

const BUILDER = join(CHECKOUT_ROOT, "tests", "fixtures", "review_case.py");
const PYTHON = which("python3");

export function tempRoot(t) {
  const root = mkdtempSync(join(tmpdir(), "spotlight-review-test-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return root;
}

export function buildCase(root, name, stage, ...flags) {
  const caseDir = join(root, name);
  execFileSync(PYTHON, [BUILDER, caseDir, stage, ...flags], { stdio: "pipe" });
  return caseDir;
}

export function seamStatus(caseDir) {
  const out = execFileSync(
    PYTHON,
    [join(CHECKOUT_ROOT, "scripts", "spotlight-orchestration.py"), "status", "--json", caseDir],
    { encoding: "utf8" },
  );
  return JSON.parse(out);
}

export async function startFor(t, caseDir, { bridgeEnv, ...options } = {}) {
  const bridge = createCaseBridge(bridgeEnv ? { caseDir, env: bridgeEnv } : { caseDir });
  const server = await startReviewServer({ bridge, ...options });
  t.after(async () => {
    server.close("closed");
    await server.closed;
  });
  const url = new URL(server.url);
  return { server, origin: url.origin, capability: url.hash.slice(1) };
}

export async function openSession(origin, capability) {
  const response = await fetch(`${origin}/session`, {
    method: "POST",
    headers: { origin, "content-type": "application/json" },
    body: JSON.stringify({ capability }),
  });
  return { response, cookie: (response.headers.get("set-cookie") ?? "").split(";")[0] };
}

export function api(origin, cookie) {
  return async (path, body = {}) => {
    const response = await fetch(`${origin}${path}`, {
      method: "POST",
      headers: { origin, cookie, "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    return { status: response.status, text: await response.text() };
  };
}

export async function authed(t, caseDir, options) {
  const started = await startFor(t, caseDir, options);
  const { cookie } = await openSession(started.origin, started.capability);
  const call = api(started.origin, cookie);
  return { ...started, cookie, call, json: async (path, body) => {
    const { status, text } = await call(path, body);
    return { status, body: JSON.parse(text) };
  } };
}

export function approveMethodology(caseDir) {
  execFileSync(PYTHON, [
    join(CHECKOUT_ROOT, "scripts", "spotlight-orchestration.py"), "approve", "methodology",
    "--approved-by", "journalist:fixture", "--approved-at", "2026-08-23T12:00:00Z", caseDir,
  ]);
}

// A stand-in browser opener that records the URL it was asked to open.
export function recordingOpener(root) {
  const record = join(root, "opened-urls.txt");
  const script = join(root, "record-open.sh");
  writeFileSync(script, `#!/bin/sh\nprintf '%s\\n' "$1" >> '${record}'\n`);
  chmodSync(script, 0o755);
  return { script, record };
}

// Engine's verbs as the fake implements them: a nonce binds one id and the
// digest at issue time, travels on stdin, and a refusal leaves it unspent;
// resolve answers state.resolve[name].
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
if (state.old || !(verb.startsWith("staged") || verb === "resolve")) out({ event: "error", message: "usage: bsig wiki <search|read>" }, 4);
if (state.fail) out({ event: "error", message: state.fail }, 1);
// The register's answer for one mention, its arguments read from stdin.
if (verb === "resolve" && (state.failFor ?? []).includes(JSON.parse(stdin).name)) out({ event: "error", message: "wiki: lookup failed" }, 1);
if (verb === "resolve") out({ event: "result", data: { tool: verb, result: (state.resolve ?? {})[JSON.parse(stdin).name] ?? { status: "unknown" } } });
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
if (state.slowMs) Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, state.slowMs);
if (state.failOnce) { const message = state.failOnce; delete state.failOnce; out({ event: "error", message }, 1); }
if (state.refuseOnce) { const reason = state.refuseOnce; delete state.refuseOnce; refuse(reason); }
if (!issued) refuse("nonce_unknown");
if (issued.used) refuse("nonce_used");
if (issued.id !== id) refuse("nonce_other_change");
if (digest !== change.digest || issued.digest !== change.digest) refuse("digest_changed");
issued.used = true;
state.changes = state.changes.filter((item) => item.id !== id);
if (state.breakListing) state.fail = state.breakListing;
out({ event: "result", data: { tool: verb, result: verb === "staged-apply" ? { op: change.op, applied: true, paths: change.paths } : { id, discarded: true } } });
`;

export function change(id, digestChar, diff = "--- a/notes/a.md\n+++ b/notes/a.md\n@@ -1 +1 @@\n-old\n+new") {
  return { id, op: "edit", summary: `Edit ${id}`, created: "2026-10-23T10:00:00Z", paths: ["notes/a.md"], diff, digest: digestChar.repeat(64), stale: false };
}

export function fakeBsig(root, state) {
  const script = join(root, "bsig");
  const statePath = join(root, "bsig-state.json");
  writeFileSync(script, FAKE_BSIG);
  chmodSync(script, 0o755);
  writeFileSync(statePath, JSON.stringify({ nonces: {}, ...state }));
  const env = { ...process.env, SPOTLIGHT_BSIG: script, FAKE_BSIG_STATE: statePath };
  return {
    env,
    wiki: createWikiBridge({ env: safeChildEnvironment(env) }),
    edit: (fn) => {
      const next = JSON.parse(readFileSync(statePath, "utf8"));
      fn(next);
      writeFileSync(statePath, JSON.stringify(next));
    },
    calls: () => readFileSync(`${statePath}.log`, "utf8").trim().split("\n").map((line) => JSON.parse(line)),
    issued: () => Object.keys(JSON.parse(readFileSync(statePath, "utf8")).nonces),
  };
}

// The shared stand-in Engine `bsig` for case search
// (tests/fixtures/case_search_bsig.py): it answers `wiki case-search` with the
// NDJSON result contract, or refuses as Engine does while its switch is off.
// Other scenario keys (args_log, mode, slow_s, pid_log) pass through.
export function standInBsig(root, { off = false, ...scenario } = {}) {
  const script = join(root, off ? "bsig-off" : "bsig");
  copyFileSync(join(CHECKOUT_ROOT, "tests", "fixtures", "case_search_bsig.py"), script);
  chmodSync(script, 0o755);
  writeFileSync(`${script}.json`, JSON.stringify({
    mode: off ? "off" : "ok",
    passages: [{ path: "research/registry.md", chunk: 0, text: "<img src=x onerror=alert(1)> Registry entry on {claim}", score: 12.5 }],
    ...scenario,
  }));
  return script;
}
