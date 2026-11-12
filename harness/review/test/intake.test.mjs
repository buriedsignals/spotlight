// The Intake view through the real server, the real intake.py and a real case:
// what the page reads, and that every action reaches the case's files or is
// refused with its reason and changes nothing.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { request } from "node:http";
import { join } from "node:path";
import { test } from "node:test";
import { CHECKOUT_ROOT, createIntakeBridge, safeChildEnvironment } from "../case-bridge.mjs";
import { PYTHON, authed, buildCase, newsroomEnv, tempRoot } from "./helpers.mjs";

const INTAKE = join(CHECKOUT_ROOT, "scripts", "intake.py");
const FIELDS = { kind: "interview", captured_by: "A. Reporter", source: { pseudonym: "Dock worker 1" }, rights_holder: { label: "A. Reporter" } };
const STATEMENT = { subject: { text: "the foreman" }, predicate: "signed off the second shift" };
const INTERVIEW = ["Notes of the call", "Dock worker 1 spoke at length.", "", "The foreman signed the second shift off.", "Nobody kept a log.", "", "Call ended.", "Second call followed."];

// The seam reads the install config: solo unless a test says otherwise.
const solo = (root) => safeChildEnvironment({ ...process.env, SPOTLIGHT_CONFIG: join(root, "no-config.json") });

function intake(caseDir, args, input) {
  return JSON.parse(execFileSync(PYTHON, ["-E", "-s", INTAKE, caseDir, ...args], { input, encoding: "utf8" }));
}

// What the page does through the server: the digest it shows for one material.
async function digestOf(json, id) {
  return (await json("/api/intake/view")).body.digests[id];
}

// A confirmation as the review server makes it: a one-time code, then the decision with the shown digest.
function confirmed(caseDir, id) {
  const { nonce } = intake(caseDir, ["code", "confirm", id]);
  intake(caseDir, ["confirm", id, "--by", "Fixture", `--nonce=${nonce}`, `--digest=${intake(caseDir, ["view"]).digests[id]}`]);
}

// A case holding one interview with two proposed claims from one passage.
function caseWithMaterial(root, { confirmed: isConfirmed = false } = {}) {
  const caseDir = buildCase(root, "case", "methodology");
  const file = join(root, "call.txt");
  writeFileSync(file, `${INTERVIEW.join("\n")}\n`);
  const record = intake(caseDir, ["add", "--file", file, "--kind", "interview"]);
  intake(caseDir, ["set", record.id], JSON.stringify(isConfirmed ? FIELDS : { kind: "interview" }));
  if (isConfirmed) confirmed(caseDir, record.id);
  const claims = ["The foreman signed off a shift.", "Nobody kept a log."].map((proposition) => ({ proposition, statement: STATEMENT }));
  intake(caseDir, ["propose", record.id], JSON.stringify({ passages: [{ text: "The foreman signed the second shift off.\nNobody kept a log.", language: "en", claims }] }));
  return { caseDir, id: record.id };
}

const read = (caseDir, name) => JSON.parse(readFileSync(join(caseDir, "data", name), "utf8"));

async function startIntake(t, root, caseDir, env = solo(root)) {
  return authed(t, caseDir, { bridgeEnv: env, intake: createIntakeBridge({ caseDir, env }) });
}

test("the view gives a material's record, its claims and each passage with the lines around it", async (t) => {
  const root = tempRoot(t);
  const { caseDir, id } = caseWithMaterial(root);
  const page = await startIntake(t, root, caseDir);
  const { status, body } = await page.json("/api/intake/view");
  assert.equal(status, 200);
  assert.equal(body.name_required, false);
  assert.equal(body.inputs[0].id, id);
  assert.equal(body.inputs[0].state, "draft");
  assert.deepEqual(body.claims.map((claim) => [claim.proposition, claim.state]), [["The foreman signed off a shift.", "proposed"], ["Nobody kept a log.", "proposed"]]);
  const lines = body.passages[0].context.lines;
  assert.deepEqual(lines.map((line) => [line.n, line.in_passage]), [[1, false], [2, false], [3, false], [4, true], [5, true], [6, false], [7, false], [8, false]]);
  assert.equal(lines[3].text, "The foreman signed the second shift off.");
});

test("a passage whose file was replaced by a link shows no context, and the page can still be read", async (t) => {
  const root = tempRoot(t);
  const { caseDir } = caseWithMaterial(root);
  const kept = join(caseDir, "intake", "interviews", "call.txt");
  const copy = join(root, "elsewhere.txt");
  execFileSync("mv", [kept, copy]);
  execFileSync("ln", ["-s", copy, kept]);
  const { body } = await (await startIntake(t, root, caseDir)).json("/api/intake/view");
  assert.equal(body.passages[0].context, null);
  assert.equal(body.claims.length, 2);
});

test("keep, edit, drop, rights, confirm and approve each reach the case's files", async (t) => {
  const root = tempRoot(t);
  const { caseDir, id } = caseWithMaterial(root);
  const { json } = await startIntake(t, root, caseDir);
  const [first, second] = read(caseDir, "intake-claims.json").claims.map((claim) => claim.id);
  const passage = read(caseDir, "intake-claims.json").passages[0].id;

  assert.equal((await json("/api/intake/set", { id, fields: { ...FIELDS, original_sensitivity: "internal" } })).status, 200);
  assert.equal(read(caseDir, "journalist-input.json").inputs[0].original_sensitivity, "internal");
  assert.equal((await json("/api/intake/confirm", { id, digest: await digestOf(json, id) })).status, 200);
  assert.equal(read(caseDir, "journalist-input.json").inputs[0].confirmed_by, "Member");

  const decided = await json("/api/intake/decide", { claim: first, action: "edit", proposition: "The foreman signed off the second shift." });
  assert.equal(decided.status, 200);
  const rights = { attribution: "background", sensitivity: "internal", consent: { publish_content: true, publish_direct_quotes: false, obtained_via: "written" } };
  assert.equal((await json("/api/intake/rights", { passage, rights })).status, 200);
  assert.equal((await json("/api/intake/decide", { claim: second, action: "drop" })).status, 200);
  const saved = read(caseDir, "intake-claims.json");
  assert.deepEqual(saved.claims.map((claim) => [claim.proposition, claim.state]), [["The foreman signed off the second shift.", "kept"], ["Nobody kept a log.", "dropped"]]);
  assert.deepEqual(saved.passages[0].rights, rights);

  // Whatever name the page sends, the solo edition records the fixed Member.
  assert.equal((await json("/api/intake/approve", { id, by: "Someone Else", digest: await digestOf(json, id) })).status, 200);
  const approval = read(caseDir, "intake-claims.json").approvals[id];
  assert.deepEqual([approval.by, approval.claims], ["Member", [first]]);
  // A later change takes the approval back.
  await json("/api/intake/decide", { claim: second, action: "keep" });
  assert.equal(read(caseDir, "intake-claims.json").approvals[id], undefined);
});

test("confirm and approve count only with the code the server issued, and the page never sees it", async (t) => {
  const root = tempRoot(t);
  const { caseDir, id } = caseWithMaterial(root);
  const { json } = await startIntake(t, root, caseDir);
  await json("/api/intake/set", { id, fields: FIELDS });

  // A call on the command line, as an agent with a shell could make it, has no code.
  for (const action of ["confirm", "approve"]) {
    assert.throws(() => intake(caseDir, [action, id, "--by", "Member"]), (error) => /Intake page/.test(String(error.stderr)));
  }
  assert.equal(read(caseDir, "journalist-input.json").inputs[0].state, "draft");

  // A digest the page did not show is refused: the record changed under it.
  const stale = await digestOf(json, id);
  await json("/api/intake/set", { id, fields: { captured_by: "Another Reporter" } });
  const refused = await json("/api/intake/confirm", { id, digest: stale });
  assert.equal(refused.status, 422);
  assert.match(refused.body.message, /changed since the page showed it/);
  assert.equal(read(caseDir, "journalist-input.json").inputs[0].state, "draft");

  const confirm = await json("/api/intake/confirm", { id, digest: await digestOf(json, id) });
  assert.equal(confirm.status, 200);
  const ledger = read(caseDir, "review-nonces.json").nonces;
  const [entry] = Object.entries(ledger).filter(([, value]) => value.gate === "intake_confirm" && value.used);
  assert.equal(entry[1].subject, id);
  // Only the code's hash is kept, and neither the answer nor the case holds the code.
  assert.equal(entry[0], read(caseDir, "journalist-input.json").inputs[0].review.nonce_sha256);
  assert.doesNotMatch(JSON.stringify(confirm.body), /"nonce"/);
  assert.deepEqual(Object.keys(read(caseDir, "journalist-input.json").inputs[0].review).sort(), ["channel", "nonce_sha256"]);
});

test("a decision the page did not make does not count: a record or approval written by hand shows as undecided", async (t) => {
  const root = tempRoot(t);
  const { caseDir, id } = caseWithMaterial(root, { confirmed: true });
  const { json } = await startIntake(t, root, caseDir);
  const claims = read(caseDir, "intake-claims.json");
  const passage = claims.passages[0].id;
  await json("/api/intake/keep-all", { id });
  await json("/api/intake/rights", { passage, rights: { attribution: "background", sensitivity: "internal", consent: { publish_content: true, publish_direct_quotes: false, obtained_via: "written" } } });
  assert.equal((await json("/api/intake/approve", { id, digest: await digestOf(json, id) })).status, 200);
  assert.deepEqual(Object.keys((await json("/api/intake/view")).body.approvals), [id]);

  // The shared file is edited by hand: the passage is now said to be publishable.
  const edited = read(caseDir, "intake-claims.json");
  edited.passages[0].rights.sensitivity = "publishable";
  writeFileSync(join(caseDir, "data", "intake-claims.json"), JSON.stringify(edited));
  assert.deepEqual((await json("/api/intake/view")).body.approvals, {});
  // Approving again needs a fresh page decision.
  assert.equal((await json("/api/intake/approve", { id, digest: await digestOf(json, id) })).status, 200);
  assert.deepEqual(Object.keys((await json("/api/intake/view")).body.approvals), [id]);
});

test("keep all keeps what is still undecided in one material", async (t) => {
  const root = tempRoot(t);
  const { caseDir, id } = caseWithMaterial(root);
  const { json } = await startIntake(t, root, caseDir);
  assert.equal((await json("/api/intake/keep-all", { id })).status, 200);
  assert.deepEqual(read(caseDir, "intake-claims.json").claims.map((claim) => claim.state), ["kept", "kept"]);
});

test("a refusal from intake.py reaches the client with its reason and changes nothing", async (t) => {
  const root = tempRoot(t);
  const { caseDir, id } = caseWithMaterial(root);
  const { json } = await startIntake(t, root, caseDir);
  const before = [read(caseDir, "journalist-input.json"), read(caseDir, "intake-claims.json")];

  const incomplete = await json("/api/intake/confirm", { id, digest: await digestOf(json, id) });
  assert.equal(incomplete.status, 422);
  assert.match(incomplete.body.message, /not complete: captured_by, source, rights_holder/);
  const early = await json("/api/intake/approve", { id, digest: await digestOf(json, id) });
  assert.equal(early.status, 422);
  assert.match(early.body.message, /not confirmed yet/);
  const badRights = await json("/api/intake/rights", { passage: "JP1", rights: { attribution: "loud" } });
  assert.equal(badRights.status, 422);
  assert.match(badRights.body.message, /rights are not valid/);
  const unknown = await json("/api/intake/decide", { claim: "JC99", action: "keep" });
  assert.equal(unknown.status, 422);
  assert.match(unknown.body.message, /no material claim JC99/);

  assert.deepEqual([read(caseDir, "journalist-input.json"), read(caseDir, "intake-claims.json")], before);
});

test("the newsroom edition needs the person's name and records it", async (t) => {
  const root = tempRoot(t);
  const { caseDir, id } = caseWithMaterial(root);
  const { json } = await startIntake(t, root, caseDir, newsroomEnv(root));
  assert.equal((await json("/api/intake/view")).body.name_required, true);
  await json("/api/intake/set", { id, fields: FIELDS });

  const digest = await digestOf(json, id);
  for (const body of [{ id, digest }, { id, by: "  ", digest }]) {
    const refused = await json("/api/intake/confirm", body);
    assert.equal(refused.status, 422);
    assert.match(refused.body.message, /enter your name/);
  }
  assert.equal(read(caseDir, "journalist-input.json").inputs[0].state, "draft");
  assert.equal((await json("/api/intake/confirm", { id, by: " A. Reporter ", digest })).status, 200);
  assert.equal(read(caseDir, "journalist-input.json").inputs[0].confirmed_by, "A. Reporter");
});

test("a request that is not exactly an intake action is refused before intake.py runs", async (t) => {
  const root = tempRoot(t);
  const { caseDir } = caseWithMaterial(root);
  const { json } = await startIntake(t, root, caseDir);
  const before = read(caseDir, "intake-claims.json");
  for (const [path, body] of [
    ["/api/intake/decide", { claim: "--help", action: "keep" }],
    ["/api/intake/decide", { claim: "JC1", action: "keep", proposition: "x" }],
    ["/api/intake/decide", { claim: "JC1", action: "edit", proposition: "   " }],
    ["/api/intake/decide", { claim: "JC1", action: "approve" }],
    ["/api/intake/confirm", { id: "-x", digest: "0".repeat(64) }],
    ["/api/intake/confirm", { id: "JI1" }],
    ["/api/intake/confirm", { id: "JI1", digest: "short" }],
    ["/api/intake/approve", { id: "JI1", digest: "0".repeat(64), nonce: "0".repeat(64) }],
    ["/api/intake/set", { id: "JI1", fields: [] }],
    ["/api/intake/rights", { passage: "JP1" }],
    ["/api/intake/nothing", {}],
  ]) {
    const { status } = await json(path, body);
    assert.ok([400, 404].includes(status), `${path} ${JSON.stringify(body)} gave ${status}`);
  }
  assert.deepEqual(read(caseDir, "intake-claims.json"), before);
});

test("intake routes need the session cookie, this Host, this origin and POST", async (t) => {
  const root = tempRoot(t);
  const { caseDir } = caseWithMaterial(root);
  const { origin, cookie } = await startIntake(t, root, caseDir);
  const post = (path, headers) => fetch(`${origin}${path}`, { method: "POST", headers: { "content-type": "application/json", ...headers }, body: "{}" });

  for (const path of ["/api/intake/view", "/api/intake/keep-all"]) {
    assert.equal((await post(path, { origin })).status, 403, "no session cookie");
    assert.equal((await post(path, { origin: "http://evil.example", cookie })).status, 403, "another origin");
  }
  assert.equal((await fetch(`${origin}/api/intake/view`, { headers: { cookie } })).status, 405);
  const wrongHost = await new Promise((settle, reject) => {
    const url = new URL(origin);
    const req = request({ hostname: url.hostname, port: url.port, path: "/api/intake/view", method: "POST", headers: { host: "evil.example", origin, cookie, "content-type": "application/json" } }, (res) => {
      res.resume();
      res.on("end", () => settle(res.statusCode));
    });
    req.on("error", reject);
    req.end("{}");
  });
  assert.equal(wrongHost, 421);
  assert.equal((await post("/api/intake/view", { origin, cookie })).status, 200);
});
