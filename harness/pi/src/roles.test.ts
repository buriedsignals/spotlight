// harness/composition.json as the launcher loads it: a role narrowed to named
// case files may declare only the tools that honour that narrowing.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { CHECKOUT_ROOT, PERSONA_CONDITIONS, agentSpec, allowedTools, loadComposition, skillAdapterNote, skillsFor, slicePersona, systemPrompt, deliverableFor } from './roles.ts';
import { invokeSkillTool } from './tools/skills.ts';
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
		// On the 12b each FULL block is its compact text; the rules those keep are
		// checked by the compact-block test below.
		const persona = readFileSync(`${CHECKOUT_ROOT}/agents/${role}.md`, 'utf8')
			.replace(/^---\n[\s\S]*?\n---\n/, '')
			.replace(/^<!-- \[FULL: ([\w-]+)\] -->\n[\s\S]*?^<!-- \[\/FULL: \1\] -->\n/gm, (_block, name: string) => readFileSync(`${CHECKOUT_ROOT}/agents/compact/${name}.md`, 'utf8'));
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

// A worker never resolves, delegates, reaches a gate or ingests, and no role
// carries a line or shell mapping for a tool it does not hold (prompt shrink,
// 038df55f: the orchestrator carried fetch's context hygiene and the workers'
// shell mappings, 1.1k tokens on every 12b call). Phase-only lines leave the
// orchestrator's prompt for their phase skill.
test('the runtime adapter gives each role only the lines for the tools it holds', () => {
	const composition = loadComposition();
	for (const edition of ['solo', 'newsroom'] as const) {
		const worker = systemPrompt(composition, 'fact-checker', '12b', edition, false, spawned('investigation', 'MODE: EXECUTION'));
		const analyst = systemPrompt(composition, 'analyst', '12b', edition, false, spawned('ingest', 'Write the proposal.'));
		const orchestrator = systemPrompt(composition, 'orchestrator', '12b', edition);
		assert.doesNotMatch(worker, /orchestrator only|Gate approvals, the report decision/i, edition);
		assert.match(worker, /- fetch -> `fetch`/, edition);
		assert.match(worker, /-> `exiftool`/, edition);
		assert.doesNotMatch(worker, /decision_signals|provenance_manifest|run_rlm|integration_status|anomaly_methods|build_review_html/, edition);
		assert.match(orchestrator, /-> `decision_signals`; .* -> `build_review_html`/, edition);
		assert.doesNotMatch(orchestrator, /- fetch -> |Context hygiene|-> `http_get`|-> `source_expressions`|-> `hash_files`/, edition);
		assert.doesNotMatch(analyst, /- fetch -> |Context hygiene|-> `http_get`|-> `exiftool`/, edition);
		assert.match(analyst, /- query-vault -> `query_vault`\./, edition);
		assert.match(orchestrator, /Gate approvals, the report decision/, edition);
		assert.doesNotMatch(orchestrator, /Preflight step 0|case-contract\.py activate-new|- Research \(orchestrator only\)|- Ingest, the /, edition);
		assert.equal(orchestrator.match(/orchestrator only/gi)?.length, edition === 'solo' ? 3 : 5, edition);
	}
});

// Skill mode reads skills/*/SKILL.md as written. Under the harness invoke_skill
// returns that text unchanged, and a phase skill's phase-only adapter lines
// after it, so every line that left the orchestrator's prompt still reaches it.
test('a phase skill loads unchanged, followed by the adapter lines that left the system prompt', async () => {
	const composition = loadComposition();
	const tools = composition.orchestrator.tools;
	const tool = invokeSkillTool(skillsFor(composition, 'orchestrator'), undefined, (skill) => skillAdapterNote(skill, tools, true));
	const moved: Record<string, RegExp[]> = {
		'phase-preflight': [/^- Preflight step 0 \(the shell check\): skipped\./m],
		'phase-research': [/^- Research \(orchestrator only\): at the Research phase `spotlight_resolve` runs the approved research topics itself/m],
		'phase-investigation': [/^- `scripts\/case-contract\.py activate-new` \(orchestrator only\) is run by the harness itself/m],
		'phase-ingest': [/^- Ingest, the analyst proposal \(orchestrator only\)/m, /^- Ingest, the wiki write \(orchestrator only, solo edition\)/m],
		'phase-report': [],
	};
	for (const [skill, lines] of Object.entries(moved)) {
		const result = await tool.execute('call', { skill }, undefined, undefined, undefined as never);
		const text = (result.content[0] as { text: string }).text;
		const source = readFileSync(resolve(CHECKOUT_ROOT, 'skills', skill, 'SKILL.md'), 'utf8');
		if (lines.length === 0) assert.equal(text, source, skill);
		else assert.ok(text.startsWith(source.trimEnd()), `${skill}: the skill text is unchanged`);
		for (const line of lines) assert.match(text.slice(source.trimEnd().length), line, skill);
	}
	assert.equal(skillAdapterNote('phase-ingest', composition.investigator.tools, false), undefined, 'a worker gets no phase lines');
});

// The 12b reads only the distilled leads and opens a raw page only for an exact
// citation passage; the larger tiers keep the longer leash.
test('the raw-page rule in every prompt follows the model tier', () => {
	const composition = loadComposition();
	const leash = /Read only the leads\. Do not read a `\.raw` file unless you need the exact source passage for a citation; then grep it first/;
	const longer = /Read a section of a `\.raw` file only when the leads look thin or you need exact wording for a citation/;
	assert.doesNotMatch(systemPrompt(composition, 'orchestrator', '12b'), /\.raw/, 'the orchestrator holds no fetch');
	for (const role of ['investigator', 'fact-checker']) {
		const worker = spawned('investigation', 'MODE: EXECUTION');
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
	// train-tor on 52bf6183: DeepSeek V4.1 Flash quoted the task string.
	assert.equal(deliverableFor('investigator', 'investigation', '"MODE: STRUCTURAL CORRECTION — shape-only. Fix D1-F2."'), undefined);
	assert.equal(deliverableFor('investigator', 'investigation', '**MODE: EVIDENCE CORRECTION**'), undefined);
	assert.deepEqual(files('investigator', 'methodology_approval', '"MODE: EXECUTION'), ['data/methodology.json']);
	assert.deepEqual(files('investigator', 'research', '"MODE: AMENDMENT', 'suggestions'), ['data/research-suggestions.json']);
	assert.equal(deliverableFor('orchestrator', 'investigation', 'x'), undefined);
});

// U4 prompt shrink: on the 12b a persona's FULL blocks are swapped for
// agents/compact/<name>.md. Skill mode reads agents/*.md as files, so the
// persona text stays whole, and no rule may be lost in the swap.
const FULL_BLOCK = /^<!-- \[FULL: ([\w-]+)\] -->\n([\s\S]*?)^<!-- \[\/FULL: \1\] -->\n/gm;
const FULL_MARKER = /^<!-- \[\/?FULL: [\w-]+\] -->\n/gm;
const WORKER_PERSONAS = ['investigator', 'fact-checker'];
const flat = (text: string) => text.replace(/\s+/g, ' ').trim();
const personaFile = (role: string) => readFileSync(join(CHECKOUT_ROOT, 'agents', `${role}.md`), 'utf8');

test('a persona file stays its full text: off the 12b it reads as if the FULL markers were not there, and it holds no compact text', () => {
	for (const role of WORKER_PERSONAS) {
		const file = personaFile(role);
		const blocks = [...file.matchAll(FULL_BLOCK)];
		assert.ok(blocks.length > 0, role);
		for (const [, name] of blocks) {
			const compact = readFileSync(join(CHECKOUT_ROOT, 'agents', 'compact', `${name}.md`), 'utf8').trim();
			if (compact) assert.ok(!file.includes(compact), `${name}: compact text is not in the persona file`);
		}
		for (const mode of MODES) {
			assert.equal(slicePersona(file, mode, ALL_ON), slicePersona(file.replace(FULL_MARKER, ''), mode, ALL_ON), `${role} ${mode}`);
		}
	}
});

test('on the 12b each FULL block is its compact text, which keeps every rule sentence or points at a file holding the block word for word', () => {
	for (const role of WORKER_PERSONAS) {
		const file = personaFile(role);
		const compactPrompts = MODES.map((mode) => flat(slicePersona(file, mode, ALL_ON, true)));
		assert.ok([...file.matchAll(FULL_BLOCK)].length > 0, role);
		for (const [, name, body] of file.matchAll(FULL_BLOCK)) {
			const compact = readFileSync(join(CHECKOUT_ROOT, 'agents', 'compact', `${name}.md`), 'utf8');
			const referenced = [...compact.matchAll(/`(agents\/references\/[\w-]+\.md)`/g)].map(([, path]) => readFileSync(join(CHECKOUT_ROOT, path), 'utf8'));
			for (const reference of referenced) assert.equal(reference, body, `${name}: the reference file is the full block, word for word`);
			const rules = body.split(/(?<=[.!])\s+|\n- /).filter((sentence) => /\b(MUST|NEVER|Never|never|Do not|do not|refuse[sd]?)\b/.test(sentence));
			for (const rule of rules) assert.ok([compact, ...referenced].some((text) => flat(text).includes(flat(rule))), `${name} keeps: ${flat(rule)}`);
			assert.ok(!compactPrompts.some((prompt) => prompt.includes(flat(body))), `${name}: the 12b prompt does not carry the full block`);
			if (compact.trim()) assert.ok(compactPrompts.some((prompt) => prompt.includes(flat(compact))), `${name}: the 12b prompt carries the compact block`);
		}
	}
});
