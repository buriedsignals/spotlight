// What a harness calls to show a member the pending gate's review page.
// It starts (or reopens with a fresh capability) the local review server and
// opens the browser. The result never contains the page URL, so it can be
// handed to a model unchanged.
import { createReviewSessionManager } from "./session.mjs";

export function createReviewOpener(options = {}) {
  const sessions = options.sessions ?? createReviewSessionManager(options);
  return async function openSpotlightReview({ caseDir }) {
    try {
      const started = await sessions.start(caseDir);
      const opened = await sessions.openLocally();
      return {
        status: opened.ok ? "opened" : opened.status,
        reopened: started.status === "reopened",
      };
    } catch (error) {
      return { status: error?.code === "no-pending-gate" ? "no-pending-gate" : "start-failed" };
    }
  };
}

export function describeOpenResult(result) {
  if (result.status === "opened")
    return "The Spotlight review page is open in the member's browser. Ask them to review and decide there, then to say here when they are done; then resolve the case again. The page address is private to the member's browser.";
  if (result.status === "no-pending-gate")
    return "No gate in this case is waiting for a member's decision. Resolve the case to see what comes next.";
  if (result.status === "start-failed")
    return "The Spotlight review page could not start. Nothing was changed.";
  return "The Spotlight review page is running, but this computer could not open the browser. Nothing was changed.";
}
