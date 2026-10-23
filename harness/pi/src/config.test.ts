import assert from 'node:assert/strict';
import test from 'node:test';
import { childEnv, readConfig, routeFor } from './config.ts';
import type { Composition } from './roles.ts';

const COMPOSITION: Composition = {
	orchestrator: { role: 'skills/spotlight/SKILL.md', skills: [], tools: [] },
	investigator: { role: 'agents/investigator.md', skills: [], tools: [], keys: ['REGISTRY_API_KEY'] },
};

const BASE = {
	SPOTLIGHT_CASES_ROOT: '/cases',
	SPOTLIGHT_CASE_ID: 'case-a',
	SPOTLIGHT_HARNESS_DATA: '/data',
	PATH: '/usr/bin',
};

test('each process gets only its role\'s declared keys and its route token, nothing else', () => {
	const env = {
		...BASE,
		SPOTLIGHT_MODEL_BASE_URL: 'http://127.0.0.1:8080/v1',
		SPOTLIGHT_MODEL_ID: 'm',
		SPOTLIGHT_PROVIDER_API_KEY: 'sk-route-token',
		REGISTRY_API_KEY: 'reg-secret',
		UNRELATED_SECRET: 'nope',
	};
	const config = readConfig(env);

	const investigator = childEnv(config, COMPOSITION, 'investigator', env);
	const orchestrator = childEnv(config, COMPOSITION, 'orchestrator', env);

	assert.equal(investigator.REGISTRY_API_KEY, 'reg-secret');
	assert.equal(orchestrator.REGISTRY_API_KEY, undefined);
	for (const child of [investigator, orchestrator]) {
		assert.equal(child.SPOTLIGHT_PROVIDER_API_KEY, 'sk-route-token');
		assert.equal(child.UNRELATED_SECRET, undefined);
		assert.equal(child.PI_CODING_AGENT_DIR.startsWith('/data/'), true);
	}
});

test('newsroom routes: every process goes only to an own-inference route', () => {
	const routes = (inference: string[]) =>
		JSON.stringify(
			inference.map((kind, index) => ({
				name: `r${index}`,
				base_url: `https://r${index}.newsroom.example/v1`,
				model: 'm',
				inference: kind,
				key_env: `ROUTE_${index}_TOKEN`,
			})),
		);
	const env = (inference: string[]) => ({
		...BASE,
		SPOTLIGHT_EDITION: 'newsroom',
		SPOTLIGHT_ROUTES: routes(inference),
		ROUTE_0_TOKEN: 'tok-0',
		ROUTE_1_TOKEN: 'tok-1',
	});

	const mixed = readConfig(env(['third-party', 'own']));
	assert.equal(routeFor(mixed).name, 'r1');
	assert.equal(childEnv(mixed, COMPOSITION, 'investigator', env(['third-party', 'own'])).SPOTLIGHT_PROVIDER_API_KEY, 'tok-1');

	const thirdPartyOnly = readConfig(env(['third-party']));
	assert.throws(() => routeFor(thirdPartyOnly), /route you run yourself/);
	assert.throws(
		() => childEnv(thirdPartyOnly, COMPOSITION, 'investigator', env(['third-party'])),
		/route you run yourself/,
	);
});

test('solo edition: only a real loopback endpoint counts as a route the operator runs', () => {
	const inference = (baseURL: string) =>
		readConfig({ ...BASE, SPOTLIGHT_MODEL_BASE_URL: baseURL, SPOTLIGHT_MODEL_ID: 'm' }).pipelineRoute.inference;
	const cases: Array<[string, string]> = [
		['http://127.0.0.1:8080/v1', 'own'],
		['http://127.8.9.10/v1', 'own'],
		['http://localhost:8080/v1', 'own'],
		['http://[::1]:8080/v1', 'own'],
		['https://models.example/v1', 'third-party'],
		['http://127.evil.example/v1', 'third-party'],
		['http://127.0.0.1.evil.example/v1', 'third-party'],
		['http://localhost.evil.example/v1', 'third-party'],
		['http://10.0.0.5/v1', 'third-party'],
		['http://[::ffff:7f00:1]/v1', 'third-party'],
	];
	for (const [baseURL, expected] of cases) assert.equal(inference(baseURL), expected, baseURL);
});

test('the RLM endpoint that distills fetched pages must be one the operator runs, or the launcher stops', () => {
	const solo = (rlm: string) => ({ ...BASE, SPOTLIGHT_MODEL_BASE_URL: 'http://127.0.0.1:8080/v1', SPOTLIGHT_MODEL_ID: 'm', SPOTLIGHT_RLM_OPENAI_BASE_URL: rlm });
	const local = solo('http://127.0.0.1:8095/v1');
	const child = childEnv(readConfig(local), COMPOSITION, 'investigator', local);
	assert.equal(child.SPOTLIGHT_RLM_OPENAI_BASE_URL, 'http://127.0.0.1:8095/v1');
	assert.equal(child.SPOTLIGHT_RLM_INFERENCE, 'own');
	assert.throws(() => readConfig(solo('https://rlm.example/v1')), /SPOTLIGHT_RLM_OPENAI_BASE_URL .*run yourself/);

	const newsroom = (rlm: string) => ({
		...BASE,
		SPOTLIGHT_EDITION: 'newsroom',
		SPOTLIGHT_ROUTES: JSON.stringify([
			{ name: 'model', base_url: 'https://model.newsroom.example/v1', model: 'm', inference: 'own' },
			{ name: 'rlm', base_url: 'https://rlm.newsroom.example/v1', model: 'e4b', inference: 'own' },
			{ name: 'cloud', base_url: 'https://cloud.example/v1', model: 'c', inference: 'third-party' },
		]),
		SPOTLIGHT_RLM_OPENAI_BASE_URL: rlm,
	});
	const own = newsroom('https://rlm.newsroom.example/v1');
	assert.equal(childEnv(readConfig(own), COMPOSITION, 'investigator', own).SPOTLIGHT_RLM_INFERENCE, 'own');
	assert.throws(() => readConfig(newsroom('https://cloud.example/v1')), /SPOTLIGHT_RLM_OPENAI_BASE_URL .*run yourself/);
	assert.throws(() => readConfig(newsroom('http://127.0.0.1:8095/v1')), /SPOTLIGHT_RLM_OPENAI_BASE_URL .*run yourself/);
});
