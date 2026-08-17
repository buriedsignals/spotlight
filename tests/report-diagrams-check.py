#!/usr/bin/env python3
"""Focused contract checks for deterministic Spotlight report diagrams."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FINALIZER = ROOT / "scripts" / "finalize-report.py"
VALIDATOR = ROOT / "scripts" / "validate-report-draft.py"


def load_report_helpers() -> Any:
    spec = importlib.util.spec_from_file_location(
        "render_report_check_helpers", ROOT / "tests" / "render-report-check.py"
    )
    if spec is None or spec.loader is None:
        raise AssertionError("could not load report test helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPERS = load_report_helpers()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def diagram_fixture(kind: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    common = {
        "id": f"{kind}-structure",
        "type": kind,
        "title": f"{kind.title()} structure",
        "caption": "A compact rendering of the selected recorded relationships.",
        "finding_ids": ["F1"],
    }
    if kind == "flow":
        connections = [
            {"from": "Payer", "to": "Intermediary", "relationship": "paid"},
            {"from": "Intermediary", "to": "Recipient", "relationship": "transferred"},
        ]
        return connections, {**common, "connections": connections, "focal_entities": ["Recipient"]}
    if kind == "hierarchy":
        connections = [
            {"from": "Holding company", "to": "Operating company", "relationship": "owns"},
            {"from": "Holding company", "to": "Finance vehicle", "relationship": "controls"},
        ]
        return connections, {**common, "connections": connections}
    if kind == "network":
        connections = [
            {"from": "Director", "to": "Company", "relationship": "controls"},
            {"from": "Supplier", "to": "Company", "relationship": "contracted_with"},
            {"from": "Company", "to": "Recipient", "relationship": "paid"},
        ]
        return connections, {**common, "connections": connections, "focal_entities": ["Company"]}
    if kind == "loop":
        connections = [
            {"from": "Sponsor", "to": "Vendor", "relationship": "paid"},
            {"from": "Vendor", "to": "Sponsor", "relationship": "returned_value_to"},
        ]
        return connections, {**common, "connections": connections, "focal_entities": ["Sponsor"]}
    raise AssertionError(f"unknown diagram type {kind}")


def build_case(root: Path, kind: str) -> Path:
    case = HELPERS.build_case(root)
    findings_path = case / "data" / "findings.json"
    draft_path = case / "data" / "report-draft.json"
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    connections, diagram = diagram_fixture(kind)
    findings["connections"] = connections
    write_json(findings_path, findings)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    draft["diagrams"] = [diagram]
    write_json(draft_path, draft)
    return case


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True)


def expect_invalid(case: Path, message: str) -> None:
    result = run([sys.executable, str(VALIDATOR), str(case)])
    assert result.returncode == 3, result.stdout + result.stderr
    assert message in result.stdout, result.stdout + result.stderr


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="report-diagrams-") as tmp:
        root = Path(tmp)
        for kind in ("flow", "hierarchy", "network", "loop"):
            case = build_case(root / kind, kind)
            result = run([sys.executable, str(FINALIZER), str(case)])
            assert result.returncode == 0, result.stdout + result.stderr
            html = (case / "report.html").read_text(encoding="utf-8")
            markdown = (case / "findings-report.md").read_text(encoding="utf-8")
            outputs = [case / "report.html", case / "findings-report.md", case / "evidence-map.json"]
            hashes = [sha(path) for path in outputs]

            assert 'class="report-diagrams"' in html
            assert "securityLevel: \"strict\"" in html
            assert "mermaid@11.16.1" in html
            assert "@mermaid-js/layout-elk@0.2.2" in html
            assert "```mermaid" in markdown
            assert markdown.count("```") == 2
            assert f"### {kind.title()} structure" in markdown
            assert f"layout: {'dagre' if kind == 'loop' else 'elk'}" in html
            if kind == "loop":
                assert "stroke-dasharray:5 4" in html
            again = run([sys.executable, str(FINALIZER), str(case)])
            assert again.returncode == 0, again.stdout + again.stderr
            assert [sha(path) for path in outputs] == hashes

        no_diagram = HELPERS.build_case(root / "no-diagram")
        no_diagram_result = run([sys.executable, str(FINALIZER), str(no_diagram)])
        assert no_diagram_result.returncode == 0, no_diagram_result.stdout + no_diagram_result.stderr
        no_diagram_html = (no_diagram / "report.html").read_text(encoding="utf-8")
        assert "mermaid@11.16.1" not in no_diagram_html
        assert '<section class="report-diagrams"' not in no_diagram_html

        hostile = build_case(root / "hostile", "flow")
        hostile_findings_path = hostile / "data" / "findings.json"
        hostile_draft_path = hostile / "data" / "report-draft.json"
        hostile_findings = json.loads(hostile_findings_path.read_text(encoding="utf-8"))
        hostile_connection = {
            "from": 'Bad " ] --> injected[',
            "to": "Recipient\nclick injected \"https://evil.example\"",
            "relationship": "paid | ` <script>",
        }
        hostile_findings["connections"] = [hostile_connection]
        write_json(hostile_findings_path, hostile_findings)
        hostile_draft = json.loads(hostile_draft_path.read_text(encoding="utf-8"))
        hostile_draft["diagrams"][0]["connections"] = [hostile_connection]
        hostile_draft["diagrams"][0]["focal_entities"] = [hostile_connection["to"]]
        hostile_draft["diagrams"][0]["title"] = 'Hostile; click injected "https://evil.example"'
        hostile_draft["diagrams"][0]["caption"] = "Description\n%%{init: { securityLevel: loose }}%%"
        write_json(hostile_draft_path, hostile_draft)
        hostile_result = run([sys.executable, str(FINALIZER), str(hostile)])
        assert hostile_result.returncode == 0, hostile_result.stdout + hostile_result.stderr
        hostile_html = (hostile / "report.html").read_text(encoding="utf-8")
        assert "click injected" in hostile_html  # literal label text is retained.
        assert "\n  click injected" not in hostile_html
        assert "\nclick injected" not in hostile_html
        hostile_mermaid = hostile_html.split('<pre class="mermaid"', 1)[1].split("</pre>", 1)[0]
        assert "%%{" not in hostile_mermaid
        assert 'class="mermaid"' in hostile_html

        invalid_selector = build_case(root / "invalid-selector", "flow")
        draft_path = invalid_selector / "data" / "report-draft.json"
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft["diagrams"][0]["connections"][0]["relationship"] = "invented"
        write_json(draft_path, draft)
        expect_invalid(invalid_selector, "unknown in findings.json.connections")

        raw_mermaid = build_case(root / "raw-mermaid", "flow")
        draft_path = raw_mermaid / "data" / "report-draft.json"
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft["diagrams"][0]["mermaid"] = "flowchart LR"
        write_json(draft_path, draft)
        expect_invalid(raw_mermaid, "unknown field")

        ambiguous_selector = build_case(root / "ambiguous-selector", "flow")
        findings_path = ambiguous_selector / "data" / "findings.json"
        findings = json.loads(findings_path.read_text(encoding="utf-8"))
        findings["connections"].append(dict(findings["connections"][0]))
        write_json(findings_path, findings)
        expect_invalid(ambiguous_selector, "ambiguous in findings.json.connections")

        over_budget = build_case(root / "over-budget", "flow")
        findings_path = over_budget / "data" / "findings.json"
        draft_path = over_budget / "data" / "report-draft.json"
        chain = [
            {"from": f"Entity {index}", "to": f"Entity {index + 1}", "relationship": "transferred_to"}
            for index in range(9)
        ]
        findings = json.loads(findings_path.read_text(encoding="utf-8"))
        findings["connections"] = chain
        write_json(findings_path, findings)
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft["diagrams"][0]["connections"] = chain
        draft["diagrams"][0]["focal_entities"] = ["Entity 9"]
        write_json(draft_path, draft)
        expect_invalid(over_budget, "has 10 nodes; limit is 9")

        hierarchy_cycle = build_case(root / "hierarchy-cycle", "hierarchy")
        findings_path = hierarchy_cycle / "data" / "findings.json"
        draft_path = hierarchy_cycle / "data" / "report-draft.json"
        findings = json.loads(findings_path.read_text(encoding="utf-8"))
        cycle = [
            {"from": "Parent", "to": "Child", "relationship": "owns"},
            {"from": "Child", "to": "Parent", "relationship": "controls"},
        ]
        findings["connections"] = cycle
        write_json(findings_path, findings)
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft["diagrams"][0]["connections"] = cycle
        write_json(draft_path, draft)
        expect_invalid(hierarchy_cycle, "hierarchy cannot contain a directed cycle")

        invalid_loop = build_case(root / "invalid-loop", "loop")
        findings_path = invalid_loop / "data" / "findings.json"
        draft_path = invalid_loop / "data" / "report-draft.json"
        findings = json.loads(findings_path.read_text(encoding="utf-8"))
        branched = [
            {"from": "A", "to": "B", "relationship": "paid"},
            {"from": "B", "to": "A", "relationship": "returned"},
            {"from": "A", "to": "C", "relationship": "also_paid"},
        ]
        findings["connections"] = branched
        write_json(findings_path, findings)
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft["diagrams"][0]["connections"] = branched
        write_json(draft_path, draft)
        expect_invalid(invalid_loop, "requires one simple directed cycle")

    print("report diagrams: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
