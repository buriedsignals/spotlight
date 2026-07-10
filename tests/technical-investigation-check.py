#!/usr/bin/env python3
"""Static contract checks for the reviewed CTI Expert adaptation."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "technical-investigation"
TASKS = {
    "indicator-triage",
    "infrastructure-history",
    "document-message-forensics",
    "github-public",
}
EXPECTED_REFERENCES = {
    *(f"{task}.md" for task in TASKS),
    *(f"{task}-compact.md" for task in TASKS),
    "verified-indicator-export.md",
    "source-map.json",
}
FORBIDDEN_RUNTIME_PATTERNS = {
    "--yolo": re.compile(r"--yolo", re.IGNORECASE),
    "sudo command": re.compile(r"(?:^|\s)sudo\s", re.IGNORECASE),
    "pip install": re.compile(r"\bpip(?:3)?\s+install\b", re.IGNORECASE),
    "npm install": re.compile(r"\bnpm\s+install\b", re.IGNORECASE),
    "uv install": re.compile(r"\buv\s+(?:tool\s+)?install\b", re.IGNORECASE),
    "git clone": re.compile(r"\bgit\s+clone\b", re.IGNORECASE),
    "remote script pipe": re.compile(r"\bcurl\b[^\n|]*\|\s*(?:ba)?sh\b", re.IGNORECASE),
}


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def body_and_description(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise ValueError("SKILL.md frontmatter missing")
    description_match = re.search(r"^description:\s*(.+)$", match.group(1), re.MULTILINE)
    if not description_match:
        raise ValueError("SKILL.md description missing")
    return text[match.end() :], description_match.group(1).strip()


def main() -> int:
    errors: list[str] = []
    try:
        body, description = body_and_description(SKILL / "SKILL.md")
    except (OSError, ValueError) as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1

    references = {path.name for path in (SKILL / "references").iterdir() if path.is_file()}
    if references != EXPECTED_REFERENCES:
        fail(errors, f"reference set drifted: got {sorted(references)}")

    if len(description) // 4 > 40:
        fail(errors, "frontmatter description exceeds the 40-token routing budget")
    if len(body) // 4 > 1200:
        fail(errors, "technical-investigation root exceeds 1,200 approximate tokens")
    if "`12b`" not in body or "exactly one" not in body:
        fail(errors, "root does not preserve the 12b single-compact-reference rule")

    for task in TASKS:
        compact = SKILL / "references" / f"{task}-compact.md"
        if len(compact.read_text(encoding="utf-8")) // 4 > 1200:
            fail(errors, f"{compact.name} exceeds 1,200 approximate tokens")

    runtime_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(SKILL.rglob("*"))
        if path.is_file()
    )
    for label, pattern in FORBIDDEN_RUNTIME_PATTERNS.items():
        if pattern.search(runtime_text):
            fail(errors, f"runtime adaptation contains forbidden {label}")

    lock = json.loads((ROOT / "upstreams" / "cti-expert" / "source.lock.json").read_text(encoding="utf-8"))
    active_sha = lock["active_sha"]
    source_map = json.loads((SKILL / "references" / "source-map.json").read_text(encoding="utf-8"))
    maintenance_map = json.loads(
        (ROOT / "upstreams" / "cti-expert" / "source-map.json").read_text(encoding="utf-8")
    )
    schema = json.loads((ROOT / "schemas" / "methodology.schema.json").read_text(encoding="utf-8"))
    source_record = json.loads(
        (ROOT / "third_party" / "cti-expert" / "SOURCE.json").read_text(encoding="utf-8")
    )
    revisions = json.loads(
        (ROOT / "upstreams" / "cti-expert" / "reviewed-revisions.json").read_text(encoding="utf-8")
    )
    if {
        source_map.get("reviewed_revision"),
        maintenance_map.get("active_sha"),
        source_record.get("active_revision"),
        revisions.get("active_sha"),
    } != {active_sha}:
        fail(errors, "reviewed SHA differs across lock, source maps, source record, and revision catalog")
    active_revisions = [
        item for item in revisions.get("revisions", []) if isinstance(item, dict) and item.get("sha") == active_sha
    ]
    if len(active_revisions) != 1:
        fail(errors, "active SHA is absent from the append-only reviewed revision catalog")
    else:
        license_path = ROOT / str(lock.get("license_path", ""))
        if not license_path.is_file():
            fail(errors, "upstream license is missing")
        elif hashlib.sha256(license_path.read_bytes()).hexdigest() != active_revisions[0].get("license_sha256"):
            fail(errors, "shipped upstream license hash differs from the reviewed revision catalog")
    if active_sha not in body or active_sha not in (ROOT / "NOTICE.md").read_text(encoding="utf-8"):
        fail(errors, "skill or NOTICE active SHA differs from the source lock")
    sha_schema = schema["properties"]["method_provenance"]["items"]["properties"]["active_sha"]
    if sha_schema.get("pattern") != "^[0-9a-f]{40}$":
        fail(errors, "methodology schema must accept historical full reviewed SHAs")
    if not maintenance_map.get("adaptations") or not all(
        item.get("adapted") is True for item in maintenance_map["adaptations"]
    ):
        fail(errors, "maintainer source map must mark every mapped section adapted=true")
    mapped_paths = {
        ROOT / relative
        for adaptation in maintenance_map.get("adaptations", [])
        if isinstance(adaptation, dict)
        for relative in adaptation.get("spotlight_paths", [])
        if isinstance(relative, str)
    }
    missing_mapped_paths = sorted(str(path.relative_to(ROOT)) for path in mapped_paths if not path.is_file())
    if missing_mapped_paths:
        fail(errors, f"source map references missing Spotlight files: {missing_mapped_paths}")
    mapped_runtime_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(mapped_paths) if path.is_file()
    )
    for label, pattern in FORBIDDEN_RUNTIME_PATTERNS.items():
        if pattern.search(mapped_runtime_text):
            fail(errors, f"source-mapped adaptation contains forbidden {label}")
    if maintenance_map.get("license") != lock.get("license"):
        fail(errors, "maintainer source map and source lock license labels differ")

    workflow = (ROOT / ".github" / "workflows" / "cti-expert-upstream.yml").read_text(
        encoding="utf-8"
    )
    if not all(
        marker in workflow
        for marker in ("issues: write", "actions/github-script@v9", "CTI Expert upstream review pending")
    ):
        fail(errors, "daily upstream watcher must persist drift in the review issue")

    for path in (ROOT / "README.md", ROOT / "NOTICE.md", ROOT / "index.html"):
        text = path.read_text(encoding="utf-8")
        if "CTI Expert" not in text or "Hieu Ngo" not in text:
            fail(errors, f"{path.name} lacks the required CTI Expert acknowledgement")

    implementation_doc = ROOT / "docs" / "technical-investigation.md"
    if not implementation_doc.is_file():
        fail(errors, "technical-investigation implementation documentation is missing")
    else:
        doc_text = implementation_doc.read_text(encoding="utf-8")
        if active_sha not in doc_text or "technical_indicator_ids" not in doc_text:
            fail(errors, "technical-investigation documentation lacks provenance or export contract")
    for retired_doc in (
        ROOT / "docs" / "cti-expert-integration-audit.md",
        ROOT / "docs" / "cti-expert-integration-prd.md",
    ):
        if retired_doc.exists():
            fail(errors, f"retired planning document remains: {retired_doc.name}")

    if errors:
        for error in errors:
            print(f"FAIL  {error}", file=sys.stderr)
        return 1
    print("technical investigation contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
