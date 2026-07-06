import { defineAgent } from '@flue/runtime';

// Smoke agent: proves the Flue-on-Pi harness reaches the local Gemma via Ollama
// with no API key (loop U6 test scenario a).
export default defineAgent(() => ({
	model: 'local/gemma-4-12b-it',
	instructions: 'You are a terse assistant. Answer in one short sentence.',
	thinkingLevel: 'low',
}));
