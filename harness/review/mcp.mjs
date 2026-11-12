#!/usr/bin/env node
// Small stdio MCP server for hosts that run Spotlight in a chat app. Its
// tools, open_spotlight_review, open_spotlight_wiki_changes and
// open_spotlight_intake, open the review page in the member's browser (like Splash's open_splash) and return
// only that they did: never the URL.
// Newline-delimited JSON-RPC 2.0 on stdin/stdout; nothing else is written to
// stdout.
import { realpathSync } from "node:fs";
import { isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";
import { createReviewSessionManager } from "./session.mjs";
import {
  createIntakeOpener,
  createReviewOpener,
  createWikiChangesOpener,
  describeIntakeResult,
  describeOpenResult,
  describeWikiChangesResult,
} from "./open.mjs";

const PROTOCOL_VERSION = "2025-06-18";
const MAX_LINE = 1 << 20;
const CASE_DIR_SCHEMA = {
  type: "object",
  properties: { case_dir: { type: "string", description: "Absolute path of the case directory." } },
  required: ["case_dir"],
  additionalProperties: false,
};
const REVIEW_TOOL = {
  name: "open_spotlight_review",
  title: "Open Spotlight review",
  description:
    "Open the pending gate's review page (methodology, Investigation gate, report or ingest) for one Spotlight case in the member's browser. Call it when the case resolver reports a pending gate, or again to reopen an expired page. The member reviews and decides on the page; when they say they are done, resolve the case again. Pass only the absolute case directory.",
  inputSchema: CASE_DIR_SCHEMA,
};
const WIKI_TOOL = {
  name: "open_spotlight_wiki_changes",
  title: "Open Spotlight wiki changes",
  description:
    "Open the Wiki changes page in the member's browser after you stage a wiki write, so the member applies or discards it. You cannot apply a staged wiki write yourself. Returns that the page opened, or that nothing is staged. Pass only the absolute case directory.",
  inputSchema: CASE_DIR_SCHEMA,
};

const INTAKE_TOOL = {
  name: "open_spotlight_intake",
  title: "Open Spotlight material intake",
  description:
    "Open the Material intake page for one Spotlight case in the member's browser, so the member checks the record of each item of material they brought (interview, email, note, document) and keeps, edits or drops each claim taken from it. Works whether or not a gate is pending. The member decides on the page; when they say they are done, resolve the case again. Pass only the absolute case directory.",
  inputSchema: CASE_DIR_SCHEMA,
};

export function createMcpHandler({ open, openWiki, openIntake }) {
  if ([open, openWiki, openIntake].some((opener) => typeof opener !== "function")) throw new Error("the MCP server needs its openers");
  const tools = {
    [REVIEW_TOOL.name]: [REVIEW_TOOL, open, describeOpenResult],
    [WIKI_TOOL.name]: [WIKI_TOOL, openWiki, describeWikiChangesResult],
    [INTAKE_TOOL.name]: [INTAKE_TOOL, openIntake, describeIntakeResult],
  };
  return async function handle(message) {
    if (!message || typeof message !== "object" || message.jsonrpc !== "2.0") {
      return { jsonrpc: "2.0", id: null, error: { code: -32600, message: "Invalid request" } };
    }
    const { id, method, params } = message;
    if (id === undefined) return null; // notifications need no reply
    const reply = (result) => ({ jsonrpc: "2.0", id, result });
    if (method === "initialize") {
      return reply({
        protocolVersion:
          typeof params?.protocolVersion === "string" ? params.protocolVersion : PROTOCOL_VERSION,
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: "spotlight-review", version: "0.1.0" },
      });
    }
    if (method === "ping") return reply({});
    if (method === "tools/list") return reply({ tools: Object.values(tools).map(([tool]) => tool) });
    if (method === "tools/call") {
      if (!Object.hasOwn(tools, params?.name ?? ""))
        return { jsonrpc: "2.0", id, error: { code: -32602, message: "Unknown tool" } };
      const [, opener, describe] = tools[params.name];
      const args = params.arguments ?? {};
      const keys = Object.keys(args);
      if (keys.length !== 1 || typeof args.case_dir !== "string" || !isAbsolute(args.case_dir)) {
        return reply({
          isError: true,
          content: [{ type: "text", text: "Pass only case_dir, the absolute case directory." }],
        });
      }
      const result = await opener({ caseDir: args.case_dir });
      return reply({
        isError: result.status === "start-failed" || result.status === "wiki-unreadable",
        content: [{ type: "text", text: describe(result) }],
        structuredContent: { review: result.status, reopened: Boolean(result.reopened) },
      });
    }
    return { jsonrpc: "2.0", id, error: { code: -32601, message: "Method not found" } };
  };
}

async function serve() {
  const casesRoot = process.env.SPOTLIGHT_CASES_ROOT ?? "";
  // One session manager: every tool opens the same review server.
  const sessions = createReviewSessionManager({ casesRoot });
  const handle = createMcpHandler({
    open: createReviewOpener({ sessions }),
    openWiki: createWikiChangesOpener({ sessions }),
    openIntake: createIntakeOpener({ sessions }),
  });
  let buffer = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) {
    buffer += chunk;
    if (buffer.length > MAX_LINE && !buffer.includes("\n")) buffer = "";
    let at;
    while ((at = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, at).trim();
      buffer = buffer.slice(at + 1);
      if (!line) continue;
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } })}\n`);
        continue;
      }
      const response = await handle(message);
      if (response) process.stdout.write(`${JSON.stringify(response)}\n`);
    }
  }
}

if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await serve();
}
