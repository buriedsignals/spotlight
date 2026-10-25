// harness/composition.json as the launcher loads it: a role narrowed to named
// case files may declare only the tools that honour that narrowing.
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { agentSpec, allowedTools, loadComposition, systemPrompt } from './roles.ts';
import type { AgentSpec } from './roles.ts';

async function compositionWith(t: TestContext, spec: AgentSpec): Promise<string> {
	const root = await mkdtemp(join(tmpdir(), 'spotlight-pi-roles-'));
	t.after(() => rm(root, { recursive: true, force: true }));
	const path = join(root, 'composition.json');
	await writeFile(path, JSON.stringify({ agents: { narrowed: spec } }));
	return path;
}

const NARROWED = { role: 'agents/narrowed.md', skills: ['epistemic-grounding'], reads: ['data/findings.json'], writes: ['data/proposal.json'] };

test('a narrowed role that declares a tool which ignores its scope is refused at load', async (t) => {
	for (const tool of ['fetch', 'search', 'http_get', 'browse', 'exiftool', 'build_review_html', 'run_rlm', 'spawn_agent']) {
		const path = await compositionWith(t, { ...NARROWED, tools: ['read_file', 'write_file', tool] });
		assert.throws(() => loadComposition(path), /do not honour that scope/, tool);
	}
});

test('a scope entry must be a plain case-relative file path', async (t) => {
	for (const entry of ['../outside.json', '/etc/passwd', 'data/./x.json', 'data//x.json', '']) {
		const path = await compositionWith(t, { ...NARROWED, writes: [entry], tools: ['write_file'] });
		assert.throws(() => loadComposition(path), /not a case-relative file path/, entry);
	}
});

// The Anomaly methods run locally with no network (integrations/anomaly), so
// listing them is not egress: the orchestrator keeps the tool in sensitive mode.
test('the orchestrator lists Anomaly methods in sensitive mode too', () => {
	const orchestrator = agentSpec(loadComposition(), 'orchestrator');
	assert.ok(allowedTools(orchestrator, true).includes('anomaly_methods'));
	assert.ok(!allowedTools(orchestrator, true).includes('integration_status'), 'a network-capable tool is still removed');
});

test('a denyReads entry must be a case-relative file, pattern or folder, and does not narrow the role\'s other tools', async (t) => {
	for (const entry of ['../outside.json', '/etc', 'research/../data/', 'data/./x.json', 'data//x.json', '']) {
		const path = await compositionWith(t, { role: 'agents/denied.md', skills: [], tools: ['read_file'], denyReads: [entry] });
		assert.throws(() => loadComposition(path), /denyReads entry/, entry);
	}
	const path = await compositionWith(t, { role: 'agents/denied.md', skills: [], tools: ['read_file', 'fetch', 'exiftool'], denyReads: ['research/swarm/', 'data/research-*.json'] });
	assert.deepEqual(loadComposition(path).narrowed.tools, ['read_file', 'fetch', 'exiftool']);
});

// The 12b reads only the distilled leads and opens a raw page only for an exact
// citation passage; the larger tiers keep the longer leash.
test('the raw-page rule in every prompt follows the model tier', () => {
	const composition = loadComposition();
	const leash = /Read only the leads\. Do not read a `\.raw` file unless you need the exact source passage for a citation; then grep it first/;
	const longer = /Read a section of a `\.raw` file only when the leads look thin or you need exact wording for a citation/;
	for (const role of ['orchestrator', 'investigator', 'fact-checker']) {
		assert.match(systemPrompt(composition, role, '12b'), leash, role);
		assert.doesNotMatch(systemPrompt(composition, role, '12b'), longer, role);
		for (const tier of ['26b', '31b', 'frontier'] as const) {
			assert.match(systemPrompt(composition, role, tier), longer, `${role} ${tier}`);
			assert.doesNotMatch(systemPrompt(composition, role, tier), leash, `${role} ${tier}`);
		}
	}
});

// D4: solo Ingest writes the member wiki through wiki_destination. It is
// local, so sensitive mode keeps it; the newsroom edition ingests by Submit
// and never gets it.
test('the orchestrator holds the solo Ingest wiki write, in sensitive mode too, and not in the newsroom edition', () => {
	const orchestrator = agentSpec(loadComposition(), 'orchestrator');
	assert.ok(allowedTools(orchestrator, false, 'solo').includes('wiki_destination'));
	assert.ok(allowedTools(orchestrator, true, 'solo').includes('wiki_destination'));
	assert.ok(!allowedTools(orchestrator, false, 'newsroom').includes('wiki_destination'));
	assert.ok(allowedTools(orchestrator, false, 'newsroom').includes('newsroom_submit'));
});
