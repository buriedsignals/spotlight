import { registerProvider } from '@flue/runtime';
import { flue } from '@flue/runtime/routing';
import { Hono } from 'hono';

// Non-frontier local tier: local llama-server (--jinja tool-calling) serving the Gemma
// GGUFs, no API key. Pi+Flue replacing opencode for
// local models (loop U6/U10).
registerProvider('local', {
	api: 'openai-completions',
	baseUrl: 'http://127.0.0.1:8080/v1', // llama-server --jinja (tool-calling)
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
		baseUrl: 'https://openrouter.ai/api/v1',
		apiKey: process.env.OPENROUTER_API_KEY,
		contextWindow: 131072, // Gemma-4 native 128K window
		maxTokens: 8192,
	});
}

const app = new Hono();
app.route('/', flue());

export default app;
