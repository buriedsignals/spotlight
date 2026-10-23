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
		SPOTLIGHT_MODEL_BASE_URL: 'https://models.example/v1',
		SPOTLIGHT_MODEL_ID: 'm',
		SPOTLIGHT_PROVIDER_API_KEY: 'sk-route-token',
		REGISTRY_API_KEY: 'reg-secret',
		UNRELATED_SECRET: 'nope',
	};
	const config = readConfig(env);

	const investigator = childEnv(config, COMPOSITION, 'investigator', 'general', env);
	const orchestrator = childEnv(config, COMPOSITION, 'orchestrator', 'general', env);

	assert.equal(investigator.REGISTRY_API_KEY, 'reg-secret');
	assert.equal(orchestrator.REGISTRY_API_KEY, undefined);
	for (const child of [investigator, orchestrator]) {
		assert.equal(child.SPOTLIGHT_PROVIDER_API_KEY, 'sk-route-token');
		assert.equal(child.UNRELATED_SECRET, undefined);
		assert.equal(child.PI_CODING_AGENT_DIR.startsWith('/data/'), true);
	}
});

test('newsroom routes: restricted material goes only to an own-inference route', () => {
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
	assert.equal(routeFor(mixed, 'general').name, 'r0');
	assert.equal(routeFor(mixed, 'restricted').name, 'r1');
	assert.equal(childEnv(mixed, COMPOSITION, 'investigator', 'restricted', env(['third-party', 'own'])).SPOTLIGHT_PROVIDER_API_KEY, 'tok-1');

	const thirdPartyOnly = readConfig(env(['third-party']));
	assert.throws(() => routeFor(thirdPartyOnly, 'restricted'), /route you run yourself/);
	assert.throws(
		() => childEnv(thirdPartyOnly, COMPOSITION, 'investigator', 'restricted', env(['third-party'])),
		/route you run yourself/,
	);
});
