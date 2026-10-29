// Execute the complete client script against a small DOM tree. Layout is not tested.
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { createCaseBridge } from "../case-bridge.mjs";
import { buildCase, tempRoot } from "./helpers.mjs";

async function renderClient(view) {
  let document;
  class Node {
    constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.listeners = {}; }
    set textContent(value) { this.text = value; this.children = []; }
    get textContent() { return this.text ?? ""; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(key, value) { this.attributes[key] = value; if (key === "id") this.id = value; }
    addEventListener(type, listener) { this.listeners[type] = listener; }
    focus() { document.activeElement = this; }
    querySelectorAll(selector) {
      const tags = selector.split(",").map((tag) => tag.trim());
      return this.children.flatMap((child) => typeof child === "object" ? [...(tags.includes(child.tag) ? [child] : []), ...child.querySelectorAll(selector)] : []);
    }
  }
  const roots = ["app", "status", "alert", "title", "boot"].map((id) => {
    const node = new Node("div"); node.id = id; return node;
  });
  const find = (node, id) => node.id === id ? node : node.children.map((child) => typeof child === "object" ? find(child, id) : null).find(Boolean);
  document = {
    activeElement: null,
    getElementById: (id) => roots.map((node) => find(node, id)).find(Boolean) ?? null,
    createElement: (tag) => new Node(tag),
    createTextNode: (text) => { const node = new Node("#text"); node.textContent = text; return node; },
  };
  document.getElementById("boot").textContent = "{}";
  runInNewContext(readFileSync(new URL("../page/app.js", import.meta.url), "utf8"), {
    document,
    location: { hash: "", search: "", pathname: "/" },
    history: { replaceState() {} },
    URLSearchParams,
    setTimeout,
    clearTimeout,
    fetch: async (path) => ({ status: 200, json: async () => {
      if (path === "/api/view") return view;
      if (path === "/api/wiki-changes") return { changes: [] };
      if (path === "/api/passages") return { available: false };
      if (path === "/session") return {};
      throw new Error(`Unexpected client request: ${path}`);
    } }),
  });
  await new Promise(setImmediate);
  return document;
}

test("approved finalization renders a usable human follow-up without enabling approval", async (t) => {
  const caseDir = buildCase(tempRoot(t), "case", "report");
  const path = join(caseDir, "data/orchestration.json");
  const state = JSON.parse(readFileSync(path, "utf8"));
  delete state.investigation_gate_finalization;
  writeFileSync(path, JSON.stringify(state));
  const view = await createCaseBridge({ caseDir }).view("investigation_gate");
  assert.equal(view.pending_gate, null);
  const page = await renderClient(view);
  const note = page.getElementById("follow-up-note");
  assert.ok(note, page.getElementById("alert").textContent);
  assert.notEqual(note.disabled, true);
  const reviewer = page.getElementById("reviewer");
  assert.notEqual(reviewer.disabled, true);
  reviewer.listeners.input({ target: { value: "A. Reviewer" } });
  note.listeners.input({ target: { value: "Recheck the filing date." } });
  assert.equal(page.getElementById("request-follow-up").attributes["aria-disabled"], "false");
  for (const button of page.getElementById("decision-controls").querySelectorAll("button")) {
    assert.equal(button.attributes["aria-disabled"], "true", "approval stays unavailable");
  }
});
