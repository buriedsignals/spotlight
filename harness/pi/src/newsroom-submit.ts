#!/usr/bin/env node
// Submits a newsroom case's approved batch from a runtime without the Pi
// launcher (a subscription desktop app running the skills):
//
//   node harness/pi/src/newsroom-submit.ts {CASE_DIR}
//
// It registers the case with the newsroom if it is not yet, sends the batch
// the member approved on the ingest page and records the receipts, with the
// same code the launcher runs (editorial.ts). The batch goes from this
// computer to the newsroom's service; it does not pass through a model.
//
// SPOTLIGHT_EDITORIAL_MCP_URL and SPOTLIGHT_EDITORIAL_MCP_TOKEN name the
// service and the sign-in, as for the launcher. The outcome is one JSON
// object on stdout, without the review address.
//
// Exit codes: 0 submitted; 3 the newsroom refused the batch (the reasons are
// in the outcome); 4 the sign-in is no longer valid; 5 the service could not
// be used; 2 anything else, with the reason on stderr.
import { spawn } from 'node:child_process';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { EditorialClient, EditorialCredentialError, ensureCaseRegistered, isAllowedOrigin, stopsTheNewsroomCase, submitCase } from './editorial.ts';
import type { RunPackage } from './editorial.ts';
import type { HarnessConfig } from './config.ts';
import { openInBrowser } from '../../review/session.mjs';

const CHECKOUT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const TOKEN_ENV = 'SPOTLIGHT_EDITORIAL_MCP_TOKEN';

class Usage extends Error {}

function endpoint(env: NodeJS.ProcessEnv): { url: string; token: string } {
	const url = URL.parse(env.SPOTLIGHT_EDITORIAL_MCP_URL?.trim() ?? '');
	const loopback = !!url && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname);
	if (!url || url.username || url.password || !(url.protocol === 'https:' || (url.protocol === 'http:' && loopback))) {
		throw new Usage('SPOTLIGHT_EDITORIAL_MCP_URL must be the newsroom\'s Editorial MCP: https, or http on a loopback address');
	}
	const token = env[TOKEN_ENV]?.trim() ?? '';
	if (token.length < 16) throw new Usage(`${TOKEN_ENV} is not set to the newsroom sign-in token`);
	return { url: url.href, token };
}

// scripts/newsroom-package.py, the only writer of the newsroom case files.
// It never gets the token.
const runPackage: RunPackage = (args, input) =>
	new Promise((done, fail) => {
		const { [TOKEN_ENV]: _token, ...env } = process.env;
		const child = spawn(join(CHECKOUT_ROOT, 'scripts', 'spotlight-python'), [join(CHECKOUT_ROOT, 'scripts', 'newsroom-package.py'), ...args], { cwd: CHECKOUT_ROOT, env });
		let stdout = '';
		let stderr = '';
		child.stdout.on('data', (chunk) => (stdout += chunk));
		child.stderr.on('data', (chunk) => (stderr += chunk));
		child.on('error', fail);
		child.on('close', (code) => done({ code: code ?? 1, stdout, stderr }));
		child.stdin.end(input === undefined ? '' : JSON.stringify(input));
	});

async function main(): Promise<number> {
	if (!process.argv[2]) throw new Usage('usage: node harness/pi/src/newsroom-submit.ts {CASE_DIR}');
	const caseDir = resolve(process.argv[2]);
	const { url, token } = endpoint(process.env);
	const origins = [new URL(url).origin];
	const client = new EditorialClient({ url, token, origins });
	const caseId = basename(caseDir);
	// What ensureCaseRegistered reads of the launcher's configuration.
	const config = { caseDir, caseId, editorial: { url, tokenEnv: TOKEN_ENV, caseTitle: process.env.SPOTLIGHT_CASE_TITLE?.trim() || caseId } } as HarnessConfig;
	await client.connect();
	const { reference } = await ensureCaseRegistered(client, config, runPackage);
	const outcome = await submitCase({
		client,
		caseRef: reference.case_ref,
		caseDir,
		runPackage,
		openReview: async (address) => {
			if (!isAllowedOrigin(origins, address)) return false;
			if (!(await openInBrowser(address)).ok) throw new Error('the newsroom review page could not be opened; the submission stays pending, run this again');
			return true;
		},
	});
	process.stdout.write(`${JSON.stringify(outcome)}\n`);
	return outcome.state === 'submitted' ? 0 : 3;
}

main().then(
	(code) => process.exit(code),
	(error: Error) => {
		process.stderr.write(`newsroom-submit: ${error.message}\n`);
		process.exit(error instanceof EditorialCredentialError ? 4 : stopsTheNewsroomCase(error) ? 5 : 2);
	},
);
