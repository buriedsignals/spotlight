// The only way the review server reads or writes a case: the Python
// orchestration seam, which applies edits and decisions under the case lock.
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { accessSync, constants, realpathSync, statSync } from "node:fs";
import { delimiter, dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const CHECKOUT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const SEAM_TIMEOUT_MS = 120_000;
const MAX_OUTPUT = 8 << 20;
// Exit codes of scripts/spotlight_orchestration/cli.py.
export const SEAM_REFUSED = 3;
export const SEAM_DIGEST_CHANGED = 4;
// Exit code of scripts/intake.py for a request it refuses.
const INTAKE_REFUSED = 2;
// What an approval says when the step named could not be done.
const STEP_FAILED = {
  rights: "The rights were not saved",
  claims: "The claims were not saved",
  confirm: "The record could not be confirmed",
  approve: "The claims could not be approved",
};

// Stand-in for Bun.which: the first executable regular file on PATH.
export function which(command, pathValue = process.env.PATH ?? "") {
  const names =
    process.platform === "win32"
      ? [command, ...(process.env.PATHEXT ?? ".EXE;.CMD").split(";").map((ext) => command + ext)]
      : [command];
  for (const directory of pathValue.split(delimiter)) {
    if (!directory || !isAbsolute(directory)) continue;
    for (const name of names) {
      const candidate = join(directory, name);
      try {
        if (!statSync(candidate).isFile()) continue;
        accessSync(candidate, constants.X_OK);
        return realpathSync(candidate);
      } catch {
        // Not here; keep looking.
      }
    }
  }
  return "";
}

// The Spotlight interpreter, the rule scripts/spotlight-python applies for the
// skills: SPOTLIGHT_PYTHON (an absolute path), else the checkout's .venv, else
// python3 on PATH. A relative SPOTLIGHT_PYTHON is refused, never looked up.
export function spotlightPython({ env = process.env, root = CHECKOUT_ROOT } = {}) {
  const configured = (env.SPOTLIGHT_PYTHON ?? "").trim();
  if (configured) {
    if (!isAbsolute(configured)) throw new Error("SPOTLIGHT_PYTHON must be an absolute path");
    return configured;
  }
  for (const candidate of [join(root, ".venv", "bin", "python"), join(root, ".venv", "Scripts", "python.exe")]) {
    try {
      if (statSync(candidate).isFile()) {
        accessSync(candidate, constants.X_OK);
        return candidate;
      }
    } catch {
      // No checkout venv here.
    }
  }
  return which("python3", env.PATH ?? "") || which("python", env.PATH ?? "");
}

// Keys and loader hooks never reach the review server or its Python calls.
export function safeChildEnvironment(source = process.env) {
  const blocked = new Set([
    "BASH_ENV",
    "ENV",
    "NODE_OPTIONS",
    "NODE_PATH",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONHOME",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
  ]);
  const env = {};
  for (const [name, value] of Object.entries(source)) {
    const upper = name.toUpperCase();
    if (value == null || blocked.has(upper)) continue;
    if (/(?:_API_KEY|_ACCESS_KEY|_KEY|_TOKEN|_SECRET|_PASSWORD|_CREDENTIALS?|_PAT)$/i.test(upper))
      continue;
    env[name] = value;
  }
  env.PYTHONNOUSERSITE = "1";
  return env;
}

// One bounded child process: its exit code (-1 when it did not exit normally),
// stdout and stderr. input, if any, is its whole stdin.
function runBounded(file, args, { cwd, env, input = "", signal }) {
  return new Promise((settle) => {
    const child = execFile(
      file,
      args,
      { cwd, env, timeout: SEAM_TIMEOUT_MS, maxBuffer: MAX_OUTPUT, windowsHide: true, signal },
      (error, stdout, stderr) =>
        settle({ code: error ? (typeof error.code === "number" ? error.code : -1) : 0, stdout: String(stdout), stderr: String(stderr) }),
    );
    child.stdin.on("error", () => {}); // a child that exits without reading
    child.stdin.end(input);
  });
}

export function createCaseBridge({
  caseDir,
  casesRoot = "",
  checkoutRoot = CHECKOUT_ROOT,
  python = spotlightPython({ root: checkoutRoot }),
  env = safeChildEnvironment(),
} = {}) {
  if (!isAbsolute(caseDir ?? "")) throw new Error("the case directory must be an absolute path");
  if (casesRoot && !isAbsolute(casesRoot)) throw new Error("the cases root must be an absolute path");
  if (!python) throw new Error("python3 is required for the review server");
  const seam = join(checkoutRoot, "scripts", "spotlight-orchestration.py");
  const boundary = casesRoot ? ["--authorized-cases-root", casesRoot] : [];

  async function run(args, signal) {
    const result = await runBounded(python, ["-E", "-s", seam, ...args, ...boundary, caseDir], { cwd: checkoutRoot, env, signal });
    return { ...result, message: result.stderr.replace(/^FAIL\s+/, "").trim() };
  }

  async function json(args, signal) {
    const result = await run(args, signal);
    if (result.code !== 0) throw seamError(result);
    return JSON.parse(result.stdout);
  }

  return Object.freeze({
    status: () => json(["status", "--json"]),
    view: (gate) => json(["review-view", gate]),
    // Case search for the claims the gate shows; retrieval only, outside the digest.
    passages: (gate, signal) => json(["review-passages", gate], signal),
    edit: (gate, edit) => json(["review-edit", gate, "--edit", JSON.stringify(edit)]),
    issueNonce: async (gate) => (await json(["issue-review-nonce", gate])).nonce,
    async decide({ gate, decision, nonce, digest, approvedBy, approvedAt }) {
      // "=" keeps a value that starts with "-" from being read as an option.
      const receipt = [`--nonce=${nonce}`, `--digest=${digest}`];
      const args =
        ["methodology", "research", "investigation_gate"].includes(gate)
          ? ["approve", gate, `--approved-by=${approvedBy}`, `--approved-at=${approvedAt}`, ...receipt]
          : [
              gate === "report" ? "decide-report" : "decide-ingest",
              decision,
              `--decided-by=${approvedBy}`,
              `--decided-at=${approvedAt}`,
              ...receipt,
            ];
      const result = await run(args);
      if (result.code !== 0) throw seamError(result);
    },
    // The Investigation gate's other outcome: back to Investigation with the member's note.
    async requestFollowUp({ instructions, nonce, digest, requestedBy, requestedAt, shownGate, shownDigest }) {
      const result = await run([
        "request-follow-up",
        `--instructions=${instructions}`,
        `--decided-by=${requestedBy}`,
        `--decided-at=${requestedAt}`,
        `--nonce=${nonce}`,
        `--digest=${digest}`,
        ...(shownGate ? [`--shown-gate=${shownGate}`, `--shown-digest=${shownDigest}`] : []),
      ]);
      if (result.code !== 0) throw seamError(result);
    },
  });
}

// The material register and its claims, through scripts/intake.py only: the
// page never reads or writes the case's files itself.
export function createIntakeBridge({
  caseDir,
  checkoutRoot = CHECKOUT_ROOT,
  python = spotlightPython({ root: checkoutRoot }),
  env = safeChildEnvironment(),
} = {}) {
  if (!isAbsolute(caseDir ?? "")) throw new Error("the case directory must be an absolute path");
  if (!python) throw new Error("python3 is required for the review server");
  const script = join(checkoutRoot, "scripts", "intake.py");

  async function run(args, input = "") {
    const result = await runBounded(python, ["-E", "-s", script, caseDir, ...args], { cwd: checkoutRoot, env, input });
    if (result.code === 0) return JSON.parse(result.stdout);
    const refused = result.code === INTAKE_REFUSED;
    const message = refused ? result.stderr.replace(/^intake:\s*/, "").trim() : "";
    throw Object.assign(new Error(message.slice(0, 2048) || "the material could not be read or saved"), { code: refused ? "refused" : "intake-failed" });
  }

  // A one-time code for this material and action, kept in this call only and
  // spent with the decision. The digest is read from the material as it is now,
  // after whatever the earlier steps saved; the page never sees either.
  async function decideAs(action, id, by) {
    const { digests } = await run(["view"]);
    const { nonce } = await run(["code", action, id]);
    return run([action, id, `--by=${by}`, `--nonce=${nonce}`, `--digest=${digests[id]}`]);
  }

  // Runs one step of an approval; a refusal names the step it came from.
  async function inStep(step, work) {
    try {
      return await work();
    } catch (error) {
      if (error.code === "refused") error.message = `${STEP_FAILED[step]}: ${error.message}`;
      throw Object.assign(error, { step });
    }
  }

  // One material's approval, in order: its rights, each claim kept or dropped
  // by the page's tick, the record's confirmation (skipped when it is already
  // confirmed), then the approval. The first refusal stops it; what the earlier
  // steps saved stays saved and nothing after it runs.
  // What the page showed of each material: its claims' wording and the passages
  // they rest on. An approval names it, so a claim reworded elsewhere after the
  // page loaded is not approved unseen.
  function seenBy(view) {
    const seen = {};
    for (const input of view.inputs) {
      const passages = view.passages.filter((passage) => passage.input_id === input.id);
      const ids = new Set(passages.map((passage) => passage.id));
      const shown = {
        passages: passages.map((passage) => [passage.id, passage.text]),
        claims: view.claims.filter((claim) => ids.has(claim.passage_id)).map((claim) => [claim.id, claim.proposition]),
      };
      seen[input.id] = createHash("sha256").update(JSON.stringify(shown)).digest("hex");
    }
    return seen;
  }

  async function view() {
    const shown = await run(["view"]);
    return { ...shown, seen: seenBy(shown) };
  }

  async function approveMaterial(id, { by, choices, keep, seen }) {
    const { by: name } = await inStep("rights", () => run(["decider", ...(by === undefined ? [] : [`--by=${by}`])]));
    await inStep("claims", async () => {
      const current = await view();
      const { passages, claims } = current;
      if (current.seen[id] !== seen)
        throw Object.assign(new Error("this material changed since the page showed it; reload the page"), { code: "refused" });
      const mine = new Set(passages.filter((passage) => passage.input_id === id).map((passage) => passage.id));
      const ids = claims.filter((claim) => mine.has(claim.passage_id)).map((claim) => claim.id);
      if (ids.length !== Object.keys(keep).length || ids.some((claim) => !Object.hasOwn(keep, claim)))
        throw Object.assign(new Error("the claims of this material changed since the page showed them; reload the page"), { code: "refused" });
      if (!Object.values(keep).includes(true))
        throw Object.assign(new Error("keep at least one claim to approve"), { code: "refused" });
    });
    await inStep("rights", () => run(["material-rights", id, `--by=${name}`], JSON.stringify(choices)));
    await inStep("claims", async () => {
      for (const [claim, kept] of Object.entries(keep)) await run(["decide", claim, kept ? "keep" : "drop"]);
    });
    const { inputs } = await run(["view"]);
    if (inputs.find((input) => input.id === id)?.state !== "confirmed") await inStep("confirm", () => decideAs("confirm", id, name));
    return inStep("approve", () => decideAs("approve", id, name));
  }

  return Object.freeze({
    view,
    decide: (claim, action, fields) => run(["decide", claim, action], action === "edit" ? JSON.stringify(fields) : ""),
    approveMaterial,
  });
}

function seamError(result) {
  const error = new Error(result.message.slice(0, 2048) || "the case refused the request");
  error.code =
    result.code === SEAM_DIGEST_CHANGED
      ? "digest-changed"
      : result.code === SEAM_REFUSED
        ? "refused"
        : "seam-failed";
  return error;
}

const WORKSPACE_LOOKUP = `import json, os, sys
sys.path.insert(0, sys.argv[1])
from integrations._credentials import config_path
try:
    print(os.path.expanduser(json.loads(config_path().read_text(encoding="utf-8"))["knowledge_destination"]["workspace_path"]))
except (OSError, ValueError, KeyError, TypeError):
    pass`;

// Engine's staged wiki changes, through `bsig wiki staged*`. bsig is found the
// way the rest of Spotlight finds it (integrations/_engine.py bsig_path). The
// confirmation nonce goes to bsig on stdin only, never on argv.
export function createWikiBridge({
  checkoutRoot = CHECKOUT_ROOT,
  python = spotlightPython({ root: checkoutRoot }),
  env = safeChildEnvironment(),
} = {}) {
  let bsig = "";
  const exec = (file, args, input, signal) => runBounded(file, args, { cwd: checkoutRoot, env, input, signal });

  // Only a found bsig is kept; a miss or a failed lookup is tried again next
  // time, and a failed lookup is an error, not "no Engine".
  async function bsigPath() {
    if (bsig || !python) return bsig;
    const found = await exec(python, ["-E", "-s", "-c", "import sys; sys.path.insert(0, sys.argv[1]); from integrations._engine import bsig_path; print(bsig_path() or '')", checkoutRoot]);
    if (found.code !== 0) throw Object.assign(new Error("could not look up bsig"), { code: "failed", exitCode: found.code });
    bsig = found.stdout.trim();
    return bsig;
  }

  // The wiki the solo Ingest write uses (knowledge_destination.workspace_path),
  // so the register lookup reads the same register; Engine's default without it.
  let workspace = null;
  async function workspaceArgs() {
    if (workspace === null) {
      const found = await exec(python, ["-E", "-s", "-c", WORKSPACE_LOOKUP, checkoutRoot]);
      workspace = found.code === 0 ? found.stdout.trim() : "";
    }
    return workspace ? [`--workspace=${workspace}`] : [];
  }

  // The last JSON event bsig printed: its result, or an error with the refusal.
  async function run(args, input = "", signal) {
    const file = await bsigPath();
    if (!file) throw Object.assign(new Error("Engine is not installed"), { code: "unsupported" });
    const result = await exec(file, ["--json", "wiki", ...args], input, signal);
    let event = {};
    try {
      event = JSON.parse(result.stdout.trim().split("\n").pop());
    } catch {
      // No event: reported below as a failure.
    }
    if (result.code === 0 && event.event === "result") return event.data?.result;
    const error = new Error(String(event.message ?? "Engine refused the request").replace(/^wiki: /, "").slice(0, 2048));
    // Exit 4 is bsig's usage error: a bsig older than these verbs.
    error.code = event.data?.refusal ?? (result.code === 4 ? "unsupported" : "failed");
    error.exitCode = result.code;
    throw error;
  }

  return Object.freeze({
    staged: () => run(["staged"]),
    nonce: async (id) => (await run(["staged-nonce", id])).nonce,
    apply: (id, digest, nonce) => run(["staged-apply", id, `--digest=${digest}`], JSON.stringify({ nonce })),
    discard: (id, digest, nonce) => run(["staged-discard", id, `--digest=${digest}`], JSON.stringify({ nonce })),
    // The register's answer for one mention ({name, type}), its arguments on
    // stdin; an abort stops bsig.
    resolve: async (mention, signal) => run(["resolve", ...(await workspaceArgs())], JSON.stringify(mention), signal),
  });
}

// Decision gates the resolver can report as pending, mapped from its status.
export function pendingGate(status) {
  // review.py investigation_stop: the way out of a stop during Investigation.
  if (status?.next_phase === "blocked" && status?.blocked?.phase === "investigation") return "investigation_gate";
  if (status?.next_phase === "methodology_approval") return "methodology";
  if (status?.next_phase === "research_approval") return "research";
  if (["investigation_gate_approval", "investigation_gate_finalization"].includes(status?.next_phase)) return "investigation_gate";
  if (status?.next_phase === "report") return "report";
  if (status?.next_phase === "ingest" && status?.ingest?.resume_at === "decision") return "ingest";
  return "";
}
