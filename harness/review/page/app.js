// Spotlight review page. Case text arrives only as JSON from this server and
// is inserted with textContent; this file never builds HTML from strings.
const app = document.getElementById("app");
const statusLine = document.getElementById("status");
const alertBox = document.getElementById("alert");
const title = document.getElementById("title");
const boot = JSON.parse(document.getElementById("boot").textContent || "{}");

let view = null;
let busy = false;
let ended = false;
// Disabling a focused control while a request runs drops focus to the body,
// so the control's id is kept to restore focus after the re-render.
let focusAfterRender = "";
// The name as typed, before it is saved; Approve follows it as the member types.
let reviewerDraft = null;
let reviewerTimer = 0;
let reviewerSaving = null;
// The follow-up note is sent with the request, not saved as a review edit.
let followUpNote = "";
// Engine's staged wiki writes, loaded after the gate (null until then); empty
// without Engine, which hides the view.
let wikiChanges = null;
// Why the last wiki request failed; shown with a retry, apart from "none".
let wikiError = "";

const DECISION_LABELS = {
  methodology: { approved: "Approve the methodology" },
  research: { approved: "Approve the research marks" },
  investigation_gate: { approved: "Approve the investigation" },
  report: { completed: "Publish the drafted report", declined: "Decline the report" },
  ingest: { requested: "Ingest into the knowledge base", declined: "Do not ingest" },
};
const DECISION_NAMES = {
  approved: "Approved",
  completed: "Report published",
  declined: "Declined",
  requested: "Ingestion requested",
  follow_up: "Follow-up requested",
};
const DISPOSITION_LABELS = { keep: "Keep in the record", set_aside: "Set aside" };
const FLAG_LABELS = {
  amend: "Amend the claim",
  exclude: "Leave the claim out",
  investigate_further: "Investigate further",
  accept_with_reason: "Accept, with the reason in the note",
};

function el(tag, options = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(options)) {
    if (value === undefined || value === null || value === false) continue;
    if (name === "text") node.textContent = String(value);
    else if (name === "className") node.className = value;
    else if (name === "checked" || name === "disabled") node[name] = Boolean(value);
    else if (name === "value") node.value = String(value);
    else if (name === "on") for (const [type, fn] of Object.entries(value)) node.addEventListener(type, fn);
    else node.setAttribute(name, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function say(message) {
  statusLine.textContent = message;
}

function warn(message) {
  if (!message) {
    alertBox.hidden = true;
    alertBox.textContent = "";
    return;
  }
  // The prefix is part of the alert's text, so it is never announced alone.
  alertBox.replaceChildren(el("strong", { text: "Attention: " }), message);
  alertBox.hidden = false;
}

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = { message: "The review server sent an unreadable reply." };
  }
  if (response.status === 403) endSession(data.message);
  return { status: response.status, data };
}

function endSession(message) {
  ended = true;
  warn(message || "This review session has ended. Ask the agent to reopen the review.");
  say("Session ended.");
  for (const control of app.querySelectorAll("button, input, textarea")) control.disabled = true;
}

function setBusy(value) {
  if (value && document.activeElement?.id) focusAfterRender = document.activeElement.id;
  busy = value;
  app.setAttribute("aria-busy", value ? "true" : "false");
  // Buttons stay focusable (aria-disabled) so keyboard focus is not lost.
  for (const control of app.querySelectorAll("input, textarea")) {
    if (value) control.disabled = true;
  }
  for (const control of app.querySelectorAll("button")) {
    if (value) control.setAttribute("aria-disabled", "true");
  }
  if (!value && view) render(view);
}

function keepFocus(render) {
  const id = focusAfterRender || document.activeElement?.id;
  render();
  focusAfterRender = "";
  const target = (id && document.getElementById(id)) || null;
  if (target && !target.disabled) target.focus();
  else if (id && readOnly()) document.getElementById("receipt-heading")?.focus();
}

function readOnly() {
  return ended || !view || view.pending_gate !== view.gate;
}

function domId(prefix, key, suffix = "") {
  return `${prefix}-${key.replace(/[^A-Za-z0-9_-]/g, "_")}${suffix ? `-${suffix}` : ""}`;
}

// ---------------------------------------------------------------- content

function list(items, empty) {
  if (!items.length) return el("p", { className: "muted", text: empty });
  return el("ul", {}, items.map((item) => el("li", { text: item })));
}

function methodologyContent(content) {
  return el(
    "section",
    { "aria-labelledby": "plan-heading" },
    el("h2", { id: "plan-heading", text: "The plan" }),
    el("div", { className: "card" }, el("h3", { text: "Brief" }), el("p", { className: "quote", text: content.brief || "No brief recorded." })),
    content.directions.length
      ? content.directions.map((direction, index) =>
          el(
            "div",
            { className: "card" },
            el("h3", { text: `Direction ${index + 1}: ${direction.direction}` }),
            el("p", { className: "muted", text: "Questions" }),
            list(direction.questions, "No questions listed."),
            el("p", { className: "muted", text: "Steps" }),
            direction.steps.length
              ? el("ol", {}, direction.steps.map((step) => el("li", { text: `${step.action} (${step.tool})${step.target ? `: ${step.target}` : ""}` })))
              : el("p", { className: "muted", text: "No steps listed." }),
          ),
        )
      : el("p", { className: "muted", text: "The methodology lists no directions." }),
    content.limitations.length ? [el("h3", { text: "Limitations" }), list(content.limitations, "")] : null,
  );
}

function findingDetails(finding) {
  if (!finding) return null;
  return [
    el("p", {}, el("span", { className: "tag strong", text: `Fact-check verdict: ${finding.verdict}` }), el("span", { className: "tag", text: `Confidence: ${finding.confidence || "not stated"}` })),
    el("p", { className: "quote", text: finding.claim }),
    finding.evidence ? el("p", { className: "quote muted", text: `Evidence: ${finding.evidence}` }) : null,
    finding.fact_check_notes ? el("p", { className: "quote muted", text: `Fact-check notes: ${finding.fact_check_notes}` }) : null,
    finding.sources.length
      ? el("ul", { "aria-label": `Sources for finding ${finding.id}` }, finding.sources.map((source) => el("li", { text: source.description ? `${source.description} (${source.url})` : source.url })))
      : null,
  ];
}

function suggestionLine(item) {
  if (item.suggested === null) {
    if (item.suggestion_source === "added by the member") return el("p", { className: "muted", text: "You added this direction." });
    return el("p", { className: "muted", text: `No suggestion: you decide. ${item.suggestion_reason}` });
  }
  const shown = typeof item.suggested === "boolean" ? (item.suggested ? "include" : "leave out") : item.kind === "clue" || item.kind === "direction" ? MARK_LABELS[item.suggested] : DISPOSITION_LABELS[item.suggested] ?? FLAG_LABELS[item.suggested];
  return el("p", { className: "muted", text: `Suggested: ${shown}. Source: ${item.suggestion_source}. ${item.suggestion_reason}` });
}

function changeTag(item) {
  if (item.suggested === null && item.final === null && !item.required) return el("span", { className: "tag", text: "No mark" });
  if (item.suggested === null) return item.final === null ? el("span", { className: "tag strong", text: "Open: needs your decision" }) : el("span", { className: "tag", text: "Decided" });
  return item.final === item.suggested
    ? el("span", { className: "tag", text: "As suggested" })
    : el("span", { className: "tag strong", text: "Changed from the suggestion" });
}

function noteField(item) {
  const id = domId("note", item.key);
  return el(
    "div",
    {},
    el("label", { for: id, text: "Note (optional)" }),
    el("textarea", {
      id,
      maxlength: "2000",
      value: item.note,
      disabled: readOnly() || busy,
      on: { change: (event) => edit({ kind: "note", key: item.key, value: event.target.value }) },
    }),
  );
}

function dispositionCard(item, finding, labels = DISPOSITION_LABELS, legend = `Finding ${finding?.id ?? item.target}`) {
  const name = domId("disposition", item.key);
  return el(
    "fieldset",
    { className: "card", "aria-describedby": `${name}-state` },
    el("legend", { text: legend }),
    finding ? findingDetails(finding) : null,
    suggestionLine(item),
    el("p", { id: `${name}-state` }, changeTag(item)),
    el(
      "div",
      { className: "choices", role: "radiogroup", "aria-label": `Disposition for ${item.target}` },
      Object.entries(labels).map(([value, label]) => {
        const id = `${name}-${value}`;
        return el(
          "span",
          { className: "choice" },
          el("input", {
            type: "radio",
            id,
            name,
            value,
            checked: item.final === value,
            disabled: readOnly() || busy,
            on: { change: () => edit({ kind: "mark", key: item.key, value }) },
          }),
          el("label", { for: id, text: label }),
        );
      }),
    ),
    noteField(item),
  );
}

function selectionCard(item, finding, label = `Include finding ${finding?.id ?? item.target} in the record`) {
  const id = domId("select", item.key);
  return el(
    "div",
    { className: "card" },
    el(
      "div",
      { className: "choice" },
      el("input", {
        type: "checkbox",
        id,
        checked: item.final === true,
        disabled: readOnly() || busy,
        "aria-describedby": `${id}-state`,
        on: { change: (event) => edit({ kind: "mark", key: item.key, value: event.target.checked }) },
      }),
      el("label", { for: id, text: label }),
    ),
    finding ? findingDetails(finding) : null,
    suggestionLine(item),
    el("p", { id: `${id}-state` }, changeTag(item)),
    noteField(item),
  );
}

function investigationContent(content, items) {
  const findings = new Map(content.findings.map((finding) => [`finding:${finding.id}`, finding]));
  const flags = items.filter((item) => item.kind === "disposition");
  const selection = items.filter((item) => item.kind === "selection");
  const open = flags.filter((item) => item.final === null).length;
  return [
    el(
      "section",
      { "aria-labelledby": "summary-heading" },
      el("h2", { id: "summary-heading", text: content.title || "Summary" }),
      el("p", { className: "quote", text: content.summary || "No summary recorded." }),
    ),
    el(
      "section",
      { "aria-labelledby": "flags-heading" },
      el("h2", { id: "flags-heading", text: `Flags needing a disposition (${open} open of ${flags.length})` }),
      flags.length ? flags.map((item) => dispositionCard(item, findings.get(item.target))) : el("p", { className: "muted", text: "No findings are flagged." }),
    ),
    el(
      "section",
      { "aria-labelledby": "selection-heading" },
      el("h2", { id: "selection-heading", text: "Selection" }),
      selection.length ? selection.map((item) => selectionCard(item, findings.get(item.target))) : el("p", { className: "muted", text: "No verified findings to select." }),
    ),
  ];
}

// A finding the fact-check did not verify says so in words, not only in the
// card's border, so it reads differently without colour.
function treatmentCard(treatment) {
  return el(
    "div",
    { className: treatment.verified ? "card" : "card unverified" },
    treatment.verified ? null : el("p", { className: "warning", text: `Not verified by the fact-check (verdict: ${treatment.verdict}).` }),
    el("h4", { text: treatment.headline || `Finding ${treatment.finding_id}` }),
    el(
      "p",
      {},
      el("span", { className: "tag", text: `Finding ${treatment.finding_id}` }),
      el("span", { className: treatment.verified ? "tag" : "tag strong", text: `Fact-check verdict: ${treatment.verdict}` }),
      el("span", { className: "tag", text: `Confidence: ${treatment.confidence || "not stated"}` }),
      treatment.quotes ? el("span", { className: "tag", text: `Quoted passages: ${treatment.quotes}` }) : null,
    ),
    treatment.summary ? el("p", { className: "quote", text: treatment.summary }) : null,
    treatment.why_it_matters ? el("p", { className: "quote muted", text: `Why it matters: ${treatment.why_it_matters}` }) : null,
  );
}

function reportDraft(content) {
  return [
    el("h3", { text: content.draft_title || "Untitled draft" }),
    content.deck ? el("p", { className: "quote", text: content.deck }) : null,
    el("h3", { text: "Findings, in the order the report presents them" }),
    content.treatments.length ? content.treatments.map(treatmentCard) : el("p", { className: "muted", text: "The draft treats no findings." }),
    content.diagrams.length
      ? [el("h3", { text: "Diagrams" }), list(content.diagrams.map((diagram) => (diagram.caption ? `${diagram.title}: ${diagram.caption}` : diagram.title)), "")]
      : null,
    el("h3", { text: "Caveats" }),
    list(content.caveats, "The draft states no caveats."),
    el("h3", { text: "Next steps" }),
    list(content.next_steps, "The draft lists no next steps."),
  ];
}

function reportContent(content) {
  return el(
    "section",
    { "aria-labelledby": "report-heading" },
    el("h2", { id: "report-heading", text: content.title || "Public report" }),
    content.draft_ready
      ? [el("p", { text: "This is the draft that publishing renders. Read it before you decide." }), el("div", { className: "card" }, reportDraft(content))]
      : el("p", { text: "No report draft exists yet. Ask the agent to draft the report first, or decline it." }),
  );
}

function ingestContent(content, items) {
  const batch = content.batch ?? {};
  const records = batch.records ?? [];
  const flags = items.filter((item) => item.kind === "flag");
  const selection = items.filter((item) => item.kind === "selection");
  const open = flags.filter((item) => item.final === null).length;
  const stateLine = {
    final: `The final batch is built. Approving binds batch ${batch.batch_sha256 ?? ""} through the digest below.`,
    draft: "This is a draft: some flags still need a disposition.",
    newsroom: "Newsroom cases are reviewed in the newsroom's own system; nothing is written to this computer's wiki.",
  }[batch.state];
  return [
    el(
      "section",
      { "aria-labelledby": "batch-heading" },
      el("h2", { id: "batch-heading", text: "Knowledge batch" }),
      stateLine ? el("p", { text: stateLine }) : null,
      records.length
        ? list(records.map((record) => `${record.type} ${record.id} v${record.version}: ${record.text}`), "")
        : el("p", { className: "muted", text: "No batch preview is available for this case. Deciding here records only whether to ingest." }),
    ),
    flags.length
      ? el(
          "section",
          { "aria-labelledby": "flags-heading" },
          el("h2", { id: "flags-heading", text: `Flags needing a disposition (${open} open of ${flags.length})` }),
          flags.map((item) => dispositionCard(item, null, FLAG_LABELS, `Flag on ${item.target}`)),
        )
      : null,
    selection.length
      ? el(
          "section",
          { "aria-labelledby": "selection-heading" },
          el("h2", { id: "selection-heading", text: "Proposed records" }),
          selection.map((item) => selectionCard(item, null, `Include ${item.target} in the batch`)),
        )
      : null,
  ].filter(Boolean);
}

// ---------------------------------------------------------------- research

const MARK_LABELS = { pursue: "Pursue", dismiss: "Dismiss", keep: "Keep", drop: "Drop", add: "Add to the plan" };
const CLUE_TYPE_LABELS = {
  watchlist: "Watchlist",
  premise_complicated: "Premise complicated",
  premise_contradicted: "Premise contradicted",
  conflict: "Conflict",
  new_actor: "New actor",
  change: "Change",
  absence: "Absence",
  change_or_absence: "Change or absence",
  timing: "Timing",
  shared_identifier: "Shared identifier",
};
const LEAD_FLAG_LABELS = {
  model_unsure: "Model unsure: the two option orders disagreed, so the answer had no effect",
  identity_unconfirmed: "Identity unconfirmed: the name matches, nothing else does",
  allegation: "Allegation: someone claims this",
  interpretation: "Interpretation: never a factual lead",
  status_unknown: "Status unknown: the item does not show whether it happened",
  summary_widens: "Summary widens what the excerpt says",
  summary_contradicts: "Summary contradicts the excerpt",
  summary_unsupported: "Summary not supported by the excerpt",
  absence_is_not_clearance: "Absence is not clearance",
  snippet_only: "Snippet only: cannot support a finding",
  summary_refused: "Summary refused: a figure is not in the excerpt",
  model_suggests_set_aside: "The model would set this aside; while triage only flags, that has no effect",
};
const LABEL_NAMES = { identity: "Identity", source: "Source", assertion: "Assertion", status: "Status" };
const CONTEXT_LABELS = { circumstantial: "Circumstantial", outside_directions: "Outside the directions" };
const SECTIONS = [
  ["circumstantial", "Circumstantial", "Leads about another person, company or event than the brief's. They are kept, never set aside for it: circumstantial material is what clues are made of."],
  ["flagged", "Flagged by triage", "Leads triage flagged for a closer look. A flag never removes a lead."],
  ["set_aside", "Set aside", "Leads set aside by their lane or by triage. They stay here so nothing is lost; read them before you approve."],
  ["snippet", "Snippet only", "Only a search snippet was kept, not the page. A snippet is a case note and cannot support a finding."],
  ["unverifiable", "Unverifiable", "The excerpt was not found word for word in the stored source. The lead is kept, never deleted."],
];
const SECTION_NAMES = { direction: "under its direction", circumstantial: "circumstantial", flagged: "flagged", set_aside: "set aside", snippet: "snippet only", unverifiable: "unverifiable" };
let newDirection = { key: "", value: "" };

function words(value) {
  return String(value ?? "").replace(/_/g, " ");
}

function usd(value) {
  return typeof value === "number" ? `$${value.toFixed(value < 0.1 && value > 0 ? 3 : 2)}` : "not set";
}

function runSection(content) {
  const run = content.run;
  const spend = run.spend;
  const spendLine = run.mode === "subscription_subagents"
    ? "Subscription subagents report no spend."
    : spend.reported
      ? `Spent ${usd(spend.round_usd)} of a ${usd(spend.budget_usd)} round budget; ${usd(spend.case_usd)} of ${usd(spend.case_budget_usd)} for the case.`
      : "No spend was recorded for this round.";
  return el(
    "section",
    { "aria-labelledby": "run-heading", className: "card" },
    el("h2", { id: "run-heading", text: "This research round" }),
    el("p", {}, el("strong", { text: "Research mode: " }), run.mode_label),
    run.round ? el("p", { text: `Round ${run.round}.` }) : null,
    el("p", { text: spendLine }),
    run.stopped ? el("p", {}, el("strong", { text: "The round stopped: " }), run.stopped.message || run.stopped.code) : null,
    el("h3", { text: `Topics left out (${run.left_out.length})` }),
    list(run.left_out.map((topic) => (topic.reason ? `${topic.topic_id}: ${topic.reason}` : topic.topic_id)), "Every approved topic ran."),
    run.refused.length ? [el("h3", { text: `Topics refused (${run.refused.length})` }), list(run.refused.map((topic) => `${topic.topic_id}: ${topic.reason}`), "")] : null,
    run.lanes.length ? [el("h3", { text: "Lanes that did not complete" }), list(run.lanes.map((lane) => `${lane.topic_id}: ${words(lane.status)}`), "")] : null,
  );
}

function triageNotice(content) {
  const triage = content.triage;
  const lines = [];
  if (triage.state === "missing") lines.push("Triage has not run: leads are ordered by the clue rules and the source checks only.");
  if (triage.state === "broken") lines.push("Triage results break the triage rules and are not used on this page.");
  if (triage.flag_only && triage.notice) lines.push(triage.notice);
  if (triage.state === "ok" && triage.backends?.length) lines.push(`Answered by: ${triage.backends.join(", ")}.`);
  if (content.suggestions === "missing") lines.push("The investigator has not suggested marks: every clue starts unmarked.");
  if (!lines.length) return null;
  return el("section", { "aria-labelledby": "triage-heading", className: "card" }, el("h2", { id: "triage-heading", text: "About these results" }), lines.map((line) => el("p", { text: line })));
}

function leadTags(lead) {
  return el(
    "p",
    {},
    el("span", { className: "tag", text: `Lead ${lead.lead_id}` }),
    el("span", { className: "tag", text: `Rank ${lead.rank}` }),
    el("span", { className: "tag", text: `Topic: ${lead.topic_id}` }),
    lead.absence ? el("span", { className: "tag strong", text: "Absence: nothing found" }) : el("span", { className: "tag", text: lead.stored === "snippet" ? "Snippet only" : "Stored page" }),
    lead.status !== "candidate" ? el("span", { className: "tag strong", text: words(lead.status) }) : null,
    lead.source_families ? el("span", { className: "tag", text: `Source families: ${lead.source_families}` }) : null,
    lead.copies ? el("span", { className: "tag", text: `Copies collapsed: ${lead.copies}` }) : null,
    lead.journalist_input ? el("span", { className: "tag strong", text: "Journalist input (local only)" }) : null,
    (lead.triage?.context ?? []).map((context) => el("span", { className: "tag", text: CONTEXT_LABELS[context] ?? words(context) })),
    lead.clues.map((clue) => el("span", { className: "tag strong", text: `Clue: ${words(clue)}` })),
  );
}

function leadCard(lead, directionLabel) {
  const triage = lead.triage;
  const labels = triage ? Object.entries(triage.labels) : [];
  return el(
    "article",
    { className: "card", id: `lead-${lead.rank}`, "aria-label": `Lead ${lead.lead_id}` },
    el("h4", { text: lead.summary || `Lead ${lead.lead_id}` }),
    leadTags(lead),
    directionLabel ? el("p", { className: "muted", text: `Direction: ${directionLabel}` }) : null,
    lead.excerpt ? el("p", { className: "quote" }, el("strong", { text: lead.direct_quote ? "Excerpt (verbatim): " : "Excerpt (not a direct quote): " }), lead.excerpt) : null,
    lead.source_url ? el("p", { className: "quote muted", text: `Source: ${lead.source_url}` }) : null,
    lead.attribution ? el("p", { className: "quote", text: `Reported claim: ${lead.attribution.actor} ${lead.attribution.stance} (“${lead.attribution.verbatim}”)` }) : null,
    lead.absence && lead.queries_tried.length ? el("p", { className: "muted", text: `Queries tried: ${lead.queries_tried.join("; ")}` }) : null,
    labels.length ? el("p", { className: "muted", text: `Triage: ${labels.map(([name, value]) => `${LABEL_NAMES[name] ?? name} ${words(value)}`).join("; ")}.` }) : null,
    triage?.flags.length
      ? el("ul", { "aria-label": `Flags on lead ${lead.lead_id}` }, triage.flags.map((flag) => el("li", { text: `${LEAD_FLAG_LABELS[flag.reason] ?? words(flag.reason)}${flag.detail ? ` (${flag.detail})` : ""}` })))
      : null,
  );
}

function markGroup(item, options, groupLabel) {
  const name = domId("mark", item.key);
  return el(
    "div",
    { className: "choices", role: "radiogroup", "aria-label": groupLabel },
    options.map(([value, label]) => {
      const id = `${name}-${value ?? "none"}`;
      return el(
        "span",
        { className: "choice" },
        el("input", {
          type: "radio",
          id,
          name,
          value: value ?? "",
          checked: item.final === value,
          disabled: readOnly() || busy,
          on: { change: () => edit({ kind: "mark", key: item.key, value }) },
        }),
        el("label", { for: id, text: label }),
      );
    }),
  );
}

function clueCard(clue, item, leadsById, index) {
  const name = domId("mark", item.key);
  const options = item.protected ? [["pursue", "Pursue"]] : [["pursue", "Pursue"], ["dismiss", "Dismiss"]];
  return el(
    "fieldset",
    { className: "card", "aria-describedby": `${name}-state` },
    el("legend", { text: `Clue ${index + 1}: ${CLUE_TYPE_LABELS[clue.type] ?? words(clue.type)} (${clue.source})` }),
    el(
      "p",
      {},
      item.protected ? el("span", { className: "tag strong", text: "Always pursued" }) : null,
      clue.model_unsure ? el("span", { className: "tag strong", text: "Model unsure" }) : null,
      clue.conflict_answer?.counted ? el("span", { className: "tag", text: `Conflict question: ${words(clue.conflict_answer.value)}` }) : null,
    ),
    el("p", { className: "quote", text: clue.summary }),
    clue.warning ? el("p", {}, el("strong", { text: "Warning: " }), clue.warning) : null,
    el(
      "ul",
      { "aria-label": `Leads behind clue ${index + 1}` },
      clue.lead_ids.map((id) => {
        const lead = leadsById.get(id);
        return el("li", { text: lead ? `Lead ${id} (rank ${lead.rank}, ${SECTION_NAMES[lead.section]}): ${lead.summary}` : `Lead ${id}` });
      }),
    ),
    suggestionLine(item),
    el("p", { id: `${name}-state` }, changeTag(item)),
    markGroup(item, options, `Mark for clue ${index + 1}`),
    item.protected ? el("p", { className: "muted", text: "Watchlist and premise clues cannot be dismissed." }) : null,
    noteField(item),
  );
}

function directionCard(direction, item, leads) {
  const name = item ? domId("mark", item.key) : "";
  const counts = direction?.counts;
  const label = item?.label || direction?.label || direction?.direction_id;
  const options = item?.planned ? [["keep", "Keep"], ["drop", "Drop"]] : [["add", "Add to the plan"], [null, "Withdraw"]];
  return el(
    "section",
    { className: "card", "aria-label": `Direction: ${label}` },
    el("h3", { text: item && !item.planned ? `New direction: ${label}` : `Direction: ${label}` }),
    direction && !direction.planned ? el("p", { className: "muted", text: "This direction is not in the approved investigation plan." }) : null,
    counts
      ? el("p", { className: "muted", text: `${direction.total} leads: ${counts.direction} listed here, ${counts.circumstantial} circumstantial, ${counts.flagged} flagged, ${counts.set_aside} set aside, ${counts.snippet} snippet only, ${counts.unverifiable} unverifiable.` })
      : null,
    direction?.gap ? el("p", {}, el("strong", { text: "Gap: " }), `no qualifying lead.${direction.gap.length ? ` Queries tried: ${direction.gap.join("; ")}` : ""}`) : null,
    item
      ? el(
          "fieldset",
          { "aria-describedby": `${name}-state` },
          el("legend", { text: `Mark for ${label}` }),
          suggestionLine(item),
          el("p", { id: `${name}-state` }, changeTag(item)),
          markGroup(item, options, `Mark for direction ${label}`),
          noteField(item),
        )
      : null,
    leads.map((lead) => leadCard(lead)),
  );
}

function addDirectionForm() {
  const reason = !newDirection.key.trim() ? "Give the direction a short id." : !newDirection.value.trim() ? "Describe the direction." : "";
  const unavailable = readOnly() || busy || Boolean(reason);
  return el(
    "div",
    { className: "card" },
    el("h3", { text: "Add a direction" }),
    el("label", { for: "new-direction-id", text: "Short id (lowercase letters, digits and hyphens)" }),
    el("input", {
      type: "text",
      id: "new-direction-id",
      maxlength: "64",
      value: newDirection.key,
      disabled: readOnly() || busy,
      on: { input: (event) => { newDirection.key = event.target.value; refreshAddDirection(); } },
    }),
    el("label", { for: "new-direction-text", text: "What should it establish?" }),
    el("textarea", {
      id: "new-direction-text",
      maxlength: "1000",
      value: newDirection.value,
      disabled: readOnly() || busy,
      on: { input: (event) => { newDirection.value = event.target.value; refreshAddDirection(); } },
    }),
    el("div", { id: "add-direction-controls" }, addDirectionControls(reason, unavailable)),
  );
}

function addDirectionControls(reason, unavailable) {
  return [
    reason ? el("p", { id: "add-direction-reason", className: "muted", text: reason }) : null,
    el("button", {
      type: "button",
      id: "add-direction",
      className: "secondary",
      "aria-disabled": unavailable ? "true" : "false",
      "aria-describedby": reason ? "add-direction-reason" : undefined,
      text: "Add the direction",
      on: { click: () => (unavailable ? warn(reason || "The review is busy.") : addDirection()) },
    }),
  ].filter(Boolean);
}

function refreshAddDirection() {
  const reason = !newDirection.key.trim() ? "Give the direction a short id." : !newDirection.value.trim() ? "Describe the direction." : "";
  document.getElementById("add-direction-controls")?.replaceChildren(...addDirectionControls(reason, readOnly() || busy || Boolean(reason)));
}

async function addDirection() {
  const change = { kind: "add_direction", key: newDirection.key.trim(), value: newDirection.value.trim() };
  await edit(change);
  if (view.items.some((item) => item.key === `direction:${change.key}`)) newDirection = { key: "", value: "" };
  if (view) render(view);
}

function researchContent(content, items) {
  const leadsById = new Map(content.leads.map((lead) => [lead.lead_id, lead]));
  const itemsByKey = new Map(items.map((item) => [item.key, item]));
  const directionLabels = new Map(content.directions.map((direction) => [direction.direction_id, direction.label]));
  const clueItems = items.filter((item) => item.kind === "clue");
  const open = clueItems.filter((item) => item.final === null).length;
  const directionItems = items.filter((item) => item.kind === "direction");
  const shownDirections = new Set(content.directions.map((direction) => `direction:${direction.direction_id}`));
  const sections = SECTIONS.map(([section, heading, explanation]) => {
    const leads = content.leads.filter((lead) => lead.section === section);
    return el(
      "section",
      { "aria-labelledby": `${section}-heading` },
      el("h2", { id: `${section}-heading`, text: `${heading} (${leads.length})` }),
      el("p", { className: "muted", text: explanation }),
      leads.length ? leads.map((lead) => leadCard(lead, directionLabels.get(lead.direction_id) ?? lead.direction_id)) : el("p", { className: "muted", text: "None." }),
    );
  });
  return [
    runSection(content),
    triageNotice(content),
    el(
      "section",
      { "aria-labelledby": "clues-heading" },
      el("h2", { id: "clues-heading", text: `Clues (${content.clues.length}; ${open} unmarked)` }),
      el("p", { className: "muted", text: "Most telling first. Mark each clue pursue or dismiss; the investigator turns your marks into the methodology change." }),
      content.clues.length
        ? content.clues.map((clue, index) => clueCard(clue, itemsByKey.get(`clue:${clue.clue_id}`), leadsById, index))
        : el("p", { className: "muted", text: "The rules found no clue in these leads." }),
    ),
    el(
      "section",
      { "aria-labelledby": "directions-heading" },
      el("h2", { id: "directions-heading", text: `Directions and their leads (${content.lead_total} leads in all)` }),
      el("p", { className: "muted", text: "Keep or drop each direction. A direction with no mark stays in the plan. Leads that are circumstantial, flagged, set aside, snippet only or unverifiable are listed in their own sections below." }),
      content.directions.map((direction) =>
        directionCard(direction, itemsByKey.get(`direction:${direction.direction_id}`), content.leads.filter((lead) => lead.direction_id === direction.direction_id && lead.section === "direction")),
      ),
      directionItems.filter((item) => !shownDirections.has(item.key)).map((item) => directionCard(null, item, [])),
      readOnly() ? null : addDirectionForm(),
    ),
    ...sections,
  ].filter(Boolean);
}

// ---------------------------------------------------------------- decision

function decisionBlockers() {
  if (readOnly()) return "";
  if (view.blocking.length) return "Approval is unavailable while a blocking failure is open.";
  if (view.open_required && view.gate === "research") return `Approval is unavailable until every clue is marked pursue or dismiss (${view.open_required} open).`;
  if (view.open_required) return `Approval is unavailable until every flag has a disposition (${view.open_required} open).`;
  return "";
}

function memberName() {
  return (reviewerDraft ?? view.reviewer).trim();
}

function reviewerField() {
  return el(
    "div",
    {},
    view.previous_reviewer
      ? el("p", { className: "muted", text: `Name entered for the previous decision on this gate: ${view.previous_reviewer}. Each decision records its own name.` })
      : null,
    el("label", { for: "reviewer", text: "Your name (recorded with your decision)" }),
    el("input", {
      type: "text",
      id: "reviewer",
      autocomplete: "name",
      maxlength: "200",
      value: reviewerDraft ?? view.reviewer,
      disabled: readOnly() || busy,
      on: { input: (event) => typeReviewer(event.target.value), change: () => flushReviewer() },
    }),
  );
}

function reasonFor(blocker, missingDraft, missingName) {
  if (blocker) return blocker;
  if (missingName) return "Enter your name before deciding.";
  if (missingDraft) return "Publishing needs a report draft. Ask the agent to draft the report, or decline it.";
  return "";
}

function reasonLine(blocker, missingName, missingDraft) {
  const reason = reasonFor(blocker, missingDraft, missingName);
  return reason ? el("p", { id: "decide-reason", text: reason }) : null;
}

function decisionControls() {
  const blocker = decisionBlockers();
  const missingName = !memberName();
  return [
    el("p", { text: "Digest of the record you are deciding:" }),
    el("code", { className: "digest", id: "digest", text: view.digest }),
    reasonLine(blocker, missingName, view.gate === "report" && !view.content.draft_ready),
    view.decisions.map((decision) => {
      const label = DECISION_LABELS[view.gate][decision];
      const missingDraft = view.gate === "report" && decision === "completed" && !view.content.draft_ready;
      // Declining ingestion needs no finished batch, as the backend allows.
      const blocked = Boolean(blocker) && !(view.gate === "ingest" && decision === "declined");
      const unavailable = readOnly() || busy || blocked || missingDraft || missingName;
      // aria-disabled keeps the button in the tab order so a keyboard user
      // can reach it and hear why it is unavailable; the handler refuses.
      return el("button", {
        type: "button",
        id: `decide-${decision}`,
        className: decision === "declined" ? "secondary" : undefined,
        "aria-disabled": unavailable ? "true" : "false",
        "aria-describedby": unavailable ? "decide-reason" : undefined,
        text: label,
        on: { click: () => (unavailable ? warn(reasonFor(blocker, missingDraft, missingName)) : decide(decision)) },
      });
    }),
  ];
}

function followUpControls() {
  const reason = !memberName() ? "Enter your name before deciding." : !followUpNote.trim() ? "Describe the follow-up you want first." : "";
  const unavailable = readOnly() || busy || Boolean(reason);
  return [
    reason ? el("p", { id: "follow-up-reason", text: reason }) : null,
    el("button", {
      type: "button",
      id: "request-follow-up",
      className: "secondary",
      "aria-disabled": unavailable ? "true" : "false",
      "aria-describedby": unavailable && reason ? "follow-up-reason" : undefined,
      text: "Request follow-up",
      on: { click: () => (unavailable ? warn(reason) : requestFollowUp()) },
    }),
  ];
}

function followUpSection() {
  return el(
    "div",
    { className: "card" },
    el("h3", { text: "Or send the case back to Investigation" }),
    el("p", { className: "muted", text: "The agent investigates again with your note, then brings the case back to this gate." }),
    el("label", { for: "follow-up-note", text: "What should the follow-up establish? (required)" }),
    el("textarea", {
      id: "follow-up-note",
      maxlength: "4000",
      value: followUpNote,
      disabled: readOnly() || busy,
      on: {
        input: (event) => {
          followUpNote = event.target.value;
          refreshControls();
        },
      },
    }),
    el("div", { id: "follow-up-controls" }, followUpControls()),
  );
}

function decisionSection() {
  const counts = view.counts;
  return el(
    "section",
    { "aria-labelledby": "decision-heading" },
    el("h2", { id: "decision-heading", text: "Decision" }),
    counts.items
      ? el("p", { className: "muted", text: `Items: ${counts.items}. As suggested: ${counts.accepted_unchanged}. Changed: ${counts.changed}. Open: ${counts.open_required}.` })
      : null,
    reviewerField(),
    el("div", { id: "decision-controls" }, decisionControls()),
    view.gate === "investigation_gate" ? followUpSection() : null,
  );
}

// Re-renders only the buttons and reasons, so a field being typed in keeps
// its caret and its text.
function refreshControls() {
  if (readOnly()) return;
  const focused = document.activeElement?.id;
  document.getElementById("decision-controls")?.replaceChildren(...decisionControls().flat().filter(Boolean));
  document.getElementById("follow-up-controls")?.replaceChildren(...followUpControls().filter(Boolean));
  if (focused && document.activeElement?.id !== focused) document.getElementById(focused)?.focus();
}

// Who asked, as the receipt's channel records it: the member on this page,
// the agent (by role), the command line, or a follow-up from before
// follow-ups recorded who asked.
function recordedThrough(receipt) {
  if (receipt.channel === "page") return "this review page";
  if (receipt.channel === "agent") return `the agent (${receipt.requested_by_role || "unknown role"})`;
  if (receipt.channel === "legacy") return "not recorded (an older follow-up)";
  return "the command line";
}

function receiptSection() {
  const receipt = view.receipt;
  if (!receipt) {
    return el("section", { "aria-labelledby": "receipt-heading" }, el("h2", { id: "receipt-heading", text: "Read-only" }), el("p", { text: "This gate is not waiting for a decision now. Return to the agent." }));
  }
  const rows = [
    ["Decision", DECISION_NAMES[receipt.decision] ?? receipt.decision],
    ["By", receipt.approved_by],
    ["At", receipt.approved_at],
    ["Digest", receipt.review_digest],
    ["Recorded through", recordedThrough(receipt)],
  ].filter(([, value]) => value);
  return el(
    "section",
    { "aria-labelledby": "receipt-heading", className: "card" },
    el("h2", { id: "receipt-heading", tabindex: "-1", text: "Decision recorded" }),
    el("dl", {}, rows.flatMap(([name, value]) => [el("dt", { text: name }), el("dd", { text: value })])),
    el("p", { text: "The review is read-only. You can close this tab and tell the agent you are done." }),
  );
}

// ---------------------------------------------------------------- wiki changes

// Diffs are untrusted: each line goes in as text, never as HTML.
function diffBlock(diff) {
  return el(
    "pre",
    { className: "diff" },
    String(diff ?? "").split("\n").map((line) =>
      el("span", { className: line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "", text: `${line}\n` }),
    ),
  );
}

function wikiChangeCard(change) {
  const unavailable = busy || ended ? "true" : "false";
  const heading = domId("wiki", change.id, "heading");
  return el(
    "article",
    { className: "card", "aria-labelledby": heading },
    el("h3", { id: heading, text: change.summary || change.op }),
    el("p", { className: "muted", text: `${change.op} · staged ${change.created}` }),
    list(change.paths ?? [], "No files."),
    change.stale ? el("p", { className: "warning", text: "A file changed since this was staged, so it can no longer be applied. Discard it, or ask the agent to stage it again." }) : null,
    diffBlock(change.diff),
    change.stale ? null : el("button", { id: domId("wiki", change.id, "apply"), type: "button", "aria-disabled": unavailable, text: "Apply", on: { click: () => confirmWiki("apply", change) } }),
    el("button", { id: domId("wiki", change.id, "discard"), type: "button", className: "secondary", "aria-disabled": unavailable, text: "Discard", on: { click: () => confirmWiki("discard", change) } }),
  );
}

function wikiSection() {
  return el(
    "section",
    { id: "wiki-section", "aria-labelledby": "wiki-heading" },
    el("h2", { id: "wiki-heading", text: `Wiki changes (${wikiChanges.length})` }),
    el("p", { text: "The agent staged these writes to your wiki. Nothing is written until you apply it." }),
    wikiChanges.map(wikiChangeCard),
  );
}

// Never rejects: a request that fails outright becomes the shown error.
async function loadWiki() {
  wikiError = "";
  try {
    const { status, data } = await post("/api/wiki-changes", {});
    if (status === 200 && Array.isArray(data.changes)) wikiChanges = data.changes;
    else if (status !== 403) wikiError = data.message || "Could not read staged changes.";
  } catch {
    wikiError = "Could not reach the review page's local server.";
  }
}

function wikiErrorSection() {
  return el(
    "section",
    { id: "wiki-section", "aria-labelledby": "wiki-heading" },
    el("h2", { id: "wiki-heading", text: "Wiki changes" }),
    el("p", { className: "warning", text: wikiError }),
    el("button", { id: "wiki-retry", type: "button", className: "secondary", text: "Try again", on: { click: retryWiki } }),
  );
}

// The button stays disabled until the load ends, so clicks do not pile up.
function retryWiki(event) {
  event.currentTarget.disabled = true;
  void loadWiki().finally(showWiki);
}

// Draws only the wiki section beside a gate, so nothing the member is typing
// is replaced; with no gate the page is the wiki section.
function showWiki() {
  if (!view.gate) return render(view);
  const section = wikiError ? wikiErrorSection() : wikiChanges?.length ? wikiSection() : null;
  const current = document.getElementById("wiki-section");
  if (current) current.replaceWith(...(section ? [section] : []));
  else if (section) app.append(section);
}

async function confirmWiki(action, change) {
  if (busy || ended) return;
  warn("");
  setBusy(true);
  say(action === "apply" ? "Applying the wiki change…" : "Discarding the wiki change…");
  try {
    const { status, data } = await post(`/api/wiki-changes/${action}`, { id: change.id, digest: change.digest });
    if (Array.isArray(data.changes)) wikiChanges = data.changes;
    const done = action === "apply" ? "Wiki change applied." : "Wiki change discarded.";
    if (status === 200) say(Array.isArray(data.changes) ? done : `${done} Reload the page to refresh the list.`);
    else if (status !== 403) {
      warn(data.message || "The wiki change was refused.");
      say(data.code === "digest-changed" ? "Reloaded." : data.code === "refused" ? "Nothing changed." : "Reload the page.");
    }
  } finally {
    setBusy(false);
  }
}

// ---------------------------------------------------------------- render

function render(next) {
  view = next;
  title.textContent = `Spotlight review: ${view.gate_label || boot.gates?.[view.gate] || view.gate}`;
  keepFocus(() => {
    // No gate pending: the page shows only the staged wiki changes.
    if (!view.gate) {
      app.replaceChildren(
        wikiError
          ? wikiErrorSection()
          : wikiChanges === null
            ? el("p", { text: "Loading the wiki changes…" })
            : wikiChanges.length
              ? wikiSection()
              : el("p", { text: "No wiki changes are waiting. You can close this page." }),
      );
      return;
    }
    const parts = [];
    if (readOnly()) parts.push(receiptSection());
    if (view.blocking.length) {
      parts.push(
        el(
          "section",
          { "aria-labelledby": "blocking-heading", className: "card" },
          el("h2", { id: "blocking-heading", text: `Blocking failures (${view.blocking.length})` }),
          el("p", { text: "These must be fixed in the case before approval. Ask the agent to fix them, then reload this page." }),
          list(view.blocking, ""),
        ),
      );
    }
    if (view.gate === "methodology") parts.push(methodologyContent(view.content));
    if (view.gate === "research") parts.push(...researchContent(view.content, view.items));
    if (view.gate === "investigation_gate") parts.push(...investigationContent(view.content, view.items));
    if (view.gate === "report") parts.push(reportContent(view.content));
    if (view.gate === "ingest") parts.push(...ingestContent(view.content, view.items));
    if (!readOnly()) parts.push(decisionSection());
    if (wikiError) parts.push(wikiErrorSection());
    else if (wikiChanges?.length) parts.push(wikiSection());
    app.replaceChildren(...parts);
  });
  app.setAttribute("aria-busy", busy ? "true" : "false");
}

async function load() {
  say("Loading the review…");
  const { status, data } = await post("/api/view", {});
  if (status !== 200) {
    if (status !== 403) warn(data.message || "The review could not load.");
    return;
  }
  render(data);
  if (view.gate) say(readOnly() ? "This gate is decided." : "Review loaded. Your edits are saved as you make them.");
  // The gate shows at once; the staged wiki changes follow when Engine answers.
  await loadWiki();
  showWiki();
  if (!view.gate && !wikiError) say("Wiki changes loaded.");
}

function typeReviewer(value) {
  reviewerDraft = value;
  refreshControls();
  clearTimeout(reviewerTimer);
  reviewerTimer = setTimeout(() => flushReviewer(), 400);
}

// Saves the typed name. It does not re-render the field, so typing goes on
// while it saves; every decision waits for it first.
async function flushReviewer() {
  clearTimeout(reviewerTimer);
  while (reviewerSaving) await reviewerSaving;
  if (reviewerDraft === null || readOnly() || reviewerDraft.trim() === view.reviewer) return;
  const value = reviewerDraft;
  reviewerSaving = (async () => {
    const { status, data } = await post("/api/edit", { edit: { kind: "reviewer", value } });
    if (status === 200) {
      view = data;
      say("Saved.");
    } else if (data.view) {
      view = data.view;
      warn(data.message);
    } else if (status !== 403) {
      warn(data.message || "Your name was not saved.");
    }
  })();
  try {
    await reviewerSaving;
  } finally {
    reviewerSaving = null;
  }
  if (readOnly()) render(view);
  else refreshControls();
}

async function edit(change) {
  if (busy || readOnly()) return;
  await flushReviewer();
  if (busy || readOnly()) return;
  warn("");
  setBusy(true);
  say("Saving…");
  try {
    const { status, data } = await post("/api/edit", { edit: change });
    if (status === 200) {
      view = data;
      say("Saved.");
    } else if (data.view) {
      view = data.view;
      warn(data.message);
    } else if (status !== 403) {
      warn(data.message || "The edit was refused. Nothing was saved.");
      say("Not saved.");
    }
  } finally {
    setBusy(false);
  }
}

async function decide(decision) {
  if (busy || readOnly()) return;
  await flushReviewer();
  if (busy || readOnly()) return;
  warn("");
  setBusy(true);
  say("Recording the decision…");
  try {
    const { status, data } = await post("/api/decide", { decision, digest: view.digest });
    if (status === 200) {
      view = data;
      say("Decision recorded.");
    } else if (data.view) {
      view = data.view;
      warn(data.message);
      say("Reloaded.");
    } else if (status !== 403) {
      warn(data.message || "The decision was refused.");
      say("Not recorded.");
    }
  } finally {
    setBusy(false);
  }
}

async function requestFollowUp() {
  if (busy || readOnly()) return;
  await flushReviewer();
  if (busy || readOnly()) return;
  warn("");
  setBusy(true);
  say("Recording the follow-up request…");
  try {
    const { status, data } = await post("/api/follow-up", { instructions: followUpNote, digest: view.digest });
    if (status === 200) {
      view = data;
      followUpNote = "";
      say("Follow-up requested.");
    } else if (data.view) {
      view = data.view;
      warn(data.message);
      say("Reloaded.");
    } else if (status !== 403) {
      warn(data.message || "The follow-up request was refused.");
      say("Not recorded.");
    }
  } finally {
    setBusy(false);
  }
}

async function start() {
  const capability = location.hash.slice(1);
  const showWiki = new URLSearchParams(location.search).get("view") === "wiki";
  history.replaceState(null, "", location.pathname);
  const { status } = await post("/session", { capability });
  if (status !== 200) return;
  await load();
  // Opened after an agent staged a wiki write: go straight to the changes.
  if (showWiki) document.getElementById("wiki-heading")?.scrollIntoView();
}

start().catch(() => warn("The review page could not reach its local server. Ask the agent to reopen the review."));
