type JSONRecord = Record<string, unknown>;

function record(value: unknown): value is JSONRecord {
	return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function mergeRequestBody(request: JSONRecord, fixed: JSONRecord): JSONRecord {
	const merged: JSONRecord = { ...request };
	for (const [key, fixedValue] of Object.entries(fixed)) {
		const requestValue = merged[key];
		merged[key] = record(requestValue) && record(fixedValue)
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
		throw new Error(`SPOTLIGHT_FLUE_REQUEST_BODY is not valid JSON: ${(error as Error).message}`);
	}
	if (!record(parsed)) throw new Error('SPOTLIGHT_FLUE_REQUEST_BODY must be a JSON object');
	return parsed;
}

function requestURL(input: RequestInfo | URL): string {
	return typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
}

function appliesTo(url: string, baseURL: string): boolean {
	const base = new URL(baseURL);
	const target = new URL(url);
	const prefix = base.pathname.replace(/\/$/, '');
	return target.origin === base.origin && (target.pathname === prefix || target.pathname.startsWith(`${prefix}/`));
}

// Installs a fail-closed request policy for the selected catalog provider.
// Fixed fields win over SDK-generated fields, so callers cannot accidentally
// downgrade provider.zdr or another catalog-mandated privacy control.
export function installFixedRequestBodyPolicy(
	baseURL: string,
	fixed: JSONRecord | undefined,
	baseFetch: typeof fetch = globalThis.fetch,
): typeof fetch {
	if (!fixed || Object.keys(fixed).length === 0) return baseFetch;
	return (async (input: RequestInfo | URL, init?: RequestInit) => {
		if (!appliesTo(requestURL(input), baseURL)) return baseFetch(input, init);

		let rawBody = init?.body;
		if (rawBody === undefined && typeof Request !== 'undefined' && input instanceof Request) {
			rawBody = await input.clone().text();
		}
		if (typeof rawBody !== 'string') {
			throw new Error('provider privacy policy requires a JSON request body; request was blocked before transmission');
		}
		let parsed: unknown;
		try {
			parsed = JSON.parse(rawBody);
		} catch {
			throw new Error('provider privacy policy could not parse the JSON request body; request was blocked before transmission');
		}
		if (!record(parsed)) {
			throw new Error('provider privacy policy requires a JSON object request body; request was blocked before transmission');
		}
		const body = JSON.stringify(mergeRequestBody(parsed, fixed));
		if (typeof Request !== 'undefined' && input instanceof Request && init?.body === undefined) {
			return baseFetch(new Request(input, { body }), init);
		}
		return baseFetch(input, { ...init, body });
	}) as typeof fetch;
}
