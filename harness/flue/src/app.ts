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
	// real output budget. contextWindow matches llama-server's -c.
	contextWindow: 8192,
	maxTokens: 4096,
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

const app = new Hono();
app.route('/', flue());

export default app;
