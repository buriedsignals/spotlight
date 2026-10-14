# Maigret Integration

Maigret is a username-led account-discovery tool. Spotlight uses it to find candidate profile URLs that may deserve manual inspection. A hit is never attribution proof and never verification.

The wrapper does not install Maigret. If the user explicitly enables this optional integration, the reviewed package pin is `maigret==0.4.4` in `VALIDATED_DEPENDENCIES.md`; unpinned PyPI installs are not allowed.

## Use When

- The seed is a username, handle, alias, or screen name.
- The task is to discover candidate accounts worth archiving and checking.
- Sensitive mode is off, or the operator explicitly accepts account-enumeration noise.

## Do Not Use When

- The question asks for confirmed identity attribution.
- The only seed is a full name, email, phone, organization, or domain.
- The next step should be profile archiving or evidence grounding.

## Request

Write a request file and run:

```bash
python3 integrations/maigret/run_maigret.py path/to/request.json
```

```json
{
  "project": "case-slug",
  "run_id": "20260604-username-scan",
  "usernames": ["example_user", "exampleuser"],
  "site_tags": ["social", "news"],
  "scan_all_sites": false,
  "timeout_seconds": 45,
  "formats": ["json", "csv", "txt"]
}
```

## Output

Artifacts are written under `{CASE_DIR}/research/maigret/{run_id}/`:

- `request.json`
- raw Maigret files
- `normalized-leads.json`
- `run-manifest.json`: command, exit code, the Maigret version (`tool_version`), and
  `outputs[]` with the path, SHA-256 and size of every other file in the run directory

Every normalized lead has `verification_status: "unverified"`. Inspect and archive underlying profile URLs before citing anything in `findings.json`.
