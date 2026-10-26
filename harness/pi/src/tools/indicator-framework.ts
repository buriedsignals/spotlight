// record_indicator_framework: the mechanical half of the Navigator-hosted
// Indicator Framework. The model sends its judgment calls (rows, current
// ratings, roots, branches, conclusions, the items only it can write); the
// harness fills the starting ratings, quadrants, the items the ratings imply,
// the temporal summary and the item order from the fetched framework data,
// then writes data/indicator-framework.json and its report. Nothing about the
// framework itself (levels, quadrants, matrix, item kinds and their order) is
// written here: it all comes from the Lab-gated bundle.
import { Type } from 'typebox';
import type { Static } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { writeConfined } from './case-files.ts';
import { HARNESS_FILES, bundleFile, frameworkBundle } from './hosted-skill.ts';
import type { HostedSkillContext } from './hosted-skill.ts';
import { checkedParams, textResult } from './run.ts';

const Text = Type.String();
const Ids = Type.Array(Type.String());
const Nullable = Type.Union([Type.String(), Type.Null()]);

const params = Type.Object({
	indicators: Type.Array(
		Type.Object({
			id: Text,
			value: Text,
			found_at: Ids,
			collected_via: Text,
			collected_at: Type.Object({ fetched: Text, source_date: Nullable }),
			table_row: Type.String({ description: 'A row id from the row list, "unlisted" or "container"' }),
			element_of: Nullable,
			current_rating: Type.Optional(Type.Object({ fidelity: Text, specificity: Type.Optional(Text) })),
			movement: Type.Array(Type.Object({ axis: Text, from: Text, to: Text, reason: Text, evidence_refs: Ids })),
			pairing_status: Text,
			root_decision: Nullable,
			branch_id: Nullable,
			finding_ids: Type.Optional(Ids),
		}),
	),
	branches: Type.Array(
		Type.Object({ branch_id: Text, root_decision: Text, description: Text, indicator_ids: Ids, strongest: Ids, set_level_signals: Type.Optional(Ids) }),
	),
	conclusions: Type.Array(
		Type.Object({
			conclusion_id: Text,
			question: Text,
			statement: Text,
			branches_present: Ids,
			decisions_not_represented: Ids,
			strengths: Ids,
			weaknesses: Ids,
			finding_ids: Type.Optional(Ids),
		}),
	),
	items: Type.Array(
		Type.Object({
			item_id: Text,
			kind: Text,
			indicator_ids: Ids,
			conclusion_ids: Ids,
			open: Text,
			suggested_step: Text,
			dates: Type.Optional(Ids),
			explanations: Type.Optional(Type.Array(Type.Object({ explanation: Text, requires: Text }))),
		}),
	),
});

export type Recording = Static<typeof params>;
type Indicator = Recording['indicators'][number];
type Rating = { fidelity?: string; specificity?: string; quadrant?: string };

// The fetched data, as framework.json and indicator-types.json hold it.
export type Framework = {
	framework: string;
	framework_version: string;
	levels: { values: string[]; aliases: Record<string, string> };
	matrix: Array<{ fidelity: string; specificity: string; quadrant: string; auto_item: string | null }>;
	item_kinds: Array<{ kind: string; auto: boolean; on_no_rating?: boolean }>;
	pairing_statuses: string[];
	questions: string[];
};
export type IndicatorTypes = { groups: Array<{ rows: Array<{ id: string; fidelity: string; specificity: string }> }> };

const UNLISTED = 'unlisted';
const CONTAINER = 'container';
const FILE = 'data/indicator-framework.json';
const REPORT = 'data/indicator-framework.md';

// Unix seconds or milliseconds, a Wayback capture timestamp, a _ga cookie
// (its last field is when it was set) or ISO 8601 (a year, month, day or
// instant), as a UTC date or instant.
export function decodeDate(value: string): string | undefined {
	const text = value.trim();
	const instant = (ms: number) => (Number.isFinite(ms) ? new Date(ms).toISOString().replace('.000Z', 'Z') : undefined);
	if (/^\d{9,10}$/.test(text)) return instant(Number(text) * 1000);
	if (/^\d{13}$/.test(text)) return instant(Number(text));
	const wayback = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$/.exec(text);
	if (wayback) return instant(Date.parse(`${wayback[1]}-${wayback[2]}-${wayback[3]}T${wayback[4]}:${wayback[5]}:${wayback[6]}Z`));
	const ga = /^GA\d+\.\d+\.\d+\.(\d{10})$/.exec(text);
	if (ga) return instant(Number(ga[1]) * 1000);
	if (/^\d{4}(?:-\d{2}){0,2}$/.test(text)) return Number.isNaN(Date.parse(text)) ? undefined : text;
	if (/^\d{4}-\d{2}-\d{2}T/.test(text)) return instant(Date.parse(text));
	return undefined;
}

// The case file from the model's judgment calls and the framework data, or
// every problem with the call. Fields the harness owns are recomputed, so a
// file read back and recorded again (the fact-checker's pass) needs no cleanup.
export function buildCaseFile(input: Recording, framework: Framework, types: IndicatorTypes) {
	const problems: string[] = [];
	const level = (value: string | undefined) =>
		value === undefined ? undefined : (framework.levels.aliases[value] ?? (framework.levels.values.includes(value) ? value : undefined));
	const place = (rating: Rating) => framework.matrix.find((cell) => cell.fidelity === level(rating.fidelity) && cell.specificity === level(rating.specificity));
	const rows = new Map(types.groups.flatMap((group) => group.rows).map((row) => [row.id, row]));
	const autoKinds = new Set(framework.item_kinds.filter((kind) => kind.auto).map((kind) => kind.kind));
	const order = framework.item_kinds.map((kind) => kind.kind);
	const unratedKind = framework.item_kinds.find((kind) => kind.on_no_rating)?.kind;
	// The item kind each indicator's rating implies, with the text for the item the harness creates.
	const implied = new Map<string, { kind: string; open: string; conclusion_ids: string[] }>();
	const dates: Array<{ date: string; text: string }> = [];

	const indicators = input.indicators.map((indicator: Indicator) => {
		const owner = `indicator ${indicator.id}`;
		const container = indicator.table_row === CONTAINER;
		const row = rows.get(indicator.table_row);
		if (!row && !container && indicator.table_row !== UNLISTED) problems.push(`${owner}: table_row ${JSON.stringify(indicator.table_row)} is not a row id from the row list, "${UNLISTED}" or "${CONTAINER}"`);
		if (!framework.pairing_statuses.includes(indicator.pairing_status)) problems.push(`${owner}: pairing_status must be one of ${framework.pairing_statuses.join(', ')}`);
		const rated = indicator.current_rating;
		for (const axis of ['fidelity', 'specificity'] as const) {
			if (rated?.[axis] !== undefined && !level(rated[axis])) problems.push(`${owner}: ${axis} must be one of ${framework.levels.values.join(', ')}`);
		}
		if (container && rated?.specificity !== undefined) problems.push(`${owner}: a container carries a fidelity only; rate each value inside it as its own indicator`);
		let source_date = indicator.collected_at.source_date;
		if (source_date !== null) {
			const decoded = decodeDate(source_date);
			if (decoded === undefined) problems.push(`${owner}: source_date ${JSON.stringify(source_date)} is not a date the harness can read; give ISO 8601`);
			else dates.push({ date: (source_date = decoded), text: `${decoded} ${indicator.value} (${indicator.id})` });
		}
		const collected_at = { ...indicator.collected_at, source_date };
		if (container) {
			const fidelity = rated?.fidelity;
			return { ...indicator, collected_at, starting_rating: null, current_rating: { fidelity } };
		}
		// Only a row gives a starting rating; an unlisted indicator's ratings are explained in its movement.
		const starting: Rating | null = row ? { fidelity: row.fidelity, specificity: row.specificity } : null;
		if (starting) starting.quadrant = place(starting)?.quadrant;
		const current: Rating | undefined = rated?.specificity !== undefined ? { ...rated, quadrant: place(rated)?.quadrant } : undefined;
		const kind = current ? place(current)?.auto_item : unratedKind;
		if (kind) {
			implied.set(indicator.id, {
				kind,
				open: `${indicator.id} ${indicator.value}: ${current?.quadrant ?? 'no rating yet'}`,
				conclusion_ids: input.conclusions.filter((conclusion) => indicator.branch_id !== null && conclusion.branches_present.includes(indicator.branch_id)).map((conclusion) => conclusion.conclusion_id),
			});
		}
		return { ...indicator, collected_at, starting_rating: starting, current_rating: current ?? rated ?? null };
	});

	for (const conclusion of input.conclusions) {
		if (!framework.questions.includes(conclusion.question)) problems.push(`conclusion ${conclusion.conclusion_id}: question must be one of ${framework.questions.join(', ')}`);
	}
	const written = input.items.filter((item) => !autoKinds.has(item.kind));
	for (const item of written) {
		if (!order.includes(item.kind)) problems.push(`item ${item.item_id}: kind must be one of ${order.filter((name) => !autoKinds.has(name)).join(', ')}`);
	}
	// An item the model wrote of a kind the harness creates keeps its text
	// (a case-specific step) for the indicators whose rating implies that kind;
	// the harness creates an item only for each indicator none of them covers.
	const covered = new Set<string>();
	const kept = input.items
		.filter((item) => autoKinds.has(item.kind))
		.flatMap((item) => {
			const ids = item.indicator_ids.filter((id) => implied.get(id)?.kind === item.kind);
			for (const id of ids) covered.add(id);
			return ids.length > 0 ? [{ ...item, indicator_ids: ids }] : [];
		});
	const created = [...implied]
		.filter(([id]) => !covered.has(id))
		.map(([id, entry]) => ({ item_id: `auto-${entry.kind}-${id}`, kind: entry.kind, indicator_ids: [id], conclusion_ids: entry.conclusion_ids, open: entry.open, suggested_step: '' }));
	const items = [...created, ...kept, ...written]
		.map((item) => ({ ...item, assign_to: null }))
		.sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind));
	const document = {
		framework: framework.framework,
		framework_version: framework.framework_version,
		indicators,
		branches: input.branches,
		conclusions: input.conclusions,
		temporal_summary: dates.sort((a, b) => a.date.localeCompare(b.date)).map((entry) => entry.text).join(' · '),
		indicator_gaps: items,
	};
	return { document, problems, created: created.length, dropped: input.items.length - written.length - kept.length };
}

const cell = (value: unknown) => String(value ?? '').replaceAll('|', '\\|').replaceAll('\n', ' ');

// The readable report, from the case file alone.
export function renderReport(document: ReturnType<typeof buildCaseFile>['document']): string {
	const lines = [
		'# Indicator Framework',
		'',
		`Framework version ${document.framework_version}. It shows what each indicator behind the findings is worth; it never changes a finding's confidence or grounding.`,
		'',
		'## Indicators',
		'',
		'| Id | Value | Row | Fidelity | Specificity | Quadrant | Branch |',
		'|---|---|---|---|---|---|---|',
		...document.indicators.map((indicator) => {
			const rating = (indicator.current_rating ?? {}) as Rating;
			return `| ${[indicator.id, indicator.value, indicator.table_row, rating.fidelity, rating.specificity, rating.quadrant, indicator.branch_id].map(cell).join(' | ')} |`;
		}),
		'',
		'## Branches',
		'',
		...document.branches.map((branch) => `- **${branch.branch_id}** ${branch.root_decision}: ${branch.description} (${branch.indicator_ids.join(', ')}; strongest ${branch.strongest.join(', ')})`),
		'',
		'## Conclusions',
		'',
		...document.conclusions.flatMap((conclusion) => [
			`- **${conclusion.conclusion_id}** (${conclusion.question}) ${conclusion.statement}`,
			`  - Branches present: ${conclusion.branches_present.join(', ') || 'none'}. Not represented: ${conclusion.decisions_not_represented.join('; ') || 'none'}.`,
			...conclusion.strengths.map((text) => `  - Strength: ${text}`),
			...conclusion.weaknesses.map((text) => `  - Weakness: ${text}`),
		]),
		'',
		'## Temporal summary',
		'',
		document.temporal_summary || 'No dated indicators.',
		'',
		'## Flags & Tasks',
		'',
		...document.indicator_gaps.map((item, index) => `${index + 1}. [${item.kind}] ${item.open}${item.suggested_step ? ` Suggested step: ${item.suggested_step}` : ''} (${item.indicator_ids.join(', ')})`),
		'',
	];
	return lines.join('\n');
}

export function recordIndicatorFrameworkTool(hosted: HostedSkillContext, writeRoot: string): ToolDefinition {
	return {
		name: 'record_indicator_framework',
		label: 'Record Indicator Framework',
		description:
			'Record the Indicator Framework case file and its report from your judgment calls. Each call replaces both files: send every indicator, branch, conclusion and item, including ones already recorded (read data/indicator-framework.json first). The harness fills the starting ratings, every quadrant, the temporal summary and the order of items from the framework data; leave those out. It also creates the items your ratings imply; an item you write of such a kind keeps your text for the indicators whose rating implies it. Nothing is written when a value is refused.',
		parameters: params,
		executionMode: 'sequential',
		async execute(_id, raw, signal) {
			const input = checkedParams<Recording>('record_indicator_framework', params, raw);
			const bundle = await frameworkBundle(hosted, signal);
			if ('unavailable' in bundle) throw new Error(`record_indicator_framework: the Indicator Framework is unavailable (${bundle.unavailable}); nothing was written. Continue without it.`);
			const framework = JSON.parse(bundleFile(bundle, HARNESS_FILES.framework)) as Framework;
			const types = JSON.parse(bundleFile(bundle, HARNESS_FILES.types)) as IndicatorTypes;
			const { document, problems, created, dropped } = buildCaseFile(input, framework, types);
			if (problems.length > 0) throw new Error(`record_indicator_framework: nothing was written. Fix every value below and send the whole record again:\n${problems.join('\n')}`);
			writeConfined(hosted.caseDir, writeRoot, FILE, `${JSON.stringify(document, null, 2)}\n`);
			writeConfined(hosted.caseDir, writeRoot, REPORT, renderReport(document));
			const note = dropped > 0 ? ` ${dropped} item(s) were dropped: their kind is one the harness creates, and no rating in them implies it.` : '';
			return textResult(`Recorded ${FILE} and ${REPORT}: ${document.indicators.length} indicators, ${document.branches.length} branches, ${document.conclusions.length} conclusions, ${document.indicator_gaps.length} items (${created} created from your ratings).${note} The harness validates the file when you finish.`);
		},
	};
}
