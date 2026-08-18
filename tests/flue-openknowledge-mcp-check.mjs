#!/usr/bin/env node

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const agent = await readFile(new URL('../harness/flue/src/agents/spotlight.ts', import.meta.url), 'utf8');
const roles = await readFile(new URL('../harness/flue/src/lib/roles.ts', import.meta.url), 'utf8');

assert.doesNotMatch(agent, /connectMcpServer|OPEN_KNOWLEDGE_MCP|mcp__openknowledge/i);
assert.equal((agent.match(/tools:\s*\[\]/g) ?? []).length, 3, 'all Flue profiles must enumerate zero provider tools');
assert.doesNotMatch(agent, /SPOTLIGHT_KNOWLEDGE_(ROOT|DB|DESTINATION)/);
assert.match(roles, /scripts\/query_vault\.py/);
assert.match(roles, /--config .*\.spotlight-config\.json --case-dir/);
assert.doesNotMatch(roles, /bsig.*knowledge (search|read|stage|commit)/i);
assert.match(roles, /Spotlight's local deterministic projection writer/);
assert.match(roles, /markers, conflict checks, journals, and receipts/);
assert.doesNotMatch(roles, /mcp__openknowledge|connectMcpServer/);
assert.match(roles, /Never use raw write\/edit\/move\/delete tools/);

console.log('Flue direct-local knowledge boundary check passed');
