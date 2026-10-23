import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { redactingLines, redactor } from './log.ts';

test('a child\'s stderr is redacted even when a key arrives split across chunks', async () => {
	const source = new PassThrough();
	const redacted = source.pipe(redactingLines(redactor(['sk-live-0123456789'])));
	let output = '';
	redacted.on('data', (chunk) => {
		output += chunk;
	});
	const ended = new Promise((done) => redacted.on('end', done));

	source.write('provider said: bad key sk-live-01');
	source.write('23456789 rejected\nnext line sk-live-0123456789');
	source.end();
	await ended;

	assert.equal(output, 'provider said: bad key [redacted] rejected\nnext line [redacted]');
});
