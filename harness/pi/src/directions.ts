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
		line: `Your deliverable: data/findings.json and data/evidence-bundle.json for this direction, every finding id starting ${tag}-F (${tag}-F1, ${tag}-F2) and every evidence item id starting ${tag}-E (${tag}-E1), with this direction's cycle appended to data/investigation-log.json; write them with write_file, then finish.`,
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
	if (letter) {
		const foreign = mine.filter((entry) => !owns(tag, entry.id)).map((entry) => String(entry.id));
		if (foreign.length > 0) {
			throw new DirectionRefused(
				`refused: in this direction every ${noun} id in ${name} starts with ${tag}-${letter} (${tag}-${letter}1, ${tag}-${letter}2), and ${foreign.join(', ')} does not; the file is unchanged. The other directions' entries are kept by the harness and not shown here.`,
			);
		}
		merged = [...entries(base, key).filter((entry) => !owns(tag, entry.id)), ...mine];
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
	// The file's other lists (gaps, connections, monitoring) gather every
	// direction's entries: a worker that wrote without reading keeps the rest.
	for (const [field, value] of Object.entries(base ?? {})) {
		if (field === key || !Array.isArray(value)) continue;
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
