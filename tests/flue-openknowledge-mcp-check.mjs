#!/usr/bin/env node

import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

import { connectMcpServer } from '../harness/flue/node_modules/@flue/runtime/dist/index.mjs';
import { Client } from '../harness/flue/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StreamableHTTPClientTransport } from '../harness/flue/node_modules/@modelcontextprotocol/sdk/dist/esm/client/streamableHttp.js';

const cli = process.env.OPEN_KNOWLEDGE_BIN ?? 'open-knowledge';
const workspace = await mkdtemp(join(tmpdir(), 'spotlight-openknowledge-mcp-'));
let server;
let connection;
let client;

function run(args, options = {}) {
	const result = spawnSync(cli, ['--cwd', workspace, ...args], {
		encoding: 'utf8',
		...options,
	});
	if (result.status !== 0) {
		throw new Error(`${cli} ${args.join(' ')} failed (${result.status}):\n${result.stderr || result.stdout}`);
	}
	return result.stdout;
}

try {
	run(['init', '--no-mcp', '--no-skills', '--shared', '--json']);
	await writeFile(join(workspace, 'mcp-proof.md'), '# MCP proof\n\nThe verification marker is cobalt-orchid-815.\n', 'utf8');
	run(['config', 'validate']);

	server = spawn(cli, [
		'--cwd', workspace,
		'start', '--only', 'server', '--no-open-browser', '--idle-shutdown', '30m',
	], { stdio: 'ignore' });

	let port;
	for (let attempt = 0; attempt < 100; attempt += 1) {
		const status = spawnSync(cli, ['--cwd', workspace, 'status', '--json'], { encoding: 'utf8' });
		if (status.status === 0) {
			try {
				const parsed = JSON.parse(status.stdout);
				if (parsed.server?.alive === true && Number.isInteger(parsed.server.port)) {
					port = parsed.server.port;
					break;
				}
			} catch {
				// Server startup can race a partial status response; retry below.
			}
		}
		await delay(100);
	}
	assert.ok(port, 'OpenKnowledge project server did not become ready');

	connection = await connectMcpServer('openknowledge', {
		url: `http://127.0.0.1:${port}/mcp`,
		timeoutMs: 10_000,
	});
	const toolNames = connection.tools.map((tool) => tool.name).sort();
	assert.ok(toolNames.includes('mcp__openknowledge__search'), `missing search tool: ${toolNames.join(', ')}`);
	assert.ok(toolNames.some((name) => /write|create|update/.test(name)), `missing durable write tool: ${toolNames.join(', ')}`);

	client = new Client({ name: 'spotlight-openknowledge-check', version: '1.0.0' });
	await client.connect(new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${port}/mcp`)));
	const marker = 'saffron-lantern-815';
	const writeResult = await client.callTool({
		name: 'write',
		arguments: {
			document: {
				path: 'flue-mcp-written',
				content: `# Flue MCP write proof\n\nThe durable marker is ${marker}.\n`,
			},
		},
	});
	assert.notEqual(writeResult.isError, true, `OpenKnowledge write failed: ${JSON.stringify(writeResult.content)}`);
	assert.match(await readFile(join(workspace, 'flue-mcp-written.md'), 'utf8'), new RegExp(marker));

	let searchResult;
	for (let attempt = 0; attempt < 50; attempt += 1) {
		searchResult = await client.callTool({
			name: 'search',
			arguments: { query: marker, intent: 'full_text', semantic: false },
		});
		const matches = searchResult.structuredContent?.results ?? [];
		if (matches.some((result) => result.docName === 'flue-mcp-written')) {
			break;
		}
		await delay(100);
	}
	assert.notEqual(searchResult?.isError, true, `OpenKnowledge search failed: ${JSON.stringify(searchResult?.content)}`);
	assert.ok(
		(searchResult?.structuredContent?.results ?? []).some((result) => result.docName === 'flue-mcp-written'),
		`OpenKnowledge search did not find the MCP-authored document: ${JSON.stringify(searchResult?.structuredContent)}`,
	);
	console.log(`Flue OpenKnowledge MCP check passed (${toolNames.join(', ')})`);
} finally {
	await client?.close().catch(() => {});
	await connection?.close().catch(() => {});
	spawnSync(cli, ['--cwd', workspace, 'stop'], { encoding: 'utf8' });
	server?.kill('SIGTERM');
	await rm(workspace, { recursive: true, force: true });
}
