# Web-Check Integration

[Web-Check](https://github.com/lissy93/web-check) (MIT) gathers what is publicly
visible about a website: DNS, registration, certificates, archive history,
headers, trackers and technology. Spotlight calls one check at a time on an
instance the member runs, and saves each answer as an unverified observation.

The wrapper does not install Web-Check and never sends case targets to the
public web-check.xyz service. Point `WEB_CHECK_URL` at your own instance, for
example one started with the upstream image:

```bash
docker run --rm -p 3000:3000 lissy93/web-check
# WEB_CHECK_URL=http://127.0.0.1:3000
```

Preflight reports `unconfigured` until `WEB_CHECK_URL` is set and `yellow` when
the instance does not answer.

## Use When

- The question is about a website or domain: who registered it, where it is
  hosted, its DNS and mail setup, subdomains in certificate logs, past versions,
  trackers or the technology it runs.
- Sensitive mode is off.
- `technical-investigation` has routed the indicator here and you will record
  each check under its method-provenance labels.

Lookup checks are passive. Page checks contact the live host, which
`infrastructure-history.md` places outside the passive method: set
`contact_target: true` only after the user explicitly approves it and the
methodology records an OPSEC plan.

## Do Not Use When

- The target is a suspicious indicator (phishing, malware, C2). Lookup checks are
  still allowed; page checks are not, per
  `skills/technical-investigation/references/indicator-triage.md`.
- The question is identity attribution. Shared hosting, CDNs, privacy proxies
  and copied trackers make infrastructure overlap weak evidence on its own.

## Checks

| Tier | Checks | Who is contacted |
|---|---|---|
| lookup (default) | `get-ip`, `dns`, `txt-records`, `caa-records`, `dnssec`, `dns-server`, `whois`, `archives`, `subdomains`, `rank`, `block-lists`, `location`, `social-presence` | DNS resolvers (and so the target's nameservers), RDAP/WHOIS, Wayback CDX, crt.sh, CertSpotter, HackerTarget, Tranco, IP-geolocation services, GitHub/Bluesky/X profile lookups |
| page (`contact_target: true`) | `status`, `headers`, `redirects`, `ssl`, `tls-connection`, `hsts`, `http-security`, `firewall`, `robots-txt`, `sitemap`, `security-txt`, `social-tags`, `linked-pages`, `mixed-content`, `mail-config`, `cookies`, `tech-stack`, `trackers` | The target's web server, like a visit; `tech-stack` and `trackers` load the page in the instance's Chromium |
| excluded | `ports`, `trace-route`, `shodan`, `threats`, `tls-labs`, `quality`, `carbon`, `breaches`, `screenshot` | Never called: active scanning or handing the target to a third-party scanner. Use `dev-browser` for screenshots. |

The aggregate `/api` endpoint is never called, because it runs every check.

## Request

```bash
python3 integrations/_runner.py webcheck path/to/request.json [--dry-run]
```

```json
{
  "project": "case-slug",
  "run_id": "20261014-example-org",
  "url": "example.org",
  "checks": ["dns", "whois", "subdomains", "archives"],
  "contact_target": false,
  "timeout_seconds": 60
}
```

Omit `checks` to run every lookup check, plus every page check when
`contact_target` is true. `--dry-run` lists the API calls without sending them.

## Output

Artifacts are written under `{CASE_DIR}/research/webcheck/{run_id}/`:

- `request.json`
- `<check>.json`: the instance's answer for each check
- `results.json`: per check, `outcome` (`observed`, `skipped` or `error`),
  `detail`, `raw_path` and `verification_status: "unverified"`
- `run-manifest.json`: the API calls, the instance version from `/healthz`
  (`tool_version`), and `outputs[]` with the SHA-256 of every other file

Map `observed` to an observed path, `skipped` to a null or skipped path with
its reason, and `error` to a blocked path in the case's method provenance.
Corroborate anything you cite (for example RDAP from the registry, a certificate
from crt.sh) and archive the origin before it goes into `findings.json`.
