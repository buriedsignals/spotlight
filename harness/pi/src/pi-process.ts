// How every Spotlight pi process starts: the pinned CLI, RPC mode, nothing
// discovered, only the Spotlight extension and the role's skills by path, and
// an explicit tool allowlist.
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Readable } from 'node:stream';
import { skillDirectory } from './roles.ts';

export const EXTENSION_PATH = resolve(dirname(fileURLToPath(import.meta.url)), 'extension.ts');
export const PROVIDER_ID = 'spotlight';

export function piCliPath(): string {
	// The package exports only its ESM entry (dist/index.js); its root is one up.
	const entry = fileURLToPath(import.meta.resolve('@earendil-works/pi-coding-agent'));
	const root = resolve(dirname(entry), '..');
	const manifest = JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8')) as { bin: Record<string, string> };
	return resolve(root, manifest.bin.pi);
}

export type PiProcessSpec = {
	systemPrompt: string;
	skills: string[];
	tools: string[];
	model: string;
};

export function piArguments(spec: PiProcessSpec): string[] {
	const args = [
		piCliPath(),
		'--mode', 'rpc',
		'--no-builtin-tools',
		'--no-extensions',
		'--no-skills',
		'--no-mcp',
		'--no-context-files',
		'--no-prompt-templates',
		'--no-themes',
		'--no-approve',
		'--no-session',
		'--offline',
		'--extension', EXTENSION_PATH,
		'--model', `${PROVIDER_ID}/${spec.model}`,
		'--system-prompt', spec.systemPrompt,
	];
	for (const skill of spec.skills) args.push('--skill', skillDirectory(skill));
	if (spec.tools.length === 0) args.push('--no-tools');
	else args.push('--tools', spec.tools.join(','));
	return args;
}

// Writes the Pi settings a process starts with into its own configuration
// folder (PI_CODING_AGENT_DIR), replacing whatever an earlier launch left
// there. Workers of one role share the folder and may start together, so the
// file is replaced atomically.
export function writePiSettings(agentDir: string, settings: Record<string, unknown>): void {
	mkdirSync(agentDir, { recursive: true, mode: 0o700 });
	const target = join(agentDir, 'settings.json');
	const temporary = `${target}.${process.pid}.${Date.now()}.${Math.random().toString(36).slice(2)}.tmp`;
	writeFileSync(temporary, `${JSON.stringify(settings, null, 2)}\n`, { mode: 0o600 });
	renameSync(temporary, target);
}

// Strict JSONL: split on LF only (Node's readline also splits on U+2028/9,
// which are legal inside JSON strings). The stream keeps flowing until it
// ends, so the writer never hits a closed pipe. A line that is not a JSON
// object is skipped with a note on stderr that names only its length, since
// the line may carry case text.
export function onJsonlRecords(stream: Readable, onRecord: (record: Record<string, unknown>) => void): void {
	let buffer = '';
	const emit = (line: string) => {
		const trimmed = line.replace(/\r$/, '');
		if (!trimmed.trim()) return;
		let record: unknown;
		try {
			record = JSON.parse(trimmed);
		} catch {
			record = undefined;
		}
		if (record === null || typeof record !== 'object' || Array.isArray(record)) {
			process.stderr.write(`spotlight: skipped a ${trimmed.length}-character stdout line that is not an RPC record\n`);
			return;
		}
		onRecord(record as Record<string, unknown>);
	};
	stream.setEncoding('utf8');
	stream.on('data', (chunk: string) => {
		buffer += chunk;
		let newline: number;
		while ((newline = buffer.indexOf('\n')) !== -1) {
			const line = buffer.slice(0, newline);
			buffer = buffer.slice(newline + 1);
			emit(line);
		}
	});
	stream.on('end', () => {
		if (buffer) emit(buffer);
		buffer = '';
	});
}
