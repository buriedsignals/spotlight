// Diagnostics for the launcher and the extension. Stdout belongs to the RPC
// protocol, so everything here goes to stderr, with secret values redacted.
import { Transform } from 'node:stream';
import type { TransformCallback } from 'node:stream';

const REDACTED = '[redacted]';

// Shorter values are not redacted (they would mangle ordinary text);
// config.ts refuses secrets that short.
export const MIN_REDACTED_LENGTH = 4;

export function redactor(secrets: Iterable<string | undefined>): (text: string) => string {
	// Longest first, so a secret that contains another is replaced whole.
	const values = [...new Set([...secrets].filter((value): value is string => !!value && value.length >= MIN_REDACTED_LENGTH))]
		.sort((a, b) => b.length - a.length);
	return (text) => values.reduce((current, value) => current.split(value).join(REDACTED), text);
}

// Redacts a child's stderr line by line, so a secret split across two chunks
// is still caught. A trailing partial line is flushed when the stream ends.
export function redactingLines(redact: (text: string) => string): Transform {
	let pending = '';
	return new Transform({
		transform(chunk: Buffer | string, _encoding: BufferEncoding, done: TransformCallback) {
			pending += chunk.toString();
			const end = pending.lastIndexOf('\n');
			if (end === -1) return done();
			const complete = pending.slice(0, end + 1);
			pending = pending.slice(end + 1);
			done(null, redact(complete));
		},
		flush(done: TransformCallback) {
			done(null, pending ? redact(pending) : undefined);
		},
	});
}

export function stderrLogger(prefix: string, redact: (text: string) => string = (text) => text) {
	return (message: string) => {
		process.stderr.write(`${prefix}: ${redact(message)}\n`);
	};
}
