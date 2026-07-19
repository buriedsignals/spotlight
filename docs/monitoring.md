# Monitoring recommendations

Spotlight identifies investigation targets worth watching; it does not operate
monitoring infrastructure. A recommendation is recorded in
`data/findings.json` as `monitoring_recommendations[]`, then shown to the user
for approval.

If the user wants durable monitoring and has Mycroft, Spotlight hands the
recommendation to Mycroft in plain language. Mycroft alone owns the optional
Scoutpost skill, authentication, CLI/API choice, durable state, and scout
creation. A standalone Spotlight install never reads Scoutpost configuration or
creates a Scoutpost project, scout, or information-unit request.

If Mycroft is unavailable, retain the recommendation in the case or let the
user create a runtime-native reminder separately. Do not emulate or configure
Scoutpost from Spotlight.
