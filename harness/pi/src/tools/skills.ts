// invoke-skill for a harness without a file-reading tool: returns the body of
// one of the skills this process was started with, and nothing else. The
// investigator and the fact-checker may also name the Navigator-hosted
// Indicator Framework (hosted-skill.ts), which never lives in the checkout.
import { readFileSync, realpathSync, statSync } from 'node:fs';
import { resolve, relative, sep, isAbsolute } from 'node:path';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { skillDirectory } from '../roles.ts';
import { INDICATOR_FRAMEWORK, indicatorFramework } from './hosted-skill.ts';
import type { HostedSkillContext } from './hosted-skill.ts';

// `note` gives the phase-only runtime adapter lines that come with a skill
// (roles.ts, skillAdapterNote), appended after its text.
export function invokeSkillTool(allowed: string[], hosted?: HostedSkillContext, note: (skill: string) => string | undefined = () => undefined): ToolDefinition {
	const hostedNote = hosted
		? ` Also \`${INDICATOR_FRAMEWORK}\` (Lab members, hosted on Navigator): it loads only when the approved methodology turns it on, and otherwise says whether it is available.`
		: '';
	return {
		name: 'invoke_skill',
		label: 'Invoke skill',
		description: `Load the full instructions of one of your skills. Load one skill per call, and only when the step in front of you needs it: each one stays in your context. Read a linked reference with reference: "references/<file>" on the same skill; only that skill's reference folder is readable. Available: ${allowed.join(', ')}.${hostedNote}`,
		parameters: Type.Object({ skill: Type.String(), reference: Type.Optional(Type.String()) }),
		async execute(_id, params, signal) {
			const { skill, reference } = params as { skill: string; reference?: string };
			if (hosted && skill === INDICATOR_FRAMEWORK && reference === undefined) {
				return { content: [{ type: 'text', text: await indicatorFramework(hosted, signal) }], details: undefined };
			}
			if (!allowed.includes(skill)) {
				throw new Error(`invoke_skill: "${skill}" is not one of this agent's skills (${allowed.join(', ')})`);
			}
			if (reference !== undefined) {
				if (!/^references\/[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/.test(reference) || reference.split('/').some((part) => part === '.' || part === '..')) throw new Error('reference must name a file under this skill references/');
				const root = realpathSync(skillDirectory(skill));
				const target = realpathSync(resolve(root, reference));
				const within = relative(resolve(root, 'references'), target);
				if (!within || within === '..' || within.startsWith('..' + sep) || isAbsolute(within) || !statSync(target).isFile()) throw new Error('reference is outside this skill references/');
				return { content: [{ type: 'text', text: readFileSync(target, 'utf8') }], details: undefined };
			}
			const text = readFileSync(resolve(skillDirectory(skill), 'SKILL.md'), 'utf8');
			const extra = note(skill);
			return { content: [{ type: 'text', text: extra ? `${text.trimEnd()}\n\n${extra}\n` : text }], details: undefined };
		},
	};
}
