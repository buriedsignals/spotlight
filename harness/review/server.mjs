// Loopback review server for one case's gate pages (Splash studio pattern).
// Binds 127.0.0.1 on a random port, checks Host on every request and Origin on
// every POST, swaps the one-time capability in the URL fragment for an
// HttpOnly session cookie, and stamps a fresh CSP nonce on the shell's own
// tags. Case text reaches the page only as JSON data.
import { randomBytes } from "node:crypto";
import { createServer } from "node:http";
import { pendingGate } from "./case-bridge.mjs";
import { inlineJson, renderShell } from "./shell.mjs";

const BODY_LIMIT = 64 << 10;
const MAX_FOLLOW_UP = 4000;
const REQUEST_TIMEOUT_MS = 150_000;
const ENTITY_ID = /^[a-z0-9][a-z0-9_-]{0,127}$/;
const MAX_ENTITY_MENTIONS = 50;
const MAX_CANDIDATES = 10;
// A gate review can take a while; the idle limit resets on every page request.
export const DEFAULT_IDLE_MS = 45 * 60_000;
export const DEFAULT_OVERALL_MS = 8 * 60 * 60_000;
const GATE_LABELS = {
  methodology: "Methodology",
  research: "Research",
  investigation_gate: "Investigation gate",
  report: "Report",
  ingest: "Ingest",
};

function randomToken() {
  return randomBytes(32).toString("base64url");
}

function exactObject(value, fields, label) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw Object.assign(new Error(`${label} must be an object`), { status: 400 });
  const keys = Object.keys(value);
  if (keys.length !== fields.length || fields.some((field) => !keys.includes(field)))
    throw Object.assign(new Error(`${label} has the wrong fields`), { status: 400 });
  return value;
}

function securityHeaders(contentType, nonce = "") {
  const headers = {
    "cache-control": "no-store, max-age=0",
    "content-type": contentType,
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
  };
  headers["content-security-policy"] = contentType.startsWith("text/html")
    ? [
        "default-src 'none'",
        `script-src 'nonce-${nonce}'`,
        `style-src 'nonce-${nonce}'`,
        "connect-src 'self'",
        "img-src 'self'",
        "form-action 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
      ].join("; ")
    : "default-src 'none'; frame-ancestors 'none'; base-uri 'none'";
  return headers;
}

function sendJson(response, status, body, extraHeaders = {}) {
  response.writeHead(status, { ...securityHeaders("application/json; charset=utf-8"), ...extraHeaders });
  response.end(inlineJson(body));
}

function cookie(request, name) {
  for (const part of (request.headers.cookie ?? "").split(";")) {
    const at = part.indexOf("=");
    if (at >= 0 && part.slice(0, at).trim() === name) return part.slice(at + 1).trim();
  }
  return "";
}

async function readJson(request) {
  const type = (request.headers["content-type"] ?? "").split(";", 1)[0].trim().toLowerCase();
  if (type !== "application/json")
    throw Object.assign(new Error("application/json is required"), { status: 415 });
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > BODY_LIMIT) throw Object.assign(new Error("request body is too large"), { status: 413 });
    chunks.push(chunk);
  }
  if (size === 0) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw Object.assign(new Error("the request body is not JSON"), { status: 400 });
  }
}

export async function startReviewServer({
  bridge,
  wiki = null,
  view = "",
  host = "127.0.0.1",
  idleMs = DEFAULT_IDLE_MS,
  overallMs = DEFAULT_OVERALL_MS,
  now = () => new Date(),
  onLifecycle = () => {},
} = {}) {
  if (!bridge || ["status", "view", "edit", "issueNonce", "decide", "requestFollowUp"].some((name) => typeof bridge[name] !== "function"))
    throw new Error("the review server requires a case bridge");
  if (host !== "127.0.0.1") throw new Error("the review server binds only 127.0.0.1");

  // Staged wiki changes; none without Engine or with a bsig that lacks the verb,
  // so the page hides the view. Any other failure is reported, not hidden.
  // The page's listings share one Engine call while it runs.
  let listing = null;
  function pageWikiChanges() {
    listing ??= wikiChanges().finally(() => {
      listing = null;
    });
    return listing;
  }

  async function wikiChanges() {
    if (!wiki) return [];
    try {
      const changes = await wiki.staged();
      return Array.isArray(changes) ? changes : [];
    } catch (error) {
      if (error.code === "unsupported") return [];
      throw Object.assign(new Error(`Could not read staged changes: ${error.message}`), { status: 502, code: "wiki-unreadable" });
    }
  }

  // The gate this session shows. It stays after a decision so the page can
  // show the receipt read-only. With no gate pending ("" here), the server
  // starts only for the Wiki changes view, which its opener found non-empty.
  let gate = pendingGate(await bridge.status());
  if (!gate && view !== "wiki")
    throw Object.assign(new Error("no review is pending for this case"), { code: "no-pending-gate" });

  let capability = randomToken();
  let session = "";
  let cookieName = "";
  let active = true;
  let origin = "";
  let expectedHost = "";
  let pageNonce = "";
  let opened = 0;
  let inFlight = 0;
  let pendingShutdown = "";
  let idleTimer;
  let overallTimer;
  let settleClosed;
  const closed = new Promise((settle) => {
    settleClosed = settle;
  });

  function lifecycle(event) {
    try {
      onLifecycle({ event });
    } catch {
      // Diagnostic only.
    }
  }

  function resetIdle() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => shutdown("expired"), idleMs);
  }

  function authorized(request) {
    return active && Boolean(session) && cookie(request, cookieName) === session;
  }

  // Wiki changes being confirmed, by id: one action at a time per change, from
  // its nonce to Engine's answer, so two tabs cannot race Apply and Discard.
  const wikiBusy = new Set();
  const WIKI_REFUSALS = new Set(["nonce_unknown", "nonce_used", "nonce_other_change"]);

  // Each confirmation takes a fresh Engine nonce, kept only in this call; the
  // page never receives it.
  async function confirmWikiChange(action, body) {
    const { id, digest } = exactObject(body, ["id", "digest"], "wiki change");
    if (!wiki || typeof id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(id) || typeof digest !== "string" || !/^[0-9a-f]{64}$/.test(digest))
      return [400, { code: "invalid-change", message: "That wiki change is not recognised." }];
    if (wikiBusy.has(id))
      return [409, { code: "in-progress", message: "This wiki change is already being applied or discarded. Reload the page to see the outcome." }];
    wikiBusy.add(id);
    try {
      let nonce;
      try {
        nonce = await wiki.nonce(id);
      } catch (error) {
        return [422, { code: "refused", message: `Could not confirm the change: ${error.message}` }];
      }
      try {
        await wiki[action](id, digest, nonce);
      } catch (error) {
        if (error.code === "digest_changed")
          return [409, { code: "digest-changed", message: "This wiki change was edited since you loaded it. The list has been reloaded: check it and decide again.", changes: await wikiChanges().catch(() => undefined) }];
        if (WIKI_REFUSALS.has(error.code)) return [422, { code: "refused", message: error.message }];
        return [502, { code: "outcome-unknown", message: "Engine did not confirm the outcome. Reload the page to see whether the change was made." }];
      }
      // Done: a failed refresh of the list does not turn it into a failure.
      return [200, { changes: await wikiChanges().catch(() => undefined) }];
    } finally {
      wikiBusy.delete(id);
    }
  }

  // Register candidates for the solo Ingest page's mentions that have no
  // strong identifier (bsig wiki resolve). They are untrusted: only id, label
  // and type go to the page, as JSON text, and only an id offered here can be
  // picked, with the review digest it was offered for. Engine's answers are
  // kept for this page; each call renews the offer for the current digest.
  // Past the limits, what is left out is counted ("more", "omitted_mentions").
  // Nothing without Engine, with an old bsig or with no candidates; any other
  // failure marks only that mention, and the others are still looked up.
  const registerAnswers = new Map();
  let offer = { digest: "", ids: new Map() };
  async function entityCandidates() {
    const none = { available: false, mentions: [] };
    if (gate !== "ingest" || typeof wiki?.resolve !== "function") return none;
    const view = await bridge.view(gate);
    const mentions = [];
    const ids = new Map();
    const unresolved = view.items.filter((entry) => entry.kind === "entity_link");
    for (const item of unresolved.slice(0, MAX_ENTITY_MENTIONS)) {
      if (!registerAnswers.has(item.key)) {
        let result;
        try {
          result = await wiki.resolve({ name: item.label, ...(item.entity_type ? { type: item.entity_type } : {}) });
        } catch (error) {
          if (error.code === "unsupported") return none;
          mentions.push({ key: item.key, name: item.label, candidates: [], failed: true });
          continue;
        }
        const valid = (Array.isArray(result?.candidates) ? result.candidates : [])
          .filter((candidate) => typeof candidate?.id === "string" && ENTITY_ID.test(candidate.id));
        registerAnswers.set(item.key, {
          more: Math.max(0, valid.length - MAX_CANDIDATES),
          candidates: valid
            .slice(0, MAX_CANDIDATES)
            .map((candidate) => ({ id: candidate.id, label: String(candidate.label ?? "").slice(0, 300), type: String(candidate.type ?? "").slice(0, 40) })),
        });
      }
      const { candidates, more } = registerAnswers.get(item.key);
      ids.set(item.key, new Set(candidates.map((candidate) => candidate.id)));
      if (candidates.length) mentions.push({ key: item.key, name: item.label, candidates, ...(more ? { more } : {}) });
    }
    const omitted = Math.max(0, unresolved.length - MAX_ENTITY_MENTIONS);
    if (!mentions.length && !omitted) return none;
    offer = { digest: view.digest, ids };
    return { available: true, digest: view.digest, mentions, ...(omitted ? { omitted_mentions: omitted } : {}) };
  }

  // The decision nonce lives only in this process and in the case ledger's
  // hash; the page never receives it.
  async function decisionNonce() {
    if (!pageNonce) pageNonce = await bridge.issueNonce(gate);
    return pageNonce;
  }

  async function currentView() {
    const pending = pendingGate(await bridge.status());
    if (pending && pending !== gate) {
      gate = pending;
      pageNonce = "";
    }
    if (!gate) return { gate: "", pending_gate: "", gate_label: "Wiki changes" };
    const view = await bridge.view(gate);
    if (view.pending_gate === gate) await decisionNonce();
    return { ...view, gate_label: GATE_LABELS[gate] };
  }

  // Passages most worth reading next to each claim (case search through
  // Engine). Retrieval only: Engine absent or off, or a search stopped, leaves
  // a quiet "unavailable" note; any other failure a distinct "failed" one,
  // never its text. The answer names the record digest it was searched for.
  // One search per gate, for the record's latest digest (the claims are in
  // the record), shared by concurrent requests. A new digest replaces the
  // gate's search and stops the old one if still running; so does shutdown.
  const searches = new Map();
  async function passages() {
    const unavailable = { gate, available: false, note: "case search unavailable", claims: [] };
    if (typeof bridge.passages !== "function") return unavailable;
    let search;
    try {
      const shown = gate;
      const digest = (await bridge.view(shown)).digest;
      search = searches.get(shown);
      if (search?.digest !== digest) {
        search?.stop.abort();
        const stop = new AbortController();
        search = { digest, stop, result: bridge.passages(shown, stop.signal) };
        searches.set(shown, search);
        const current = search;
        search.result.catch(() => searches.get(shown) === current && searches.delete(shown));
      }
      const result = await search.result;
      // The claims changed while Engine searched: these passages are not the record's.
      if ((await bridge.view(shown)).digest !== search.digest) return unavailable;
      return { ...result, gate: shown, digest: search.digest };
    } catch {
      return search?.stop.signal.aborted ? unavailable : { ...unavailable, failed: true, note: "case search failed" };
    }
  }

  // Checks shared by every decision: the gate still waits, the page saw the
  // current record, and the member's name is recorded.
  async function decisionPreconditions(digest) {
    const view = await bridge.view(gate);
    if (view.pending_gate !== gate)
      return [409, { code: "not-pending", message: "This gate is no longer waiting for a decision.", view: { ...view, gate_label: GATE_LABELS[gate] } }];
    if (typeof digest !== "string" || digest !== view.digest)
      return [409, { code: "digest-changed", message: "The record changed since you loaded it. It has been reloaded: review it and decide again.", view: { ...view, gate_label: GATE_LABELS[gate] } }];
    if (!view.reviewer)
      return [422, { code: "reviewer-required", message: "Enter your name before deciding." }];
    return [0, view];
  }

  async function decide(body) {
    const { decision, digest } = exactObject(body, ["decision", "digest"], "decision");
    const [refusal, view] = await decisionPreconditions(digest);
    if (refusal) return [refusal, view];
    if (!view.decisions.includes(decision))
      return [400, { code: "invalid-decision", message: "That decision is not offered at this gate." }];
    return record((nonce) =>
      bridge.decide({
        gate,
        decision,
        nonce,
        digest,
        approvedBy: view.reviewer,
        approvedAt: now().toISOString(),
      }),
    );
  }

  async function requestFollowUp(body) {
    const { instructions, digest } = exactObject(body, ["instructions", "digest"], "follow-up");
    if (gate !== "investigation_gate")
      return [400, { code: "invalid-decision", message: "A follow-up is requested at the Investigation gate." }];
    if (typeof instructions !== "string" || !instructions.trim() || instructions.length > MAX_FOLLOW_UP)
      return [400, { code: "note-required", message: `Describe the follow-up in at most ${MAX_FOLLOW_UP} characters.` }];
    const [refusal, view] = await decisionPreconditions(digest);
    if (refusal) return [refusal, view];
    return record((nonce) =>
      bridge.requestFollowUp({
        instructions,
        nonce,
        digest,
        requestedBy: view.reviewer,
        requestedAt: now().toISOString(),
      }),
    );
  }

  // Records one decision with the nonce this page holds.
  async function record(write) {
    try {
      await write(await decisionNonce());
    } catch (error) {
      if (error.code === "digest-changed") {
        const fresh = await bridge.view(gate);
        return [409, { code: "digest-changed", message: "The record changed since you loaded it. It has been reloaded: review it and decide again.", view: { ...fresh, gate_label: GATE_LABELS[gate] } }];
      }
      // A spent or unknown nonce cannot be retried; the next attempt gets a new one.
      if (/nonce/.test(error.message)) pageNonce = "";
      return [422, { code: "refused", message: error.message }];
    }
    pageNonce = "";
    lifecycle("decided");
    return [200, { ...(await bridge.view(gate)), gate_label: GATE_LABELS[gate] }];
  }

  async function handler(request, response) {
    try {
      if (request.headers.host !== expectedHost)
        return sendJson(response, 421, { code: "wrong-host", message: "This review address belongs to a different host." });
      const url = new URL(request.url, origin);
      if (request.method === "GET" && url.pathname === "/") {
        const nonce = randomToken();
        response.writeHead(200, securityHeaders("text/html; charset=utf-8", nonce));
        return response.end(renderShell({ nonce, boot: { gates: GATE_LABELS } }));
      }
      if (request.method !== "POST")
        return sendJson(response, 405, { code: "method-not-allowed", message: "This route does not support that method." }, { allow: "GET, POST" });
      if (request.headers.origin !== origin)
        return sendJson(response, 403, { code: "wrong-origin", message: "The request did not come from this review page." });

      if (url.pathname === "/session") {
        const body = exactObject(await readJson(request), ["capability"], "session request");
        if (authorized(request)) return sendJson(response, 200, { ok: true });
        if (!active || !capability || body.capability !== capability)
          return sendJson(response, 403, { code: "expired-capability", message: "This review link has expired. Ask the agent to reopen the review." });
        capability = "";
        session = randomToken();
        resetIdle();
        lifecycle("session-opened");
        return sendJson(response, 200, { ok: true }, { "set-cookie": `${cookieName}=${session}; HttpOnly; SameSite=Strict; Path=/` });
      }
      if (!authorized(request))
        return sendJson(response, 403, { code: "unauthorized", message: "This review session is not active. Ask the agent to reopen the review." });
      resetIdle();
      inFlight += 1;
      clearTimeout(idleTimer);
      try {
        if (url.pathname === "/api/view") {
          exactObject(await readJson(request), [], "view request");
          return sendJson(response, 200, await currentView());
        }
        if (!gate && ["/api/edit", "/api/decide", "/api/follow-up", "/api/passages", "/api/entity-candidates"].includes(url.pathname))
          return sendJson(response, 409, { code: "not-pending", message: "No gate is waiting for a decision." });
        if (url.pathname === "/api/passages") {
          exactObject(await readJson(request), [], "passages request");
          return sendJson(response, 200, await passages());
        }
        if (url.pathname === "/api/entity-candidates") {
          exactObject(await readJson(request), [], "entity candidates request");
          return sendJson(response, 200, await entityCandidates());
        }
        if (url.pathname === "/api/edit") {
          const body = await readJson(request);
          // An entity pick also names the digest its candidates were offered for.
          const { edit, digest } = exactObject(body, body && typeof body === "object" && "digest" in body ? ["edit", "digest"] : ["edit"], "edit request");
          const view = await bridge.view(gate);
          if (view.pending_gate !== gate)
            return sendJson(response, 409, { code: "not-pending", message: "This gate is decided; the review is read-only.", view: { ...view, gate_label: GATE_LABELS[gate] } });
          // An entity pick is one of the candidates offered for the record the
          // page shows, or false for no link.
          const pick = view.items.find((item) => item.key === edit?.key && item.kind === "entity_link");
          if (pick && edit.kind === "mark") {
            if (digest !== view.digest || offer.digest !== digest)
              return sendJson(response, 409, { code: "digest-changed", message: "The record changed since the register's entities were shown. It has been reloaded: pick again.", view: { ...view, gate_label: GATE_LABELS[gate] } });
            if (edit.value !== false && !offer.ids.get(pick.key)?.has(edit.value))
              return sendJson(response, 422, { code: "refused", message: "Pick one of the register entities shown, or none." });
          }
          return sendJson(response, 200, { ...(await bridge.edit(gate, edit)), gate_label: GATE_LABELS[gate] });
        }
        if (url.pathname === "/api/decide") {
          const [status, body] = await decide(await readJson(request));
          return sendJson(response, status, body);
        }
        if (url.pathname === "/api/follow-up") {
          const [status, body] = await requestFollowUp(await readJson(request));
          return sendJson(response, status, body);
        }
        if (url.pathname === "/api/wiki-changes") {
          exactObject(await readJson(request), [], "wiki changes request");
          return sendJson(response, 200, { changes: await pageWikiChanges() });
        }
        if (url.pathname === "/api/wiki-changes/apply" || url.pathname === "/api/wiki-changes/discard") {
          const [status, body] = await confirmWikiChange(url.pathname.endsWith("apply") ? "apply" : "discard", await readJson(request));
          return sendJson(response, status, body);
        }
        if (url.pathname === "/api/close") {
          exactObject(await readJson(request), [], "close request");
          sendJson(response, 200, { ok: true, state: "closed" });
          queueMicrotask(() => shutdown("closed"));
          return;
        }
        return sendJson(response, 404, { code: "not-found", message: "This route does not exist." });
      } finally {
        inFlight -= 1;
        if (pendingShutdown && !inFlight) shutdown(pendingShutdown);
        else if (active) resetIdle();
      }
    } catch (error) {
      const status = error?.status ?? (error?.code === "refused" ? 422 : 500);
      const message = status === 500 ? "The review request failed." : String(error?.message ?? "The request was refused.").slice(0, 2048);
      return sendJson(response, status, { code: error?.code ?? "review-error", message });
    }
  }

  const server = createServer(handler);
  server.requestTimeout = REQUEST_TIMEOUT_MS;
  server.headersTimeout = REQUEST_TIMEOUT_MS;
  server.keepAliveTimeout = 1000;

  let stopped = false;
  function shutdown(reason = "closed") {
    if (stopped) return;
    for (const search of searches.values()) search.stop.abort();
    if (inFlight) {
      active = false;
      pendingShutdown = reason;
      clearTimeout(idleTimer);
      clearTimeout(overallTimer);
      return;
    }
    stopped = true;
    active = false;
    capability = "";
    session = "";
    pageNonce = "";
    clearTimeout(idleTimer);
    clearTimeout(overallTimer);
    server.close(() => {
      lifecycle(reason);
      settleClosed({ reason });
    });
    server.closeAllConnections?.();
  }

  // A fresh capability for the same running page, while a gate is pending.
  // Any page already open loses its session and must use the new link.
  async function reopen() {
    if (!active) throw Object.assign(new Error("the review server has closed"), { code: "closed" });
    const pending = pendingGate(await bridge.status());
    // Only a gate is reopened: the Wiki changes opener never reopens a page.
    if (!pending) throw Object.assign(new Error("no review is pending for this case"), { code: "no-pending-gate" });
    if (pending !== gate) {
      gate = pending;
      pageNonce = "";
    }
    capability = randomToken();
    session = "";
    resetIdle();
    // A fresh query makes a tab already on the page load it as a new
    // document; a change to the fragment alone would not reload it.
    opened += 1;
    return `${origin}/?open=${opened}#${capability}`;
  }

  await new Promise((settle, reject) => {
    server.once("error", reject);
    server.listen(0, host, settle);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    shutdown("error");
    throw new Error("the review server did not receive a loopback port");
  }
  expectedHost = `${host}:${address.port}`;
  cookieName = `spotlight_review_${address.port}`;
  origin = `http://${expectedHost}`;
  resetIdle();
  overallTimer = setTimeout(() => shutdown("expired"), overallMs);
  lifecycle("ready");
  return {
    origin,
    url: `${origin}/#${capability}`,
    gate: () => gate,
    reopen,
    closed,
    close: shutdown,
  };
}
