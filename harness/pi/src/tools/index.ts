// Every typed tool a Spotlight pi process can be given, bound to its context.
// Which of them a process actually gets is its role's allowlist in
// harness/composition.json, minus the egress tools in sensitive mode
// (roles.ts, allowedTools).
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { browseTool } from './browse.ts';
import { caseFileTools } from './case-files.ts';
import { fetchTool } from './fetch.ts';
import { httpGetTool } from './http-get.ts';
import { osintToolsTool } from './osint-tools.ts';
import { programTools } from './programs.ts';
import { queryVaultTool } from './query-vault.ts';
import type { ToolContext } from './run.ts';
import { searchTool } from './search.ts';

export function typedTools(context: ToolContext): ToolDefinition[] {
	return [
		...caseFileTools(context),
		fetchTool(context),
		searchTool(context),
		httpGetTool(context),
		browseTool(context),
		queryVaultTool(context),
		osintToolsTool(context),
		...programTools(context),
	];
}
