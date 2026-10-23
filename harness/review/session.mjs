// Parent side of the review server: starts the child with keys stripped from
// its environment, holds the capability URL, and opens it in the member's
// browser. Callers get a status, never the URL.
import { spawn as nodeSpawn } from "node:child_process";
import { lstat, realpath } from "node:fs/promises";
import { isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";
import { createCaseBridge, safeChildEnvironment, which as findExecutable } from "./case-bridge.mjs";

const MAX_CONTROL_BYTES = 64 << 10;
const START_TIMEOUT_MS = 60_000;
const EVENTS = new Set(["ready", "session-opened", "decided", "closed", "error"]);
const CHILD = fileURLToPath(new URL("./child.mjs", import.meta.url));

async function realFile(path, label) {
  if (!isAbsolute(path ?? "")) throw new Error(`${label} must be absolute`);
  const canonical = await realpath(path);
  const info = await lstat(canonical);
  if (!info.isFile()) throw new Error(`${label} must be a real file`);
  return canonical;
}

function validateReviewURL(raw) {
  const url = new URL(raw);
  if (
    url.protocol !== "http:" ||
    url.hostname !== "127.0.0.1" ||
    !url.port ||
    url.pathname !== "/" ||
    // A reopened page carries only an open counter, so it loads afresh.
    (url.search && !/^\?open=\d+$/.test(url.search)) ||
    !url.hash ||
    url.username ||
    url.password
  )
    throw new Error("the review child returned an invalid local capability URL");
  return url.href;
}

function validateEvent(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || !EVENTS.has(value.event))
    throw new Error("the review child returned an invalid control event");
  const allowed =
    value.event === "ready"
      ? ["event", "url"]
      : value.event === "closed"
        ? ["event", "reason"]
        : value.event === "error"
          ? ["event", "code"]
          : ["event"];
  if (JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...allowed].sort()))
    throw new Error("the review child returned an invalid control event shape");
  return value;
}

// Stand-in for Bun.spawn(...).exited.
function exited(child) {
  return new Promise((settle) => {
    if (child.exitCode !== null) return settle(child.exitCode);
    child.once("exit", (code) => settle(code ?? 1));
    child.once("error", () => settle(1));
  });
}

export function createReviewSessionManager({
  nodeExecutable = process.execPath,
  childPath = CHILD,
  casesRoot = "",
  idleMs,
  overallMs,
  spawn = nodeSpawn,
  which = findExecutable,
  platform = process.platform,
  env = process.env,
} = {}) {
  let active = null;

  function send(current, message) {
    current.child.stdin.write(`${JSON.stringify(message)}\n`);
  }

  function nextReady(current) {
    return new Promise((settle, reject) => {
      current.waiters.push({ settle, reject });
    });
  }

  async function startChild(caseDir, view) {
    const [node, child] = await Promise.all([
      realFile(nodeExecutable, "Node executable"),
      realFile(childPath, "review child"),
    ]);
    const args = [child, "--case-dir", caseDir];
    if (casesRoot) args.push("--cases-root", casesRoot);
    if (idleMs) args.push("--idle-ms", String(idleMs));
    if (overallMs) args.push("--overall-ms", String(overallMs));
    if (view) args.push("--view", view);
    const process_ = spawn(node, args, {
      env: safeChildEnvironment(env),
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    const current = { caseDir, child: process_, url: "", events: [], waiters: [], exited: exited(process_) };
    active = current;
    // stderr is diagnostic only and is never forwarded.
    process_.stderr.on("data", () => {});
    let bytes = 0;
    let buffer = "";
    const fail = (error) => {
      for (const waiter of current.waiters.splice(0)) waiter.reject(error);
    };
    // A child that stopped reading its commands (EPIPE) is ended, not a crash.
    process_.stdin.on("error", () => {
      process_.kill();
      fail(new Error("the review child stopped reading its commands"));
    });
    process_.stdout.on("data", (chunk) => {
      try {
        bytes += chunk.length;
        if (bytes > MAX_CONTROL_BYTES) throw new Error("the review child exceeded its control-channel bound");
        buffer += chunk.toString("utf8");
        while (buffer.includes("\n")) {
          const at = buffer.indexOf("\n");
          const line = buffer.slice(0, at);
          buffer = buffer.slice(at + 1);
          if (!line) continue;
          const event = validateEvent(JSON.parse(line));
          if (event.event === "ready") {
            current.url = validateReviewURL(event.url);
            current.waiters.shift()?.settle(current.url);
          } else if (event.event === "error") {
            const error = Object.assign(new Error("the review server could not start"), {
              code: String(event.code).slice(0, 40),
            });
            current.waiters.shift()?.reject(error);
          } else {
            current.events.push(event.event === "closed" ? { event: "closed", reason: String(event.reason).slice(0, 40) } : { event: event.event });
          }
        }
      } catch (error) {
        process_.kill();
        fail(error);
      }
    });
    void current.exited.then(() => {
      fail(new Error("the review child exited"));
      if (active === current) active = null;
    });
    return current;
  }

  async function withTimeout(promise, current) {
    let timer;
    try {
      return await Promise.race([
        promise,
        new Promise((_, reject) => {
          timer = setTimeout(() => {
            current.child.kill();
            reject(new Error("the review server did not become ready"));
          }, START_TIMEOUT_MS);
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
  }

  // Start the server for a case, or give a running one a fresh capability.
  // view "wiki" lets it start with no gate pending, for the Wiki changes view.
  async function start(caseDir, view = "") {
    if (!isAbsolute(caseDir ?? "")) throw new Error("the case directory must be absolute");
    if (active && active.caseDir !== caseDir) {
      close();
      await active?.exited;
    }
    if (active) {
      const current = active;
      const ready = nextReady(current);
      send(current, { command: "reopen" });
      await withTimeout(ready, current);
      return { status: "reopened" };
    }
    const current = await startChild(caseDir, view);
    await withTimeout(nextReady(current), current);
    return { status: "ready" };
  }

  // view "wiki" opens the page on its Wiki changes view.
  async function openLocally(view = "") {
    if (!active?.url) return { ok: false, status: "session-expired" };
    const url = new URL(active.url);
    if (view) url.searchParams.set("view", view);
    const discovered =
      platform === "darwin" ? which("open") : platform === "win32" ? which("rundll32.exe") : which("xdg-open");
    if (!discovered) return { ok: false, status: "opener-unavailable" };
    const args = platform === "win32" ? ["url.dll,FileProtocolHandler", url.href] : [url.href];
    try {
      const opener = spawn(discovered, args, {
        stdio: "ignore",
        env: safeChildEnvironment(env),
        windowsHide: true,
      });
      const code = await exited(opener);
      return code === 0 ? { ok: true, status: "opened" } : { ok: false, status: "opener-failed" };
    } catch {
      return { ok: false, status: "opener-failed" };
    }
  }

  function close() {
    if (!active) return;
    try {
      send(active, { command: "close" });
      active.child.stdin.end();
    } catch {
      active.child.kill();
    }
  }

  // The case-root check the review server makes when it starts (the seam's
  // status under the same boundary), for callers that act before starting it.
  async function authorize(caseDir) {
    if (!isAbsolute(caseDir ?? "")) throw new Error("the case directory must be absolute");
    await createCaseBridge({ caseDir, casesRoot, env: safeChildEnvironment(env) }).status();
  }

  return Object.freeze({
    authorize,
    start,
    openLocally,
    close,
    wait: () => active?.exited ?? Promise.resolve(),
    status: () => (active ? { active: true, caseDir: active.caseDir, events: [...active.events] } : { active: false, events: [] }),
  });
}
