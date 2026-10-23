// Loopback boundary of the review server, ported from Splash's studio tests:
// Host, Origin, capability swap, cookie, CSP nonce and idle/overall limits.
import assert from "node:assert/strict";
import { request } from "node:http";
import { test } from "node:test";
import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { safeChildEnvironment, spotlightPython, which } from "../case-bridge.mjs";
import { api, buildCase, openSession, startFor, tempRoot } from "./helpers.mjs";

function rawGet(origin, host) {
  const url = new URL(origin);
  return new Promise((settle, reject) => {
    const req = request({ hostname: url.hostname, port: url.port, path: "/", method: "GET", headers: { host } }, (res) => {
      res.resume();
      res.on("end", () => settle(res.statusCode));
    });
    req.on("error", reject);
    req.end();
  });
}

test("binds loopback, checks Host, and stamps a fresh CSP nonce on the shell's own tags", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  const { server, origin } = await startFor(t, caseDir);
  assert.equal(new URL(server.url).hostname, "127.0.0.1");
  assert.equal(await rawGet(origin, "localhost.example:80"), 421);

  const nonces = [];
  for (let i = 0; i < 2; i += 1) {
    const page = await fetch(`${origin}/`);
    assert.equal(page.status, 200);
    assert.equal(page.headers.get("cache-control"), "no-store, max-age=0");
    const csp = page.headers.get("content-security-policy");
    const nonce = /script-src 'nonce-([A-Za-z0-9_-]+)'/.exec(csp)?.[1];
    assert.ok(nonce, "CSP names a script nonce");
    assert.match(csp, new RegExp(`style-src 'nonce-${nonce}'`));
    assert.match(csp, /default-src 'none'/);
    const html = await page.text();
    // Exactly the shell's executable script and its style carry the nonce.
    assert.equal(html.split(`nonce="${nonce}"`).length - 1, 2);
    assert.match(html, new RegExp(`<script type="module" nonce="${nonce}">`));
    assert.match(html, new RegExp(`<style nonce="${nonce}">`));
    nonces.push(nonce);
  }
  assert.notEqual(nonces[0], nonces[1]);
});

test("swaps the one-time capability for an HttpOnly session cookie and refuses replay", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  const first = await startFor(t, caseDir);

  const before = await api(first.origin, "")("/api/view");
  assert.equal(before.status, 403);

  const opened = await openSession(first.origin, first.capability);
  assert.equal(opened.response.status, 200);
  const setCookie = opened.response.headers.get("set-cookie");
  assert.match(setCookie, /^spotlight_review_\d+=[A-Za-z0-9_-]+; HttpOnly; SameSite=Strict; Path=\/$/);
  assert.equal((await api(first.origin, opened.cookie)("/api/view")).status, 200);

  // The capability works once; a resumed page with its cookie stays signed in.
  const replay = await openSession(first.origin, first.capability);
  assert.equal(replay.response.status, 403);
  const resumed = await fetch(`${first.origin}/session`, {
    method: "POST",
    headers: { origin: first.origin, cookie: opened.cookie, "content-type": "application/json" },
    body: JSON.stringify({ capability: "" }),
  });
  assert.equal(resumed.status, 200);

  // Two servers never share a cookie name.
  const second = await startFor(t, caseDir);
  const secondSession = await openSession(second.origin, second.capability);
  assert.notEqual(secondSession.cookie.split("=")[0], opened.cookie.split("=")[0]);
  assert.equal((await api(second.origin, opened.cookie)("/api/view")).status, 403);
});

test("refuses POSTs from another origin, before and after sign-in", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  const { origin, capability } = await startFor(t, caseDir);
  const forged = await fetch(`${origin}/session`, {
    method: "POST",
    headers: { origin: "http://evil.example", "content-type": "application/json" },
    body: JSON.stringify({ capability }),
  });
  assert.equal(forged.status, 403);
  // The forged attempt did not spend the capability.
  const { cookie, response } = await openSession(origin, capability);
  assert.equal(response.status, 200);
  const crossSite = await fetch(`${origin}/api/view`, {
    method: "POST",
    headers: { origin: "http://evil.example", cookie, "content-type": "application/json" },
    body: "{}",
  });
  assert.equal(crossSite.status, 403);
  const missing = await fetch(`${origin}/api/view`, {
    method: "POST",
    headers: { cookie, "content-type": "application/json" },
    body: "{}",
  });
  assert.equal(missing.status, 403);
});

test("an idle session expires and its capability and cookie stop working", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  const { server, origin, capability } = await startFor(t, caseDir, { idleMs: 300 });
  assert.deepEqual(await server.closed, { reason: "expired" });
  await assert.rejects(openSession(origin, capability));
});

test("the overall limit closes a signed-in session that is still active", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  const { server, origin, capability } = await startFor(t, caseDir, { idleMs: 60_000, overallMs: 1500 });
  const { cookie } = await openSession(origin, capability);
  const call = api(origin, cookie);
  assert.equal((await call("/api/view")).status, 200);
  assert.deepEqual(await server.closed, { reason: "expired" });
});

test("the seam runs the Spotlight interpreter: SPOTLIGHT_PYTHON, else the checkout venv, else python3", (t) => {
  const root = tempRoot(t);
  const PATH = process.env.PATH;
  assert.equal(spotlightPython({ env: { PATH, SPOTLIGHT_PYTHON: "/opt/spotlight/bin/python" }, root }), "/opt/spotlight/bin/python");
  assert.throws(() => spotlightPython({ env: { PATH, SPOTLIGHT_PYTHON: "python3.12" }, root }), /absolute path/);
  assert.equal(spotlightPython({ env: { PATH }, root }), which("python3", PATH));
  const venvPython = join(root, ".venv", "bin", "python");
  mkdirSync(join(root, ".venv", "bin"), { recursive: true });
  writeFileSync(venvPython, "#!/bin/sh\n");
  chmodSync(venvPython, 0o755);
  assert.equal(spotlightPython({ env: { PATH }, root }), venvPython);
});

test("the child environment drops keys, tokens and loader hooks", () => {
  const env = safeChildEnvironment({
    PATH: "/usr/bin",
    HOME: "/home/member",
    OPENROUTER_API_KEY: "k",
    OSINT_NAV_API_KEY: "k",
    GITHUB_TOKEN: "t",
    NOOSPHERE_C2PA_SECRET: "s",
    NODE_OPTIONS: "--require x",
    PYTHONPATH: "/tmp/x",
  });
  assert.deepEqual(Object.keys(env).sort(), ["HOME", "PATH", "PYTHONNOUSERSITE"]);
});
