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

const DECISION_LABELS = {
  methodology: { approved: "Approve the methodology" },
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
  if (item.suggested === null) return el("p", { className: "muted", text: `No suggestion: you decide. ${item.suggestion_reason}` });
  const shown = typeof item.suggested === "boolean" ? (item.suggested ? "include" : "leave out") : DISPOSITION_LABELS[item.suggested] ?? FLAG_LABELS[item.suggested];
  return el("p", { className: "muted", text: `Suggested: ${shown}. Source: ${item.suggestion_source}. ${item.suggestion_reason}` });
}

function changeTag(item) {
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

// ---------------------------------------------------------------- decision

function decisionBlockers() {
  if (readOnly()) return "";
  if (view.blocking.length) return "Approval is unavailable while a blocking failure is open.";
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

// ---------------------------------------------------------------- render

function render(next) {
  view = next;
  title.textContent = `Spotlight review: ${view.gate_label || boot.gates?.[view.gate] || view.gate}`;
  keepFocus(() => {
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
    if (view.gate === "investigation_gate") parts.push(...investigationContent(view.content, view.items));
    if (view.gate === "report") parts.push(reportContent(view.content));
    if (view.gate === "ingest") parts.push(...ingestContent(view.content, view.items));
    if (!readOnly()) parts.push(decisionSection());
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
  say(readOnly() ? "This gate is decided." : "Review loaded. Your edits are saved as you make them.");
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
  history.replaceState(null, "", location.pathname);
  const { status } = await post("/session", { capability });
  if (status !== 200) return;
  await load();
}

start().catch(() => warn("The review page could not reach its local server. Ask the agent to reopen the review."));
