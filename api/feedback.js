const LINEAR_ENDPOINT = 'https://api.linear.app/graphql';
const LINEAR_TEAM_ID = process.env.LINEAR_TEAM_ID || 'c80ef17d-cce9-4f37-9e1d-1b2e5ca62c1f';
const PROJECT_TAG = 'spotlight';
const MAX_MESSAGE_LENGTH = 4000;
const MAX_CONTACT_LENGTH = 240;
const MAX_CONTEXT_LENGTH = 1200;

function send(res, status, body) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store');
  res.end(JSON.stringify(body));
}

function trim(value, max) {
  return typeof value === 'string' ? value.trim().slice(0, max) : '';
}

function linearErrors(data) {
  return Array.isArray(data && data.errors)
    ? data.errors.map((error) => error && error.message ? error.message : String(error))
    : [];
}

async function linearRequest(apiKey, query, variables) {
  const response = await fetch(LINEAR_ENDPOINT, {
    method: 'POST',
    headers: {
      Authorization: apiKey,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ query, variables }),
  });
  if (!response.ok) throw new Error(`Linear HTTP ${response.status}`);
  const data = await response.json();
  const errors = linearErrors(data);
  if (errors.length) throw new Error(errors.join('; '));
  return data;
}

async function resolveLabelIds(apiKey) {
  const data = await linearRequest(apiKey, `
    query FeedbackLabels {
      issueLabels(first: 100) { nodes { id name } }
    }
  `);
  const nodes = (((data.data || {}).issueLabels || {}).nodes || []);
  const labels = new Map(nodes.map((label) => [String(label.name || '').toLowerCase(), label.id]));
  return [PROJECT_TAG, 'feedback']
    .map((name) => labels.get(name))
    .filter(Boolean);
}

function buildDescription(payload, req) {
  const lines = [payload.message];
  const meta = [];
  if (payload.contact) meta.push(`**Contact:** ${payload.contact}`);
  if (payload.context) meta.push(`**Context:** ${payload.context}`);
  if (payload.url) meta.push(`**URL:** ${payload.url}`);
  if (payload.userAgent) meta.push(`**User agent:** ${payload.userAgent}`);
  meta.push(`**Source:** ${PROJECT_TAG} feedback widget`);
  meta.push(`**IP:** ${req.headers['x-forwarded-for'] || req.socket.remoteAddress || 'unknown'}`);
  lines.push('---');
  lines.push(meta.join('\n'));
  return lines.join('\n\n');
}

module.exports = async function handler(req, res) {
  if (req.method === 'OPTIONS') {
    res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    return send(res, 204, {});
  }
  if (req.method !== 'POST') return send(res, 405, { error: 'Method not allowed' });
  if (!process.env.LINEAR_API_KEY) return send(res, 503, { error: 'Feedback is not configured' });

  const body = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body || {});
  const payload = {
    message: trim(body.message, MAX_MESSAGE_LENGTH),
    contact: trim(body.contact, MAX_CONTACT_LENGTH),
    context: trim(body.context, MAX_CONTEXT_LENGTH),
    url: trim(body.url, 1000),
    userAgent: trim(body.userAgent, 500),
  };
  if (!payload.message) return send(res, 400, { error: 'Message is required' });

  try {
    const labelIds = await resolveLabelIds(process.env.LINEAR_API_KEY);
    const input = {
      teamId: LINEAR_TEAM_ID,
      title: `Spotlight feedback: ${payload.message.split(/\s+/).slice(0, 10).join(' ')}`,
      description: buildDescription(payload, req),
    };
    if (labelIds.length) input.labelIds = labelIds;
    const data = await linearRequest(process.env.LINEAR_API_KEY, `
      mutation IssueCreate($input: IssueCreateInput!) {
        issueCreate(input: $input) { success issue { id identifier url title } }
      }
    `, { input });
    const issue = data.data.issueCreate.issue;
    return send(res, 200, { ok: true, url: issue.url, identifier: issue.identifier });
  } catch (error) {
    console.error('Feedback submission failed:', error);
    return send(res, 502, { error: 'Failed to submit feedback' });
  }
};
