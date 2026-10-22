#!/usr/bin/env python3
"""The single passage writer and the case-contract writers.

Covers the source-expression pilot's blocker 4 (interrupted and concurrent
producer writes) with fault injection: a write that fails mid-publish leaves
the previous file byte for byte, and concurrent writers never lose an
update.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


WRITER = load("write_source_expressions", SCRIPTS / "write-source-expressions.py")
CASES = load("validate_case_check", ROOT / "tests" / "validate-case-check.py")
from spotlight_orchestration import case_writer  # noqa: E402
from spotlight_orchestration.contract import OrchestrationError  # noqa: E402

PASSAGES = [f"Passage number {i}." for i in range(8)]


def make_case(root: Path) -> Path:
    """An activated case born under the new default: a born event, no
    artifact hashes until its gate approval binds them."""
    case = root / "case"
    CASES.write_activated_case(case)
    contract_path = case / "data" / "case-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["activation_events"] = [{
        "event_id": "born-test", "kind": "born", "activated_contract_version": "1.1",
        "activated_at": "2026-10-22T12:00:00Z", "tool_version": "test"}]
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    passages = case / "research" / "passages.txt"
    passages.write_text("\n".join(PASSAGES) + "\n", encoding="utf-8")
    bundle_path = case / "data" / "evidence-bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    item = json.loads(json.dumps(bundle["items"][0]))
    item.update({"id": "E2", "raw_path": "research/passages.txt",
                 "sha256": hashlib.sha256(passages.read_bytes()).hexdigest()})
    item.pop("text_derivatives", None)
    bundle["items"].append(item)
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    return case


def proposal(index: int, finding: str = "F1", relation: str = "context") -> dict:
    line = 1 + index
    return {"expressions": [{
        "id": f"SXW{index}",
        "text": PASSAGES[index],
        "anchor_ref": {"path": "research/passages.txt", "line_start": line, "line_end": line},
        "original_evidence_bundle_id": "E2",
        "language": "en",
        "finding_links": [{"finding_id": finding, "relation": relation}],
    }]}


def run_writer(case: Path, body: dict, actor: str = "investigator") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "write-source-expressions.py"), str(case), "--actor", actor, "--cycle", "1"],
        input=json.dumps(body), capture_output=True, text=True,
    )


def expressions(case: Path) -> dict:
    return json.loads((case / "data" / "source-expressions.json").read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        case = make_case(root)
        target = case / "data" / "source-expressions.json"

        # The writer computes fingerprints and hashes and merges by id.
        result = run_writer(case, proposal(0))
        require(result.returncode == 0, result.stderr)
        added = next(e for e in expressions(case)["expressions"] if e["id"] == "SXW0")
        require(len(added["expression_fingerprint"]) == 64 and added["created_by"] == "investigator", "writer did not build the record")
        require(added["anchor_sha256"] == hashlib.sha256((case / "research/passages.txt").read_bytes()).hexdigest(), "anchor hash not computed")
        again = run_writer(case, proposal(0, relation="context"), actor="fact-checker")
        require(again.returncode == 0 and next(e for e in expressions(case)["expressions"] if e["id"] == "SXW0")["created_by"] == "investigator",
                "re-proposing an expression changed its author")
        changed = proposal(0)
        changed["expressions"][0]["text"] = "A different passage."
        changed["expressions"][0]["anchor_ref"] = {"path": "research/passages.txt", "line_start": 2, "line_end": 2}
        before = target.read_bytes()
        refused = run_writer(case, changed)
        require(refused.returncode == 2 and "different passage" in refused.stderr and target.read_bytes() == before,
                "a changed passage under an existing id was accepted")
        forged = proposal(1)
        forged["expressions"][0]["expression_fingerprint"] = "0" * 64
        require(run_writer(case, forged).returncode == 2, "a caller-supplied fingerprint was accepted")
        wrong_text = proposal(1)
        wrong_text["expressions"][0]["text"] = "Not what line 2 says."
        require(run_writer(case, wrong_text).returncode == 2 and target.read_bytes() == before,
                "a passage that does not match its locator was written")

        # Interrupted write: the publish fails after staging; the previous file
        # stays byte for byte and no temporary file is left behind.
        before = target.read_bytes()
        real_replace = case_writer.os.replace

        def failing_replace(*args, **kwargs):
            raise OSError("injected failure during publish")

        case_writer.os.replace = failing_replace
        try:
            WRITER.write(case, proposal(2), "investigator", 1)
            raise AssertionError("an interrupted publish reported success")
        except (OSError, OrchestrationError):
            pass
        finally:
            case_writer.os.replace = real_replace
        require(target.read_bytes() == before, "an interrupted write changed the file")
        leftovers = [p.name for p in (case / "data").iterdir() if p.name.startswith(".source-expressions.json.")]
        require(not leftovers, f"an interrupted write left {leftovers}")

        # Concurrent writers: each reads its proposal from a file, so all six
        # start at once and contend for the case lock; every update survives.
        files = []
        for i in range(2, 8):
            path = root / f"proposal-{i}.json"
            path.write_text(json.dumps(proposal(i)), encoding="utf-8")
            files.append(path)
        procs = [
            subprocess.Popen(
                [sys.executable, str(SCRIPTS / "write-source-expressions.py"), str(case),
                 "--actor", "investigator" if i % 2 else "fact-checker", "--cycle", "1", "--file", str(path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for i, path in zip(range(2, 8), files)
        ]
        for i, proc in zip(range(2, 8), procs):
            _, err = proc.communicate()
            require(proc.returncode == 0, f"concurrent writer {i} failed: {err}")
        ids = {e["id"] for e in expressions(case)["expressions"]}
        require({"SXW0"} | {f"SXW{i}" for i in range(2, 8)} <= ids, f"a concurrent write was lost: {sorted(ids)}")
        validation = subprocess.run([sys.executable, str(SCRIPTS / "validate-case.py"), str(case)], capture_output=True, text=True)
        require(validation.returncode == 0, validation.stdout + validation.stderr)

        # A two-file publish that fails on its second file puts the first
        # back: findings and passages never disagree after a failure.
        findings_path = case / "data" / "findings.json"
        original_findings, original_passages = findings_path.read_bytes(), target.read_bytes()
        changed_claim = json.loads(original_findings)
        changed_claim["findings"][0]["claim"] = "Acme paid Doe in full."
        findings_path.write_text(json.dumps(changed_claim), encoding="utf-8")
        staged_findings = findings_path.read_bytes()
        calls = {"n": 0}

        def fail_second_replace(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("injected failure on the second file")
            return real_replace(*args, **kwargs)

        case_writer.os.replace = fail_second_replace
        try:
            WRITER.fingerprint_findings(case)
            raise AssertionError("a failed two-file publish reported success")
        except (OSError, OrchestrationError):
            pass
        finally:
            case_writer.os.replace = real_replace
        require(findings_path.read_bytes() == staged_findings and target.read_bytes() == original_passages,
                "a failed two-file publish left one file changed")
        findings_path.write_bytes(original_findings)

        # Finding fingerprints come from code, and a changed claim refreshes
        # its passage links in the same write.
        findings_path = case / "data" / "findings.json"
        findings = json.loads(findings_path.read_text(encoding="utf-8"))
        findings["findings"][0]["claim"] = "Acme paid Doe twice."
        findings_path.write_text(json.dumps(findings), encoding="utf-8")
        fp = subprocess.run([sys.executable, str(SCRIPTS / "write-source-expressions.py"), str(case), "--fingerprint-findings"],
                            capture_output=True, text=True)
        require(fp.returncode == 0 and '"findings_changed": 1' in fp.stdout, fp.stdout + fp.stderr)
        doc = expressions(case)
        errors = CASES.vc.validate_source_expressions(case, doc, json.loads(findings_path.read_text(encoding="utf-8")), None, None, enforce_positive_verdicts=False)
        require(not [e for e in errors if "fingerprint" in e], f"stale fingerprints after --fingerprint-findings: {errors}")
        findings["findings"][0]["claim"] = "Acme paid Doe."
        findings_path.write_text(json.dumps(findings), encoding="utf-8")
        subprocess.run([sys.executable, str(SCRIPTS / "write-source-expressions.py"), str(case), "--fingerprint-findings"], check=True, capture_output=True)

        # Locator re-validation (pilot blocker 2): an edited stored artifact
        # shows up as a failed locator.
        report = json.loads(subprocess.run([sys.executable, str(SCRIPTS / "revalidate-locators.py"), str(root), "--json"],
                                           capture_output=True, text=True, check=True).stdout)
        require(report["cases"] == 1 and report["revalidation_rate"] == 1.0, report)
        passages_path = case / "research" / "passages.txt"
        original_passages = passages_path.read_bytes()
        passages_path.write_text("rewritten\n" * 8, encoding="utf-8")
        report = json.loads(subprocess.run([sys.executable, str(SCRIPTS / "revalidate-locators.py"), str(root), "--json"],
                                           capture_output=True, text=True, check=True).stdout)
        require(report["revalidation_rate"] is not None and report["revalidation_rate"] < 1.0, report)
        (root / "unreadable" / "data").mkdir(parents=True)
        (root / "unreadable" / "data" / "case-contract.json").write_text("{}", encoding="utf-8")
        report = json.loads(subprocess.run([sys.executable, str(SCRIPTS / "revalidate-locators.py"), str(root), "--json"],
                                           capture_output=True, text=True, check=True).stdout)
        require(report["revalidation_rate"] is None and report["skipped_cases"], "an unreadable case was reported as stable")
        import shutil
        shutil.rmtree(root / "unreadable")
        passages_path.write_bytes(original_passages)

        # Born activation and the gate-approval binding.
        born = root / "born"
        (born / "data").mkdir(parents=True)
        (born / "data" / "methodology.json").write_text(json.dumps({"project": "born-case"}), encoding="utf-8")
        made = subprocess.run([sys.executable, str(SCRIPTS / "case-contract.py"), "activate-new", str(born)], capture_output=True, text=True)
        require(made.returncode == 0, made.stderr)
        contract = json.loads((born / "data" / "case-contract.json").read_text(encoding="utf-8"))
        require([e["kind"] for e in contract["activation_events"]] == ["born"] and contract["project"] == "born-case", contract)
        require(not CASES.vc.validate_case_contract(contract), CASES.vc.validate_case_contract(contract))
        twice = subprocess.run([sys.executable, str(SCRIPTS / "case-contract.py"), "activate-new", str(born)], capture_output=True, text=True)
        require(twice.returncode == 2, "a case was activated twice")
        # A run that stopped after the contract but before the passages file
        # (an older publish order) is finished, not refused.
        (born / "data" / "source-expressions.json").unlink()
        resumed = subprocess.run([sys.executable, str(SCRIPTS / "case-contract.py"), "activate-new", str(born)], capture_output=True, text=True)
        require(resumed.returncode == 0 and (born / "data" / "source-expressions.json").is_file(), resumed.stderr)
        pilot = root / "pilot"
        (pilot / "data").mkdir(parents=True)
        (pilot / "data" / "source-expressions.json").write_text("{}", encoding="utf-8")
        refused_pilot = subprocess.run([sys.executable, str(SCRIPTS / "case-contract.py"), "activate-new", str(pilot)], capture_output=True, text=True)
        require(refused_pilot.returncode == 2 and not (pilot / "data" / "case-contract.json").exists(), "a case with passages was re-born")
        legacy = root / "legacy"
        (legacy / "data").mkdir(parents=True)
        (legacy / "data" / "findings.json").write_text(json.dumps(CASES.valid_findings()), encoding="utf-8")
        reborn = subprocess.run([sys.executable, str(SCRIPTS / "case-contract.py"), "activate-new", str(legacy)], capture_output=True, text=True)
        require(reborn.returncode == 2 and not (legacy / "data" / "case-contract.json").exists(), "a legacy case was re-born")

        # Once the gate is approved, the artifacts must match the binding.
        # Only the gate approval binds (there is no bind command); here the
        # approval's own order is reproduced: bind, then record the approval.
        from spotlight_orchestration.case_contract import bind
        from spotlight_orchestration.storage import transaction
        with transaction(case) as descriptor:
            bind(case, descriptor)
        (case / "data" / "orchestration.json").write_text(json.dumps({"approvals": {"investigation_gate": {}}}), encoding="utf-8")
        no_cli = subprocess.run([sys.executable, str(SCRIPTS / "case-contract.py"), "bind", str(case)], capture_output=True, text=True)
        require(no_cli.returncode == 2 and "invalid choice" in no_cli.stderr, "a bind command exists")
        fresh = subprocess.run([sys.executable, str(SCRIPTS / "validate-case.py"), str(case)], capture_output=True, text=True)
        require(fresh.returncode == 0, "a freshly bound approved case does not validate: " + fresh.stdout[-1200:] + fresh.stderr[-1200:])
        require(run_writer(case, {"expressions": [], "lifecycle": [{"expression_id": "SXW7", "event": "withdrawn", "reason": "test"}]}).returncode == 0,
                "writer refused a lifecycle event")
        after = subprocess.run([sys.executable, str(SCRIPTS / "validate-case.py"), str(case)], capture_output=True, text=True)
        require(after.returncode != 0 and "approval covers" in after.stdout + after.stderr,
                "a change after approval was not caught by the binding: " + after.stdout[-800:] + after.stderr[-800:])

    print("source-expression writer and case contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
