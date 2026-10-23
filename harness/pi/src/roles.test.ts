// harness/composition.json as the launcher loads it: a role narrowed to named
// case files may declare only the tools that honour that narrowing.
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { loadComposition } from './roles.ts';
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
