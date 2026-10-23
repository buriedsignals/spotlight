// Launch configuration. The harness reads its configuration only from the
// environment the caller sets; nothing comes from the member's own Pi config.
import { isAbsolute, resolve } from 'node:path';
import { parseFixedRequestBody } from './provider-policy.ts';
import { agentSpec } from './roles.ts';
import type { Composition } from './roles.ts';

export type Edition = 'solo' | 'newsroom';
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

// What a process is allowed to send to its model. Restricted material
// (journalist input, local-corpus content) may reach only an own-inference route.
export type Material = 'general' | 'restricted';

export type HarnessConfig = {
	edition: Edition;
	casesRoot: string;
	caseId: string;
	caseDir: string;
	dataDir: string;
	routes: Route[];
	pipelineRoute: Route;
	helperURL?: string;
	searchURL?: string;
	requestBody?: string;
	contextWindow: number;
	python: string;
};

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

function isLoopback(baseURL: string): boolean {
	const host = new URL(baseURL).hostname;
	return host === 'localhost' || host === '[::1]' || /^127\./.test(host);
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

export function readConfig(env: Env): HarnessConfig {
	const editionValue = env.SPOTLIGHT_EDITION?.trim() || 'solo';
	if (editionValue !== 'solo' && editionValue !== 'newsroom') {
		throw new ConfigError('SPOTLIGHT_EDITION must be "solo" or "newsroom".');
	}
	const edition: Edition = editionValue;
	const casesRoot = required(env, 'SPOTLIGHT_CASES_ROOT');
	const caseId = required(env, 'SPOTLIGHT_CASE_ID');
	const dataDir = required(env, 'SPOTLIGHT_HARNESS_DATA');
	if (!isAbsolute(casesRoot)) throw new ConfigError('SPOTLIGHT_CASES_ROOT must be an absolute path.');
	if (!isAbsolute(dataDir)) throw new ConfigError('SPOTLIGHT_HARNESS_DATA must be an absolute path.');
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
	return {
		edition,
		casesRoot: resolve(casesRoot),
		caseId,
		caseDir: resolve(casesRoot, caseId),
		dataDir: resolve(dataDir),
		routes,
		pipelineRoute,
		helperURL: optionalURL(env, 'SPOTLIGHT_HELPER_URL'),
		searchURL: optionalURL(env, 'SPOTLIGHT_SEARCH_URL'),
		requestBody: env.SPOTLIGHT_PROVIDER_REQUEST_BODY || undefined,
		contextWindow,
		python: env.SPOTLIGHT_PYTHON?.trim() || 'python3',
	};
}

export class RouteRefused extends Error {}

// No silent fallback: restricted material goes to an own-inference route or
// nowhere.
export function routeFor(config: HarnessConfig, material: Material): Route {
	if (material === 'general') return config.pipelineRoute;
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
	return [...names].map((name) => env[name]).filter((value): value is string => !!value);
}

const PASSTHROUGH = ['PATH', 'HOME', 'TMPDIR', 'TEMP', 'TMP', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'SYSTEMROOT'];

// The complete environment of one pi process: system basics, the harness's own
// settings, the selected route's token under a fixed name, and only the keys
// the role declares. Everything else in the launcher's environment is dropped.
export function childEnv(
	config: HarnessConfig,
	composition: Composition,
	role: string,
	material: Material,
	env: Env,
): Record<string, string> {
	const route = routeFor(config, material);
	const spec = agentSpec(composition, role);
	const out: Record<string, string> = {};
	for (const name of PASSTHROUGH) if (env[name]) out[name] = env[name] as string;
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
		SPOTLIGHT_MATERIAL: material,
		SPOTLIGHT_CASES_ROOT: config.casesRoot,
		SPOTLIGHT_CASE_DIR: config.caseDir,
		SPOTLIGHT_MODEL_BASE_URL: route.baseURL,
		SPOTLIGHT_MODEL_ID: route.model,
		SPOTLIGHT_ROUTE_INFERENCE: route.inference,
		SPOTLIGHT_MODEL_CONTEXT_WINDOW: String(config.contextWindow),
		SPOTLIGHT_PYTHON: config.python,
	});
	if (config.requestBody) out.SPOTLIGHT_PROVIDER_REQUEST_BODY = config.requestBody;
	return out;
}
