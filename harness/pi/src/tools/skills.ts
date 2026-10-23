// invoke-skill for a harness without a file-reading tool: returns the body of
// one of the skills this process was started with, and nothing else.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { skillDirectory } from '../roles.ts';

export function invokeSkillTool(allowed: string[]): ToolDefinition {
	return {
		name: 'invoke_skill',
		label: 'Invoke skill',
		description: `Load the full instructions of one of your skills. Available: ${allowed.join(', ')}.`,
		parameters: Type.Object({ skill: Type.String() }),
		async execute(_id, params) {
			const { skill } = params as { skill: string };
			if (!allowed.includes(skill)) {
				throw new Error(`invoke_skill: "${skill}" is not one of this agent's skills (${allowed.join(', ')})`);
			}
			const text = readFileSync(resolve(skillDirectory(skill), 'SKILL.md'), 'utf8');
			return { content: [{ type: 'text', text }], details: undefined };
		},
	};
}
