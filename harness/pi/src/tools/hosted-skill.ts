// The Indicator Framework: a Navigator-hosted skill for Lab members, never
// part of this checkout. invoke_skill resolves it for the investigator and the
// fact-checker: the Navigator CLI lists the revisions this member is entitled
// to, the skill is fetched by that revision and cached under the harness data
// folder (never the case, never the checkout). It loads only when the approved
// methodology turns it on; otherwise invoke_skill says whether it is available.
// Sensitive mode makes no Navigator request and uses a cached revision only.
import { createHash, randomUUID } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';
import type { ModelTier } from '../config.ts';
import { programEnv, runFixed } from './run.ts';
import type { Env } from './run.ts';

export const INDICATOR_FRAMEWORK = 'indicator-framework';
// The roles that may load it (the 12b tier gets SKILL.md and the compact tables).
export const INDICATOR_FRAMEWORK_ROLES = ['investigator', 'fact-checker'];
// What the Navigator CLI reads to find its account (as navigator_tools).
export const NAVIGATOR_ENV = ['NAVIGATOR_API_URL', 'NAVIGATOR_TOKEN', 'XDG_CONFIG_HOME'];

export type HostedSkillContext = { caseDir: string; dataDir: string; env: Env; tier: ModelTier; sensitive: boolean };

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

export async function indicatorFramework(context: HostedSkillContext, signal?: AbortSignal): Promise<string> {
	const found = await currentRevision(context, signal);
	if ('unavailable' in found) return `The Indicator Framework is unavailable: ${found.unavailable}. Continue without it; nothing else changes.`;
	if (!approvedIndicatorFramework(context.caseDir)) {
		return 'The Indicator Framework is available to this member (Lab access) but not loaded: the approved methodology does not turn it on. At PLANNING you may propose `indicator_framework: {"on": true, "reason": "..."}` for a connection or attribution question about digital assets; the member decides at the Methodology gate.';
	}
	const folder = cacheFolder(context, found.revision);
	if (!existsSync(join(folder, MANIFEST))) {
		const fetched = await fetchRevision(context, found.revision, folder, signal);
		if (fetched) return `The Indicator Framework is unavailable: ${fetched}. Continue without it; nothing else changes.`;
	}
	return compose(folder, found.revision, context.tier);
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

// Fetches every file of one revision into a temporary folder, then moves it
// into place, so a cache folder with its manifest is always complete. Returns
// the reason on failure.
async function fetchRevision(context: HostedSkillContext, revision: string, folder: string, signal?: AbortSignal): Promise<string | undefined> {
	const shown = await navigator(context, ['skills', 'show', INDICATOR_FRAMEWORK, '--revision', revision, '--json'], signal).catch((error: Error) => error);
	if (shown instanceof Error || shown.code !== 0) return `Navigator did not return revision ${revision.slice(0, 12)} (${shown instanceof Error ? shown.message : firstLine(shown.stderr)})`;
	let detail: { skill_md?: unknown; files?: Array<{ path?: unknown }> };
	try {
		detail = JSON.parse(shown.stdout);
	} catch {
		return 'Navigator returned an unreadable skill';
	}
	const paths = (detail.files ?? []).map((file) => file.path);
	if (typeof detail.skill_md !== 'string' || !paths.every((path): path is string => typeof path === 'string' && SKILL_FILE.test(path) && !path.split('/').includes('..'))) {
		return 'Navigator returned a skill this harness cannot store safely';
	}
	const temporary = join(cacheRoot(context), `.partial-${randomUUID()}`);
	try {
		mkdirSync(temporary, { recursive: true, mode: 0o700 });
		writeFileSync(join(temporary, 'SKILL.md'), detail.skill_md, { mode: 0o600 });
		for (const path of paths) {
			const file = await navigator(context, ['skills', 'file', INDICATOR_FRAMEWORK, path, '--revision', revision], signal);
			if (file.code !== 0) return `Navigator did not return ${path} (${firstLine(file.stderr)})`;
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

// SKILL.md plus its references: on the 12b tier only the compact tables (the
// files whose name says "compact"), on the larger tiers everything else.
function compose(folder: string, revision: string, tier: ModelTier): string {
	const { files } = JSON.parse(readFileSync(join(folder, MANIFEST), 'utf8')) as { files: string[] };
	const compact = (path: string) => basename(path).toLowerCase().includes('compact');
	const chosen = files.filter((path) => (tier === '12b' ? compact(path) : !compact(path)));
	const parts = [
		`# Indicator Framework (Navigator-hosted skill, revision ${revision})`,
		'Licensed to Lab members for this case. Apply it; never copy its text into case files, reports or the wiki.',
		readFileSync(join(folder, 'SKILL.md'), 'utf8'),
		...chosen.map((path) => `## Reference: ${path}\n\n${readFileSync(join(folder, 'files', path), 'utf8')}`),
	];
	return parts.join('\n\n');
}
