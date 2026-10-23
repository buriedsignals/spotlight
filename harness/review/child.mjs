#!/usr/bin/env node
// Child process that runs one case's review server. The parent starts it with
// keys stripped from the environment and talks to it over a small JSON-lines
// control channel: the child reports {event:"ready", url}, lifecycle events and
// {event:"closed"}; the parent sends {command:"reopen"} or {command:"close"}.
// Nothing written here carries case text.
import { createCaseBridge, createWikiBridge } from "./case-bridge.mjs";
import { startReviewServer } from "./server.mjs";

const MAX_CONTROL_BYTES = 4096;
const ALLOWED = new Set(["--case-dir", "--cases-root", "--idle-ms", "--overall-ms"]);

function option(argv, name, fallback) {
  const at = argv.indexOf(name);
  if (at < 0) {
    if (fallback === undefined) throw new Error(`${name} is required`);
    return fallback;
  }
  return argv[at + 1];
}

function numberOption(argv, name, fallback) {
  const value = Number(option(argv, name, String(fallback)));
  if (!Number.isFinite(value) || value <= 0) throw new Error(`${name} must be a positive number`);
  return value;
}

function event(name, data = {}) {
  process.stdout.write(`${JSON.stringify({ event: name, ...data })}\n`);
}

async function monitorControl(server) {
  let bytes = 0;
  let text = "";
  for await (const chunk of process.stdin) {
    bytes += chunk.length;
    if (bytes > MAX_CONTROL_BYTES) return server.close("invalid-control");
    text += chunk.toString("utf8");
    while (text.includes("\n")) {
      const at = text.indexOf("\n");
      const line = text.slice(0, at);
      text = text.slice(at + 1);
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        return server.close("invalid-control");
      }
      if (!message || typeof message !== "object" || Array.isArray(message) || Object.keys(message).length !== 1)
        return server.close("invalid-control");
      if (message.command === "close") return server.close("parent-close");
      if (message.command !== "reopen") return server.close("invalid-control");
      try {
        event("ready", { url: await server.reopen() });
      } catch (error) {
        event("error", { code: error?.code === "no-pending-gate" ? "no-pending-gate" : "reopen-failed" });
      }
    }
  }
  server.close("parent-eof");
}

async function main() {
  const argv = process.argv.slice(2);
  for (let index = 0; index < argv.length; index += 2) {
    if (!ALLOWED.has(argv[index]) || index + 1 >= argv.length)
      throw new Error("the review child received unsupported arguments");
  }
  const bridge = createCaseBridge({
    caseDir: option(argv, "--case-dir"),
    casesRoot: option(argv, "--cases-root", ""),
  });
  const lifecycle = [];
  let announced = false;
  const server = await startReviewServer({
    bridge,
    wiki: createWikiBridge(),
    idleMs: numberOption(argv, "--idle-ms", 45 * 60_000),
    overallMs: numberOption(argv, "--overall-ms", 8 * 60 * 60_000),
    onLifecycle(value) {
      // Closure is reported once, below, with its reason.
      if (value.event !== "session-opened" && value.event !== "decided") return;
      if (announced) event(value.event);
      else lifecycle.push(value.event);
    },
  });
  event("ready", { url: server.url });
  announced = true;
  for (const name of lifecycle.splice(0)) event(name);
  void monitorControl(server);
  const result = await server.closed;
  event("closed", { reason: result.reason });
  process.exit(0);
}

main().catch((error) => {
  event("error", { code: error?.code === "no-pending-gate" ? "no-pending-gate" : "start-failed" });
  process.exitCode = 1;
});
