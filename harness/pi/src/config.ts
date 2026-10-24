// Launch configuration. The harness reads its configuration only from the
// environment the caller sets; nothing comes from the member's own Pi config.
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { isIP } from 'node:net';
import { isAbsolute, resolve } from 'node:path';
import { CaseHeldError, acquireCaseLock } from './lock.ts';
import type { CaseLock } from './lock.ts';
import { piCliPath } from './pi-process.ts';
import { parseFixedRequestBody } from './provider-policy.ts';
import { CHECKOUT_ROOT, agentSpec } from './roles.ts';
import type { Composition } from './roles.ts';

export type Edition = 'solo' | 'newsroom';
export type ModelTier = '12b' | '26b' | '31b' | 'frontier';

// Conversation folding for a small local model: past `at` tokens the
// conversation is rewritten into a checkpoint, keeping the last `keep` tokens
// verbatim. Bigger models hold discipline deeper, so they fold later. The
// frontier tier has no entry and keeps Pi's default compaction.
export const LOCAL_TIER_FOLD: Record<Exclude<ModelTier, 'frontier'>, { at: number; keep: number }> = {
	'12b': { at: 16384, keep: 4000 },
	'26b': { at: 24576, keep: 6000 },
	'31b': { at: 28672, keep: 7000 },
};
export type Fold = { at: number; keep: number };
export type Inference = 'own' | 'third-party';

// A model route. `own` means the operator runs the inference itself (a local
// model, or a newsroom's own servers); anything else is `third-party`.
export type Route = {
	name: string;
	baseURL: string;
	model: string;
	inference: Inference;
	keyEnv?: string;
};

export type HarnessConfig = {
	edition: Edition;
	casesRoot: string;
	caseId: string;
	caseDir: string;
	// The chat id the case was bound from, when SPOTLIGHT_CASE_ID was unset.
	boundChat?: string;
	dataDir: string;
	routes: Route[];
	pipelineRoute: Route;
	helperURL?: string;
	searchURL?: string;
	// RLM endpoint that distills fetched pages; always one the operator runs.
	rlmURL?: string;
	requestBody?: string;
	contextWindow: number;
	modelTier: ModelTier;
	// Where the local tier folds the conversation; undefined on the frontier tier.
	fold?: Fold;
	python: string;
	// Installed Spotlight config, read by the scripts the typed tools wrap.
	spotlightConfig: string;
	// Sensitive mode: no egress tools in any process.
	sensitive: boolean;
	// The third route, beside the pipeline route (`general`, the process
	// route) and the own-inference route every case-reading process is held to
	// (`restricted`, routeFor): the research swarm. It carries only approved
	// topic briefs, so it may be a third-party endpoint. Off unless the member
	// opted in (the "Research swarms" switch plus a key).
	swarm: SwarmSetting;
};

// The swarm route when the member opted in, or why there is none. The key is
// named, never held here: lanes.ts reads it from the environment per request.
export type SwarmRoute = {
	name: 'swarm';
	baseURL: string;
	// Primary model first, then the fallbacks OpenRouter tries in order.
	models: string[];
	keyEnv: string;
	// Fixed request-body fields; zero data retention is always among them.
	requestBody: Record<string, unknown>;
	concurrency: number;
	// OpenRouter routes `models` as a fallback list; another endpoint gets `model`.
	openRouter: boolean;
};
export type SwarmSetting = { available: true; route: SwarmRoute } | { available: false; reason: string };

export const SWARM_DEFAULTS = {
	baseURL: 'https://openrouter.ai/api/v1',
	model: 'deepseek/deepseek-v4-flash',
	fallbackModel: 'z-ai/glm-5.3',
	keyEnv: 'OPENROUTER_API_KEY',
	concurrency: 8,
} as const;

export class ConfigError extends Error {}

const CASE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const PROVIDER_KEY = 'SPOTLIGHT_PROVIDER_API_KEY';

type Env = Record<string, string | undefined>;

function required(env: Env, name: string): string {
	const value = env[name]?.trim();
	if (!value) throw new ConfigError(`${name} is not set.`);
	return value;
}

function httpURL(name: string, value: string): string {
	let url: URL;
	try {
		url = new URL(value);
	} catch {
		throw new ConfigError(`${name} is not a valid URL.`);
	}
	if (url.protocol !== 'http:' && url.protocol !== 'https:') {
		throw new ConfigError(`${name} must be an http(s) URL.`);
	}
	return url.href.replace(/\/$/, '');
}

function optionalURL(env: Env, name: string): string | undefined {
	const value = env[name]?.trim();
	return value ? httpURL(name, value) : undefined;
}

// Only a real loopback address counts: an IPv4 literal in 127.0.0.0/8,
// `localhost` or [::1]. A name that merely starts with "127." is a remote host.
function isLoopback(baseURL: string): boolean {
	const host = new URL(baseURL).hostname;
	if (host === 'localhost' || host === '[::1]') return true;
	return isIP(host) === 4 && host.startsWith('127.');
}

function parseRoutes(raw: string | undefined): Route[] {
	if (!raw?.trim()) return [];
	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch {
		throw new ConfigError('SPOTLIGHT_ROUTES is not valid JSON.');
	}
	if (!Array.isArray(parsed)) throw new ConfigError('SPOTLIGHT_ROUTES must be a JSON array.');
	return parsed.map((entry, index) => {
		const item = (entry ?? {}) as Record<string, unknown>;
		const label = `SPOTLIGHT_ROUTES[${index}]`;
		for (const field of ['name', 'base_url', 'model', 'inference']) {
			if (typeof item[field] !== 'string' || !(item[field] as string).trim()) {
				throw new ConfigError(`${label}.${field} must be a non-empty string.`);
			}
		}
		if (item.inference !== 'own' && item.inference !== 'third-party') {
			throw new ConfigError(`${label}.inference must be "own" or "third-party".`);
		}
		if (item.key_env !== undefined && (typeof item.key_env !== 'string' || !/^[A-Z][A-Z0-9_]*$/.test(item.key_env))) {
			throw new ConfigError(`${label}.key_env must name an environment variable.`);
		}
		return {
			name: item.name as string,
			baseURL: httpURL(`${label}.base_url`, item.base_url as string),
			model: item.model as string,
			inference: item.inference as Inference,
			keyEnv: item.key_env as string | undefined,
		};
	});
}

function soloRoute(env: Env): Route {
	const baseURL = httpURL('SPOTLIGHT_MODEL_BASE_URL', required(env, 'SPOTLIGHT_MODEL_BASE_URL'));
	return {
		name: 'model',
		baseURL,
		model: required(env, 'SPOTLIGHT_MODEL_ID'),
		inference: isLoopback(baseURL) ? 'own' : 'third-party',
		keyEnv: PROVIDER_KEY,
	};
}

function newsroomRoutes(env: Env): { routes: Route[]; pipelineRoute: Route } {
	const routes = parseRoutes(env.SPOTLIGHT_ROUTES);
	if (routes.length === 0) {
		throw new ConfigError('newsroom edition has no allowed model route (SPOTLIGHT_ROUTES is empty), so Spotlight will not start.');
	}
	for (const route of routes) {
		if (route.keyEnv && !env[route.keyEnv]) {
			throw new ConfigError(`route "${route.name}" needs ${route.keyEnv}, which is not set.`);
		}
	}
	const requested = optionalURL(env, 'SPOTLIGHT_MODEL_BASE_URL');
	const pipelineRoute = requested ? routes.find((route) => route.baseURL === requested) : routes[0];
	if (!pipelineRoute) {
		throw new ConfigError('SPOTLIGHT_MODEL_BASE_URL is not one of the newsroom\'s allowed routes.');
	}
	for (const name of ['SPOTLIGHT_HELPER_URL', 'SPOTLIGHT_SEARCH_URL']) {
		const url = optionalURL(env, name);
		if (url && !routes.some((route) => route.baseURL === url)) {
			throw new ConfigError(`${name} is not one of the newsroom's allowed routes.`);
		}
	}
	return { routes, pipelineRoute };
}

// Sensitive mode is on when SPOTLIGHT_SENSITIVE is "true" or the checkout's
// AGENTS.md frontmatter says `sensitive: true`, as the scripts read it.
function sensitiveMode(env: Env): boolean {
	const value = env.SPOTLIGHT_SENSITIVE?.trim().toLowerCase();
	if (value === 'true') return true;
	if (value && value !== 'false') throw new ConfigError('SPOTLIGHT_SENSITIVE must be "true" or "false".');
	let manifest: string;
	try {
		manifest = readFileSync(resolve(CHECKOUT_ROOT, 'AGENTS.md'), 'utf8');
	} catch {
		return false;
	}
	const frontmatter = /^---\r?\n([\s\S]*?)\r?\n---/.exec(manifest)?.[1] ?? '';
	return /^sensitive:\s*true\s*$/m.test(frontmatter);
}

// The RLM reads every fetched page of the case, so it may only be an endpoint
// the operator runs: a loopback address in the solo edition, an `own` route in
// the newsroom edition. Anything else stops the launcher.
function rlmEndpoint(env: Env, edition: Edition, routes: Route[]): string | undefined {
	const url = optionalURL(env, 'SPOTLIGHT_RLM_OPENAI_BASE_URL');
	if (!url) return undefined;
	const own = edition === 'solo'
		? isLoopback(url)
		: routes.some((route) => route.baseURL === url && route.inference === 'own');
	if (!own) {
		throw new ConfigError(
			'SPOTLIGHT_RLM_OPENAI_BASE_URL reads case material, so it must be an endpoint you run yourself (solo: a loopback address; newsroom: an "own" route in SPOTLIGHT_ROUTES).',
		);
	}
	return url;
}

export function readConfig(env: Env, chatId?: string): HarnessConfig {
	const editionValue = env.SPOTLIGHT_EDITION?.trim() || 'solo';
	if (editionValue !== 'solo' && editionValue !== 'newsroom') {
		throw new ConfigError('SPOTLIGHT_EDITION must be "solo" or "newsroom".');
	}
	const edition: Edition = editionValue;
	const casesRoot = required(env, 'SPOTLIGHT_CASES_ROOT');
	const dataDir = required(env, 'SPOTLIGHT_HARNESS_DATA');
	if (!isAbsolute(casesRoot)) throw new ConfigError('SPOTLIGHT_CASES_ROOT must be an absolute path.');
	if (!isAbsolute(dataDir)) throw new ConfigError('SPOTLIGHT_HARNESS_DATA must be an absolute path.');
	// SPOTLIGHT_CASE_ID wins; otherwise a chat id picks the case (caseForChat).
	const boundChat = env.SPOTLIGHT_CASE_ID?.trim() ? undefined : chatId;
	const caseId = boundChat ? caseForChat(resolve(casesRoot), resolve(dataDir), boundChat) : required(env, 'SPOTLIGHT_CASE_ID');
	if (!CASE_ID.test(caseId) || caseId === '.' || caseId === '..') {
		throw new ConfigError('SPOTLIGHT_CASE_ID must be a plain case folder name (letters, digits, ".", "_", "-").');
	}
	const { routes, pipelineRoute } = edition === 'newsroom'
		? newsroomRoutes(env)
		: (() => {
				const route = soloRoute(env);
				return { routes: [route], pipelineRoute: route };
			})();
	try {
		parseFixedRequestBody(env.SPOTLIGHT_PROVIDER_REQUEST_BODY);
	} catch (error) {
		throw new ConfigError((error as Error).message);
	}
	const contextWindow = Number(env.SPOTLIGHT_MODEL_CONTEXT_WINDOW || 32768);
	if (!Number.isInteger(contextWindow) || contextWindow < 1024) {
		throw new ConfigError('SPOTLIGHT_MODEL_CONTEXT_WINDOW must be an integer of at least 1024.');
	}
	const modelTier = readModelTier(env);
	const sensitive = sensitiveMode(env);
	return {
		edition,
		casesRoot: resolve(casesRoot),
		caseId,
		caseDir: resolve(casesRoot, caseId),
		boundChat,
		dataDir: resolve(dataDir),
		routes,
		pipelineRoute,
		helperURL: optionalURL(env, 'SPOTLIGHT_HELPER_URL'),
		searchURL: optionalURL(env, 'SPOTLIGHT_SEARCH_URL'),
		rlmURL: rlmEndpoint(env, edition, routes),
		requestBody: env.SPOTLIGHT_PROVIDER_REQUEST_BODY || undefined,
		contextWindow,
		modelTier,
		fold: readFold(env, modelTier, contextWindow),
		python: env.SPOTLIGHT_PYTHON?.trim() || 'python3',
		spotlightConfig: spotlightConfigPath(env),
		sensitive,
		swarm: swarmSetting(env, edition, routes, sensitive),
	};
}

const SWITCH_ON = new Set(['on', 'true', '1', 'yes']);
const SWITCH_OFF = new Set(['', 'off', 'false', '0', 'no']);

// The swarm route. Opt-in only: the "Research swarms" switch
// (SPOTLIGHT_RESEARCH_SWARMS, set by Engine) and the member's key. Sensitive
// mode has none. In the newsroom edition the endpoint must be one of the
// newsroom's allowed routes, and that route's token is the key.
function swarmSetting(env: Env, edition: Edition, routes: Route[], sensitive: boolean): SwarmSetting {
	const switchValue = env.SPOTLIGHT_RESEARCH_SWARMS?.trim().toLowerCase() ?? '';
	if (!SWITCH_ON.has(switchValue) && !SWITCH_OFF.has(switchValue)) {
		throw new ConfigError('SPOTLIGHT_RESEARCH_SWARMS must be "on" or "off".');
	}
	if (!SWITCH_ON.has(switchValue)) return { available: false, reason: 'the Research swarms switch is off' };
	if (sensitive) return { available: false, reason: 'sensitive mode sends nothing to a cloud model' };
	const baseURL = optionalURL(env, 'SPOTLIGHT_SWARM_BASE_URL') ?? SWARM_DEFAULTS.baseURL;
	let keyEnv: string = SWARM_DEFAULTS.keyEnv;
	if (edition === 'newsroom') {
		const allowed = routes.find((route) => route.baseURL === baseURL);
		if (!allowed) return { available: false, reason: 'the swarm endpoint is not one of the newsroom\'s allowed routes' };
		if (allowed.keyEnv) keyEnv = allowed.keyEnv;
	}
	if (!env[keyEnv]?.trim()) return { available: false, reason: `no key for the swarm endpoint (${keyEnv} is not set)` };
	const models = [
		env.SPOTLIGHT_SWARM_MODEL?.trim() || SWARM_DEFAULTS.model,
		...(env.SPOTLIGHT_SWARM_FALLBACK_MODEL === undefined
			? [SWARM_DEFAULTS.fallbackModel]
			: env.SPOTLIGHT_SWARM_FALLBACK_MODEL.trim() ? [env.SPOTLIGHT_SWARM_FALLBACK_MODEL.trim()] : []),
	];
	let extra: Record<string, unknown> | undefined;
	try {
		extra = parseFixedRequestBody(env.SPOTLIGHT_SWARM_REQUEST_BODY);
	} catch (error) {
		throw new ConfigError((error as Error).message.replace('SPOTLIGHT_PROVIDER_REQUEST_BODY', 'SPOTLIGHT_SWARM_REQUEST_BODY'));
	}
	const provider = { ...((extra?.provider as Record<string, unknown> | undefined) ?? {}), zdr: true };
	return {
		available: true,
		route: {
			name: 'swarm',
			baseURL,
			models,
			keyEnv,
			requestBody: { ...extra, provider },
			concurrency: positiveInteger(env, 'SPOTLIGHT_SWARM_CONCURRENCY', SWARM_DEFAULTS.concurrency),
			openRouter: new URL(baseURL).hostname === 'openrouter.ai',
		},
	};
}

function readModelTier(env: Env): ModelTier {
	const value = env.SPOTLIGHT_MODEL_TIER?.trim() || '12b';
	if (value !== 'frontier' && !Object.hasOwn(LOCAL_TIER_FOLD, value)) {
		throw new ConfigError('SPOTLIGHT_MODEL_TIER must be "12b", "26b", "31b" or "frontier".');
	}
	return value as ModelTier;
}

function positiveInteger(env: Env, name: string, fallback: number): number {
	const raw = env[name]?.trim();
	if (!raw) return fallback;
	const value = Number(raw);
	if (!Number.isSafeInteger(value) || value < 1) throw new ConfigError(`${name} must be a positive integer.`);
	return value;
}

// The tier's fold point, with SPOTLIGHT_COMPACT_AT / SPOTLIGHT_COMPACT_KEEP
// overriding it. A fold point at or past the context window would never fire
// before the model overflows, so it is refused rather than silently moved.
function readFold(env: Env, tier: ModelTier, contextWindow: number): Fold | undefined {
	if (tier === 'frontier') return undefined;
	const at = positiveInteger(env, 'SPOTLIGHT_COMPACT_AT', LOCAL_TIER_FOLD[tier].at);
	const keep = positiveInteger(env, 'SPOTLIGHT_COMPACT_KEEP', LOCAL_TIER_FOLD[tier].keep);
	if (at >= contextWindow) {
		throw new ConfigError(`the ${tier} fold point (${at} tokens) must be below SPOTLIGHT_MODEL_CONTEXT_WINDOW (${contextWindow}); set SPOTLIGHT_COMPACT_AT or SPOTLIGHT_MODEL_TIER.`);
	}
	if (keep >= at) throw new ConfigError('SPOTLIGHT_COMPACT_KEEP must be below the fold point.');
	return { at, keep };
}

// Pi reads compaction settings from settings.json in its configuration
// folder. The local tier compacts when the context passes `fold.at`
// (Pi triggers at contextWindow - reserveTokens). The frontier tier writes no
// compaction settings, so Pi's defaults apply.
export function piSettings(config: HarnessConfig): Record<string, unknown> {
	if (!config.fold) return {};
	return {
		compaction: {
			enabled: true,
			reserveTokens: config.contextWindow - config.fold.at,
			keepRecentTokens: config.fold.keep,
		},
	};
}

function spotlightConfigPath(env: Env): string {
	const value = env.SPOTLIGHT_CONFIG?.trim();
	if (!value) return resolve(CHECKOUT_ROOT, '.spotlight-config.json');
	if (!isAbsolute(value)) throw new ConfigError('SPOTLIGHT_CONFIG must be an absolute path.');
	return resolve(value);
}

export class RouteRefused extends Error {}

// Every process reads the case, which may hold journalist input or
// local-corpus content, so it runs only on a route the operator runs itself.
// No silent fallback: an own-inference route or nowhere.
export function routeFor(config: HarnessConfig): Route {
	if (config.pipelineRoute.inference === 'own') return config.pipelineRoute;
	const own = config.routes.find((route) => route.inference === 'own');
	if (!own) {
		throw new RouteRefused(
			'journalist input and local-corpus material can only go to a model route you run yourself, and none is configured; Spotlight will not process them.',
		);
	}
	return own;
}

// Every value a log line must never show: the route tokens and every key an
// agent declares.
export function secretValues(config: HarnessConfig, composition: Composition, env: Env): string[] {
	const names = new Set<string>([PROVIDER_KEY]);
	for (const route of config.routes) if (route.keyEnv) names.add(route.keyEnv);
	for (const spec of Object.values(composition)) for (const key of spec.keys ?? []) names.add(key);
	if (config.swarm.available) names.add(config.swarm.route.keyEnv);
	return [...names].map((name) => env[name]).filter((value): value is string => !!value);
}

const PASSTHROUGH = ['PATH', 'HOME', 'TMPDIR', 'TEMP', 'TMP', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'SYSTEMROOT'];
// Non-secret settings of the backings the typed tools wrap (Tor posture for
// fetch, browse's Chromium, the RLM model), passed to every process unchanged.
// The RLM endpoint itself is passed only after readConfig confirmed it is own.
const BACKING_SETTINGS = [
	// The Anomaly install the anomaly_methods tool reads (a path, not a secret).
	'ANOMALY_HOME',
	'SPOTLIGHT_ANONYMIZE_FETCH',
	'TOR_SOCKS',
	'SPOTLIGHT_BROWSE_CHROMIUM',
	'PLAYWRIGHT_BROWSERS_PATH',
	'SPOTLIGHT_RLM_MODE',
	'SPOTLIGHT_RLM_OPENAI_MODEL',
	'SPOTLIGHT_RLM_MODEL',
];

// The complete environment of one pi process: system basics, the harness's own
// settings, the selected route's token under a fixed name, and only the keys
// the role declares. Everything else in the launcher's environment is dropped.
export function childEnv(
	config: HarnessConfig,
	composition: Composition,
	role: string,
	env: Env,
): Record<string, string> {
	const route = routeFor(config);
	const spec = agentSpec(composition, role);
	const out: Record<string, string> = {};
	for (const name of [...PASSTHROUGH, ...BACKING_SETTINGS]) if (env[name]) out[name] = env[name] as string;
	for (const key of spec.keys ?? []) if (env[key]) out[key] = env[key] as string;
	const token = route.keyEnv ? env[route.keyEnv] : undefined;
	if (token) out[PROVIDER_KEY] = token;
	else delete out[PROVIDER_KEY];
	const piDir = resolve(config.dataDir, 'pi', role);
	Object.assign(out, {
		PI_CODING_AGENT_DIR: piDir,
		PI_CODING_AGENT_SESSION_DIR: resolve(piDir, 'sessions'),
		PI_OFFLINE: '1',
		PI_SKIP_VERSION_CHECK: '1',
		PI_TELEMETRY: '0',
		SPOTLIGHT_ROLE: role,
		SPOTLIGHT_CASES_ROOT: config.casesRoot,
		SPOTLIGHT_CASE_DIR: config.caseDir,
		SPOTLIGHT_MODEL_BASE_URL: route.baseURL,
		SPOTLIGHT_MODEL_ID: route.model,
		SPOTLIGHT_ROUTE_INFERENCE: route.inference,
		SPOTLIGHT_MODEL_CONTEXT_WINDOW: String(config.contextWindow),
		SPOTLIGHT_MODEL_TIER: config.modelTier,
		SPOTLIGHT_PYTHON: config.python,
		SPOTLIGHT_CONFIG: config.spotlightConfig,
		SPOTLIGHT_SENSITIVE: config.sensitive ? 'true' : 'false',
	});
	if (config.searchURL) out.SPOTLIGHT_SEARCH_URL = config.searchURL;
	if (config.rlmURL) {
		out.SPOTLIGHT_RLM_OPENAI_BASE_URL = config.rlmURL;
		out.SPOTLIGHT_RLM_INFERENCE = 'own';
	}
	if (config.requestBody) out.SPOTLIGHT_PROVIDER_REQUEST_BODY = config.requestBody;
	return out;
}

// The launcher's arguments when a chat client such as Osaurus (a `pi_rpc`
// harness runtime) starts it, and the binding from that client's chat id to a
// case folder. Without arguments the launcher takes everything from the
// environment, as before.

export type LaunchArgs = { sessionId?: string; appendSystemPrompt?: string };

const VALUE_FLAGS = { '--mode': 'mode', '--session-id': 'sessionId', '--append-system-prompt': 'appendSystemPrompt' } as const;

// Anything outside this contract stops the launcher, so a runtime
// misconfigured in the chat client fails loudly instead of being ignored.
export function parseLaunchArgs(argv: string[]): LaunchArgs {
	const values: Partial<Record<'mode' | 'sessionId' | 'appendSystemPrompt', string>> = {};
	for (let index = 0; index < argv.length; index += 2) {
		const key = VALUE_FLAGS[argv[index] as keyof typeof VALUE_FLAGS];
		if (!key) throw new ConfigError(`unknown argument "${argv[index]}". Accepted: --mode rpc, --session-id <id>, --append-system-prompt <file>.`);
		const value = argv[index + 1];
		if (!value?.trim()) throw new ConfigError(`${argv[index]} needs a value.`);
		values[key] = value;
	}
	if (argv.length > 0 && values.mode !== 'rpc') throw new ConfigError('--mode rpc is required; it is the only mode.');
	if (values.appendSystemPrompt) {
		values.appendSystemPrompt = resolve(values.appendSystemPrompt);
		if (!existsSync(values.appendSystemPrompt)) throw new ConfigError('--append-system-prompt names a file that does not exist.');
	}
	return { sessionId: values.sessionId, appendSystemPrompt: values.appendSystemPrompt };
}

const MAP_FILE = 'chats.json';

// Keyed by the SHA-256 of the chat id, so the file holds no raw chat ids and
// no id can name an Object.prototype member.
function chatKey(chatId: string): string {
	return createHash('sha256').update(chatId).digest('hex');
}

function readChatMap(dataDir: string): Record<string, string> {
	const path = resolve(dataDir, MAP_FILE);
	if (!existsSync(path)) return {};
	try {
		return Object.assign(Object.create(null), JSON.parse(readFileSync(path, 'utf8'))) as Record<string, string>;
	} catch {
		throw new ConfigError(`${path} is not valid JSON; fix or remove it.`);
	}
}

// A known chat resumes its case. A new chat gets a folder named from today's
// date and a short hash of its id (never the id itself), unique among the
// folders on disk and the cases other chats hold.
export function caseForChat(casesRoot: string, dataDir: string, chatId: string): string {
	const chats = readChatMap(dataDir);
	const key = chatKey(chatId);
	if (Object.hasOwn(chats, key)) return chats[key];
	const taken = new Set(Object.values(chats));
	const base = `${new Date().toISOString().slice(0, 10)}-${key.slice(0, 8)}`;
	let caseId = base;
	for (let suffix = 2; taken.has(caseId) || existsSync(resolve(casesRoot, caseId)); suffix += 1) caseId = `${base}-${suffix}`;
	return caseId;
}

const CHAT_MAP_LOCK = '.chats'; // not a valid case id, so never a case's lock
const CHAT_MAP_LOCK_WAIT_MS = 5_000;

// Two new chats may start at once: the read-modify-write runs under the case
// lock's mechanism (exclusive create, stale takeover), held only for the
// write, and re-reads the map under it. The rename makes the write atomic.
export function recordChatCase(dataDir: string, chatId: string, caseId: string): void {
	const deadline = Date.now() + CHAT_MAP_LOCK_WAIT_MS;
	let lock: CaseLock | undefined;
	while (!lock) {
		try {
			lock = acquireCaseLock(dataDir, CHAT_MAP_LOCK, piCliPath());
		} catch (error) {
			if (!(error instanceof CaseHeldError)) throw error;
			if (Date.now() > deadline) throw new ConfigError('the chat map stayed locked by another Spotlight chat; try again.');
			Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 25);
		}
	}
	try {
		const chats = readChatMap(dataDir);
		const key = chatKey(chatId);
		if (chats[key] === caseId) return;
		chats[key] = caseId;
		const target = resolve(dataDir, MAP_FILE);
		const temporary = `${target}.${process.pid}.tmp`;
		writeFileSync(temporary, `${JSON.stringify(chats, null, 2)}\n`, { mode: 0o600 });
		renameSync(temporary, target);
	} finally {
		lock.release();
	}
}
