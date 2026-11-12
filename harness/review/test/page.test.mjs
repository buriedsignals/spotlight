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
const DIGEST = "d".repeat(64);

const PLAIN = { kind: "interview", kind_detail: "", source_as: "pseudonym", source: "Dock worker 1", holder: "A. Reporter",
  publish: "not_attributed", quotes: "not_allowed", consent: "recorded_oral", summary: "Dock worker 1 (pseudonym) \u00b7 yes, not attributed by name" };
const CHOICES = {
  kind: [{ value: "interview", label: "Interview" }, { value: "other", label: "Other" }],
  source_as: [{ value: "pseudonym", label: "A pseudonym" }, { value: "real_name", label: "The source's real name" }],
  publish: [{ value: "not_attributed", label: "Yes, not attributed by name" }, { value: "off_record", label: "No, off the record" }],
  quotes: [{ value: "not_allowed", label: "Not allowed" }, { value: "allowed", label: "Allowed" }],
  consent: [{ value: "recorded_oral", label: "Spoken, on the recording" }, { value: "unrecorded_oral", label: "Spoken, not recorded" }],
};
const RESTRICTIVE = { source_as: "pseudonym", publish: "off_record", quotes: "not_allowed", consent: "unrecorded_oral" };
const STATEMENT = { subject: { text: "the foreman" }, predicate: "signed off", object: { text: "the shift" } };

// Two items of material: the first holds a claim the model suggested leaving out.
function material({ nameRequired = false, approved = [], plain = {} } = {}) {
  const item = (id, file) => ({ id, state: "draft", content_ref: `intake/interviews/${file}`, plain: { ...PLAIN, ...plain } });
  const passage = (id, input, text) => ({ id, input_id: input, text, anchor_ref: { path: "intake/interviews/call.txt", line_start: 2, line_end: 2 }, context: { before: "words before ", after: " words after" } });
  const claim = (id, passageId, proposition, extra = {}) => ({ id, passage_id: passageId, proposition, statement: STATEMENT, state: "proposed", ...extra });
  return {
    name_required: nameRequired, choices: CHOICES, restrictive: RESTRICTIVE,
    inputs: [item("JI1", "call.txt"), item("JI2", "second.txt")],
    passages: [passage("JP1", "JI1", HOSTILE), passage("JP2", "JI1", "my brother-in-law is on nights"), passage("JP3", "JI2", "second material")],
    claims: [claim("JC1", "JP1", "The <b>foreman</b> signed off."), claim("JC2", "JP2", "His brother-in-law works nights.", { leave_out: "it could identify the source" }), claim("JC3", "JP3", "Second claim.")],
    digests: {},
    seen: { JI1: "a".repeat(64), JI2: "b".repeat(64) },
    approvals: Object.fromEntries(approved.map((id) => [id, { by: "Member", at: "2026-11-12T10:00:00Z", claims: ["JC1"] }])),
  };
}

const intakePage = (data, { routes = {} } = {}) =>
  renderClient(NOTHING_PENDING, { search: "?view=intake", boot: { intake: true }, routes: { "/api/intake/view": () => ({ status: 200, body: data }), ...routes } });
const sections = (page) => nodes(page.getElementById("app"), (node) => node.tag === "details" && node.className === "material");
const isOpen = (section) => section.attributes.open !== undefined;
// A person typing: into a text box (its value) or into a claim's sentence (its text).
const typed = (node, text) => { node[node.tag === "input" ? "value" : "textContent"] = text; node.listeners.input({ target: node }); };

test("the Intake page is a list of expandable items; the first one still to review is open", async () => {
  const page = await intakePage(material());
  const [first, second] = sections(page);
  assert.deepEqual([isOpen(first), isOpen(second)], [true, false]);
  const closed = words(first.children[0]);
  assert.ok(closed.includes("Interview: call.txt") && closed.includes("To review") && closed.includes("1 of 2 claims kept"), closed);
  assert.ok(words(first).includes(PLAIN.summary), "the folded line is not the recorded summary");
  const fold = nodes(first, (node) => node.tag === "details" && node.className === "fold strip")[0];
  assert.deepEqual(nodes(fold, (node) => node.tag === "label").map((node) => words(node).trim().split("  ")[0]).slice(0, 2), ["Kind of material", "The source is shown as"]);
  assert.ok(page.getElementById("approve-JI1"));
});

test("material reaches the page as text, with no line numbers, and the words around each passage wait for the one button", async () => {
  const page = await intakePage(material());
  const app = page.getElementById("app");
  assert.equal(nodes(app, (node) => node.tag === "img" || node.tag === "pre").length, 0, "material became markup");
  const [first] = sections(page);
  assert.ok(words(first).includes("The <b>foreman</b> signed off.") && words(first).includes(HOSTILE));
  assert.doesNotMatch(words(first), /\bline \d|lines? \d/i);
  const inside = first.children[1];
  assert.equal(inside.className, "inside");
  await page.getElementById("around-JI1").listeners.click();
  const [reopened] = sections(page);
  assert.equal(reopened.children[1].className, "inside context");
  assert.ok(words(reopened).includes("words before") && words(reopened).includes("Hide what was said around each"));
});

test("a claim the model suggested leaving out arrives unticked with its reason, and the tick decides what is sent", async () => {
  const page = await intakePage(material(), { routes: { "/api/intake/approve-material": () => ({ status: 200, body: {} }) } });
  assert.deepEqual(["JC1", "JC2"].map((id) => Boolean(page.getElementById(`keep-${id}`).checked)), [true, false]);
  assert.ok(words(page.getElementById("app")).includes("Suggested to leave out: it could identify the source"));
  assert.equal(page.getElementById("approve-JI1").textContent, "Approve 1 claim");
  page.getElementById("keep-JC2").listeners.change({ target: { checked: true } });
  assert.equal(page.getElementById("approve-JI1").textContent, "Approve 2 claims");
  assert.ok(words(page.getElementById("app")).includes("2 kept, 0 left out"));
  page.getElementById("keep-JC1").listeners.change({ target: { checked: false } });
  await page.getElementById("approve-JI1").listeners.click();
  assert.deepEqual(page.posts.find((post) => post.path === "/api/intake/approve-material").body.keep, { JC1: false, JC2: true });
});

test("approving sends the material's fields and ticks in one request, with no digest or code, then closes it and opens the next", async () => {
  let approved = [];
  const page = await intakePage(material(), { routes: {
    "/api/intake/view": () => ({ status: 200, body: material({ approved }) }),
    "/api/intake/approve-material": () => { approved = ["JI1"]; return { status: 200, body: { by: "Member" } }; },
  } });
  page.getElementById("item-JI1-publish").listeners.change({ target: { value: "off_record" } });
  typed(page.getElementById("item-JI1-holder"), "B. Holder");
  await page.getElementById("approve-JI1").listeners.click();
  const posted = page.posts.filter((post) => post.path.startsWith("/api/intake/") && post.path !== "/api/intake/view");
  assert.deepEqual(posted.map((post) => post.path), ["/api/intake/approve-material"]);
  assert.deepEqual(posted[0].body, { id: "JI1", choices: { ...Object.fromEntries(Object.entries(PLAIN).filter(([name]) => name !== "summary")), publish: "off_record", holder: "B. Holder" }, keep: { JC1: true, JC2: false }, seen: "a".repeat(64) });
  const [first, second] = sections(page);
  assert.deepEqual([isOpen(first), isOpen(second)], [false, true]);
  assert.ok(words(first.children[0]).includes("Approved"));
});

test("a refusal is shown beside the button with its reason, and the item stays open", async () => {
  const page = await intakePage(material(), { routes: { "/api/intake/approve-material": () => ({ status: 422, body: { code: "refused", step: "confirm", message: "The record could not be confirmed: not complete" } }) } });
  await page.getElementById("approve-JI1").listeners.click();
  const [first, second] = sections(page);
  assert.deepEqual([isOpen(first), isOpen(second)], [true, false]);
  assert.ok(nodes(first, (node) => node.attributes?.role === "alert").some((node) => node.text === "The record could not be confirmed: not complete"));
  assert.ok(words(first.children[0]).includes("To review"));
});

test("a claim is rewritten in place and saved when it loses focus and has changed; a refusal keeps the words", async () => {
  const page = await intakePage(material(), { routes: { "/api/intake/rewrite": () => ({ status: 422, body: { message: "proposition cannot be empty" } }) } });
  const sentence = page.getElementById("claim-JC1");
  sentence.textContent = "The <b>foreman</b> signed off.";
  await sentence.listeners.blur({ target: sentence });
  assert.equal(page.posts.filter((post) => post.path === "/api/intake/rewrite").length, 0, "an unchanged sentence was saved");
  typed(sentence, "The foreman signed the shift off.");
  await sentence.listeners.blur({ target: sentence });
  assert.deepEqual(page.posts.find((post) => post.path === "/api/intake/rewrite").body, { claim: "JC1", proposition: "The foreman signed the shift off." });
  assert.equal(page.getElementById("claim-JC1").textContent, "The foreman signed the shift off.", "the typed words were lost");
  assert.ok(nodes(page.getElementById("app"), (node) => node.attributes?.role === "alert").some((node) => node.text === "proposition cannot be empty"));
});

test("when no rights are stored the most restrictive choices are selected and the folded line says so", async () => {
  const page = await intakePage(material({ plain: { publish: null, quotes: null, consent: null, summary: "Dock worker 1 (pseudonym) \u00b7 rights not set yet" } }));
  const selected = (id) => nodes(page.getElementById(id), (node) => node.tag === "option" && node.attributes.selected !== undefined).map((node) => node.value);
  assert.deepEqual([selected("item-JI1-publish"), selected("item-JI1-quotes"), selected("item-JI1-consent")], [["off_record"], ["not_allowed"], ["unrecorded_oral"]]);
  assert.ok(words(page.getElementById("app")).includes("rights not set yet"));
});

test("approving with nothing ticked, or without a name in the newsroom edition, sends nothing", async () => {
  const page = await intakePage(material({ nameRequired: true }));
  const posts = () => page.posts.filter((post) => post.path === "/api/intake/approve-material").length;
  await page.getElementById("approve-JI1").listeners.click();
  assert.ok(words(page.getElementById("app")).includes("Enter your name before approving."));
  typed(page.getElementById("intake-name"), "A. Reporter");
  page.getElementById("keep-JC1").listeners.change({ target: { checked: false } });
  await page.getElementById("approve-JI1").listeners.click();
  assert.ok(words(page.getElementById("app")).includes("Keep at least one claim to approve."));
  assert.equal(posts(), 0);
  page.getElementById("keep-JC1").listeners.change({ target: { checked: true } });
  await page.getElementById("approve-JI1").listeners.click();
  assert.equal(page.posts.find((post) => post.path === "/api/intake/approve-material").body.by, "A. Reporter");
});

test("a confirmed item's fields can no longer be changed", async () => {
  const data = material();
  data.inputs[0].state = "confirmed";
  const page = await intakePage(data);
  assert.equal(page.getElementById("item-JI1-publish").disabled, true);
  assert.notEqual(page.getElementById("item-JI2-publish").disabled, true);
});

test("the Ingest page shows what a claim from material rests on, as text, and nothing under a claim from an investigation", async (t) => {
  const basis = { passage: HOSTILE, material: "interview, call.txt", rights: ["Attribution: background", "Consent: content may be published, direct quotes may not be published", "Sensitivity: internal"] };
  const records = [{ type: "claims", id: "claim-1", version: 1, text: "From material.", basis }, { type: "claims", id: "claim-2", version: 1, text: "From an investigation." }];
  const view = await createCaseBridge({ caseDir: buildCase(tempRoot(t), "case", "report") }).view("report");
  const page = await renderClient({ ...view, gate: "ingest", content: { batch: { state: "final", records } }, pending_gate: null, receipt: { decision: "requested", approved_by: "Member" } });
  const app = page.getElementById("app");
  assert.equal(nodes(app, (node) => node.tag === "img").length, 0, "the passage became markup");
  const shown = words(page.getElementById("basis-claim-1"));
  assert.ok(shown.includes(HOSTILE) && shown.includes("From the interview, call.txt.") && shown.includes("Attribution: background") && shown.includes("direct quotes may not be published"), shown);
  assert.equal(page.getElementById("basis-claim-2"), null);
});

test("a gate page offers the Intake page only when the server has one", async (t) => {
  const view = await createCaseBridge({ caseDir: buildCase(tempRoot(t), "case", "methodology") }).view("methodology");
  assert.ok((await renderClient(view, { boot: { intake: true } })).getElementById("show-intake"));
  assert.equal((await renderClient(view)).getElementById("show-intake"), null);
});
