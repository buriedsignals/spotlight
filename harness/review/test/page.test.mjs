// Execute the complete client script against a small DOM tree. Layout is not tested.
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { createCaseBridge } from "../case-bridge.mjs";
import { buildCase, tempRoot } from "./helpers.mjs";

// routes answer a path with a function of the request body returning { status, body }.
// The requests the page made are kept on the returned document as `posts`.
async function renderClient(view, { search = "", boot = {}, routes = {} } = {}) {
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
  document.getElementById("boot").textContent = JSON.stringify(boot);
  document.posts = [];
  runInNewContext(readFileSync(new URL("../page/app.js", import.meta.url), "utf8"), {
    document,
    location: { hash: "", search, pathname: "/" },
    history: { replaceState() {} },
    URL,
    URLSearchParams,
    setTimeout,
    clearTimeout,
    fetch: async (path, init = {}) => {
      const body = init.body ? JSON.parse(init.body) : undefined;
      document.posts.push({ path, body });
      if (routes[path]) {
        const answer = routes[path](body);
        return { status: answer.status, json: async () => answer.body };
      }
      return { status: 200, json: async () => {
        if (path === "/api/view") return view;
        if (path === "/api/wiki-changes") return { changes: [] };
        if (path === "/api/passages") return { available: false };
        if (path === "/session") return {};
        throw new Error(`Unexpected client request: ${path}`);
      } };
    },
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
  const page = await renderClient({ ...view, name_required: true, reviewer: "" });
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

test("finding sources link only http and https, in a new tab without an opener", async (t) => {
  const caseDir = buildCase(tempRoot(t), "case", "investigation_gate");
  const view = await createCaseBridge({ caseDir }).view("investigation_gate");
  view.content.findings[0].sources = [
    { url: "https://www.example.org/filing", description: "" },
    { url: "javascript:alert(1)", description: "" },
    { url: "data:text/html,hello", description: "" },
  ];
  const page = await renderClient(view);
  const links = page.getElementById("app").querySelectorAll("a");
  assert.ok(links.length, "the finding's web source is a link");
  for (const link of links) {
    assert.equal(link.attributes.href, "https://www.example.org/filing");
    assert.equal(link.attributes.target, "_blank");
    assert.match(link.attributes.rel, /\bnoopener\b/);
    assert.equal(link.textContent, "example.org");
  }
});

test("a solo gate asks for no name and its follow-up needs only the note", async (t) => {
  const caseDir = buildCase(tempRoot(t), "case", "investigation_gate");
  const page = await renderClient(await createCaseBridge({ caseDir }).view("investigation_gate"));
  assert.ok(page.getElementById("decision-controls"));
  assert.equal(page.getElementById("reviewer"), null);
  page.getElementById("follow-up-note").listeners.input({ target: { value: "Recheck the filing date." } });
  assert.equal(page.getElementById("request-follow-up").attributes["aria-disabled"], "false");
});

test("at a stop the failures name the send-back, not the agent", async (t) => {
  const caseDir = buildCase(tempRoot(t), "case", "investigation_gate");
  const path = join(caseDir, "data/orchestration.json");
  const state = JSON.parse(readFileSync(path, "utf8"));
  state.attempts = { "structural-correction": 2 };
  state.blocked = { phase: "investigation", exhausted_attempt: "structural-correction", gap: "FAIL  record:F1 [investigator]: no link", attempts: state.attempts };
  writeFileSync(path, JSON.stringify(state));
  const page = await renderClient(await createCaseBridge({ caseDir }).view("investigation_gate"));
  const said = JSON.stringify(page.getElementById("app"), (key, value) => (key === "listeners" ? undefined : value));
  assert.match(said, /Request follow-up below to send it back to Investigation/);
  assert.doesNotMatch(said, /Ask the agent to fix them/);
  assert.ok(page.getElementById("request-follow-up"));
});

// Tom, 2026-11-06: the journalist verifies, and a local tier's pages say so.
test("the Investigation page warns about the local model on a local tier, and not on frontier", async (t) => {
  const line = "Spotlight's local model can get facts wrong, even on findings marked verified. Check every finding against its source before you use it.";
  for (const [tier, shown] of [["12b", true], ["frontier", false]]) {
    const caseDir = buildCase(tempRoot(t), "case", "investigation_gate");
    const env = { ...process.env, SPOTLIGHT_MODEL_TIER: tier };
    const page = await renderClient(await createCaseBridge({ caseDir, env }).view("investigation_gate"));
    assert.equal(JSON.stringify(page.getElementById("app"), (key, value) => (key === "listeners" ? undefined : value)).includes(line), shown, tier);
  }
});

// Tom, 2026-11-05: a page with nothing to approve holds no turn, so a decline there
// carries nothing on. The member is told where to go next.
test("a decline with nothing offered sends the member back to the chat; any other decision keeps the usual line", async (t) => {
  const view = await createCaseBridge({ caseDir: buildCase(tempRoot(t), "case", "report") }).view("report");
  const words = (node) => [node.text ?? "", ...(node.children ?? []).map((child) => (typeof child === "object" ? words(child) : String(child)))].join(" ");
  const said = async (gate, content, decision) => words((await renderClient({ ...view, gate, content: { ...view.content, ...content }, pending_gate: null, receipt: { decision, approved_by: "Member" } })).getElementById("app"));
  const back = "Go back to the Spotlight chat and say what to do next.";

  assert.ok((await said("report", { draft_ready: false }, "declined")).includes(back));
  assert.ok((await said("ingest", { batch: { state: "empty", records: [] } }, "declined")).includes(back));
  assert.ok(!(await said("report", { draft_ready: true }, "declined")).includes(back));
  assert.ok(!(await said("ingest", { batch: { state: "ready", records: [{}] } }, "declined")).includes(back));
  assert.ok(!(await said("report", { draft_ready: true }, "completed")).includes(back));
});

// Invented names appear in unverified findings, so the page keeps them apart and says so.
test("the gate page lists verified findings first and the rest under a clear warning", async (t) => {
  const view = await createCaseBridge({ caseDir: buildCase(tempRoot(t), "case", "investigation_gate") }).view("investigation_gate");
  const finding = (id, claim, verdict) => ({ ...view.content.findings[0], id, claim, verdict });
  const content = { ...view.content, findings: [finding("F1", "A verified claim.", "verified"), finding("F2", "An invented name.", "unverified")] };
  const items = [{ key: "disposition:F2", kind: "disposition", target: "finding:F2", suggested: null, final: null, suggestion_reason: "" }];
  const page = await renderClient({ ...view, content, items, pending_gate: "investigation_gate" });
  const words = (node) => [node.text ?? "", ...(node.children ?? []).map((child) => (typeof child === "object" ? words(child) : String(child)))].join(" ");
  const sections = (node) => [...(node.tag === "section" ? [node] : []), ...(node.children ?? []).flatMap((child) => (typeof child === "object" ? sections(child) : []))];
  const all = sections(page.getElementById("app")).map(words);
  const main = all.find((text) => text.includes("Verified findings (1)"));
  const doubtful = all.find((text) => text.includes("Not verified, check before use"));
  assert.ok(main?.includes("A verified claim.") && !main.includes("An invented name."), main);
  assert.ok(doubtful?.includes("An invented name."), doubtful);
});

// The basis is counted by code from the fact-check record. The page shows it next to the verdict and adds nothing to decide.
test("the gate page shows what each verdict rests on, and a view without it shows no empty line", async (t) => {
  const view = await createCaseBridge({ caseDir: buildCase(tempRoot(t), "case", "investigation_gate") }).view("investigation_gate");
  const finding = (id, verdict, basis, claim_note) => ({ ...view.content.findings[0], id, claim: `Claim ${id}.`, verdict, basis, claim_note });
  const findings = [
    finding("F1", "verified", "Rests on one source (example.org).", "Long claim (52 words): it may bundle several assertions that need separate checks."),
    finding("F2", "unverified", "Not checked: the fact-check recorded no evidence for or against this claim.", ""),
    finding("F3", "verified", undefined, undefined),
  ];
  const items = [{ key: "disposition:F2", kind: "disposition", target: "finding:F2", suggested: null, final: null, suggestion_reason: "" }];
  const page = await renderClient({ ...view, content: { ...view.content, findings }, items, pending_gate: "investigation_gate" });
  const words = (node) => [node.text ?? "", ...(node.children ?? []).map((child) => (typeof child === "object" ? words(child) : String(child)))].join(" ");
  const text = words(page.getElementById("app"));
  assert.ok(text.includes("Rests on one source (example.org)."), text);
  assert.ok(text.includes("Long claim (52 words)"), text);
  assert.ok(text.includes("Not checked: the fact-check recorded no evidence"), text);
  assert.ok(!text.includes("undefined"), text);
});

test("the methodology page offers the drop for a plan code added, and says when it is dropped", async (t) => {
  const view = await createCaseBridge({ caseDir: buildCase(tempRoot(t), "case", "methodology") }).view("methodology");
  const words = (node) => [node.text ?? "", ...(node.children ?? []).map((child) => (typeof child === "object" ? words(child) : String(child)))].join(" ");
  const plan = { code_default: true, topics: [], consent: {}, keys: [], mode: "openrouter_swarm" };
  const item = { key: "selection:research_plan", kind: "selection", target: "research_plan", suggested: true, final: true, suggestion_source: "Research switch", suggestion_reason: "", note: "", required: false };
  const shown = words((await renderClient({ ...view, content: { ...view.content, research_plan: plan }, items: [item], pending_gate: "methodology" })).getElementById("app"));
  assert.match(shown, /Spotlight added this plan/);
  assert.match(shown, /Run Research on this case/);
  const dropped = words((await renderClient({ ...view, content: { ...view.content, research_dropped: true }, items: [], pending_gate: "methodology" })).getElementById("app"));
  assert.match(dropped, /Research is dropped for this case/);
  assert.doesNotMatch(dropped, /Run Research on this case/);
});

test("a lead that shares its source says so on the Research page", async (t) => {
  const switched = process.env.SPOTLIGHT_RESEARCH_SWARMS;
  process.env.SPOTLIGHT_RESEARCH_SWARMS = "on";
  t.after(() => { if (switched === undefined) delete process.env.SPOTLIGHT_RESEARCH_SWARMS; else process.env.SPOTLIGHT_RESEARCH_SWARMS = switched; });
  const view = await createCaseBridge({ caseDir: buildCase(tempRoot(t), "case", "research") }).view("research");
  const words = (node) => [node.text ?? "", ...(node.children ?? []).map((child) => (typeof child === "object" ? words(child) : String(child)))].join(" ");
  const leads = view.content.leads.map((lead, index) => ({ ...lead, same_source: index === 0 ? 2 : 0 }));
  const page = await renderClient({ ...view, content: { ...view.content, leads } });
  assert.ok(words(page.getElementById("app")).includes("Same source as 2 other leads, counts once"));
});

// u9mac-od3b81 F1 as the page got it: the people page's raw lines, images and all. The script tag and the tracking image are added.
const CASE_EVIDENCE = [
  "![](https://www.torproject.org/static/images/yec/x-dark.svg?h=353e04cc) Close banner",
  "![Illustration of a dark pink hand](https://www.torproject.org/static/images/yec/bulb-illo-yec.svg?h=9351f2c7)",
  "# Tor: powered by you",
  "<script>fetch('https://evil.example/x')</script>",
  "![isabela](https://www.torproject.org/static/images/people/isabela.png?h=b116eebb) [](https://twitter.com/isa)",
  "##### isabela",
  "IRC: isabela",
  "Executive Director of the Tor Project.",
  "Leads the **Tor** team, see [the people page](https://www.torproject.org/about/people/) and [a trap](javascript:alert(1)).",
  "- runs *the* board",
  "- [![pixel](https://evil.example/p.png)](https://evil.example/)",
  "![nvjacobo](https://www.torproject.org/static/images/people/jacobo.png?h=77e71d57) [](https://twitter.com/jacobonajera) [ ](https://www.torproject.org/static/keys/jac",
].join("\n");

test("recorded evidence reads as formatted text: no raw markdown, no live HTML, no image, safe links, and a cut says so", async (t) => {
  const view = await createCaseBridge({ caseDir: buildCase(tempRoot(t), "case", "investigation_gate") }).view("investigation_gate");
  const nodes = (node) => [node, ...(node.children ?? []).flatMap((child) => (typeof child === "object" ? nodes(child) : []))];
  const words = (node) => [node.text ?? "", ...(node.children ?? []).map((child) => (typeof child === "object" ? words(child) : String(child)))].join(" ");
  const shown = async (evidence) => {
    const finding = { ...view.content.findings[0], id: "F1", claim: "Isabela is Executive Director.", verdict: "verified", evidence };
    const items = [{ key: "selection:F1", kind: "selection", target: "finding:F1", suggested: true, final: true, suggestion_source: "", suggestion_reason: "", note: "" }];
    const page = await renderClient({ ...view, content: { ...view.content, findings: [finding] }, items, pending_gate: "investigation_gate" });
    return nodes(page.getElementById("app")).find((node) => node.className === "evidence");
  };
  const block = await shown(CASE_EVIDENCE);
  assert.ok(block, "the evidence has its own block");
  const text = words(block);
  const all = nodes(block);
  for (const raw of ["![", "#####", "](", "**", "static/images", "pixel"]) assert.ok(!text.includes(raw), `${raw} is not shown: ${text}`);
  assert.ok(!all.some((node) => ["img", "script", "iframe", "style"].includes(node.tag)));
  assert.match(text, /<script>fetch\('https:\/\/evil\.example\/x'\)<\/script>/, "HTML is shown as text");
  assert.ok(all.some((node) => node.className === "evidence-heading" && words(node).includes("isabela")));
  assert.ok(all.some((node) => node.tag === "strong" && words(node).includes("Tor")));
  assert.ok(all.some((node) => node.tag === "em" && words(node).includes("the")));
  assert.deepEqual(all.filter((node) => node.tag === "ul").flatMap((list) => list.children.map((item) => words(item).replace(/\s+/g, " ").trim())), ["runs the board"]);
  const links = all.filter((node) => node.tag === "a");
  assert.deepEqual(links.map((node) => node.attributes.href), ["https://www.torproject.org/about/people/"]);
  assert.equal(links[0].attributes.rel, "noopener noreferrer nofollow");
  assert.equal(links[0].attributes.target, "_blank");
  assert.match(text, /a trap/, "a link with an unsafe address keeps its words only");
  assert.match(text, /The excerpt is cut here/);

  // Cut by the page's 4,000-character limit, in plain words.
  assert.match(words(await shown("word ".repeat(800))), /The excerpt is cut here/);
  // Whole evidence says nothing about a cut, and evidence of images only says what happened.
  assert.doesNotMatch(words(await shown("Plain line.")), /cut here/);
  assert.match(words(await shown("![a](https://x.example/a.png)")), /only images/);
  // A link that hides its host behind a user name keeps its words only.
  assert.ok(!nodes(await shown("[torproject.org](https://www.torproject.org@evil.example/)")).some((node) => node.tag === "a"));
  // Text a parser could trip on stays as it is.
  assert.match(words(await shown("snake_case_name costs 2 * 3 * 4")), /snake_case_name costs 2 \* 3 \* 4/);
});


// Intake page: the material is untrusted, so it must reach the page as text.
const words = (node) => [node.text ?? "", ...(node.children ?? []).map((child) => (typeof child === "object" ? words(child) : String(child)))].join(" ");
const nodes = (node, test) => [...(test(node) ? [node] : []), ...(node.children ?? []).flatMap((child) => (typeof child === "object" ? nodes(child, test) : []))];
const NOTHING_PENDING = { gate: "", pending_gate: "", gate_label: "Wiki changes" };
const HOSTILE = "<img src=x onerror=alert(1)> the foreman signed it";

function material({ record = {}, claim = {}, approvals = {}, nameRequired = false } = {}) {
  return {
    name_required: nameRequired,
    inputs: [{ id: "JI1", state: "draft", kind: "interview", captured_by: "A. Reporter", source: { pseudonym: "Dock worker 1" }, rights_holder: { label: "A. Reporter" },
      original_sensitivity: "case_only", content_ref: "intake/interviews/call.txt", provenance: { method: "journalist" }, ...record }],
    passages: [{ id: "JP1", input_id: "JI1", text: HOSTILE, anchor_ref: { path: "intake/interviews/call.txt", line_start: 2, line_end: 2 }, rights: { attribution: "background" },
      context: { truncated: false, lines: [{ n: 1, text: "line before", in_passage: false }, { n: 2, text: HOSTILE, in_passage: true }, { n: 3, text: "line after", in_passage: false }] } }],
    claims: [{ id: "JC1", passage_id: "JP1", proposition: "The <b>foreman</b> signed off.", state: "proposed", ...claim }],
    approvals,
  };
}

const intakePage = (data, { routes = {}, edition = {} } = {}) =>
  renderClient(NOTHING_PENDING, { search: "?view=intake", boot: { intake: true }, routes: { "/api/intake/view": () => ({ status: 200, body: { ...data, ...edition } }), ...routes } });

test("the Intake page shows each claim above its passage in context, with the passage's lines marked and its text inert", async () => {
  const page = await intakePage(material());
  const app = page.getElementById("app");
  assert.equal(nodes(app, (node) => node.tag === "img").length, 0, "material became markup");
  const shown = words(app);
  assert.ok(shown.includes("The <b>foreman</b> signed off."));
  assert.ok(shown.includes("intake/interviews/call.txt, line 2"));
  assert.ok(shown.includes("Attribution: Background") && shown.includes("Publishable: no"));
  const context = nodes(app, (node) => node.tag === "pre")[0];
  const lines = context.children;
  assert.deepEqual(lines.map((node) => [node.className, node.text.trim()]), [["", "1  line before"], ["hit", `2  ${HOSTILE}`], ["", "3  line after"]]);
  // Keep, Edit and Drop come with every claim.
  for (const name of ["keep", "edit", "drop"]) assert.ok(page.getElementById(`${name}-JC1`), name);
});

test("a decision is sent at once, and a refusal shows beside the claim and leaves it undecided", async () => {
  const page = await intakePage(material(), { routes: { "/api/intake/decide": () => ({ status: 422, body: { message: "no material claim JC1" } }) } });
  await page.getElementById("keep-JC1").listeners.click();
  assert.deepEqual(page.posts.find((post) => post.path === "/api/intake/decide").body, { claim: "JC1", action: "keep" });
  const card = nodes(page.getElementById("app"), (node) => node.tag === "article")[0];
  assert.ok(nodes(card, (node) => node.attributes?.role === "alert").some((node) => node.text === "no material claim JC1"));
  assert.ok(words(card).includes("To decide"), "the claim was shown as decided though nothing was saved");
});

test("editing a claim sends the proposition alone, and rights are sent as the form states them", async () => {
  const page = await intakePage(material());
  await page.getElementById("edit-JC1").listeners.click();
  page.getElementById("claim-text-JC1").listeners.input({ target: { value: "The foreman signed off the shift." } });
  await page.getElementById("save-claim-JC1").listeners.click();
  assert.deepEqual(page.posts.find((post) => post.path === "/api/intake/decide").body, { claim: "JC1", action: "edit", proposition: "The foreman signed off the shift." });

  page.getElementById("rights-JP1-attribution").listeners.change({ target: { value: "on_record" } });
  page.getElementById("rights-JP1-consent").listeners.change({ target: { value: "written" } });
  page.getElementById("rights-JP1-content").listeners.change({ target: { checked: true } });
  await page.getElementById("save-rights-JP1").listeners.click();
  assert.deepEqual(page.posts.find((post) => post.path === "/api/intake/rights").body,
    { passage: "JP1", rights: { attribution: "on_record", consent: { publish_content: true, publish_direct_quotes: false, obtained_via: "written" } } });
});

test("approving needs a confirmed record and no claim left undecided; a confirmed record is read-only", async () => {
  const state = async (data) => {
    const page = await intakePage(data);
    return { approve: page.getElementById("approve-JI1").attributes["aria-disabled"], form: Boolean(page.getElementById("record-JI1-kind")), keepAll: Boolean(page.getElementById("keep-all-JI1")) };
  };
  const confirmed = { state: "confirmed", confirmed_by: "Member", confirmed_at: "2026-11-12T10:00:00Z" };
  assert.deepEqual(await state(material()), { approve: "true", form: true, keepAll: true });
  assert.deepEqual(await state(material({ record: confirmed })), { approve: "true", form: false, keepAll: true });
  assert.deepEqual(await state(material({ record: confirmed, claim: { state: "kept" } })), { approve: "false", form: false, keepAll: false });
});

test("the solo Intake page asks for no name; the newsroom page asks and sends it with a confirmation", async () => {
  const solo = await intakePage(material());
  assert.equal(solo.getElementById("intake-name"), null);
  await solo.getElementById("confirm-JI1").listeners.click();
  assert.deepEqual(solo.posts.find((post) => post.path === "/api/intake/confirm").body, { id: "JI1" });

  const newsroom = await intakePage(material({ nameRequired: true }));
  newsroom.getElementById("intake-name").listeners.input({ target: { value: "A. Reporter" } });
  await newsroom.getElementById("confirm-JI1").listeners.click();
  assert.deepEqual(newsroom.posts.find((post) => post.path === "/api/intake/confirm").body, { id: "JI1", by: "A. Reporter" });
});

test("a gate page offers the Intake page only when the server has one", async (t) => {
  const view = await createCaseBridge({ caseDir: buildCase(tempRoot(t), "case", "methodology") }).view("methodology");
  assert.ok((await renderClient(view, { boot: { intake: true } })).getElementById("show-intake"));
  assert.equal((await renderClient(view)).getElementById("show-intake"), null);
});
