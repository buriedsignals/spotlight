// The only way the review server reads or writes a case: the Python
// orchestration seam, which applies edits and decisions under the case lock.
import { execFile } from "node:child_process";
import { accessSync, constants, realpathSync, statSync } from "node:fs";
import { delimiter, dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const CHECKOUT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const SEAM_TIMEOUT_MS = 120_000;
const MAX_OUTPUT = 8 << 20;
// Exit codes of scripts/spotlight_orchestration/cli.py.
export const SEAM_REFUSED = 3;
export const SEAM_DIGEST_CHANGED = 4;

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

  function run(args) {
    return new Promise((settle) => {
      execFile(
        python,
        ["-E", "-s", seam, ...args, ...boundary, caseDir],
        { cwd: checkoutRoot, env, timeout: SEAM_TIMEOUT_MS, maxBuffer: MAX_OUTPUT, windowsHide: true },
        (error, stdout, stderr) => {
          const code = error ? (typeof error.code === "number" ? error.code : -1) : 0;
          settle({ code, stdout: String(stdout), message: String(stderr).replace(/^FAIL\s+/, "").trim() });
        },
      );
    });
  }

  async function json(args) {
    const result = await run(args);
    if (result.code !== 0) throw seamError(result);
    return JSON.parse(result.stdout);
  }

  return Object.freeze({
    status: () => json(["status", "--json"]),
    view: (gate) => json(["review-view", gate]),
    edit: (gate, edit) => json(["review-edit", gate, "--edit", JSON.stringify(edit)]),
    issueNonce: async (gate) => (await json(["issue-review-nonce", gate])).nonce,
    async decide({ gate, decision, nonce, digest, approvedBy, approvedAt }) {
      // "=" keeps a value that starts with "-" from being read as an option.
      const receipt = [`--nonce=${nonce}`, `--digest=${digest}`];
      const args =
        gate === "methodology" || gate === "investigation_gate"
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
    async requestFollowUp({ instructions, nonce, digest, requestedBy, requestedAt }) {
      const result = await run([
        "request-follow-up",
        `--instructions=${instructions}`,
        `--decided-by=${requestedBy}`,
        `--decided-at=${requestedAt}`,
        `--nonce=${nonce}`,
        `--digest=${digest}`,
      ]);
      if (result.code !== 0) throw seamError(result);
    },
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

// Decision gates the resolver can report as pending, mapped from its status.
export function pendingGate(status) {
  if (status?.next_phase === "methodology_approval") return "methodology";
  if (status?.next_phase === "investigation_gate_approval") return "investigation_gate";
  if (status?.next_phase === "report") return "report";
  if (status?.next_phase === "ingest" && status?.ingest?.resume_at === "decision") return "ingest";
  return "";
}
