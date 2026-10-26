// A stand-in `navigator` on PATH serving a synthetic Indicator Framework
// bundle, and a case whose approved methodology turns it on or off. The
// framework data is invented (levels, matrix and kinds named nothing like the
// real ones), so the harness is shown to take all of it from the bundle.
import { createHash } from 'node:crypto';
import { chmod, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { delimiter, dirname, join } from 'node:path';
import type { TestContext } from 'node:test';

export const REVISION = 'a'.repeat(64);

export const FRAMEWORK = {
	schema_version: 1,
	framework: 'synthetic-framework',
	framework_version: '2000-01-01',
	levels: { values: ['Hi', 'Mid', 'Lo'], aliases: { 'Mid-Hi': 'Mid' } },
	matrix: [
		{ fidelity: 'Hi', specificity: 'Hi', quadrant: 'Anchor', position: 'quadrant', auto_item: null, task: 'T1' },
		{ fidelity: 'Hi', specificity: 'Lo', quadrant: 'Context', position: 'quadrant', auto_item: null, task: 'T2' },
		{ fidelity: 'Lo', specificity: 'Hi', quadrant: 'Suspect', position: 'quadrant', auto_item: 'recollect', task: 'T3' },
		{ fidelity: 'Hi', specificity: 'Mid', quadrant: 'Context/Anchor', position: 'specificity_line', auto_item: 'pairing', task: 'T4' },
	],
	item_kinds: [
		{ kind: 'no-rating', auto: true, on_no_rating: true },
		{ kind: 'recollect', auto: true },
		{ kind: 'claim', auto: false },
		{ kind: 'pairing', auto: true },
		{ kind: 'dead', auto: false },
	],
	pairing_statuses: ['open', 'theirs'],
	questions: ['link', 'who'],
};

export const TYPES = {
	groups: [
		{
			id: 'A',
			name: 'Hosting',
			rows: [
				{ id: 'A1', indicator: 'Address', fidelity: 'Hi', specificity: 'Lo', caveat: 'CAVEAT ONE' },
				{ id: 'A2', indicator: 'Tag', fidelity: 'Hi', specificity: 'Mid-Hi', caveat: 'CAVEAT TWO' },
			],
		},
	],
};

const BUNDLE: Record<string, string> = {
	'assets/framework.json': JSON.stringify(FRAMEWORK),
	'assets/indicator-types.json': JSON.stringify(TYPES),
	'references/harness-mode.md': 'SYNTHETIC JUDGMENT: rate what you collected.',
	'scripts/run.py': 'print("never fetched")',
};

const NAVIGATOR = `#!${process.execPath}
const { appendFileSync, readFileSync } = require('node:fs');
const { join } = require('node:path');
const args = process.argv.slice(2);
appendFileSync(join(__dirname, 'argv.jsonl'), JSON.stringify(args) + '\\n');
const state = JSON.parse(readFileSync(join(__dirname, 'state.json'), 'utf8'));
if (args[1] === 'list') process.stdout.write(JSON.stringify({ schema_version: 'navigator-skills/v1', count: state.catalogue.length, skills: state.catalogue }));
else if (args[1] === 'show') process.stdout.write(JSON.stringify(state.skill));
else if (args[1] === 'file') process.stdout.write(state.files[args[3]]);
`;

// `served` replaces a file's bytes after its digest is listed.
export async function hostedFixture(t: TestContext, options: { entitled?: boolean; served?: Record<string, string> } = {}) {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-hosted-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const bin = join(root, 'bin');
	const caseDir = join(root, 'case');
	const dataDir = join(root, 'harness-data');
	await mkdir(bin);
	await mkdir(join(caseDir, 'data'), { recursive: true });
	await mkdir(dataDir);
	await writeFile(join(bin, 'navigator'), NAVIGATOR);
	await chmod(join(bin, 'navigator'), 0o755);
	const catalogue = options.entitled === false ? [] : [{ id: 'indicator-framework', title: 'Indicator Framework', skill_revision: REVISION }];
	const files = Object.entries(BUNDLE).map(([path, text]) => ({ path, size: text.length, sha256: createHash('sha256').update(text).digest('hex') }));
	const skill = { id: 'indicator-framework', skill_revision: REVISION, skill_md: 'SYNTHETIC SKILL.md, never loaded in harness mode', files };
	await writeFile(join(bin, 'state.json'), JSON.stringify({ catalogue, skill, files: { ...BUNDLE, ...options.served } }));
	const context = (sensitive = false) => ({ caseDir, dataDir, env: { PATH: `${bin}${delimiter}${dirname(process.execPath)}`, HOME: root }, sensitive });
	const calls = async () =>
		(await readFile(join(bin, 'argv.jsonl'), 'utf8').catch(() => '')).split('\n').filter(Boolean).map((line) => JSON.parse(line) as string[]);
	return { caseDir, dataDir, context, calls };
}

// The methodology turns the framework on or off, and the member's approval bound that file.
export async function approve(caseDir: string, on: boolean) {
	const methodology = JSON.stringify({ indicator_framework: { on, reason: 'Are the two sites linked?' } });
	await writeFile(join(caseDir, 'data/methodology.json'), methodology);
	const sha = createHash('sha256').update(methodology).digest('hex');
	await writeFile(join(caseDir, 'data/orchestration.json'), JSON.stringify({ approvals: { methodology: { input_sha256: { 'data/methodology.json': sha } } } }));
}
