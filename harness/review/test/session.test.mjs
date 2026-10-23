// Opening, reopening and the MCP tool, through the real child process.
import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { CHECKOUT_ROOT, which } from "../case-bridge.mjs";
import { createMcpHandler } from "../mcp.mjs";
import { createReviewOpener } from "../open.mjs";
import { createReviewSessionManager } from "../session.mjs";
import { api, buildCase, openSession, recordingOpener, tempRoot } from "./helpers.mjs";

function openedUrls(record) {
  return existsSync(record) ? readFileSync(record, "utf8").trim().split("\n").filter(Boolean) : [];
}

function manager(t, root, options = {}) {
  const opener = recordingOpener(root);
  const sessions = createReviewSessionManager({ which: () => opener.script, ...options });
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
  const { sessions, record } = manager(t, root, { idleMs: 1500 });
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
  const handle = createMcpHandler({ open: createReviewOpener({ sessions }) });

  const listed = await handle({ jsonrpc: "2.0", id: 1, method: "tools/list" });
  assert.deepEqual(listed.result.tools.map((tool) => tool.name), ["open_spotlight_review"]);

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

test("the stdio MCP server answers initialize and lists one tool", async () => {
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
  assert.deepEqual(lines[1].result.tools.map((tool) => tool.name), ["open_spotlight_review"]);
});
