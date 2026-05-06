#!/usr/bin/env node
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync('index.html', 'utf8');

assert.match(html, /id="feedback-button"/, 'fixed feedback button is present');
assert.match(html, /id="feedback-modal"/, 'feedback modal is present');
assert.match(html, /id="feedback-form"/, 'feedback form is present');
assert.match(html, /name="message"[^>]*required/, 'message field is required');
assert.match(html, /name="contact"/, 'optional contact field is present');
assert.match(html, /name="context"/, 'optional context field is present');
assert.match(html, /data-product="spotlight"/, 'Spotlight product tag is embedded as metadata');
assert.match(html, /https:\/\/api\.buriedsignals\.com\/v1\/feedback/, 'shared feedback API endpoint is configured');
assert.match(html, /product:\s*modal\.dataset\.product/, 'feedback payload sends product');
assert.doesNotMatch(html, /LINEAR_API_KEY/, 'Linear API key is never referenced client-side');
assert.doesNotMatch(html, /Bug report|bug-report|Feature request|feature-request|type-pill|selectedFeedbackType/i, 'feedback-only UI has no bug/feature tabs');
assert.match(html, /feedback-endpoint/, 'endpoint is configurable via meta tag');
assert.doesNotMatch(html, /\/api\/feedback/, 'static GitHub Pages site does not point at a local serverless path');
console.log('feedback widget checks passed');
