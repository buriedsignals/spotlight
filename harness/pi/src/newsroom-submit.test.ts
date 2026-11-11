// newsroom-submit.ts, the Submit step for a runtime without the Pi launcher,
// run as a subscription host's agent would run it. No network.
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { readFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { startFakeEditorial } from './test-support/fake-editorial.ts';
import { caseAtIngest, newsroomEnv, requestNewsroomIngest, workspace } from './test-support/launch.ts';
import { CHECKOUT_ROOT } from './roles.ts';

function submit(caseDir: string, env: Record<string, string>): Promise<{ code: number; stdout: string; stderr: string }> {
	return new Promise((done) =>
		execFile(process.execPath, [join(CHECKOUT_ROOT, 'harness', 'pi', 'src', 'newsroom-submit.ts'), caseDir], { env: { ...process.env, ...env } }, (error, stdout, stderr) =>
			done({ code: error ? Number((error as { code?: number }).code ?? 1) : 0, stdout, stderr })),
	);
}

test('without the launcher, one command registers the case, sends the approved batch and records the receipts; a revoked sign-in stops it', async (t) => {
	const space = await workspace(t);
	const newsroom = await startFakeEditorial();
	t.after(() => newsroom.close());
	const env = newsroomEnv(space, [], newsroom);
	const caseDir = await caseAtIngest(space, ['--proposal']);
	await requestNewsroomIngest(caseDir, env);

	const sent = await submit(caseDir, env);

	assert.equal(sent.code, 0, sent.stderr);
	const outcome = JSON.parse(sent.stdout) as { state: string; batch_id: string; notice?: string };
	assert.equal(outcome.state, 'submitted');
	// The fake's review address is off the newsroom's endpoint: not opened, and not in the output.
	assert.match(outcome.notice ?? '', /outside the newsroom's allowed endpoints/);
	assert.ok(!sent.stdout.includes('editorial.newsroom.example') && !(sent.stdout + sent.stderr).includes(newsroom.token));
	assert.deepEqual(newsroom.calls.map((call) => call.tool), ['create_id', 'register_case', 'create_id', 'start_case_review', 'get_knowledge_batch_info']);
	assert.deepEqual(newsroom.staged, [outcome.batch_id]);
	assert.deepEqual((await readdir(join(caseDir, 'receipts'))).sort(), [`review-status-${outcome.batch_id}.json`, `submission-${outcome.batch_id}.json`]);
	assert.equal(JSON.parse(await readFile(join(caseDir, 'data', 'ingestion.json'), 'utf8')).status, 'completed');

	newsroom.revoke();
	const refused = await submit(caseDir, env);
	assert.equal(refused.code, 4);
	assert.match(refused.stderr, /newsroom sign-in is no longer valid/);
});
