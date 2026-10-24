// The solo Ingest page's entity picks: for a mention with no strong
// identifier, the register's candidates (bsig wiki resolve) shown as text, and
// the member's pick or "none" saved in the ingest review.
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { createWikiBridge, safeChildEnvironment } from "../case-bridge.mjs";
import { authed, buildCase, fakeBsig, tempRoot } from "./helpers.mjs";

const HOSTILE = "<img src=x onerror=alert(1)>Acme Ltd</script>";
const ACME = { status: "candidates", candidates: [{ id: "acme-ltd", type: "company", label: HOSTILE, score: 0.8, evidence: ["name"] }] };

const ingestCase = (root, name = "case") => buildCase(root, name, "ingest", "--clean", "--activated", "--proposal");
// Each fake bsig keeps its state beside it, so each gets its own folder.
const own = (root, name) => {
  const folder = join(root, name);
  mkdirSync(folder);
  return folder;
};

test("unresolved mentions show the register's candidates as inert text, and only an offered pick is saved", async (t) => {
  const root = tempRoot(t);
  const fake = fakeBsig(root, { changes: [], resolve: { Acme: ACME } });
  const { call, json } = await authed(t, ingestCase(root), { wiki: fake.wiki });
  const before = (await json("/api/view")).body;
  const { status, text } = await call("/api/entity-candidates");
  assert.equal(status, 200);
  assert.ok(!/[<>]/.test(text), "raw markup in the JSON response");
  // Doe has no candidates, so it is not shown. The offer is for the digest shown.
  assert.deepEqual(JSON.parse(text), {
    available: true,
    digest: before.digest,
    mentions: [{ key: "entity:Acme", name: "Acme", candidates: [{ id: "acme-ltd", label: HOSTILE, type: "company" }] }],
  });
  const lookups = fake.calls().filter(({ argv }) => argv[2] === "resolve");
  assert.deepEqual(lookups.map(({ stdin }) => JSON.parse(stdin).name).sort(), ["Acme", "Doe"]);
  assert.ok(lookups.every(({ argv }) => !argv.some((arg) => arg.includes("Acme") || arg.includes("Doe"))), "a mention reached argv");

  const pick = (value, digest) => json("/api/edit", { edit: { kind: "mark", key: "entity:Acme", value }, digest });
  // "none" is an entity id like any other; only false means "no link".
  for (const value of ["someone-else", "none"]) assert.equal((await pick(value, before.digest)).status, 422);
  const saved = await pick("acme-ltd", before.digest);
  assert.equal(saved.status, 200);
  assert.equal(saved.body.items.find((item) => item.key === "entity:Acme").final, "acme-ltd");
  assert.notEqual(saved.body.digest, before.digest);
  // A pick naming a digest the record has left (a stale tab) is refused with the fresh view.
  const stale = await pick(false, before.digest);
  assert.equal(stale.status, 409);
  assert.equal(stale.body.code, "digest-changed");
  assert.equal(stale.body.view.digest, saved.body.digest);
  const reoffered = (await json("/api/entity-candidates")).body;
  assert.equal(reoffered.digest, saved.body.digest);
  assert.equal(fake.calls().filter(({ argv }) => argv[2] === "resolve").length, 2, "the register's answers are kept for the page; only the offer is renewed");
  const none = await pick(false, reoffered.digest);
  assert.equal(none.status, 200);
  assert.equal(none.body.items.find((item) => item.key === "entity:Acme").final, false);
});

test("without Engine, with an old bsig, with no candidates or in newsroom mode nothing is shown; a failure is a short note", async (t) => {
  const root = tempRoot(t);
  const caseDir = ingestCase(root);
  const empty = { status: 200, body: { available: false, mentions: [] } };
  const missing = createWikiBridge({ env: { ...safeChildEnvironment(), SPOTLIGHT_BSIG: join(root, "no-bsig") } });
  for (const wiki of [null, missing, fakeBsig(own(root, "old"), { old: true, resolve: { Acme: ACME } }).wiki, fakeBsig(own(root, "none"), { changes: [] }).wiki]) {
    const { json } = await authed(t, caseDir, { wiki });
    assert.deepEqual(await json("/api/entity-candidates"), empty);
  }

  const config = join(root, "newsroom-config.json");
  writeFileSync(config, JSON.stringify({ knowledge_destination: { mode: "newsroom" } }));
  const newsroom = fakeBsig(own(root, "newsroom"), { changes: [], resolve: { Acme: ACME } });
  const env = { ...safeChildEnvironment(), SPOTLIGHT_CONFIG: config };
  const shown = await authed(t, caseDir, { wiki: newsroom.wiki, bridgeEnv: env });
  assert.deepEqual(await shown.json("/api/entity-candidates"), empty);

  // One mention's failed lookup marks that mention only; the others are still looked up.
  const failing = fakeBsig(own(root, "failing"), { changes: [], failFor: ["Acme"], resolve: { Doe: ACME } });
  const { json } = await authed(t, caseDir, { wiki: failing.wiki });
  const failed = await json("/api/entity-candidates");
  assert.equal(failed.status, 200);
  assert.deepEqual(failed.body.mentions, [
    { key: "entity:Acme", name: "Acme", candidates: [], failed: true },
    { key: "entity:Doe", name: "Doe", candidates: ACME.candidates.map(({ id, label, type }) => ({ id, label, type })) },
  ]);
  assert.equal((await json("/api/view")).status, 200);
});

test("candidates beyond the page's limit are counted, not dropped silently", async (t) => {
  const root = tempRoot(t);
  const many = Array.from({ length: 12 }, (_, i) => ({ id: `acme-${i}`, type: "company", label: `Acme ${i}`, score: 0.5, evidence: [] }));
  const fake = fakeBsig(root, { changes: [], resolve: { Acme: { status: "candidates", candidates: many } } });
  const { json } = await authed(t, ingestCase(root), { wiki: fake.wiki });
  const [acme] = (await json("/api/entity-candidates")).body.mentions;
  assert.equal(acme.candidates.length, 10);
  assert.equal(acme.more, 2);
});

test("a slow lookup is cut at its deadline and marked, without holding up the other mentions", async (t) => {
  const root = tempRoot(t);
  const fake = fakeBsig(root, { changes: [], slowFor: { Acme: 4000 }, resolve: { Doe: ACME } });
  const { json } = await authed(t, ingestCase(root), { wiki: fake.wiki, entityLookupMs: 1000, entityBudgetMs: 2000 });
  const started = Date.now();
  const { mentions } = (await json("/api/entity-candidates")).body;
  assert.ok(Date.now() - started < 3500, "the page waited for the slow lookup");
  assert.deepEqual(mentions.map(({ name, failed }) => [name, Boolean(failed)]), [["Acme", true], ["Doe", false]]);
});

test("two picks in a row are both saved without asking Engine again", async (t) => {
  const root = tempRoot(t);
  const fake = fakeBsig(root, { changes: [], resolve: { Acme: ACME, Doe: { status: "candidates", candidates: [{ id: "jane-doe", type: "person", label: "Jane Doe" }] } } });
  const { json } = await authed(t, ingestCase(root), { wiki: fake.wiki });
  const offer = (await json("/api/entity-candidates")).body;
  const first = await json("/api/edit", { edit: { kind: "mark", key: "entity:Acme", value: "acme-ltd" }, digest: offer.digest });
  assert.equal(first.status, 200);
  // The page sends the digest its view now shows; the offer followed the pick.
  const second = await json("/api/edit", { edit: { kind: "mark", key: "entity:Doe", value: "jane-doe" }, digest: first.body.digest });
  assert.equal(second.status, 200, second.body.message);
  assert.deepEqual(["entity:Acme", "entity:Doe"].map((key) => second.body.items.find((item) => item.key === key).final), ["acme-ltd", "jane-doe"]);
  assert.equal(fake.calls().filter(({ argv }) => argv[2] === "resolve").length, 2);
});

test("an offer that finishes after a newer one does not replace it", async (t) => {
  const root = tempRoot(t);
  const fake = fakeBsig(root, { changes: [], slowOnce: { Acme: 2500 }, resolve: { Acme: ACME } });
  const { json } = await authed(t, ingestCase(root), { wiki: fake.wiki });
  const late = json("/api/entity-candidates");
  await new Promise((settle) => setTimeout(settle, 300));
  const renamed = await json("/api/edit", { edit: { kind: "reviewer", value: "Jane" } });
  const fresh = (await json("/api/entity-candidates")).body;
  assert.equal(fresh.digest, renamed.body.digest);
  await late;
  const pick = await json("/api/edit", { edit: { kind: "mark", key: "entity:Acme", value: "acme-ltd" }, digest: fresh.digest });
  assert.equal(pick.status, 200, pick.body.message);
});

test("a changed mention type asks the register again", async (t) => {
  const root = tempRoot(t);
  const fake = fakeBsig(root, { changes: [], resolve: { Acme: ACME } });
  const caseDir = ingestCase(root);
  const { json } = await authed(t, caseDir, { wiki: fake.wiki });
  await json("/api/entity-candidates");
  writeFileSync(join(caseDir, "data", "knowledge-mentions.json"), JSON.stringify({ schema_version: "1.0", mentions: { Acme: { entity_type: "company" } } }));
  await json("/api/entity-candidates");
  const acme = fake.calls().filter(({ argv, stdin }) => argv[2] === "resolve" && JSON.parse(stdin).name === "Acme");
  assert.deepEqual(acme.map(({ stdin }) => JSON.parse(stdin).type), [undefined, "company"]);
});
