// One research direction per investigator spawn. U3 census on 84674fd9 (base
// LFM3 and r5, 7 held-out cases): investigators ran 35-130 calls with 8-24
// context folds and at most one write, and peaked above 30k prompt tokens,
// working every direction of the methodology in one context, as did the 12B
// (34 of 237 runs), the 31B (38 of 181) and Sonnet (5 of 7). On a local tier,
// when the approved methodology has two or more directions, an EXECUTION task
// runs one worker per direction, in order, each in a fresh process.
//
// A direction worker sees only its own direction in the three files it
// writes: reading data/findings.json shows its findings, the bundle its items
// and the log its cycles. Its write is merged into the whole file, so the
// other directions' entries, and every entry no direction owns (the
// fact-checker's bundle items, findings from before the split), stay as they
// are. Its ids carry its tag (D2-F1, D2-E1), so ids never collide and the
// passage links, which hash the finding id, stay valid. A plan with one
// direction keeps plain ids and a single spawn.
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import type { Deliverable } from './roles.ts';

export type Direction = { tag: string; ref: string; text: string; questions: string[]; steps: unknown[] };

// Each worker's share of the investigator's 80 iterations.
export const MIN_DIRECTION_CALLS = 25;
export function directionCallBudget(count: number): number {
	return Math.max(MIN_DIRECTION_CALLS, Math.floor(80 / count));
}

type Entry = Record<string, unknown>;
type Doc = Record<string, unknown>;

function readJson(path: string): Doc | undefined {
	try {
		const value = JSON.parse(readFileSync(path, 'utf8'));
		return value && typeof value === 'object' && !Array.isArray(value) ? value : undefined;
	} catch {
		return undefined;
	}
}

function entries(doc: Doc | undefined, key: string): Entry[] {
	const list = doc?.[key];
	return Array.isArray(list) ? list.filter((entry): entry is Entry => !!entry && typeof entry === 'object') : [];
}

// The approved plan's directions, tagged D1, D2, ... in plan order.
export function planDirections(caseDir: string): Direction[] {
	return entries(readJson(join(caseDir, 'data', 'methodology.json')), 'investigation_plan').map((entry, index) => ({
		tag: `D${index + 1}`,
		ref: `direction:${typeof entry.direction_id === 'string' && entry.direction_id ? entry.direction_id : `d${index + 1}`}`,
		text: String(entry.direction ?? ''),
		questions: Array.isArray(entry.questions) ? entry.questions.map(String) : [],
		steps: Array.isArray(entry.steps) ? entry.steps : [],
	}));
}

const owns = (tag: string, id: unknown) => typeof id === 'string' && id.startsWith(`${tag}-`);

// The directions a cycle runs. Before any direction has findings, all of
// them. After, those the last cycle left open: no findings of their own, a
// research gap naming the direction, or a fact-checked finding of theirs
// that is not verified. With none open, all of them: the orchestrator asked
// for another cycle.
export function directionsToRun(caseDir: string, directions: Direction[]): Direction[] {
	const findings = readJson(join(caseDir, 'data', 'findings.json'));
	const ids = entries(findings, 'findings').map((finding) => finding.id);
	if (!directions.some((direction) => ids.some((id) => owns(direction.tag, id)))) return directions;
	const gaps = entries(findings, 'research_gaps').map((gap) => gap.ref);
	const unverified = entries(readJson(join(caseDir, 'data', 'fact-check.json')), 'claims')
		.filter((claim) => claim.verdict !== 'verified')
		.map((claim) => claim.finding_id);
	const open = directions.filter(
		(direction) =>
			!ids.some((id) => owns(direction.tag, id)) || gaps.includes(direction.ref) || unverified.some((id) => owns(direction.tag, id)),
	);
	return open.length > 0 ? open : directions;
}

// What the worker is told about its direction, after the orchestrator's task.
export function directionBlock(direction: Direction, count: number): string {
	const steps = direction.steps.map((step) => `- ${JSON.stringify(step)}`);
	return [
		`DIRECTION ${direction.tag.slice(1)} of ${count} (${direction.ref}): ${direction.text}`,
		...(direction.questions.length > 0 ? ['Questions:', ...direction.questions.map((question) => `- ${question}`)] : []),
		...(steps.length > 0 ? ['Steps:', ...steps] : []),
		'Work only this direction; each other direction runs in its own task. data/findings.json, data/evidence-bundle.json and data/investigation-log.json show only this direction, and the harness keeps the rest.',
	].join('\n');
}

export function directionDeliverable(tag: string): Deliverable {
	return {
		files: ['data/findings.json', 'data/evidence-bundle.json'],
		line: `Your deliverable: data/findings.json and data/evidence-bundle.json for this direction, every finding id starting ${tag}-F (${tag}-F1, ${tag}-F2) and every evidence item id starting ${tag}-E (${tag}-E1), with this direction's cycle appended to data/investigation-log.json. Write up to 3 findings or items at a time with write_file: each write adds or replaces the entries with those ids and keeps the others. Then finish.`,
	};
}

// The list each direction file divides, and how an entry is keyed to its
// direction: findings and bundle items by their id's tag, log cycles by the
// tag the merge puts in front of their focus.
const SPLIT: Record<string, { key: string; noun: string; letter?: string }> = {
	'data/findings.json': { key: 'findings', noun: 'finding', letter: 'F' },
	'data/evidence-bundle.json': { key: 'items', noun: 'evidence item', letter: 'E' },
	'data/investigation-log.json': { key: 'cycles', noun: 'cycle' },
};

export function isDirectionFile(name: string): boolean {
	return name in SPLIT;
}

const focusTag = (tag: string) => `${tag}: `;
function ownedBy(name: string, tag: string, entry: Entry): boolean {
	return SPLIT[name].letter ? owns(tag, entry.id) : typeof entry.focus === 'string' && entry.focus.startsWith(focusTag(tag));
}

// The file as the direction's worker reads it: the whole file with only its
// own entries in the divided list.
export function directionView(name: string, text: string, tag: string): string {
	const doc = JSON.parse(text) as Doc;
	const { key } = SPLIT[name];
	if (Array.isArray(doc[key])) doc[key] = entries(doc, key).filter((entry) => ownedBy(name, tag, entry));
	return `${JSON.stringify(doc, null, 2)}\n`;
}

export class DirectionRefused extends Error {}

// Entries written replace the entry with the same key in place; new ones are
// added and none is dropped. Writers build a file a few entries at a time
// (wave 1 on 038df55f: 7 of 9 big-case fact-checkers never wrote in 109-260
// calls, gathering for one whole-file write past the 10,240-token output cap,
// and both whole-file writes made, 52k and 79k characters, were refused over
// one claim; investigators wrote up to 20 findings in one call).
//
// An entry leaves only by a withdrawal the writer states, never by being left
// out: `{"<field>": "<id>", "withdrawn": "<reason>"}` removes it from the
// list, and the file's top-level `withdrawn` list keeps its id and reason.
// validate-case.py tells a worker to drop a finding it cannot state, so a
// correction must be able to.
export const WITHDRAW_HINT = 'To remove an entry, write it as {"id": "<its id>", "withdrawn": "<why>"}; leaving it out keeps it.';

function byKey(base: Entry[], mine: Entry[], field: string, name: string): { merged: Entry[]; withdrawn: Entry[] } {
	const merged = [...base];
	const withdrawn: Entry[] = [];
	for (const entry of mine) {
		const same = entry[field] === undefined ? -1 : merged.findIndex((kept) => kept[field] === entry[field]);
		if ('withdrawn' in entry) {
			if (typeof entry.withdrawn !== 'string' || !entry.withdrawn.trim()) {
				throw new DirectionRefused(`refused: withdrawing ${String(entry[field])} from ${name} needs a reason: write {"${field}": "${String(entry[field])}", "withdrawn": "<why>"}; the file is unchanged.`);
			}
			if (same === -1) throw new DirectionRefused(`refused: ${name} has no entry ${String(entry[field])} to withdraw; the file is unchanged.`);
			merged.splice(same, 1);
			withdrawn.push({ [field]: entry[field], reason: entry.withdrawn });
		} else if (same === -1) merged.push(entry);
		else merged[same] = entry;
	}
	return { merged, withdrawn };
}

function withWithdrawn(out: Doc, base: Doc | undefined, withdrawn: Entry[]): void {
	const earlier = Array.isArray(base?.withdrawn) ? (base!.withdrawn as Entry[]) : [];
	if (earlier.length + withdrawn.length > 0) out.withdrawn = [...earlier, ...withdrawn];
}

// The files a role's writes merge into by key, outside a direction split.
const BY_KEY: Record<string, Record<string, { key: string; field: string }>> = {
	investigator: { 'data/findings.json': { key: 'findings', field: 'id' }, 'data/evidence-bundle.json': { key: 'items', field: 'id' } },
	'fact-checker': { 'data/fact-check.json': { key: 'claims', field: 'finding_id' }, 'data/evidence-bundle.json': { key: 'items', field: 'id' } },
};

export function mergesByKey(role: string | undefined, name: string): boolean {
	return !!role && !!BY_KEY[role]?.[name];
}

// The whole file after `written`: its entries merged by key into the current
// ones, its other fields as written, the fields that name the file as the case
// has them. A whole-file write gives the same file it always did, less any
// entry it left out, which stays. Text that is not one JSON object is returned
// as written, for the write checks to refuse.
export function mergeByKey(role: string, name: string, current: string | undefined, written: string): string {
	let doc: Doc;
	let base: Doc | undefined;
	try {
		doc = JSON.parse(written);
		base = current === undefined ? undefined : JSON.parse(current);
	} catch {
		return written;
	}
	if (!doc || typeof doc !== 'object' || Array.isArray(doc) || !base || typeof base !== 'object' || Array.isArray(base)) return written;
	const { key, field } = BY_KEY[role][name];
	const { merged, withdrawn } = byKey(entries(base, key), entries(doc, key), field, name);
	const out: Doc = { ...base, ...doc, [key]: merged };
	withWithdrawn(out, base, withdrawn);
	for (const identity of IDENTITY_FIELDS) if (base[identity] !== undefined) out[identity] = base[identity];
	if (name === 'data/findings.json' && merged.length > 0) delete out.no_qualifying_findings;
	return `${JSON.stringify(out, null, 2)}\n`;
}

const IDENTITY_FIELDS = ['schema_version', 'project', 'lead', 'run_id', 'created_at'];

// The whole file after the direction's worker wrote `written` from its view:
// the other entries as they are, its own replaced by what it wrote, the
// other fields as it wrote them. A log cycle it writes is tagged and added;
// one it rewrites (same tag and cycle number) is replaced, and none is
// dropped, since the log only grows. Text that is not one JSON object is
// returned as written, for the write checks to refuse.
export function mergeDirection(name: string, current: string | undefined, written: string, tag: string): string {
	let doc: Doc;
	try {
		doc = JSON.parse(written);
	} catch {
		return written;
	}
	if (!doc || typeof doc !== 'object' || Array.isArray(doc)) return written;
	const { key, noun, letter } = SPLIT[name];
	const base = current === undefined ? undefined : (JSON.parse(current) as Doc);
	const mine = entries(doc, key);
	let merged: Entry[];
	let withdrawnHere: Entry[] = [];
	if (letter) {
		const foreign = mine.filter((entry) => !owns(tag, entry.id)).map((entry) => String(entry.id));
		if (foreign.length > 0) {
			throw new DirectionRefused(
				`refused: in this direction every ${noun} id in ${name} starts with ${tag}-${letter} (${tag}-${letter}1, ${tag}-${letter}2), and ${foreign.join(', ')} does not; the file is unchanged. The other directions' entries are kept by the harness and not shown here.`,
			);
		}
		const result = byKey(entries(base, key), mine, 'id', name);
		merged = result.merged;
		withdrawnHere = result.withdrawn;
	} else {
		merged = entries(base, key);
		for (const cycle of mine) {
			const tagged = typeof cycle.focus === 'string' && cycle.focus.startsWith(focusTag(tag)) ? cycle : { ...cycle, focus: `${focusTag(tag)}${String(cycle.focus ?? '')}` };
			const same = merged.findIndex((entry) => ownedBy(name, tag, entry) && entry.cycle === tagged.cycle);
			if (same === -1) merged.push(tagged);
			else if (!isDeepStrictEqual(merged[same], tagged)) merged[same] = tagged;
		}
	}
	const out: Doc = { ...doc, [key]: merged };
	withWithdrawn(out, base, withdrawnHere);
	// The fields that name the file stay as the case has them. train-tor on
	// 52bf6183: one direction's write renamed the project "... (direction 1)"
	// and left out run_id and created_at, and the case failed validation.
	for (const field of IDENTITY_FIELDS) if (base?.[field] !== undefined) out[field] = base[field];
	// The file's other lists (gaps, connections, monitoring) gather every
	// direction's entries: a worker that wrote without reading keeps the rest.
	for (const [field, value] of Object.entries(base ?? {})) {
		if (field === key || field === 'withdrawn' || !Array.isArray(value)) continue;
		const added = Array.isArray(out[field]) ? (out[field] as unknown[]) : [];
		out[field] = [...value, ...added.filter((entry) => !value.some((kept) => isDeepStrictEqual(kept, entry)))];
	}
	// Allowed only when the case has no findings at all (findings schema).
	if (name === 'data/findings.json' && merged.length > 0) delete out.no_qualifying_findings;
	return `${JSON.stringify(out, null, 2)}\n`;
}

export function currentText(path: string): string | undefined {
	return existsSync(path) ? readFileSync(path, 'utf8') : undefined;
}
