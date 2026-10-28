// harness/composition.json as the launcher loads it: a role narrowed to named
// case files may declare only the tools that honour that narrowing.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { CHECKOUT_ROOT, PERSONA_CONDITIONS, agentSpec, allowedTools, loadComposition, systemPrompt, deliverableFor } from './roles.ts';
import type { AgentSpec, PersonaCondition, WorkerContext } from './roles.ts';

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

const ALL_ON = Object.fromEntries(PERSONA_CONDITIONS.map((name) => [name, true])) as Record<PersonaCondition, boolean>;
const spawned = (phase: string, task: string, conditions = ALL_ON): WorkerContext => ({ phase, task, conditions });
const MODES = ['PLANNING', 'EXECUTION', 'AMENDMENT'];

// 12B r1/r2: a worker's first request ran to 11-15k tokens because it carried
// every mode of its persona. It now gets the block its task needs: PLANNING
// before the methodology is approved, else the task's mode line, else the phase.
test('an investigator gets only the persona block of the mode its spawn implies', () => {
	const composition = loadComposition();
	const cases: [string, string, string][] = [
		['methodology', 'MODE: PLANNING\nCASE_DIR: .', 'PLANNING'],
		['methodology_approval', 'MODE: EXECUTION\nCASE_DIR: .', 'PLANNING'],
		['investigation', 'MODE: EXECUTION\nCASE_DIR: .', 'EXECUTION'],
		['investigation', 'MODE: STRUCTURAL CORRECTION\nCASE_DIR: .', 'EXECUTION'],
		['investigation', 'METHODOLOGY CORRECTION: the plan lacks a step', 'PLANNING'],
		['investigation', 'MODE: AMENDMENT\nTASK: ROUND', 'AMENDMENT'],
		['investigation', 'Rewrite data/findings.json with the missing ids.', 'EXECUTION'],
		['research', 'Suggest marks for the clues.', 'AMENDMENT'],
	];
	for (const [phase, task, mode] of cases) {
		const prompt = systemPrompt(composition, 'investigator', '12b', 'solo', false, spawned(phase, task));
		for (const other of MODES) assert.equal(prompt.includes(`## [MODE: ${other}]`), other === mode, `${phase} / ${task.split('\n')[0]}: ${other}`);
	}
});

// The cut drops only what the task cannot use: with every condition on, a
// mode's prompt holds every persona line outside the other modes' blocks, and
// each condition's section goes with its condition alone.
test('a worker persona keeps all of its mode\'s rules and drops a conditional section only with its condition', () => {
	const composition = loadComposition();
	for (const role of ['investigator', 'fact-checker']) {
		const persona = readFileSync(`${CHECKOUT_ROOT}/agents/${role}.md`, 'utf8').replace(/^---\n[\s\S]*?\n---\n/, '');
		const markers = /^(<!-- \[\/?IF: \w+\] -->|## \[\/?MODE: \w+\])$/;
		for (const mode of role === 'investigator' ? MODES : ['EXECUTION']) {
			const prompt = systemPrompt(composition, role, '12b', 'solo', false, spawned('investigation', `MODE: ${mode}`));
			const others = MODES.filter((name) => name !== mode).map((name) => new RegExp(`^## \\[MODE: ${name}\\]\\n[\\s\\S]*?^## \\[/MODE: ${name}\\]\\n`, 'm'));
			const kept = others.reduce((text, block) => text.replace(block, ''), persona).split('\n');
			for (const line of kept) {
				if (markers.test(line) && !line.includes(`MODE: ${mode}`)) assert.ok(!prompt.includes(`${line}\n`), `${role} ${mode}: marker ${line} reached the prompt`);
				else if (!markers.test(line)) assert.ok(prompt.includes(line), `${role} ${mode}: lost ${JSON.stringify(line)}`);
			}
		}
		for (const name of PERSONA_CONDITIONS) {
			const section = new RegExp(`^<!-- \\[IF: ${name}\\] -->\\n([\\s\\S]*?)^<!-- \\[/IF: ${name}\\] -->`, 'gm');
			const bodies = [...persona.matchAll(section)].map((match) => match[1].trim()).filter((body) => !/^## \[MODE: (?!EXECUTION)/m.test(body));
			const off = systemPrompt(composition, role, '12b', 'solo', false, spawned('investigation', 'MODE: EXECUTION', { ...ALL_ON, [name]: false }));
			for (const body of bodies) assert.ok(!off.includes(body), `${role}: ${name} off kept its section`);
			const on = systemPrompt(composition, role, '12b', 'solo', false, spawned('investigation', 'MODE: EXECUTION', ALL_ON));
			assert.equal(off.length < on.length, bodies.length > 0, `${role}: ${name}`);
		}
	}
});

// A worker never resolves, delegates, reaches a gate or ingests, so its adapter
// leaves out the orchestrator's lines; the orchestrator keeps all of them.
test('the runtime adapter gives a worker only the worker lines', () => {
	const composition = loadComposition();
	for (const edition of ['solo', 'newsroom'] as const) {
		const worker = systemPrompt(composition, 'fact-checker', '12b', edition, false, spawned('investigation', 'MODE: EXECUTION'));
		const orchestrator = systemPrompt(composition, 'orchestrator', '12b', edition);
		assert.doesNotMatch(worker, /orchestrator only|Gate approvals, the report decision/i, edition);
		assert.match(worker, /- fetch -> `fetch`/, edition);
		assert.match(worker, /-> `exiftool`/, edition);
		assert.doesNotMatch(worker, /decision_signals|provenance_manifest|run_rlm|integration_status|anomaly_methods|build_review_html/, edition);
		assert.match(orchestrator, /-> `decision_signals`; .* -> `build_review_html`/, edition);
		assert.match(orchestrator, /Gate approvals, the report decision/, edition);
		assert.equal(orchestrator.match(/orchestrator only/gi)?.length, edition === 'solo' ? 7 : 9, edition);
	}
});

// The 12b reads only the distilled leads and opens a raw page only for an exact
// citation passage; the larger tiers keep the longer leash.
test('the raw-page rule in every prompt follows the model tier', () => {
	const composition = loadComposition();
	const leash = /Read only the leads\. Do not read a `\.raw` file unless you need the exact source passage for a citation; then grep it first/;
	const longer = /Read a section of a `\.raw` file only when the leads look thin or you need exact wording for a citation/;
	for (const role of ['orchestrator', 'investigator', 'fact-checker']) {
		const worker = role === 'orchestrator' ? undefined : spawned('investigation', 'MODE: EXECUTION');
		assert.match(systemPrompt(composition, role, '12b', 'solo', false, worker), leash, role);
		assert.doesNotMatch(systemPrompt(composition, role, '12b', 'solo', false, worker), longer, role);
		for (const tier of ['26b', '31b', 'frontier'] as const) {
			assert.match(systemPrompt(composition, role, tier, 'solo', false, worker), longer, `${role} ${tier}`);
			assert.doesNotMatch(systemPrompt(composition, role, tier, 'solo', false, worker), leash, `${role} ${tier}`);
		}
	}
});

// Gold run 1 on 99c6567e: nothing the orchestrator saw said to refuse unmasking a
// tipster, and the 31B went into the Brief asking for "a list of internal suspects".
test('the orchestrator prompt carries the limits on unmasking people and sources', () => {
	assert.match(systemPrompt(loadComposition(), 'orchestrator', '12b'), /Refuse a request to locate, identify or unmask a private person .* a request to identify a confidential source or whistleblower\./);
});

// tools-da on r4 (LFM3): a fact-checker task naming another role's file, and
// the worker writing that instead of data/fact-check.json. Each role's
// deliverable comes from its return_contract in agents/*.md.
test('each spawn names the role\'s deliverable from its return contract, and a correction task names its own', () => {
	const files = (role: string, phase: string, task: string, step?: string) => deliverableFor(role, phase, task, step)?.files;
	assert.deepEqual(files('fact-checker', 'investigation', 'Ingest: write only knowledge-proposal.json'), ['data/fact-check.json']);
	assert.match(deliverableFor('fact-checker', 'investigation', '')!.line, /^Your deliverable: data\/fact-check\.json covering every finding in data\/findings\.json/);
	assert.deepEqual(files('analyst', 'ingest', 'Propose.'), ['data/knowledge-proposal.json']);
	assert.deepEqual(files('investigator', 'methodology', 'MODE: EXECUTION'), ['data/methodology.json'], 'before the methodology is approved it plans');
	assert.deepEqual(files('investigator', 'investigation', 'MODE: EXECUTION\nWork.'), ['data/findings.json', 'data/evidence-bundle.json']);
	assert.deepEqual(files('investigator', 'research', 'MODE: AMENDMENT', 'suggestions'), ['data/research-suggestions.json']);
	assert.deepEqual(files('investigator', 'research', 'MODE: AMENDMENT', 'amendment'), ['data/methodology-amendment.json']);
	assert.deepEqual(files('investigator', 'investigation', 'MODE: AMENDMENT', 'round_topics'), ['data/research-round.json']);
	assert.equal(deliverableFor('investigator', 'investigation', 'MODE: STRUCTURAL CORRECTION\nFix F2.'), undefined);
	assert.equal(deliverableFor('investigator', 'investigation', 'MODE: EVIDENCE CORRECTION'), undefined);
	assert.equal(deliverableFor('orchestrator', 'investigation', 'x'), undefined);
});
