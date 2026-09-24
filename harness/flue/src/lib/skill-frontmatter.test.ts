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

test('checkout-authored manifest skills have Flue-loadable frontmatter', () => {
	const skillIds = readFileSync(new URL('skills.manifest', ROOT), 'utf8')
		.split(/\r?\n/)
		.map((line) => line.trim())
		.filter(Boolean);
	const manifest = JSON.parse(readFileSync(new URL('skills-manifest.json', ROOT), 'utf8')) as {
		skills: { id: string; source?: { kind: string } }[];
	};
	const packageSkills = new Set(manifest.skills.filter((skill) => skill.source?.kind === 'package').map((skill) => skill.id));
	const errors: string[] = [];

	for (const skillId of skillIds) {
		// Package bytes are verified against the signed catalog by Engine;
		// the same-named checkout text is not their authoritative source.
		if (packageSkills.has(skillId)) continue;
		const canonicalPath = new URL(`skills/${skillId}/SKILL.md`, ROOT);
		const canonical = readFileSync(canonicalPath);
		validateFrontmatter(canonical.toString('utf8'), `canonical ${skillId}`, errors);
	}

	assert.equal(errors.length, 0, errors.join('\n'));
});
