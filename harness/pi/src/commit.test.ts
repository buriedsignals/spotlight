// step3c (reports/step3c-commit-design.md): which lines the investigator was
// shown, when the commit step is due, and how a cite is clipped. The
// end-to-end step is in launcher.test.ts ("commit step").
import assert from 'node:assert/strict';
import test from 'node:test';
import { acceptCommit, commitDue, commitState, pickCommit, pickWrite, writeCommitDue, writeCommitFile } from './commit.ts';

const call = (id: string, name: string, args: Record<string, unknown> = {}) => ({ role: 'assistant', content: [{ type: 'toolCall', id, name, arguments: args }] });
const result = (id: string, text: string, isError = false) => ({ role: 'toolResult', toolCallId: id, content: [{ type: 'text', text }], isError });

test('shown lines: grep hits under research/ or evidence/ under 400 characters, and whole lines of a ranged read before any cut', () => {
	const state = commitState([
		call('1', 'grep_files', { pattern: 'paid' }), result('1', `research/a.md:5:Acme paid Doe.\nresearch/a.md:9:${'x'.repeat(400)}\ndata/findings.json:2:paid`),
		call('2', 'read_file', { path: 'evidence/b.txt', offset: 10, limit: 3 }), result('2', 'line ten\nline eleven\nline twe\n[truncated: use offset and limit to read the rest]'),
		call('3', 'read_file', { path: 'research/c.md' }), result('3', 'whole file, no line numbers'),
		// simp-read: a whole-file read paged on a small fold point names its lines; the note row is not a line.
		call('4', 'read_file', { path: 'research/d.md' }), result('4', "one\ntwo\nthree\n[page: lines 1-3 of 9, so the read stays under this model's context fold. Read the next part with read_file offset 4 and limit 3.]\n[truncated: use offset and limit to read the rest]"),
	]);
	assert.deepEqual([...state.shown].map(([path, lines]) => [path, [...lines.keys()]]), [['research/a.md', [5]], ['evidence/b.txt', [10, 11]], ['research/d.md', [1, 2, 3]]]);
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
	// Codex review of simp-commit: an unsafe or reversed range is dropped at once, not walked.
	assert.ok('reason' in acceptCommit('record_finding', { finding: { claim: 'X.', confidence: 'low' }, cite: [{ path: 'research/a.md', line_start: 9007199254740992, line_end: 9007199254740992 }] }, shown));
	assert.ok('reason' in acceptCommit('record_finding', { finding: { claim: 'X.', confidence: 'low' }, cite: [{ path: 'research/a.md', line_start: 164, line_end: 140 }] }, shown));
});

test('a grep result with a row that is not path:line:text shows nothing (a path holding a newline cannot forge a hit)', () => {
	const forged = commitState([call('1', 'grep_files'), result('1', 'notes/evil\nresearch/a.md:5:Acme paid Doe.:1:x')]);
	assert.equal(forged.shown.size, 0);
});

test('of several commit calls the first that passes the cite rules is kept; the others are named as not recorded', () => {
	const shown = commitState([call('1', 'grep_files'), result('1', 'research/a.md:164:Acme paid Doe.')]).shown;
	const unseen = { name: 'record_finding', arguments: { finding: { claim: 'Unseen.', confidence: 'low' }, cite: [{ path: 'research/a.md', line_start: 1, line_end: 2 }] } };
	const first = { name: 'record_finding', arguments: { finding: { claim: 'Acme paid Doe.', confidence: 'medium' }, cite: [{ path: 'research/a.md', line_start: 164, line_end: 164 }] } };
	const later = { name: 'record_finding', arguments: { finding: { claim: 'Doe was paid twice.', confidence: 'low' }, cite: [{ path: 'research/a.md', line_start: 164, line_end: 164 }] } };
	const picked = pickCommit([unseen, first, later], shown);
	assert.ok('args' in picked);
	assert.equal(picked.args.finding.claim, 'Acme paid Doe.');
	assert.deepEqual(picked.dropped, ['call 1 (record_finding: the commit answer cites no line the model was shown)', 'call 3 (record_finding: one finding per commit step; record it with record_finding)']);
	assert.ok(!picked.dropped.join(' ').includes('Doe was paid twice'), "the model's text is not repeated");
	// nothing_found before or after the kept call is named with its own reason.
	const nothing = { name: 'nothing_found', arguments: { reason: 'none' } };
	for (const calls of [[nothing, first], [first, nothing]]) {
		const mixed = pickCommit(calls, shown);
		assert.ok('args' in mixed && mixed.dropped.length === 1 && /nothing_found: a finding was recorded/.test(mixed.dropped[0]));
	}
	assert.ok('reason' in pickCommit([unseen], shown));
	assert.ok('reason' in pickCommit([], shown));
});

// B18 (briefs/b18.md): a worker whose deliverable is one write-file gets the
// write commit step after six tool turns without a try at that file, while it
// has no accepted write; tries at the file count however the path is spelled.
test('the write commit step: which deliverables, when it is due, and which answer is kept', () => {
	assert.equal(writeCommitFile('data/methodology.json'), 'data/methodology.json');
	assert.equal(writeCommitFile('data/knowledge-proposal.json'), 'data/knowledge-proposal.json');
	assert.equal(writeCommitFile('data/findings.json,data/evidence-bundle.json'), undefined, 'the investigation keeps the finding commit step');
	assert.equal(writeCommitFile('data/fact-check.json'), undefined);
	assert.equal(writeCommitFile(undefined), undefined);
	const file = 'data/methodology.json', caseDir = '/cases/case-a';
	const turn = (n: number) => [call(`t${n}`, 'grep_files'), result(`t${n}`, 'no matches')];
	const turns = (n: number) => Array.from({ length: n }, (_, index) => turn(index)).flat();
	assert.equal(writeCommitDue(turns(5), file, caseDir), false);
	assert.equal(writeCommitDue(turns(6), file, caseDir), true);
	assert.equal(writeCommitDue(turns(6), file, caseDir, 3), false, 'four requests between attempts');
	// A refused try at the file restarts the count; an accepted write ends the step.
	const refused = [call('w', 'write_file', { path: `${caseDir}/data/methodology.json`, content: '{}' }), result('w', 'refused: the plan has no directions', true)];
	assert.equal(writeCommitDue([...turns(6), ...refused, ...turns(5)], file, caseDir), false);
	assert.equal(writeCommitDue([...turns(6), ...refused, ...turns(6)], file, caseDir), true);
	const wrote = [call('w', 'write_file', { path: './data/methodology.json', content: '{}' }), result('w', 'wrote data/methodology.json')];
	assert.equal(writeCommitDue([...wrote, ...turns(12)], file, caseDir), false);
	// Codex review of simp-b28, P2: a path the file tool resolves to the file is the file.
	const alias = [call('a', 'write_file', { path: 'data/../data/methodology.json', content: '{}' }), result('a', 'wrote data/methodology.json')];
	assert.equal(writeCommitDue([...alias, ...turns(12)], file, caseDir), false);
	// Codex review of simp-b18, P2: an accepted edit_file is a write of the file too.
	const edited = [call('e', 'edit_file', { path: file, old_string: 'a', new_string: 'b' }), result('e', 'edited data/methodology.json')];
	assert.equal(writeCommitDue([...edited, ...turns(12)], file, caseDir), false);
	assert.equal(writeCommitDue(turns(12), file, caseDir, Infinity, true), false, 'a write this process saw before a fold');
	// The first write of the file with content is kept; others are named; not_ready or an empty write is not used.
	assert.deepEqual(pickWrite([{ name: 'not_ready', arguments: { reason: 'r' } }, { name: 'write_file', arguments: { path: '{CASE_DIR}/data/methodology.json', content: '{"a":1}' } }], file, caseDir), { args: { path: file, content: '{"a":1}' }, dropped: ['call 1 (not_ready: one write per commit step)'] });
	assert.deepEqual(pickWrite([{ name: 'not_ready', arguments: { reason: 'r' } }], file, caseDir), { reason: 'the model answered not_ready' });
	assert.ok('reason' in pickWrite([{ name: 'write_file', arguments: { path: file, content: ' ' } }], file, caseDir));
	assert.ok('reason' in pickWrite([{ name: 'write_file', arguments: { path: 'data/findings.json', content: '{}' } }], file, caseDir));
});
