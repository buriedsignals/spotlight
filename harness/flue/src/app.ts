import { registerProvider } from '@flue/runtime';
import { flue } from '@flue/runtime/routing';
import { Hono } from 'hono';

// DIAGNOSTIC + MITIGATION (2026-07-10): @flue/runtime 1.0.0-beta.9 intermittently
// terminalizes a submission with "Connection error." and a SWALLOWED cause (~50-75%
// of submissions on a 46MB event-stream DB; the request dies in the internal loopback
// hop without reaching any llama-server). Until the upstream fix: wrap global fetch to
// (a) LOG the real underlying cause (undici error code) so the failure is diagnosable,
// (b) RETRY pure network-layer failures — a request that never got a response — up to
// 3x with backoff. Safe here: bodies are JSON strings (re-sendable) and the hop is
// local. Deliberate aborts and stream bodies are never retried. Remove when the
// runtime surfaces causes and retries internally.
const _fetch = globalThis.fetch;
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
	for (let attempt = 1; ; attempt++) {
		try {
			return await _fetch(input, init);
		} catch (err) {
			const e = err as Error & { cause?: { code?: string; message?: string } };
			const url =
				typeof input === 'string' ? input : ((input as Request)?.url ?? String(input));
			console.error(
				`[fetch-retry] attempt ${attempt} failed: ${url} — ${e?.message}; cause: ${
					e?.cause?.code ?? e?.cause?.message ?? e?.cause ?? 'none'
				}`,
			);
			const aborted =
				e?.name === 'AbortError' || (init?.signal as AbortSignal | undefined)?.aborted;
			const streamBody =
				typeof (init?.body as { getReader?: unknown } | undefined)?.getReader === 'function';
			if (aborted || streamBody || attempt >= 3) throw err;
			await new Promise((r) => setTimeout(r, 500 * attempt));
		}
	}
}) as typeof fetch;

// Non-frontier local tier: local llama-server (--jinja tool-calling) serving the Gemma
// GGUFs, no API key. Pi+Flue replacing opencode for
// local models (loop U6/U10).
registerProvider('local', {
	api: 'openai-completions',
	// llama-server --jinja (tool-calling). Default = local; SPOTLIGHT_LOCAL_BASEURL points it
	// at a remote llama-server (e.g. a RunPod pod proxy `https://<id>-8080.proxy.runpod.net/v1`)
	// for on-policy sampling (U20a) without a code edit.
	baseUrl: process.env.SPOTLIGHT_LOCAL_BASEURL ?? 'http://127.0.0.1:8080/v1',
	apiKey: 'local', // llama-server ignores it, but the OpenAI wire protocol requires a non-empty key
	// Custom (non-catalog) providers have no catalog metadata, so maxTokens falls
	// back to 0 → a 1-token output cap. Declare them so the thinking Gemma gets a
	// real output budget. contextWindow MUST match llama-server's -c (the launcher
	// sets SPOTLIGHT_LOCAL_CTX to the same value): a full investigation loop reaches
	// ~26K tokens by synthesis, so 8192 truncates the run mid-loop.
	contextWindow: Number(process.env.SPOTLIGHT_LOCAL_CTX ?? 32768),
	maxTokens: 8192,
});

// Cloud non-frontier tier: Fireworks GLM-5.2 (ZDR), OpenAI-compatible. Registered
// only when the key is present; exercised in U10.
if (process.env.FIREWORKS_API_KEY) {
	registerProvider('fireworks', {
		api: 'openai-completions',
		baseUrl: 'https://api.fireworks.ai/inference/v1',
		apiKey: process.env.FIREWORKS_API_KEY,
	});
}

// Validation-only tier: OpenRouter serving `google/gemma-4-31b-it`. Per the testing
// ladder (invariant 8), the 31B's harness *logic* is iterated here — it produces output
// reliably on OpenRouter and this isolates harness bugs from local serving bugs.
// Custom provider, so contextWindow+maxTokens are mandatory (else max_completion_tokens:1).
// Model string: `openrouter/google/gemma-4-31b-it` (provider = first path segment).
if (process.env.OPENROUTER_API_KEY) {
	registerProvider('openrouter', {
		api: 'openai-completions',
		// Point at the local inject-proxy (SPOTLIGHT_OPENROUTER_BASEURL=http://127.0.0.1:8091/v1)
		// to add `reasoning:{enabled:true}` so Gemma-4's thought returns in a separate field
		// instead of leaking `<channel>` into content/tool-calls (the bug that sent us local).
		baseUrl: process.env.SPOTLIGHT_OPENROUTER_BASEURL ?? 'https://openrouter.ai/api/v1',
		apiKey: process.env.OPENROUTER_API_KEY,
		contextWindow: 131072, // Gemma-4 native 128K window
		maxTokens: 8192,
	});
}

// RLM / compaction-summarizer tier: the Gemma e4b served by its OWN llama.cpp
// (SPOTLIGHT_RLM_OPENAI_BASE_URL, e.g. http://127.0.0.1:8095/v1 — the SAME endpoint the
// Python `fetch --rlm` seam distills scraped pages through). Registered as a Flue provider so
// the local tier can use it as the CHEAP compaction summarizer (agents/spotlight.ts) instead of
// spending minutes summarizing with the slow session 12b/26b at every gate — the thrash that
// turned the gold-investigation eval into a crawl. Registered only when the endpoint is set, so
// cloud/frontier deployments (no local e4b) never reference it. See docs/local-serving-efficiency.md.
if (process.env.SPOTLIGHT_RLM_OPENAI_BASE_URL) {
	const rlmBase = process.env.SPOTLIGHT_RLM_OPENAI_BASE_URL.replace(/\/$/, '');
	registerProvider('rlm', {
		api: 'openai-completions',
		baseUrl: rlmBase.endsWith('/v1') ? rlmBase : `${rlmBase}/v1`,
		apiKey: 'local', // llama-server ignores it; the OpenAI wire protocol requires a non-empty key
		// MUST match the e4b llama-server's --ctx-size (launcher exports SPOTLIGHT_RLM_CTX).
		// 24576 fits the largest compaction-summarizer input (serialized since-last-cut
		// transcript, tool results truncated to 2000 chars, + prompt + previous summary)
		// with headroom; a structured checkpoint summary is short, but generateSummary caps
		// maxTokens at min(0.8*reserveTokens, 16000) so declare real output budget.
		contextWindow: Number(process.env.SPOTLIGHT_RLM_CTX ?? 24576),
		maxTokens: 16000,
	});
}

const app = new Hono();
app.route('/', flue());

export default app;
