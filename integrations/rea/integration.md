# REA Integration (static analysis)

[REA](https://github.com/morluto/rea) (MIT, npm package `rea-agents`) inspects
apps and captures without running them. Spotlight uses five of its static
operations to look inside a file the case already holds: an Electron app, a
saved network capture, a .NET assembly or an Android APK. Typical questions:
which servers an app is built to talk to, what an Electron app exposes over IPC,
which trackers a capture shows a page calling.

The wrapper does not install REA. The reviewed release is `rea-agents@6.4.0`
(Node.js `^22.19`, `^24.11` or `>=26`):

```bash
npm install --global rea-agents@6.4.0
```

Do not run `rea setup`, `npx rea-agents setup` or `rea update`: setup rewrites
agent configuration, and REA ships breaking major releases days apart. A run
with another version records a warning in `run-manifest.json`.

## Use When

- The case holds the artifact: an `.asar` or extracted Electron app, a HAR or
  mitmproxy capture, a .NET `.exe`/`.dll`, or an `.apk`.
- The question is what the file contains or declares.

## Do Not Use When

- The question is what an app did on a specific device or account. Static
  analysis shows code paths and declarations, not behaviour on that device.
- Analysis would need running the target, a browser, a device, or a commercial
  decompiler (Hopper, IDA, JEB). Those REA commands are not reachable from this
  wrapper.
- The artifact's licence or how it was obtained has not been cleared.
  REA's disclaimer limits use to software you own or are authorized to examine.

## Operations

| `operation` | REA command | Input |
|---|---|---|
| `javascript-application` | `analyze-javascript-application` | `.asar` or extracted app directory |
| `network-capture` | `inspect-web-network-capture` | HAR or mitmproxy file; set `capture_format` to `har` or `mitmproxy` |
| `managed-artifact` | `inspect-managed-artifact` | .NET executable or assembly |
| `managed-members` | `inspect-managed-members` | .NET executable or assembly |
| `android-package` | `inspect-android-package` | `.apk` (REA may launch its JADX/apktool provider; it reports when one is missing) |

All five declare `accessesNetwork: false` in `rea capabilities` for 6.4.0. A
HAR can hold cookies and tokens; REA reads it locally and does not fetch the
recorded URLs, but the capture itself stays case-confidential.

## Request

Copy the artifact into the case directory first, then:

```bash
python3 integrations/_runner.py rea path/to/request.json [--dry-run]
```

```json
{
  "project": "case-slug",
  "run_id": "20261014-app-ipc",
  "operation": "javascript-application",
  "input": "evidence/app.asar",
  "timeout_seconds": 600
}
```

`input` is relative to the case directory. Absolute paths, `..` and symlinks
that leave the case directory are refused.

## Output

Artifacts are written under `{CASE_DIR}/research/rea/{run_id}/`:

- `request.json`
- `evidence.json`: REA's Evidence JSON, including the input's SHA-256
- `summary.json`: `evidence_id`, `subject_sha256` and `verification_status: "unverified"`
- `stderr.txt`: REA's progress lines
- `run-manifest.json`: the command, the REA version (`tool_version`), and
  `outputs[]` with the SHA-256 of every other file

Quote REA's evidence with its limitations. Before a finding relies on it,
reproduce the key observation another way where possible (for example, the
same endpoint seen in a capture and in the app's code) and record the artifact's
provenance in `evidence-bundle.json`.
