// The Indicator Framework: a Navigator-hosted skill for Lab members, never
// part of this checkout. invoke_skill resolves it for the investigator and the
// fact-checker: the Navigator CLI lists the revisions this member is entitled
// to, the skill is fetched by that revision and cached under the harness data
// folder (never the case, never the checkout). It loads only when the approved
// methodology turns it on; otherwise invoke_skill says whether it is available.
// Sensitive mode makes no Navigator request and uses a cached revision only.
// Only the harness-mode files are fetched, never the skill's scripts: the
// model reads the judgment text and a row list, and record_indicator_framework
// (indicator-framework.ts) does the mechanics from the fetched data.
import { createHash, randomUUID } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { programEnv, runFixed } from './run.ts';
import type { Env } from './run.ts';

export const INDICATOR_FRAMEWORK = 'indicator-framework';
// The roles that may load it and record through record_indicator_framework.
export const INDICATOR_FRAMEWORK_ROLES = ['investigator', 'fact-checker'];
export const HARNESS_FILES = { framework: 'assets/framework.json', types: 'assets/indicator-types.json', judgment: 'references/harness-mode.md' };
// What the Navigator CLI reads to find its account (as navigator_tools).
export const NAVIGATOR_ENV = ['NAVIGATOR_API_URL', 'NAVIGATOR_TOKEN', 'XDG_CONFIG_HOME'];

export type HostedSkillContext = { caseDir: string; dataDir: string; env: Env; sensitive: boolean };

const REVISION = /^[0-9a-f]{64}$/;
const SKILL_FILE = /^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/;
const MANIFEST = 'manifest.json';

type Revision = { revision: string } | { unavailable: string };

// On only when methodology.json carries indicator_framework.on === true and is
// the file the member's methodology approval bound (its SHA-256 in the
// approval's input hashes). The resolver and the review page alone write
// data/orchestration.json.
export function approvedIndicatorFramework(caseDir: string): boolean {
	try {
		const bytes = readFileSync(join(caseDir, 'data/methodology.json'));
		const methodology = JSON.parse(bytes.toString('utf8')) as { indicator_framework?: { on?: unknown } };
		if (methodology.indicator_framework?.on !== true) return false;
		const state = JSON.parse(readFileSync(join(caseDir, 'data/orchestration.json'), 'utf8')) as {
			approvals?: { methodology?: { input_sha256?: Record<string, string> } };
		};
		const bound = state.approvals?.methodology?.input_sha256?.['data/methodology.json'];
		return bound === createHash('sha256').update(bytes).digest('hex');
	} catch {
		return false;
	}
}

export type Bundle = { revision: string; folder: string };

// The approved case's revision from the cache, fetched when missing; or why
// there is none.
export async function frameworkBundle(context: HostedSkillContext, signal?: AbortSignal): Promise<Bundle | { unavailable: string }> {
	const found = await currentRevision(context, signal);
	if ('unavailable' in found) return found;
	if (!approvedIndicatorFramework(context.caseDir)) return { unavailable: 'the approved methodology does not turn it on' };
	return cachedBundle(context, found.revision, signal);
}

async function cachedBundle(context: HostedSkillContext, revision: string, signal?: AbortSignal): Promise<Bundle | { unavailable: string }> {
	const folder = cacheFolder(context, revision);
	if (!existsSync(join(folder, MANIFEST))) {
		const fetched = await fetchRevision(context, revision, folder, signal);
		if (fetched) return { unavailable: fetched };
	}
	// A revision cached before the harness mode holds none of its files.
	if (!Object.values(HARNESS_FILES).every((path) => existsSync(join(folder, 'files', path)))) {
		return { unavailable: `cached revision ${revision.slice(0, 12)} has no harness-mode files` };
	}
	return { revision, folder };
}

export async function indicatorFramework(context: HostedSkillContext, signal?: AbortSignal): Promise<string> {
	const unavailable = (reason: string) => `The Indicator Framework is unavailable: ${reason}. Continue without it; nothing else changes.`;
	const found = await currentRevision(context, signal);
	if ('unavailable' in found) return unavailable(found.unavailable);
	if (!approvedIndicatorFramework(context.caseDir)) {
		return 'The Indicator Framework is available to this member (Lab access) but not loaded: the approved methodology does not turn it on. At PLANNING you may propose `indicator_framework: {"on": true, "reason": "..."}` for a connection or attribution question about digital assets; the member decides at the Methodology gate.';
	}
	const bundle = await cachedBundle(context, found.revision, signal);
	return 'unavailable' in bundle ? unavailable(bundle.unavailable) : compose(bundle);
}

function cacheRoot(context: HostedSkillContext): string {
	return join(context.dataDir, 'hosted-skills', INDICATOR_FRAMEWORK);
}

function cacheFolder(context: HostedSkillContext, revision: string): string {
	return join(cacheRoot(context), revision);
}

// The most recently cached complete revision, for sensitive mode and offline use.
function newestCached(context: HostedSkillContext): string | undefined {
	const root = cacheRoot(context);
	if (!existsSync(root)) return undefined;
	return readdirSync(root)
		.filter((name) => REVISION.test(name) && existsSync(join(root, name, MANIFEST)))
		.map((name) => ({ name, at: statSync(join(root, name, MANIFEST)).mtimeMs }))
		.sort((a, b) => b.at - a.at)[0]?.name;
}

function navigator(context: HostedSkillContext, args: string[], signal?: AbortSignal) {
	return runFixed('navigator', args, { env: programEnv(context.env, NAVIGATOR_ENV), cwd: context.dataDir, signal, timeoutMs: 60_000 });
}

function firstLine(text: string): string {
	return text.trim().split('\n').pop()?.slice(0, 300) || 'no message';
}

async function currentRevision(context: HostedSkillContext, signal?: AbortSignal): Promise<Revision> {
	const cached = () => {
		const revision = newestCached(context);
		return revision ? { revision } : undefined;
	};
	if (context.sensitive) return cached() ?? { unavailable: 'sensitive mode makes no Navigator request, and no revision is cached' };
	let result;
	try {
		result = await navigator(context, ['skills', 'list', '--json'], signal);
	} catch (error) {
		signal?.throwIfAborted();
		return { unavailable: `the Navigator CLI could not run (${(error as Error).message})` };
	}
	if (result.code !== 0) {
		// Only an unreachable Navigator falls back to a cached revision; a
		// refused account (signed out, no entitlement) does not.
		if (/could not reach/i.test(result.stderr)) return cached() ?? { unavailable: 'Navigator could not be reached, and no revision is cached' };
		return { unavailable: `Navigator refused the request (${firstLine(result.stderr || result.stdout)})` };
	}
	let skills: Array<{ id?: unknown; skill_revision?: unknown }>;
	try {
		skills = (JSON.parse(result.stdout) as { skills?: typeof skills }).skills ?? [];
	} catch {
		return { unavailable: 'Navigator returned an unreadable skills catalogue' };
	}
	const entry = skills.find((skill) => skill.id === INDICATOR_FRAMEWORK);
	if (!entry) return { unavailable: 'this Navigator account has no access to it (Lab members only)' };
	if (typeof entry.skill_revision !== 'string' || !REVISION.test(entry.skill_revision)) return { unavailable: 'Navigator listed it without a valid revision' };
	return { revision: entry.skill_revision };
}

// Fetches the harness-mode files of one revision, each checked against the
// digest Navigator lists for it, into a temporary folder, then moves it into
// place, so a cache folder with its manifest is always complete. Returns the
// reason on failure.
async function fetchRevision(context: HostedSkillContext, revision: string, folder: string, signal?: AbortSignal): Promise<string | undefined> {
	const shown = await navigator(context, ['skills', 'show', INDICATOR_FRAMEWORK, '--revision', revision, '--json'], signal).catch((error: Error) => error);
	if (shown instanceof Error || shown.code !== 0) return `Navigator did not return revision ${revision.slice(0, 12)} (${shown instanceof Error ? shown.message : firstLine(shown.stderr)})`;
	let detail: { files?: Array<{ path?: unknown; sha256?: unknown }> };
	try {
		detail = JSON.parse(shown.stdout);
	} catch {
		return 'Navigator returned an unreadable skill';
	}
	const listed = new Map((detail.files ?? []).map((file) => [file.path, file.sha256]));
	const paths = Object.values(HARNESS_FILES);
	if (!paths.every((path) => typeof listed.get(path) === 'string')) return `revision ${revision.slice(0, 12)} has no harness-mode files`;
	const temporary = join(cacheRoot(context), `.partial-${randomUUID()}`);
	try {
		mkdirSync(temporary, { recursive: true, mode: 0o700 });
		for (const path of paths) {
			const file = await navigator(context, ['skills', 'file', INDICATOR_FRAMEWORK, path, '--revision', revision], signal);
			if (file.code !== 0) return `Navigator did not return ${path} (${firstLine(file.stderr)})`;
			if (createHash('sha256').update(file.stdout).digest('hex') !== listed.get(path)) return `${path} does not match the digest Navigator listed`;
			mkdirSync(dirname(join(temporary, 'files', path)), { recursive: true, mode: 0o700 });
			writeFileSync(join(temporary, 'files', path), file.stdout, { mode: 0o600 });
		}
		writeFileSync(join(temporary, MANIFEST), JSON.stringify({ revision, files: paths }), { mode: 0o600 });
		if (!existsSync(join(folder, MANIFEST))) {
			rmSync(folder, { recursive: true, force: true });
			renameSync(temporary, folder);
		}
		return undefined;
	} finally {
		rmSync(temporary, { recursive: true, force: true });
	}
}

export function bundleFile(bundle: Bundle, path: string): string {
	return readFileSync(join(bundle.folder, 'files', path), 'utf8');
}

type IndicatorTypes = { groups: Array<{ id: string; name: string; rows: Array<{ id: string; indicator: string }> }> };

// The judgment text and a list of row ids and names to pick table_row from;
// the full tables stay with the harness.
function compose(bundle: Bundle): string {
	const types = JSON.parse(bundleFile(bundle, HARNESS_FILES.types)) as IndicatorTypes;
	const rows = types.groups.map((group) => `- ${group.name}: ${group.rows.map((row) => `${row.id} ${row.indicator}`).join('; ')}`);
	return [
		`# Indicator Framework (Navigator-hosted skill, revision ${bundle.revision})`,
		'Licensed to Lab members for this case. Apply it; never copy its text into case files, reports or the wiki. Record your work with `record_indicator_framework`.',
		bundleFile(bundle, HARNESS_FILES.judgment),
		`## The row list\n\n${rows.join('\n')}`,
	].join('\n\n');
}
