// What a harness calls to show a member the pending gate's review page.
// It starts (or reopens with a fresh capability) the local review server and
// opens the browser. The result never contains the page URL, so it can be
// handed to a model unchanged.
import { createWikiBridge, safeChildEnvironment } from "./case-bridge.mjs";
import { createReviewSessionManager } from "./session.mjs";

async function openPage(sessions, caseDir, view = "") {
  try {
    const started = await sessions.start(caseDir);
    const opened = await sessions.openLocally(view);
    return {
      status: opened.ok ? "opened" : opened.status,
      reopened: started.status === "reopened",
    };
  } catch (error) {
    return { status: error?.code === "no-pending-gate" ? "no-pending-gate" : "start-failed" };
  }
}

export function createReviewOpener(options = {}) {
  const sessions = options.sessions ?? createReviewSessionManager(options);
  return ({ caseDir }) => openPage(sessions, caseDir);
}

// After an agent stages a wiki write: the same page, opened on its Wiki
// changes view, with or without a pending gate. Nothing staged opens nothing.
export function createWikiChangesOpener(options = {}) {
  const sessions = options.sessions ?? createReviewSessionManager(options);
  const wiki = options.wiki ?? createWikiBridge({ env: safeChildEnvironment(options.env) });
  return async function openWikiChanges({ caseDir }) {
    const staged = await wiki.staged().catch(() => []);
    if (!Array.isArray(staged) || !staged.length) return { status: "nothing-staged" };
    const result = await openPage(sessions, caseDir, "wiki");
    return result.status === "no-pending-gate" ? { status: "nothing-staged" } : result;
  };
}

export function describeWikiChangesResult(result) {
  if (result.status === "opened")
    return "The Wiki changes page is open in the member's browser. Ask them to apply or discard each staged change there; nothing reaches the wiki until they do. The page address is private to the member's browser.";
  return describeOpenResult(result);
}

export function describeOpenResult(result) {
  if (result.status === "opened")
    return "The Spotlight review page is open in the member's browser. Ask them to review and decide there, then to say here when they are done; then resolve the case again. The page address is private to the member's browser.";
  if (result.status === "no-pending-gate")
    return "No gate in this case is waiting for a member's decision. Resolve the case to see what comes next.";
  if (result.status === "nothing-staged")
    return "No wiki changes are staged. Nothing was opened.";
  if (result.status === "start-failed")
    return "The Spotlight review page could not start. Nothing was changed.";
  return "The Spotlight review page is running, but this computer could not open the browser. Nothing was changed.";
}
