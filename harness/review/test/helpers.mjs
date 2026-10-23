// Shared fixtures: real offline cases built by the Python fixture builder and a
// real review server talking to the real orchestration seam.
import { execFileSync } from "node:child_process";
import { chmodSync, copyFileSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { CHECKOUT_ROOT, createCaseBridge, which } from "../case-bridge.mjs";
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

// A stand-in browser opener that records the URL it was asked to open.
export function recordingOpener(root) {
  const record = join(root, "opened-urls.txt");
  const script = join(root, "record-open.sh");
  writeFileSync(script, `#!/bin/sh\nprintf '%s\\n' "$1" >> '${record}'\n`);
  chmodSync(script, 0o755);
  return { script, record };
}

// The shared stand-in Engine `bsig` for case search
// (tests/fixtures/case_search_bsig.py): it answers `wiki case-search` with the
// NDJSON result contract, or refuses as Engine does while its switch is off.
export function standInBsig(root, { off = false } = {}) {
  const script = join(root, off ? "bsig-off" : "bsig");
  copyFileSync(join(CHECKOUT_ROOT, "tests", "fixtures", "case_search_bsig.py"), script);
  chmodSync(script, 0o755);
  writeFileSync(`${script}.json`, JSON.stringify({
    mode: off ? "off" : "ok",
    passages: [{ path: "research/registry.md", chunk: 0, text: "<img src=x onerror=alert(1)> Registry entry on {claim}", score: 12.5 }],
  }));
  return script;
}
