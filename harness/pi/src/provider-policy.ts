// Fixed request-body privacy policy for the selected model provider.
//
// The launch contract can require fields in every provider request body (for
// example `{"provider":{"zdr":true}}` for a zero-data-retention route). Pi runs
// `before_provider_request` handlers on the serialized payload just before the
// HTTP call, so the policy is applied there, inside every Spotlight pi process.
type JSONRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JSONRecord {
	return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function mergeRequestBody(request: JSONRecord, fixed: JSONRecord): JSONRecord {
	const merged: JSONRecord = { ...request };
	for (const [key, fixedValue] of Object.entries(fixed)) {
		const requestValue = merged[key];
		merged[key] = isRecord(requestValue) && isRecord(fixedValue)
			? mergeRequestBody(requestValue, fixedValue)
			: fixedValue;
	}
	return merged;
}

export function parseFixedRequestBody(raw: string | undefined): JSONRecord | undefined {
	if (!raw) return undefined;
	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch (error) {
		throw new Error(`SPOTLIGHT_PROVIDER_REQUEST_BODY is not valid JSON: ${(error as Error).message}`);
	}
	if (!isRecord(parsed)) throw new Error('SPOTLIGHT_PROVIDER_REQUEST_BODY must be a JSON object');
	return parsed;
}

export type PolicyOutcome = { payload: unknown; blocked: boolean };

// Fixed fields win over SDK-generated fields, so nothing upstream can downgrade
// a catalog-mandated control. Pi sends the payload even when a handler throws
// (the error is only reported), so a payload the policy cannot merge into is
// replaced by the fixed fields alone: the request then carries no transcript,
// and the caller aborts the run.
export function applyFixedRequestBody(payload: unknown, fixed: JSONRecord | undefined): PolicyOutcome {
	if (!fixed || Object.keys(fixed).length === 0) return { payload, blocked: false };
	if (!isRecord(payload)) return { payload: { ...fixed }, blocked: true };
	return { payload: mergeRequestBody(payload, fixed), blocked: false };
}

// Pi replays each earlier assistant turn's thinking in the field it streamed
// from (`reasoning_content` on llama-server), and the Gemma 4 template renders
// it for every assistant message after the last user message, so a worker's
// tool chain would show the model all its earlier thoughts. The 12b was trained
// on renders without them, so on the 12b every assistant message in the
// request goes without them; the model still thinks on the current turn.
const HISTORY_REASONING_FIELDS = ['reasoning_content', 'reasoning', 'reasoning_text'];

export function withoutHistoryReasoning(payload: unknown): unknown {
	if (!isRecord(payload) || !Array.isArray(payload.messages)) return payload;
	const messages = payload.messages.map((message) => {
		if (!isRecord(message) || message.role !== 'assistant') return message;
		const kept = { ...message };
		for (const field of HISTORY_REASONING_FIELDS) delete kept[field];
		return kept;
	});
	return { ...payload, messages };
}
