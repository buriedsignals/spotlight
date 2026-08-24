import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { FAILSAFE_SCHEMA, load } from 'js-yaml';

const ROOT = new URL('../../../../', import.meta.url);
const FRONTMATTER = /^---\s*\r?\n([\s\S]*?)\r?\n---\s*(?:\r?\n|$)/;

function validateFrontmatter(markdown: string, label: string, errors: string[]): void {
	const match = markdown.replace(/^\uFEFF/, '').match(FRONTMATTER);
	if (!match) {
		errors.push(`${label}: missing YAML frontmatter`);
		return;
	}

	try {
		const metadata = load(match[1] ?? '', { schema: FAILSAFE_SCHEMA });
		if (typeof metadata !== 'object' || metadata === null || Array.isArray(metadata)) {
			errors.push(`${label}: frontmatter must be a YAML mapping`);
			return;
		}
		for (const field of ['name', 'description'] as const) {
			const value = (metadata as Record<string, unknown>)[field];
			if (typeof value !== 'string' || value.trim().length === 0) {
				errors.push(`${label}: frontmatter ${field} must be a non-empty scalar string`);
			}
		}
	} catch (error) {
		const detail = error instanceof Error ? error.message : String(error);
		errors.push(`${label}: invalid YAML frontmatter: ${detail}`);
	}
}

test('every manifest skill has Flue-loadable frontmatter and an identical generated mirror', () => {
	const skillIds = readFileSync(new URL('skills.manifest', ROOT), 'utf8')
		.split(/\r?\n/)
		.map((line) => line.trim())
		.filter(Boolean);
	const errors: string[] = [];

	for (const skillId of skillIds) {
		const canonicalPath = new URL(`skills/${skillId}/SKILL.md`, ROOT);
		const mirrorPath = new URL(`plugins/spotlight/skills/${skillId}/SKILL.md`, ROOT);
		const canonical = readFileSync(canonicalPath);
		const mirror = readFileSync(mirrorPath);

		if (!canonical.equals(mirror)) {
			errors.push(`${skillId}: generated mirror differs from canonical skill`);
		}
		validateFrontmatter(canonical.toString('utf8'), `canonical ${skillId}`, errors);
		validateFrontmatter(mirror.toString('utf8'), `generated ${skillId}`, errors);
	}

	assert.equal(errors.length, 0, errors.join('\n'));
});
