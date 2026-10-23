// Opening, reopening and the MCP tool, through the real child process.
import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { CHECKOUT_ROOT, which } from "../case-bridge.mjs";
import { createMcpHandler } from "../mcp.mjs";
import { createReviewOpener, createWikiChangesOpener } from "../open.mjs";
import { createReviewSessionManager } from "../session.mjs";
import { api, approveMethodology, buildCase, change, fakeBsig, openSession, recordingOpener, tempRoot } from "./helpers.mjs";

function openedUrls(record) {
  return existsSync(record) ? readFileSync(record, "utf8").trim().split("\n").filter(Boolean) : [];
}

function manager(t, root, options = {}) {
  const opener = recordingOpener(root);
  // No Engine unless a test supplies a fake bsig.
  const env = { ...process.env, SPOTLIGHT_BSIG: join(root, "no-bsig") };
  const sessions = createReviewSessionManager({ which: () => opener.script, env, ...options });
  t.after(async () => {
    sessions.close();
    await sessions.wait();
  });
  return { sessions, record: opener.record };
}

async function viewThrough(url) {
  const parsed = new URL(url);
  const { cookie, response } = await openSession(parsed.origin, parsed.hash.slice(1));
  assert.equal(response.status, 200);
  const { status, text } = await api(parsed.origin, cookie)("/api/view");
  assert.equal(status, 200);
  return { view: JSON.parse(text), origin: parsed.origin, cookie };
}

test("a page reopened after the session expired shows the marks saved before", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "investigation_gate", "--clean");
  // Long enough that the edits below (each runs the Python seam) finish before
  // the idle limit ends the session, even on a loaded machine.
  const { sessions, record } = manager(t, root, { idleMs: 6000 });
  assert.deepEqual(await sessions.start(caseDir), { status: "ready" });
  assert.equal((await sessions.openLocally()).status, "opened");
  const first = await viewThrough(openedUrls(record)[0]);
  const edited = await api(first.origin, first.cookie)("/api/edit", {
    edit: { kind: "mark", key: "selection:F1", value: false },
  });
  assert.equal(edited.status, 200);
  await api(first.origin, first.cookie)("/api/edit", { edit: { kind: "note", key: "selection:F1", value: "Hold back." } });

  await sessions.wait(); // idle expiry ends the child
  assert.deepEqual(await sessions.start(caseDir), { status: "ready" });
  await sessions.openLocally();
  const urls = openedUrls(record);
  assert.equal(urls.length, 2);
  assert.notEqual(urls[0], urls[1]);
  const { view } = await viewThrough(urls[1]);
  const item = view.items.find((entry) => entry.key === "selection:F1");
  assert.deepEqual([item.suggested, item.final, item.note], [true, false, "Hold back."]);
});

test("reopening a running page issues a fresh capability and ends the old session", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  const { sessions, record } = manager(t, root);
  await sessions.start(caseDir);
  await sessions.openLocally();
  const first = await viewThrough(openedUrls(record)[0]);
  assert.deepEqual(await sessions.start(caseDir), { status: "reopened" });
  await sessions.openLocally();
  const second = openedUrls(record)[1];
  assert.equal(new URL(second).origin, first.origin);
  // A tab already on the page loads the reopened link as a new document,
  // which a change to the fragment alone would not do.
  const firstUrl = new URL(openedUrls(record)[0]);
  assert.notEqual(`${new URL(second).pathname}${new URL(second).search}`, `${firstUrl.pathname}${firstUrl.search}`);
  assert.equal((await api(first.origin, first.cookie)("/api/view")).status, 403);
  await viewThrough(second);
});

test("a review child that stops reading its commands fails the reopen instead of crashing the server", async (t) => {
  const root = tempRoot(t);
  const child = join(root, "deaf-child.mjs");
  // Closes its command channel, reports ready, then lingers like a stuck child.
  writeFileSync(
    child,
    'import { closeSync } from "node:fs";\n' +
      "closeSync(0);\n" +
      'process.stdout.write(JSON.stringify({ event: "ready", url: "http://127.0.0.1:9/#capability" }) + "\\n");\n' +
      "setTimeout(() => {}, 3000);\n",
  );
  const { sessions } = manager(t, root, { childPath: child });
  const caseDir = join(root, "case");
  assert.deepEqual(await sessions.start(caseDir), { status: "ready" });

  await assert.rejects(sessions.start(caseDir), /stopped reading|exited/);
});

test("opening refuses a case with no pending gate", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  const { sessions } = manager(t, root);
  // Approved on the command line, the case moves on to investigation.
  execFileSync(which("python3"), [
    join(CHECKOUT_ROOT, "scripts", "spotlight-orchestration.py"), "approve", "methodology",
    "--approved-by", "journalist:fixture", "--approved-at", "2026-08-23T12:00:00Z", caseDir,
  ]);
  const open = createReviewOpener({ sessions });
  assert.deepEqual(await open({ caseDir }), { status: "no-pending-gate" });
});

test("open_spotlight_review opens the page and its result carries no URL", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  const { sessions, record } = manager(t, root);
  const handle = createMcpHandler({ open: createReviewOpener({ sessions }), openWiki: createWikiChangesOpener({ sessions }) });

  const listed = await handle({ jsonrpc: "2.0", id: 1, method: "tools/list" });
  assert.deepEqual(listed.result.tools.map((tool) => tool.name), ["open_spotlight_review", "open_spotlight_wiki_changes"]);

  for (const id of [2, 3]) {
    const result = await handle({
      jsonrpc: "2.0",
      id,
      method: "tools/call",
      params: { name: "open_spotlight_review", arguments: { case_dir: caseDir } },
    });
    const text = JSON.stringify(result);
    const url = openedUrls(record).at(-1);
    assert.ok(url, "the browser opener received the page URL");
    const capability = new URL(url).hash.slice(1);
    assert.equal(result.result.isError, false);
    assert.equal(result.result.structuredContent.review, "opened");
    for (const secret of [url, new URL(url).origin, new URL(url).port, capability, "127.0.0.1", "http"]) {
      assert.ok(!text.includes(secret), `tool result leaks ${secret}`);
    }
  }
  assert.equal(openedUrls(record).length, 2);
});

test("open_spotlight_wiki_changes opens the Wiki changes view with no gate pending, and returns no URL or nonce", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  approveMethodology(caseDir);
  const fake = fakeBsig(root, { changes: [change("c1", "a")] });
  const { sessions, record } = manager(t, root, { env: fake.env });
  const handle = createMcpHandler({ open: createReviewOpener({ sessions }), openWiki: createWikiChangesOpener({ sessions, env: fake.env }) });
  const call = (id) => handle({ jsonrpc: "2.0", id, method: "tools/call", params: { name: "open_spotlight_wiki_changes", arguments: { case_dir: caseDir } } });

  const result = await call(1);
  assert.equal(result.result.structuredContent.review, "opened");
  const url = openedUrls(record).at(-1);
  assert.equal(new URL(url).searchParams.get("view"), "wiki");
  const { view, origin, cookie } = await viewThrough(url);
  assert.equal(view.gate, "");
  const applied = await api(origin, cookie)("/api/wiki-changes/apply", { id: "c1", digest: "a".repeat(64) });
  assert.equal(applied.status, 200);

  const text = JSON.stringify(result) + JSON.stringify(await call(2));
  const nonces = fake.issued();
  assert.equal(nonces.length, 1);
  for (const secret of [url, new URL(url).port, new URL(url).hash.slice(1), "127.0.0.1", "http", ...nonces])
    assert.ok(!text.includes(secret), `tool result leaks ${secret}`);
  // The second call found nothing staged after the apply.
  assert.match(text, /No wiki changes are staged/);
  assert.equal(openedUrls(record).length, 1);
});

test("open_spotlight_wiki_changes opens no browser when nothing is staged, even with a gate pending", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  const fake = fakeBsig(root, { changes: [] });
  const { sessions, record } = manager(t, root, { env: fake.env });
  mkdirSync(join(root, "old"));
  const old = fakeBsig(join(root, "old"), { old: true, changes: [change("c1", "a")] });
  for (const env of [fake.env, old.env, { ...process.env, SPOTLIGHT_BSIG: join(root, "no-bsig") }]) {
    const open = createWikiChangesOpener({ sessions, env });
    assert.deepEqual(await open({ caseDir }), { status: "nothing-staged" });
  }
  assert.equal(sessions.status().active, false);
  assert.deepEqual(openedUrls(record), []);
});

test("open_spotlight_wiki_changes leaves a gate page that is already open alone", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  const fake = fakeBsig(root, { changes: [change("c1", "a")] });
  const { sessions, record } = manager(t, root, { env: fake.env });
  assert.equal((await createReviewOpener({ sessions })({ caseDir })).status, "opened");
  const { origin, cookie } = await viewThrough(openedUrls(record).at(-1));

  assert.deepEqual(await createWikiChangesOpener({ sessions, env: fake.env })({ caseDir }), { status: "already-open" });
  assert.equal(openedUrls(record).length, 1);
  // The member's session survives, so nothing typed on the gate page is lost.
  assert.equal((await api(origin, cookie)("/api/view")).status, 200);
});

test("an Engine failure is reported, not taken for nothing staged, and the gate page still works", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  const fake = fakeBsig(root, { fail: "wiki: permission denied", changes: [] });
  const { sessions, record } = manager(t, root, { env: fake.env });
  const handle = createMcpHandler({ open: createReviewOpener({ sessions }), openWiki: createWikiChangesOpener({ sessions, env: fake.env }) });
  const result = await handle({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "open_spotlight_wiki_changes", arguments: { case_dir: caseDir } } });
  assert.equal(result.result.structuredContent.review, "wiki-unreadable");
  // Engine's own text never reaches the model.
  assert.ok(!JSON.stringify(result).includes("permission denied"));
  assert.deepEqual(openedUrls(record), []);

  await createReviewOpener({ sessions })({ caseDir });
  const { origin, cookie } = await viewThrough(openedUrls(record).at(-1));
  const listed = await api(origin, cookie)("/api/wiki-changes");
  assert.equal(listed.status, 502);
  assert.match(JSON.parse(listed.text).message, /Could not read staged changes: permission denied/);
});

test("open_spotlight_wiki_changes refuses a case outside the authorized root before asking Engine", async (t) => {
  const root = tempRoot(t);
  mkdirSync(join(root, "cases"));
  const outside = buildCase(root, "outside", "methodology");
  const { sessions, record } = manager(t, root, { casesRoot: join(root, "cases") });
  let asked = false;
  const wiki = { staged: async () => { asked = true; return [change("c1", "a")]; } };
  assert.deepEqual(await createWikiChangesOpener({ sessions, wiki })({ caseDir: outside }), { status: "start-failed" });
  assert.equal(asked, false);
  assert.deepEqual(openedUrls(record), []);
});

test("open_spotlight_wiki_changes treats another spelling of the open case as the same case", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  symlinkSync(caseDir, join(root, "alias"));
  const fake = fakeBsig(root, { changes: [change("c1", "a")] });
  const { sessions, record } = manager(t, root, { env: fake.env });
  assert.equal((await createReviewOpener({ sessions })({ caseDir: join(root, "alias") })).status, "opened");
  const { origin, cookie } = await viewThrough(openedUrls(record).at(-1));

  assert.deepEqual(await createWikiChangesOpener({ sessions, env: fake.env })({ caseDir }), { status: "already-open" });
  assert.equal((await api(origin, cookie)("/api/view")).status, 200);
});

test("open_spotlight_wiki_changes leaves another case's open review alone", async (t) => {
  const root = tempRoot(t);
  const other = buildCase(root, "other", "methodology");
  const caseDir = buildCase(root, "case", "methodology");
  const fake = fakeBsig(root, { changes: [change("c1", "a")] });
  const { sessions, record } = manager(t, root, { env: fake.env });
  await createReviewOpener({ sessions })({ caseDir: other });
  const { origin, cookie } = await viewThrough(openedUrls(record).at(-1));

  assert.deepEqual(await createWikiChangesOpener({ sessions, env: fake.env })({ caseDir }), { status: "other-review-open" });
  assert.equal(openedUrls(record).length, 1);
  assert.equal((await api(origin, cookie)("/api/view")).status, 200);
});

test("open_spotlight_review leaves a Wiki-changes-only page alone when no gate is pending", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  approveMethodology(caseDir);
  const fake = fakeBsig(root, { changes: [change("c1", "a")] });
  const { sessions, record } = manager(t, root, { env: fake.env });
  assert.equal((await createWikiChangesOpener({ sessions, env: fake.env })({ caseDir })).status, "opened");
  const { origin, cookie } = await viewThrough(openedUrls(record).at(-1));

  assert.deepEqual(await createReviewOpener({ sessions })({ caseDir }), { status: "no-pending-gate" });
  assert.equal(openedUrls(record).length, 1);
  assert.equal((await api(origin, cookie)("/api/view")).status, 200);
});

test("a page the browser could not open is closed, not left to read as already open", async (t) => {
  const root = tempRoot(t);
  const caseDir = buildCase(root, "case", "methodology");
  const failing = join(root, "failing-open.sh");
  writeFileSync(failing, "#!/bin/sh\nexit 1\n");
  chmodSync(failing, 0o755);
  const fake = fakeBsig(root, { changes: [change("c1", "a")] });
  const { sessions } = manager(t, root, { env: fake.env, which: () => failing });
  assert.equal((await createReviewOpener({ sessions })({ caseDir })).status, "opener-failed");
  assert.equal(sessions.status().active, false);
  assert.equal((await createWikiChangesOpener({ sessions, env: fake.env })({ caseDir })).status, "opener-failed");
});

test("the stdio MCP server answers initialize and lists its tools", async () => {
  const child = spawn(process.execPath, [fileURLToPath(new URL("../mcp.mjs", import.meta.url))], {
    stdio: ["pipe", "pipe", "ignore"],
  });
  const lines = [];
  let buffer = "";
  child.stdout.on("data", (chunk) => {
    buffer += chunk;
    let at;
    while ((at = buffer.indexOf("\n")) >= 0) {
      lines.push(JSON.parse(buffer.slice(0, at)));
      buffer = buffer.slice(at + 1);
    }
  });
  const send = (message) => child.stdin.write(`${JSON.stringify(message)}\n`);
  send({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "test", version: "0" } } });
  send({ jsonrpc: "2.0", method: "notifications/initialized" });
  send({ jsonrpc: "2.0", id: 2, method: "tools/list" });
  child.stdin.end();
  await new Promise((settle) => child.on("exit", settle));
  assert.equal(lines.length, 2);
  assert.equal(lines[0].result.serverInfo.name, "spotlight-review");
  assert.deepEqual(lines[1].result.tools.map((tool) => tool.name), ["open_spotlight_review", "open_spotlight_wiki_changes"]);
});
