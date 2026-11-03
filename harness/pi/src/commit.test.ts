// step3c (reports/step3c-commit-design.md): which lines the investigator was
// shown, when the commit step is due, and how a cite is clipped. The
// end-to-end step is in launcher.test.ts ("commit step").
import assert from 'node:assert/strict';
import test from 'node:test';
import { acceptCommit, commitDue, commitState } from './commit.ts';

const call = (id: string, name: string, args: Record<string, unknown> = {}) => ({ role: 'assistant', content: [{ type: 'toolCall', id, name, arguments: args }] });
const result = (id: string, text: string, isError = false) => ({ role: 'toolResult', toolCallId: id, content: [{ type: 'text', text }], isError });

test('shown lines: grep hits under research/ or evidence/ under 400 characters, and whole lines of a ranged read before any cut', () => {
	const state = commitState([
		call('1', 'grep_files', { pattern: 'paid' }), result('1', `research/a.md:5:Acme paid Doe.\nresearch/a.md:9:${'x'.repeat(400)}\ndata/findings.json:2:paid`),
		call('2', 'read_file', { path: 'evidence/b.txt', offset: 10, limit: 3 }), result('2', 'line ten\nline eleven\nline twe\n[truncated: use offset and limit to read the rest]'),
		call('3', 'read_file', { path: 'research/c.md' }), result('3', 'whole file, no line numbers'),
	]);
	assert.deepEqual([...state.shown].map(([path, lines]) => [path, [...lines.keys()]]), [['research/a.md', [5]], ['evidence/b.txt', [10, 11]]]);
	// A successful record_finding covering a line makes it recorded; a refused one does not.
	const recorded = commitState([call('1', 'grep_files'), result('1', 'research/a.md:5:Acme paid Doe.'), call('2', 'record_finding', { cite: [{ path: 'research/a.md', line_start: 5, line_end: 5 }] }), result('2', 'refused', true)]);
	assert.equal(recorded.uncommitted.length, 1);
	assert.equal(commitState([call('1', 'grep_files'), result('1', 'research/a.md:5:x'), call('2', 'record_finding', { cite: [{ path: 'research/a.md', line_start: 5, line_end: 5 }] }), result('2', 'wrote')]).uncommitted.length, 0);
});

test('the commit step waits two tool turns after the first unrecorded line and four between attempts', () => {
	const grep = [call('1', 'grep_files'), result('1', 'research/a.md:5:Acme paid Doe.')];
	const turn = (n: number) => [call(`t${n}`, 'list_files'), result(`t${n}`, 'no matching files')];
	assert.equal(commitDue(commitState([...grep, ...turn(2)])), false);
	assert.equal(commitDue(commitState([...grep, ...turn(2), ...turn(3)])), true);
	assert.equal(commitDue(commitState([...grep, ...turn(2), ...turn(3)]), 3), false);
	assert.equal(commitDue(commitState([...grep, ...turn(2), ...turn(3)]), 4), true);
	assert.equal(commitDue(commitState([...turn(1), ...turn(2), ...turn(3)])), false, 'no shown line, no commit step');
});

test('a commit answer is clipped to the lines the model was shown; no surviving cite or nothing_found is not used', () => {
	const shown = commitState([call('1', 'grep_files'), result('1', 'research/a.md:140:Heading\nresearch/a.md:164:Acme paid Doe.')]).shown;
	assert.deepEqual(acceptCommit('record_finding', { finding: { claim: 'Acme paid Doe.', confidence: 'medium' }, cite: [{ path: 'research/a.md', line_start: 140, line_end: 164 }] }, shown),
		{ args: { finding: { claim: 'Acme paid Doe.', confidence: 'medium' }, cite: [{ path: 'research/a.md', line_start: 140, line_end: 140 }, { path: 'research/a.md', line_start: 164, line_end: 164 }] } });
	assert.ok('reason' in acceptCommit('record_finding', { finding: { claim: 'X.', confidence: 'low' }, cite: [{ path: 'research/a.md', line_start: 1, line_end: 100 }] }, shown));
	assert.ok('reason' in acceptCommit('nothing_found', { reason: 'none' }, shown));
	assert.ok('reason' in acceptCommit('record_finding', { finding: { claim: '', confidence: 'low' }, cite: [{ path: 'research/a.md', line_start: 164, line_end: 164 }] }, shown));
});
