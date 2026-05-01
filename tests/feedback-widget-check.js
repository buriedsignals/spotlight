#!/usr/bin/env node
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync('index.html', 'utf8');
const api = fs.readFileSync('api/feedback.js', 'utf8');

assert.match(html, /id="feedback-button"/, 'fixed feedback button is present');
assert.match(html, /id="feedback-modal"/, 'feedback modal is present');
assert.match(html, /id="feedback-form"/, 'feedback form is present');
assert.match(html, /name="message"[^>]*required/, 'message field is required');
assert.match(html, /name="contact"/, 'optional contact field is present');
assert.match(html, /name="context"/, 'optional context field is present');
assert.match(html, /data-project-tag="spotlight"/, 'Spotlight project tag is embedded as metadata');
assert.doesNotMatch(html, /LINEAR_API_KEY/, 'Linear API key is never referenced client-side');
assert.doesNotMatch(html, /Bug report|bug-report|Feature request|feature-request|type-pill|selectedFeedbackType/i, 'feedback-only UI has no bug/feature tabs');
assert.match(html, /feedback-endpoint/, 'endpoint is configurable via meta tag');
assert.match(api, /process\.env\.LINEAR_API_KEY/, 'serverless backend reads LINEAR_API_KEY server-side');
assert.match(api, /PROJECT_TAG\s*=\s*['"]spotlight['"]/, 'serverless backend applies Spotlight label');
assert.match(api, /issueCreate/, 'serverless backend creates Linear issues');
assert.doesNotMatch(api, /module\.exports\.LINEAR_API_KEY|const\s+LINEAR_API_KEY\s*=\s*['"][^'"]+['"]/, 'serverless backend does not hard-code secrets');
console.log('feedback widget checks passed');
