// extract_document against a stand-in for the operator's NuExtract3 endpoint.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, realpath, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { extractDocumentTool } from './extract.ts';

const NOTE_TEXT = 'Payment note\nPayee: Fixture GmbH\nAmount: CHF 290.00\nDate: 01.07.2020\n';
const TEMPLATE = { payee: 'string', amount: 'number', date: 'date-time' };

// The model's answers in turn: the filled template, then a run that hit the cap.
test('extract_document sends the template in the message and keeps the result tied to its source; a capped run keeps nothing', async (t) => {
	const bodies: Array<Record<string, any>> = [];
	const answers = [
		{ message: { content: '{"payee": "Fixture GmbH", "amount": 290, "date": "2020-07-01"}' }, finish_reason: 'stop' },
		{ message: { content: '{"payee": "Fixture GmbH", "items": ["Troy", "Troy", "Troy"' }, finish_reason: 'length' },
	];
	const server = createServer((request, response) => {
		let raw = '';
		request.on('data', (chunk) => (raw += chunk));
		request.on('end', () => {
			bodies.push(JSON.parse(raw));
			response.setHeader('content-type', 'application/json');
			response.end(JSON.stringify({ choices: [answers[bodies.length - 1]] }));
		});
	});
	await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
	t.after(() => server.close());
	const caseDir = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-extract-')));
	t.after(() => rm(caseDir, { recursive: true, force: true }));
	await mkdir(join(caseDir, 'evidence'));
	await writeFile(join(caseDir, 'evidence/note.txt'), NOTE_TEXT);
	const tool = extractDocumentTool({
		caseDir,
		writeRoot: caseDir,
		python: 'python3',
		configPath: '/',
		env: {
			SPOTLIGHT_EXTRACT_OPENAI_BASE_URL: `http://127.0.0.1:${(server.address() as AddressInfo).port}/v1`,
			SPOTLIGHT_EXTRACT_INFERENCE: 'own',
		},
	});
	const call = async () =>
		JSON.parse(((await tool.execute('x', { path: 'evidence/note.txt', template: TEMPLATE }, undefined, undefined, undefined as never)).content[0] as { text: string }).text);

	const filled = await call();
	assert.equal(bodies[0].model, 'nuextract3-mlx-nvfp4');
	assert.equal(bodies[0].enable_thinking, false);
	assert.equal(bodies[0].messages[0].content, `【template_start】${JSON.stringify(TEMPLATE)}【template_end】\n${NOTE_TEXT}`);
	assert.deepEqual(filled.source, { path: 'evidence/note.txt', sha256: createHash('sha256').update(NOTE_TEXT).digest('hex') });
	assert.deepEqual(filled.extracted, { payee: 'Fixture GmbH', amount: 290, date: '2020-07-01' });
	assert.match(filled.note, /Never cite this output as evidence/);

	const capped = await call();
	assert.equal(capped.extracted, null);
	assert.match(capped.stopped, /token cap/);
});
