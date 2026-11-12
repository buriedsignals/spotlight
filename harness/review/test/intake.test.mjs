// The Intake view through the real server, the real intake.py and a real case:
// what the page reads, and that one approval reaches the case's files, stopping
// at the first step that refuses.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { chmodSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { request } from "node:http";
import { join } from "node:path";
import { test } from "node:test";
import { CHECKOUT_ROOT, createIntakeBridge, safeChildEnvironment } from "../case-bridge.mjs";
import { PYTHON, authed, newsroomEnv, tempRoot } from "./helpers.mjs";

const INTAKE = join(CHECKOUT_ROOT, "scripts", "intake.py");
const BUILDER = join(CHECKOUT_ROOT, "scripts", "build-knowledge-batch.py");
const RIGHTS = { attribution: "not_for_attribution", sensitivity: "publishable", consent: { publish_content: true, publish_direct_quotes: false, obtained_via: "recorded_oral" } };
const FIELDS = { kind: "interview", captured_by: "A. Reporter", source: { pseudonym: "Dock worker 1" }, rights_holder: { label: "A. Reporter" }, passage_defaults: RIGHTS };
// What the page sends for FIELDS: the journalist's plain choices.
const CHOICES = { kind: "interview", source_as: "pseudonym", source: "Dock worker 1", holder: "A. Reporter", publish: "not_attributed", quotes: "not_allowed", consent: "recorded_oral" };
const STATEMENT = { subject: { text: "the foreman" }, predicate: "signed off the second shift" };
const INTERVIEW = ["Notes of the call", "Dock worker 1 spoke at length.", "", "The foreman signed the second shift off.", "Nobody kept a log.", "", "Call ended.", "Second call followed."];

// The seam reads the install config: solo unless a test says otherwise.
const solo = (root) => safeChildEnvironment({ ...process.env, SPOTLIGHT_CONFIG: join(root, "no-config.json") });

function intake(caseDir, args, input) {
  return JSON.parse(execFileSync(PYTHON, ["-E", "-s", INTAKE, caseDir, ...args], { input, encoding: "utf8" }));
}

// A record confirmed as the review server does: a one-time code, then the decision with the shown digest.
function confirmed(caseDir, id) {
  const { nonce } = intake(caseDir, ["code", "confirm", id]);
  intake(caseDir, ["confirm", id, "--by", "Fixture", `--nonce=${nonce}`, `--digest=${intake(caseDir, ["view"]).digests[id]}`]);
}

// A case that holds only material, with one interview. Its claims: JC1 and JC2 from one passage, and JC3 from another
// that the model suggested leaving out.
function caseWithMaterial(root, { confirmed: isConfirmed = false } = {}) {
  const caseDir = join(root, "case");
  mkdirSync(join(caseDir, "data"), { recursive: true });
  const file = join(root, "call.txt");
  writeFileSync(file, `${INTERVIEW.join("\n")}\n`);
  const record = intake(caseDir, ["add", "--file", file, "--kind", "interview"]);
  intake(caseDir, ["set", record.id], JSON.stringify(isConfirmed ? FIELDS : { kind: "interview" }));
  if (isConfirmed) confirmed(caseDir, record.id);
  const claim = (proposition, extra = {}) => ({ proposition, statement: STATEMENT, ...extra });
  intake(caseDir, ["propose", record.id], JSON.stringify({ passages: [
    { text: "The foreman signed the second shift off.\nNobody kept a log.", language: "en", claims: [claim("The foreman signed off a shift."), claim("Nobody kept a log.")] },
    { text: "Dock worker 1 spoke at length.", language: "en", claims: [claim("Dock worker 1 spoke at length.", { leave_out: "it could identify the source" })] },
  ] }));
  return { caseDir, id: record.id };
}

const read = (caseDir, name) => JSON.parse(readFileSync(join(caseDir, "data", name), "utf8"));
const files = (caseDir) => ["journalist-input.json", "intake-claims.json"].map((name) => readFileSync(join(caseDir, "data", name), "utf8"));
const approveBody = (id, keep = { JC1: true, JC2: true, JC3: false }, choices = CHOICES) => ({ id, choices, keep });
const states = (caseDir) => Object.fromEntries(read(caseDir, "intake-claims.json").claims.map((claim) => [claim.id, claim.state]));

async function startIntake(t, root, caseDir, env = solo(root), extra = {}) {
  return authed(t, caseDir, { bridgeEnv: env, intake: createIntakeBridge({ caseDir, env, ...extra }), view: "intake" });
}

test("the view gives each material with its plain choices and summary, and each passage with the words around it and no line numbers", async (t) => {
  const root = tempRoot(t);
  const { caseDir, id } = caseWithMaterial(root);
  intake(caseDir, ["material-rights", id], JSON.stringify(CHOICES));
  const { status, body } = await (await startIntake(t, root, caseDir)).json("/api/intake/view");
  assert.equal(status, 200);
  assert.equal(body.name_required, false);
  assert.equal(body.inputs[0].id, id);
  assert.deepEqual(body.inputs[0].plain, { ...CHOICES, kind_detail: "", summary: "Dock worker 1 (pseudonym) · yes, not attributed by name · direct quotes not allowed · consent spoken, on the recording" });
  assert.deepEqual(body.choices.publish.map((choice) => choice.label), ["Yes, on the record", "Yes, not attributed by name", "Background only", "No, off the record"]);
  assert.deepEqual(body.restrictive, { source_as: "pseudonym", publish: "off_record", quotes: "not_allowed", consent: "unrecorded_oral" });
  assert.deepEqual(body.claims.map((claim) => [claim.proposition, claim.state, claim.leave_out]),
    [["The foreman signed off a shift.", "proposed", undefined], ["Nobody kept a log.", "proposed", undefined], ["Dock worker 1 spoke at length.", "proposed", "it could identify the source"]]);
  assert.deepEqual(body.passages[0].context, { before: "Notes of the call\nDock worker 1 spoke at length.\n\n", after: "\n\nCall ended.\nSecond call followed." });
  assert.ok(body.passages.every((passage) => !("lines" in (passage.context ?? {}))));
});

test("a material with no rights stored says so, and a passage whose file was replaced by a link shows no words around it", async (t) => {
  const root = tempRoot(t);
  const { caseDir } = caseWithMaterial(root);
  const kept = join(caseDir, "intake", "interviews", "call.txt");
  const copy = join(root, "elsewhere.txt");
  execFileSync("mv", [kept, copy]);
  execFileSync("ln", ["-s", copy, kept]);
  const { body } = await (await startIntake(t, root, caseDir)).json("/api/intake/view");
  assert.match(body.inputs[0].plain.summary, /rights not set yet$/);
  assert.equal(body.passages[0].context, null);
  assert.equal(body.claims.length, 3);
});

test("one approval saves the rights, decides each claim by its tick, confirms the record and approves, so the batch builder accepts it", async (t) => {
  const root = tempRoot(t);
  const { caseDir, id } = caseWithMaterial(root);
  const { json } = await startIntake(t, root, caseDir);

  const approved = await json("/api/intake/approve-material", approveBody(id, { JC1: true, JC2: false, JC3: false }));
  assert.equal(approved.status, 200);
  assert.doesNotMatch(JSON.stringify(approved.body), /"nonce"/);
  assert.deepEqual(states(caseDir), { JC1: "kept", JC2: "dropped", JC3: "dropped" });
  const record = read(caseDir, "journalist-input.json").inputs[0];
  assert.deepEqual([record.state, record.confirmed_by, record.captured_by, record.passage_defaults], ["confirmed", "Member", "Member", RIGHTS]);
  const saved = read(caseDir, "intake-claims.json");
  assert.deepEqual(saved.passages.map((passage) => passage.rights), [RIGHTS, RIGHTS]);
  assert.deepEqual([saved.approvals[id].by, saved.approvals[id].claims], ["Member", ["JC1"]]);
  // The page's code backs both decisions: each is a ledger entry spent for this material, and only its hash is kept.
  const ledger = Object.values(read(caseDir, "review-nonces.json").nonces);
  assert.deepEqual(ledger.map((entry) => [entry.gate, entry.subject, entry.used]).sort(), [["intake_approve", id, true], ["intake_confirm", id, true]]);
  assert.deepEqual(Object.keys((await json("/api/intake/view")).body.approvals), [id]);
  // The batch builder takes the approval: the case holds only material.
  execFileSync(PYTHON, [BUILDER, caseDir], { stdio: "pipe" });
  const batch = read(caseDir, "knowledge-batch.json");
  assert.deepEqual(batch.claims.map((claim) => claim.proposition), ["The foreman signed off a shift."]);
});

test("a claim the model suggested leaving out is dropped unless the page ticked it", async (t) => {
  for (const [ticked, want] of [[false, "dropped"], [true, "kept"]]) {
    const root = tempRoot(t);
    const { caseDir, id } = caseWithMaterial(root);
    const { json } = await startIntake(t, root, caseDir);
    assert.equal((await json("/api/intake/approve-material", approveBody(id, { JC1: true, JC2: true, JC3: ticked }))).status, 200);
    assert.equal(states(caseDir).JC3, want);
    // The suggestion stays on the claim, so it can be compared with the choice later.
    assert.equal(read(caseDir, "intake-claims.json").claims[2].leave_out, "it could identify the source");
  }
});

test("an already confirmed record is not confirmed again, and its own rights go on every passage", async (t) => {
  const root = tempRoot(t);
  const { caseDir, id } = caseWithMaterial(root, { confirmed: true });
  const { json } = await startIntake(t, root, caseDir);
  assert.equal((await json("/api/intake/approve-material", approveBody(id))).status, 200);
  const gates = Object.values(read(caseDir, "review-nonces.json").nonces).map((entry) => entry.gate).sort();
  assert.deepEqual(gates, ["intake_approve", "intake_confirm"], "one confirmation (the fixture's) and one approval");
  assert.deepEqual(read(caseDir, "intake-claims.json").passages.map((passage) => passage.rights), [RIGHTS, RIGHTS]);
  // Other rights are not a change the confirmed record accepts.
  const other = await json("/api/intake/approve-material", approveBody(id, undefined, { ...CHOICES, publish: "on_record" }));
  assert.equal(other.status, 422);
  assert.equal(other.body.step, "rights");
  assert.match(other.body.message, /no longer changes/);
});

test("a refusal at the first steps stops there, names the step and leaves nothing decided", async (t) => {
  const root = tempRoot(t);
  const { caseDir, id } = caseWithMaterial(root);
  const { json } = await startIntake(t, root, caseDir);
  const before = files(caseDir);
  for (const [body, step, message] of [
    [approveBody(id, undefined, { ...CHOICES, holder: " " }), "rights", /Who holds the rights/],
    [approveBody(id, undefined, { ...CHOICES, publish: "loud" }), "rights", /May be published: choose one of/],
    [approveBody(id, { JC1: true, JC2: true }), "claims", /changed since the page showed them/],
    [approveBody(id, { JC1: false, JC2: false, JC3: false }), "claims", /keep at least one claim/],
  ]) {
    const refused = await json("/api/intake/approve-material", body);
    assert.equal(refused.status, 422);
    assert.equal(refused.body.step, step);
    assert.match(refused.body.message, message);
  }
  assert.deepEqual(files(caseDir), before);
});

test("a refusal at the confirmation leaves the earlier steps saved and no approval", async (t) => {
  const root = tempRoot(t);
  const { caseDir, id } = caseWithMaterial(root);
  const { json } = await startIntake(t, root, caseDir);
  // The file changed after it was registered: its record cannot be confirmed.
  writeFileSync(join(caseDir, "intake", "interviews", "call.txt"), "Changed after it was registered.\n");
  const refused = await json("/api/intake/approve-material", approveBody(id, { JC1: true, JC2: false, JC3: false }));
  assert.equal(refused.status, 422);
  assert.equal(refused.body.step, "confirm");
  assert.match(refused.body.message, /The record could not be confirmed: .*registered bytes/);
  assert.deepEqual(states(caseDir), { JC1: "kept", JC2: "dropped", JC3: "dropped" });
  assert.equal(read(caseDir, "journalist-input.json").inputs[0].state, "draft");
  assert.deepEqual(read(caseDir, "intake-claims.json").approvals, {});
});

test("a refusal at the approval is reported and leaves no approval", async (t) => {
  const root = tempRoot(t);
  const { caseDir, id } = caseWithMaterial(root);
  // intake.py as the server runs it, except that it refuses the approval itself.
  const wrapper = join(root, "refuse-approval.sh");
  writeFileSync(wrapper, `#!/bin/sh\ncase " $* " in *" approve "*) echo "intake: the approval was refused" >&2; exit 2;; esac\nexec '${PYTHON}' "$@"\n`);
  chmodSync(wrapper, 0o755);
  const { json } = await startIntake(t, root, caseDir, solo(root), { python: wrapper });
  const refused = await json("/api/intake/approve-material", approveBody(id));
  assert.equal(refused.status, 422);
  assert.deepEqual([refused.body.step, refused.body.message], ["approve", "The claims could not be approved: the approval was refused"]);
  assert.equal(read(caseDir, "journalist-input.json").inputs[0].state, "confirmed");
  assert.deepEqual(read(caseDir, "intake-claims.json").approvals, {});
});

test("a decision the page did not make does not count: an approval edited by hand shows as undecided", async (t) => {
  const root = tempRoot(t);
  const { caseDir, id } = caseWithMaterial(root);
  const { json } = await startIntake(t, root, caseDir);
  assert.equal((await json("/api/intake/approve-material", approveBody(id))).status, 200);
  assert.deepEqual(Object.keys((await json("/api/intake/view")).body.approvals), [id]);

  // The shared file is edited by hand: the passage is now said to be internal.
  const edited = read(caseDir, "intake-claims.json");
  edited.passages[0].rights.sensitivity = "internal";
  writeFileSync(join(caseDir, "data", "intake-claims.json"), JSON.stringify(edited));
  assert.deepEqual((await json("/api/intake/view")).body.approvals, {});
  // Approving again needs a fresh page decision.
  assert.equal((await json("/api/intake/approve-material", approveBody(id))).status, 200);
  assert.deepEqual(Object.keys((await json("/api/intake/view")).body.approvals), [id]);
});

test("rewriting a claim saves its words and leaves its decision, and a refusal changes nothing", async (t) => {
  const root = tempRoot(t);
  const { caseDir } = caseWithMaterial(root);
  const { json } = await startIntake(t, root, caseDir);
  assert.equal((await json("/api/intake/rewrite", { claim: "JC3", proposition: "Dock worker 1 spoke for a long time." })).status, 200);
  const claim = read(caseDir, "intake-claims.json").claims[2];
  assert.deepEqual([claim.proposition, claim.state, claim.leave_out], ["Dock worker 1 spoke for a long time.", "proposed", "it could identify the source"]);
  const before = files(caseDir);
  const unknown = await json("/api/intake/rewrite", { claim: "JC99", proposition: "x" });
  assert.equal(unknown.status, 422);
  assert.match(unknown.body.message, /no material claim JC99/);
  assert.deepEqual(files(caseDir), before);
});

test("the newsroom edition needs the person's name and records it", async (t) => {
  const root = tempRoot(t);
  const { caseDir, id } = caseWithMaterial(root);
  const { json } = await startIntake(t, root, caseDir, newsroomEnv(root));
  assert.equal((await json("/api/intake/view")).body.name_required, true);
  const before = files(caseDir);
  for (const body of [approveBody(id), { ...approveBody(id), by: "  " }]) {
    const refused = await json("/api/intake/approve-material", body);
    assert.equal(refused.status, 422);
    assert.match(refused.body.message, /enter your name/);
  }
  assert.deepEqual(files(caseDir), before);
  assert.equal((await json("/api/intake/approve-material", { ...approveBody(id), by: " A. Reporter " })).status, 200);
  const saved = [read(caseDir, "journalist-input.json").inputs[0], read(caseDir, "intake-claims.json").approvals[id]];
  assert.deepEqual([saved[0].captured_by, saved[0].confirmed_by, saved[1].by], ["A. Reporter", "A. Reporter", "A. Reporter"]);
});

test("a request that is not exactly an intake action is refused before intake.py runs", async (t) => {
  const root = tempRoot(t);
  const { caseDir } = caseWithMaterial(root);
  const { json } = await startIntake(t, root, caseDir);
  const before = files(caseDir);
  const approval = approveBody("JI1");
  for (const [path, body] of [
    ["/api/intake/approve-material", { ...approval, id: "--help" }],
    ["/api/intake/approve-material", { ...approval, digest: "0".repeat(64) }],
    ["/api/intake/approve-material", { ...approval, nonce: "0".repeat(64) }],
    ["/api/intake/approve-material", { id: "JI1", keep: approval.keep }],
    ["/api/intake/approve-material", { ...approval, choices: [] }],
    ["/api/intake/approve-material", { ...approval, keep: { JC1: "yes" } }],
    ["/api/intake/approve-material", { ...approval, keep: { "--help": true } }],
    ["/api/intake/rewrite", { claim: "--help", proposition: "x" }],
    ["/api/intake/rewrite", { claim: "JC1", proposition: "   " }],
    ["/api/intake/rewrite", { claim: "JC1", proposition: "x", action: "keep" }],
    ...["confirm", "approve", "set", "rights", "keep-all", "decide", "nothing"].map((action) => [`/api/intake/${action}`, {}]),
  ]) {
    const { status } = await json(path, body);
    assert.ok([400, 404].includes(status), `${path} ${JSON.stringify(body)} gave ${status}`);
  }
  assert.deepEqual(files(caseDir), before);
});

test("intake routes need the session cookie, this Host, this origin and POST", async (t) => {
  const root = tempRoot(t);
  const { caseDir } = caseWithMaterial(root);
  const { origin, cookie } = await startIntake(t, root, caseDir);
  const post = (path, headers, body = "{}") => fetch(`${origin}${path}`, { method: "POST", headers: { "content-type": "application/json", ...headers }, body });
  const approval = JSON.stringify(approveBody("JI1"));

  for (const path of ["/api/intake/view", "/api/intake/approve-material"]) {
    assert.equal((await post(path, { origin }, approval)).status, 403, "no session cookie");
    assert.equal((await post(path, { origin: "http://evil.example", cookie }, approval)).status, 403, "another origin");
  }
  assert.equal((await fetch(`${origin}/api/intake/approve-material`, { headers: { cookie } })).status, 405);
  const wrongHost = await new Promise((settle, reject) => {
    const url = new URL(origin);
    const req = request({ hostname: url.hostname, port: url.port, path: "/api/intake/approve-material", method: "POST", headers: { host: "evil.example", origin, cookie, "content-type": "application/json" } }, (res) => {
      res.resume();
      res.on("end", () => settle(res.statusCode));
    });
    req.on("error", reject);
    req.end(approval);
  });
  assert.equal(wrongHost, 421);
  assert.equal(read(caseDir, "journalist-input.json").inputs[0].state, "draft", "a refused request changed the record");
  assert.equal((await post("/api/intake/view", { origin, cookie })).status, 200);
});
