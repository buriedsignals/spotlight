// step2-design.md section 4 (Tom, 2026-11-03, plan C13): pure paperwork in a
// methodology goes to code. On every write of data/methodology.json code
// copies the member's approved brief into brief_directions, and fills, only
// where the plan leaves them out, the direction and topic IDs, the research
// plan's settings (mode from the edition and the Research switch, keys and
// egress from the lane catalogue, consent, the $1/$1 budgets, the 200,000
// token budget), tools_required, and Navigator's skip reason from the
// pre-planning lookup. With Research on, on the local tiers, a plan with no
// research_plan gets a default one until the Methodology approval, unless the
// member dropped it. The model writes the methodology itself; D1 and the
// methodology validators are unchanged and still decide. An AMENDMENT may
// send only the directions it changes; code completes it from the approved
// methodology and the member's marks, and D8 checks the result.
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { parse as parseYaml } from 'yaml';
import { CHECKOUT_ROOT } from '../roles.ts';
import { localTier } from './case-files.ts';

type Row = Record<string, unknown>;
type Lane = { route: string; keys: string[]; egress: string[] };

export const DEFAULT_TOKEN_BUDGET = 200_000; // lanes.ts
export const DEFAULT_BUDGET_USD = 1;
// Where the pre-planning Navigator step leaves its outcome when no lookup ran.
export const NAVIGATOR_PRE_STEP = 'research/navigator/pre-planning.json';

const rows = (value: unknown): Row[] => (Array.isArray(value) ? value.filter((row): row is Row => !!row && typeof row === 'object' && !Array.isArray(row)) : []);
const object = (value: unknown): Row | undefined => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Row) : undefined);

function lanes(): Map<string, Lane> {
	const dir = resolve(CHECKOUT_ROOT, 'lanes');
	const out = new Map<string, Lane>();
	for (const file of readdirSync(dir).filter((name) => name.endsWith('.yaml'))) {
		try {
			const lane = parseYaml(readFileSync(join(dir, file), 'utf8')) as Row;
			if (typeof lane.id !== 'string') continue;
			const list = (value: unknown) => (Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []);
			out.set(lane.id, { route: String(lane.route ?? ''), keys: list(lane.keys), egress: list(lane.egress) });
		} catch {
			// An unreadable lane file fills nothing; D1 names the lane types.
		}
	}
	return out;
}

function readJson(caseDir: string, path: string): Row | undefined {
	try {
		return object(JSON.parse(readFileSync(join(caseDir, path), 'utf8')));
	} catch {
		return undefined;
	}
}

// The member's approved brief, line by line as written.
function briefLines(caseDir: string): string[] | undefined {
	const path = join(caseDir, 'brief-directions.txt');
	if (!existsSync(path)) return undefined;
	return readFileSync(path, 'utf8').split('\n').map((line) => line.replace(/\r$/, '')).filter((line) => line.trim() !== '');
}

export function fillMethodology(caseDir: string, env: Record<string, string | undefined>, document: Row): void {
	const brief = briefLines(caseDir);
	if (brief) document.brief_directions = brief;
	const plan = rows(document.investigation_plan);
	// d1 is the first direction, d2 the second (the rule the prompt gives).
	// Only a plan with no direction_id at all is numbered, so a topic's
	// reference never shifts to another direction (Codex review of simp-4).
	if (plan.every((entry) => entry.direction_id === undefined)) plan.forEach((entry, index) => { entry.direction_id = `d${index + 1}`; });
	// When the pre-planning lookup found Navigator unavailable and no lookup
	// left a receipt, the block says what ran: a 'used' claim without a
	// receipt is not kept (Codex review of simp-4).
	const pre = readJson(caseDir, NAVIGATOR_PRE_STEP);
	const receipted = rows(object(document.navigator)?.queries).some((query) => typeof query.receipt_path === 'string');
	if (pre && typeof pre.reason === 'string' && typeof pre.outcome === 'string' && !receipted) {
		document.navigator = { required: false, used: false, outcome: pre.outcome, fallback_reason: pre.reason };
	}
	const navigator = object(document.navigator);
	const steps = plan.flatMap((entry) => rows(entry.steps));
	const selected = rows(navigator?.queries).flatMap((query) => (Array.isArray(query.selected_tools) ? query.selected_tools : []));
	const tools = [...(Array.isArray(document.tools_required) ? document.tools_required : []), ...steps.map((step) => step.tool), ...selected]
		.filter((tool): tool is string => typeof tool === 'string' && tool.trim() !== '');
	if (tools.length) document.tools_required = [...new Set(tools)];
	if (document.research_plan === undefined && researchOwed(caseDir, env)) {
		const topics = plan.filter((entry) => typeof entry.direction_id === 'string' && SLUG.test(entry.direction_id)).map((entry) => {
			const questions = Array.isArray(entry.questions) ? entry.questions.filter((item) => typeof item === 'string' && item.trim() !== '') : [];
			return { direction_id: entry.direction_id, lane_type: 'ad-hoc', questions: questions.length ? questions : [String(entry.direction)] };
		});
		if (topics.length) document.research_plan = { code_default: true, topics };
	}
	const research = object(document.research_plan);
	if (research) fillResearchPlan(research, env);
}

// Case 2026-11-05-938c2a6a: with Research on, r3's methodology had no plan and
// nothing noticed, so Research was skipped in silence. Research on means
// Research runs: writing the plan is paperwork. ad-hoc needs no keys. Newsroom
// and sensitive cases refuse it at D1, an approved case has no plan to gain, and
// a plan the member dropped at the review (review.py) stays dropped.
const SLUG = /^[a-z0-9]+(-[a-z0-9]+)*$/;
function researchOwed(caseDir: string, env: Record<string, string | undefined>): boolean {
	if (env.SPOTLIGHT_RESEARCH_SWARMS !== 'on' || !localTier(env) || env.SPOTLIGHT_EDITION === 'newsroom' || env.SPOTLIGHT_SENSITIVE?.trim().toLowerCase() === 'true') return false;
	if (object(readJson(caseDir, 'data/orchestration.json')?.approvals)?.methodology) return false;
	return object(object(readJson(caseDir, 'data/methodology-review.json')?.items)?.['selection:research_plan'])?.final !== false;
}

function fillResearchPlan(plan: Row, env: Record<string, string | undefined>): void {
	const catalogue = lanes();
	const topics = rows(plan.topics);
	const used = new Set(topics.map((topic) => topic.topic_id).filter((id): id is string => typeof id === 'string'));
	for (const topic of topics) {
		if (topic.token_budget === undefined) topic.token_budget = DEFAULT_TOKEN_BUDGET;
		if (topic.topic_id !== undefined || typeof topic.direction_id !== 'string' || typeof topic.lane_type !== 'string') continue;
		const base = `${topic.direction_id}-${topic.lane_type}`;
		let id = base;
		for (let n = 2; used.has(id); n++) id = `${base}-${n}`;
		topic.topic_id = id;
		used.add(id);
	}
	const known = topics.filter((topic) => typeof topic.topic_id === 'string' && catalogue.has(String(topic.lane_type)));
	const laneOf = (topic: Row) => catalogue.get(String(topic.lane_type))!;
	const on = env.SPOTLIGHT_RESEARCH_SWARMS === 'on';
	const cloud = known.some((topic) => laneOf(topic).route === 'swarm');
	if (plan.mode === undefined && on) plan.mode = env.SPOTLIGHT_EDITION === 'newsroom' ? 'newsroom' : cloud ? 'openrouter_swarm' : 'local_only';
	if (plan.egress === undefined) plan.egress = known.flatMap((topic) => laneOf(topic).egress.map((destination) => ({ topic_id: topic.topic_id, destination })));
	if (plan.keys === undefined) {
		const names = new Map<string, string[]>();
		for (const topic of known) if (topic.lane_type !== 'ad-hoc') for (const key of laneOf(topic).keys) names.set(key, [...(names.get(key) ?? []), String(topic.topic_id)]);
		plan.keys = [...names].map(([name, ids]) => ({ name, topics: ids }));
	}
	// The member's approval at the Methodology gate is the consent (D1's own rule).
	if (plan.consent === undefined && on) plan.consent = { swarm: cloud };
	if (plan.round_budget_usd === undefined) plan.round_budget_usd = DEFAULT_BUDGET_USD;
	if (plan.case_budget_usd === undefined) plan.case_budget_usd = DEFAULT_BUDGET_USD;
}

// An AMENDMENT that sends only {"investigation_plan": [the directions it
// changes or adds]}: the approved methodology with each sent direction in
// place of the one with its id (or name), new ones appended, and the
// directions the member marked drop removed. A whole methodology (with
// schema_version) is left as written.
export function completeAmendment(caseDir: string, submitted: Row): Row | undefined {
	if (submitted.schema_version !== undefined || !Array.isArray(submitted.investigation_plan)) return undefined;
	const approved = readJson(caseDir, 'data/methodology.json');
	if (!approved) return undefined;
	const key = (entry: Row) => String(entry.direction_id ?? entry.direction);
	const plan = rows(approved.investigation_plan).map((entry) => ({ ...entry }));
	for (const entry of rows(submitted.investigation_plan)) {
		// By its id; or, for a fragment that omits the id code gave the direction
		// (review-simp.md #3), by a name only one approved direction has, keeping
		// that direction's id (Codex review of simp-review). Otherwise it is new.
		let at = entry.direction_id !== undefined ? plan.findIndex((old) => old.direction_id === entry.direction_id) : -1;
		if (at < 0 && entry.direction_id === undefined && entry.direction !== undefined) {
			const named = plan.flatMap((old, index) => (old.direction === entry.direction ? [index] : []));
			if (named.length === 1) at = named[0];
		}
		if (at >= 0) plan[at] = plan[at].direction_id !== undefined && entry.direction_id === undefined ? { ...entry, direction_id: plan[at].direction_id } : entry;
		else plan.push(entry);
	}
	const review = readJson(caseDir, 'data/research-review.json');
	const dropped = new Set(Object.values(object(review?.items) ?? {}).flatMap((item) => {
		const mark = object(item);
		return typeof mark?.target === 'string' && mark.target.startsWith('direction:') && mark.final === 'drop' ? [mark.target.slice('direction:'.length)] : [];
	}));
	return { ...approved, investigation_plan: plan.filter((entry) => !dropped.has(key(entry))) };
}
