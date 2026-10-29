// invoke-skill for a harness without a file-reading tool: returns the body of
// one of the skills this process was started with, and nothing else. The
// investigator and the fact-checker may also name the Navigator-hosted
// Indicator Framework (hosted-skill.ts), which never lives in the checkout.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
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
		description: `Load the full instructions of one of your skills. Load one skill per call, and only when the step in front of you needs it: each one stays in your context. Available: ${allowed.join(', ')}.${hostedNote}`,
		parameters: Type.Object({ skill: Type.String() }),
		async execute(_id, params, signal) {
			const { skill } = params as { skill: string };
			if (hosted && skill === INDICATOR_FRAMEWORK) {
				return { content: [{ type: 'text', text: await indicatorFramework(hosted, signal) }], details: undefined };
			}
			if (!allowed.includes(skill)) {
				throw new Error(`invoke_skill: "${skill}" is not one of this agent's skills (${allowed.join(', ')})`);
			}
			const text = readFileSync(resolve(skillDirectory(skill), 'SKILL.md'), 'utf8');
			const extra = note(skill);
			return { content: [{ type: 'text', text: extra ? `${text.trimEnd()}\n\n${extra}\n` : text }], details: undefined };
		},
	};
}
