// read_file, write_file, edit_file, list_files and grep_files, confined to the
// active case. Reads stay inside the case (plus the checkout's schemas/);
// writes stay inside the caller's write folder, never follow a symlink, and
// never touch the files only the resolver and the review page may write.
import { closeSync, constants, globSync, lstatSync, mkdirSync, openSync, readFileSync, readdirSync, realpathSync, writeSync } from 'node:fs';
import type { Stats } from 'node:fs';
import { isAbsolute, join, relative, resolve, sep } from 'node:path';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { CHECKOUT_ROOT } from '../roles.ts';
import { checkedParams, textResult } from './run.ts';
import type { ToolContext } from './run.ts';

export class FileRefused extends Error {}

const SCHEMAS_DIR = resolve(CHECKOUT_ROOT, 'schemas');
const MAX_READ_CHARS = 200_000;
const MAX_WRITE_BYTES = 20 * 1024 * 1024;
const MAX_LIST = 500;
const MAX_MATCHES = 200;
const MAX_GREP_FILE_BYTES = 4 * 1024 * 1024;

// Case-relative, lower-case (case-insensitive file systems), forward slashes.
const PROTECTED = [
	/^data\/orchestration\.json$/, // resolver state
	/^data\/review-nonces\.json$/, // review page
	/^data\/[^/]*-review\.json$/, // review page decisions
	/(^|\/)[^/]*receipt[^/]*$/, // signing, gate and ingest receipts
];

function inside(root: string, path: string): boolean {
	const child = relative(root, path);
	return child === '' || (child !== '..' && !child.startsWith(`..${sep}`) && !isAbsolute(child));
}

function caseRelative(root: string, path: string): string {
	return relative(root, path).split(sep).join('/');
}

function requested(base: string, path: string): string {
	if (path.includes('\0')) throw new FileRefused('refused: path contains a NUL byte');
	return isAbsolute(path) ? resolve(path) : resolve(base, path);
}

function lstatOrUndefined(path: string): Stats | undefined {
	try {
		return lstatSync(path);
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === 'ENOENT') return undefined;
		throw error;
	}
}

// Resolves a write target under `root` without following any symlink: every
// existing parent must be a real directory, missing ones are created owner-only,
// and an existing target must be a regular file.
export function writeTarget(caseDir: string, root: string, path: string): string {
	const target = requested(caseDir, path);
	if (!inside(root, target) || target === root) throw new FileRefused(`refused: ${path} is outside the folder this agent may write`);
	const parts = relative(root, target).split(sep);
	let current = root;
	for (const part of parts.slice(0, -1)) {
		current = join(current, part);
		const stats = lstatOrUndefined(current);
		if (stats?.isSymbolicLink()) throw new FileRefused(`refused: ${caseRelative(caseDir, current)} is a symlink`);
		if (stats && !stats.isDirectory()) throw new FileRefused(`refused: ${caseRelative(caseDir, current)} is not a folder`);
		if (!stats) mkdirSync(current, { mode: 0o700 });
	}
	const stats = lstatOrUndefined(target);
	if (stats?.isSymbolicLink()) throw new FileRefused(`refused: ${path} is a symlink`);
	if (stats && !stats.isFile()) throw new FileRefused(`refused: ${path} is not a regular file`);
	const name = caseRelative(caseDir, target).toLowerCase();
	if (PROTECTED.some((pattern) => pattern.test(name))) {
		throw new FileRefused(`refused: ${name} is protected; only the resolver or the review page writes it`);
	}
	return target;
}

// Writes without following a symlink at the last step either.
export function writeConfined(caseDir: string, root: string, path: string, content: string | Buffer): string {
	const target = writeTarget(caseDir, root, path);
	const bytes = typeof content === 'string' ? Buffer.from(content, 'utf8') : content;
	if (bytes.length > MAX_WRITE_BYTES) throw new FileRefused(`refused: content is larger than ${MAX_WRITE_BYTES} bytes`);
	const fd = openSync(target, constants.O_WRONLY | constants.O_CREAT | constants.O_TRUNC | constants.O_NOFOLLOW, 0o600);
	try {
		writeSync(fd, bytes);
	} finally {
		closeSync(fd);
	}
	return caseRelative(caseDir, target);
}

// A read target is wherever the path really leads, which must be inside the case
// or the checkout's schemas/.
export function readTarget(caseDir: string, path: string): string {
	let real: string;
	try {
		real = realpathSync(requested(caseDir, path));
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === 'ENOENT') throw new Error(`not found: ${path}`);
		throw error;
	}
	if (!inside(caseDir, real) && !inside(SCHEMAS_DIR, real)) throw new FileRefused(`refused: ${path} is outside the case`);
	return real;
}

const readParams = Type.Object(
	{
		path: Type.String({ minLength: 1, maxLength: 1024 }),
		offset: Type.Optional(Type.Integer({ minimum: 1 })),
		limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 5000 })),
	},
	{ additionalProperties: false },
);
const writeParams = Type.Object(
	{ path: Type.String({ minLength: 1, maxLength: 1024 }), content: Type.String({ maxLength: MAX_WRITE_BYTES }) },
	{ additionalProperties: false },
);
const editParams = Type.Object(
	{
		path: Type.String({ minLength: 1, maxLength: 1024 }),
		old_text: Type.String({ minLength: 1 }),
		new_text: Type.String(),
	},
	{ additionalProperties: false },
);
const listParams = Type.Object({ pattern: Type.String({ minLength: 1, maxLength: 512 }) }, { additionalProperties: false });
const grepParams = Type.Object(
	{
		pattern: Type.String({ minLength: 1, maxLength: 512 }),
		path: Type.Optional(Type.String({ minLength: 1, maxLength: 1024 })),
		ignore_case: Type.Optional(Type.Boolean()),
	},
	{ additionalProperties: false },
);

function* caseFiles(caseDir: string, start: string): Generator<string> {
	const stats = lstatSync(start);
	if (stats.isFile()) {
		yield start;
		return;
	}
	if (!stats.isDirectory()) return;
	for (const entry of readdirSync(start, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
		const path = join(start, entry.name);
		// Symlinks are skipped: grep never reads through one.
		if (entry.isDirectory()) yield* caseFiles(caseDir, path);
		else if (entry.isFile()) yield path;
	}
}

export function caseFileTools(context: Pick<ToolContext, 'caseDir' | 'writeRoot'>): ToolDefinition[] {
	const { caseDir, writeRoot } = context;
	return [
		{
			name: 'read_file',
			label: 'Read file',
			description:
				'Read a text file in the active case (path relative to the case folder, or absolute inside it), or a Spotlight schema under the checkout schemas/ folder. Optional 1-based line offset and line limit.',
			parameters: readParams,
			async execute(_id, params) {
				const input = checkedParams<{ path: string; offset?: number; limit?: number }>('read_file', readParams, params);
				const text = readFileSync(readTarget(caseDir, input.path), 'utf8');
				const lines = text.split('\n');
				const from = (input.offset ?? 1) - 1;
				const selected = input.offset || input.limit ? lines.slice(from, from + (input.limit ?? lines.length)).join('\n') : text;
				return textResult(
					selected.length > MAX_READ_CHARS
						? `${selected.slice(0, MAX_READ_CHARS)}\n[truncated: use offset and limit to read the rest]`
						: selected,
				);
			},
		},
		{
			name: 'write_file',
			label: 'Write file',
			description:
				'Create or overwrite a file in the active case (path relative to the case folder). Parent folders are created. Refused outside your folder, through symlinks, and for resolver, review and receipt files.',
			parameters: writeParams,
			executionMode: 'sequential',
			async execute(_id, params) {
				const input = checkedParams<{ path: string; content: string }>('write_file', writeParams, params);
				return textResult(`wrote ${writeConfined(caseDir, writeRoot, input.path, input.content)}`);
			},
		},
		{
			name: 'edit_file',
			label: 'Edit file',
			description: 'Replace one exact, unique occurrence of old_text with new_text in a case file. Same limits as write_file.',
			parameters: editParams,
			executionMode: 'sequential',
			async execute(_id, params) {
				const input = checkedParams<{ path: string; old_text: string; new_text: string }>('edit_file', editParams, params);
				const target = writeTarget(caseDir, writeRoot, input.path);
				let text: string;
				try {
					text = readFileSync(target, 'utf8');
				} catch (error) {
					if ((error as NodeJS.ErrnoException).code === 'ENOENT') throw new Error(`not found: ${input.path}`);
					throw error;
				}
				const first = text.indexOf(input.old_text);
				if (first === -1) throw new Error(`edit_file: old_text not found in ${input.path}`);
				if (text.indexOf(input.old_text, first + 1) !== -1) {
					throw new Error(`edit_file: old_text occurs more than once in ${input.path}; include more context`);
				}
				const updated = text.slice(0, first) + input.new_text + text.slice(first + input.old_text.length);
				return textResult(`edited ${writeConfined(caseDir, writeRoot, input.path, updated)}`);
			},
		},
		{
			name: 'list_files',
			label: 'List files',
			description: 'List case files matching a glob pattern relative to the case folder, for example "data/*.json" or "research/**/*.md".',
			parameters: listParams,
			async execute(_id, params) {
				const { pattern } = checkedParams<{ pattern: string }>('list_files', listParams, params);
				if (isAbsolute(pattern) || pattern.split(/[\\/]/).includes('..')) {
					throw new FileRefused('refused: list_files patterns are relative to the case and cannot contain ".."');
				}
				const matches = globSync(pattern, { cwd: caseDir })
					.map((path) => path.split(sep).join('/'))
					.filter((path) => inside(caseDir, resolve(caseDir, path)))
					.sort();
				const shown = matches.slice(0, MAX_LIST);
				const more = matches.length > MAX_LIST ? `\n[${matches.length - MAX_LIST} more not shown]` : '';
				return textResult(shown.length ? shown.join('\n') + more : 'no matching files');
			},
		},
		{
			name: 'grep_files',
			label: 'Grep files',
			description:
				'Search case files for a regular expression. Optional path (file or folder, relative to the case) narrows the search. Returns path:line:text.',
			parameters: grepParams,
			async execute(_id, params) {
				const input = checkedParams<{ pattern: string; path?: string; ignore_case?: boolean }>('grep_files', grepParams, params);
				let expression: RegExp;
				try {
					expression = new RegExp(input.pattern, input.ignore_case ? 'i' : '');
				} catch (error) {
					throw new Error(`grep_files: invalid regular expression: ${(error as Error).message}`);
				}
				const start = input.path ? readTarget(caseDir, input.path) : caseDir;
				if (!inside(caseDir, start)) throw new FileRefused(`refused: ${input.path} is outside the case`);
				const matches: string[] = [];
				for (const file of caseFiles(caseDir, start)) {
					if (lstatSync(file).size > MAX_GREP_FILE_BYTES) continue;
					const lines = readFileSync(file, 'utf8').split('\n');
					for (let index = 0; index < lines.length && matches.length < MAX_MATCHES; index += 1) {
						if (expression.test(lines[index])) {
							matches.push(`${caseRelative(caseDir, file)}:${index + 1}:${lines[index].slice(0, 400)}`);
						}
					}
					if (matches.length >= MAX_MATCHES) break;
				}
				return textResult(matches.length ? matches.join('\n') : 'no matches');
			},
		},
	];
}
