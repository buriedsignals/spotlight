// The fetch receipt a fact-checker's fetch leaves in the harness registry
// (tools/fetch.ts recordFetched), for a fixture's own fact-checker capture.
// Only a file the fixture states the fact-checker fetched: a model-written
// created_by never makes one (Codex review of fix11).
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

// The shared activated fixture's fact-checker re-fetch (tests/validate-case-check.py activated_documents).
export const FIXTURE_CAPTURE = 'research/fc-filing.md.raw';

export function registerCapture(dataDir: string, caseDir: string, relative: string, url = 'https://example.org/x'): void {
	const file = join(dataDir, 'work', 'fetched', `${createHash('sha256').update(realpathSync(caseDir)).digest('hex').slice(0, 16)}.json`);
	let all: Record<string, unknown> = {};
	try {
		all = JSON.parse(readFileSync(file, 'utf8'));
	} catch {
		all = {};
	}
	all[relative] = { url, accessed: '2026-07-16T12:30:00Z', provider: 'crawl4ai', sha256: createHash('sha256').update(readFileSync(join(caseDir, relative))).digest('hex'), acquired_by: 'fact-checker' };
	mkdirSync(dirname(file), { recursive: true });
	writeFileSync(file, JSON.stringify(all));
}

// The fixture's capture, when the case has it.
export function registerFixtureCapture(dataDir: string, caseDir: string): void {
	if (existsSync(join(caseDir, FIXTURE_CAPTURE))) registerCapture(dataDir, caseDir, FIXTURE_CAPTURE);
}
