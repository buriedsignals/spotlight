# Public GitHub History

## Scope

Trace relevant public repository and account history while preserving stable identifiers and the difference between platform observations and identity claims. This method excludes secret hunting, credential validation, access to private material, behavioral profiling, and contact with the subject.

## Workflow

1. State the question and time window. Record the exact owner/repository or account URL supplied by the case.
2. Capture the current public surface and stable identifiers: repository ID when exposed, full name, default branch, commit SHAs, tag/release IDs, fork relationships, event timestamps, source URL, API/page access method, and collection UTC.
3. Follow only case-relevant public history:
   - commits and parent relationships for content changes;
   - tags and releases for published versions;
   - forks for surviving public history or provenance;
   - issues and pull requests for documented decisions;
   - public archive captures for pages or repositories that later changed.
4. Save raw public responses or page captures inside the case directory where terms and access permit. Record pagination, truncation, rate limits, and missing event windows.
5. Build a chronological table. Keep platform timestamps and Git timestamps in their original fields; do not combine author, committer, push, merge, and release times.
6. Search for contradictions and independent corroboration before promoting a lead.

WARNING: Do not enumerate exposed secrets, search commit history for credentials, test a token, bypass access controls, use a leaked dataset, infer a person's daily routine, or message an account. Stop if the next step requires authentication or interaction beyond the journalist's authorized public access.

## Interpretation Limits

Git author and committer names/emails are user-supplied metadata. A signed commit can support control of a key, but identity still depends on the key's independent provenance. A contribution does not by itself prove employment, organizational control, intent, location, or authorship of every changed line. Fork creation, stars, watches, and follows are weak relationship signals.

A deletion or disappearance is an observation, not proof of concealment. Platform indexing, moderation, renames, transfers, legal requests, or ordinary cleanup may explain it.

## Evidence Packet

For each event retain the object type, stable ID/SHA, actor as represented by GitHub, event time, collection time, source URL, archived/local artifact, relevant diff or field, visibility limits, and alternative explanation. Route identity or control claims through `epistemic-grounding` and cap confidence to the weakest material element.
