#!/usr/bin/env python3
"""The newsroom Submit package (scripts/newsroom-package.py) and the newsroom
review pages.

The package carries the cited evidence only, with hashes that match its
evidence manifest; raw research stays behind; a file changed after it was
cited stops the build; the submission is recorded only after the member
requested ingestion, under a recorded intent; the pages show the newsroom's check flags and a refused
submission's reasons. Offline cases from tests/fixtures/review_case.py.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import schema_check  # noqa: E402

PACKAGE = ROOT / "scripts" / "newsroom-package.py"
_SPEC = importlib.util.spec_from_file_location("review_gate_check", ROOT / "tests" / "review-gate-check.py")
GATES = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(GATES)


class NewsroomPackage(GATES.CaseFixture):
    def setUp(self) -> None:
        super().setUp()
        self.config = self.root / "spotlight-config.json"
        self.config.write_text(json.dumps({"knowledge_destination": {"mode": "newsroom"}}), encoding="utf-8")
        self.env = {**os.environ, "SPOTLIGHT_CONFIG": str(self.config)}

    def run_seam(self, *args: str, expect: int = 0, script: Path = PACKAGE) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, expect, result.stderr or result.stdout)
        return result

    def build_package(self, case: Path, expect: int = 0) -> Any:
        """The built package (written to --out), or the refused run."""
        out = self.root / f"{case.name}-package.json"
        result = self.run_seam("build", str(case), "--out", str(out), expect=expect)
        return json.loads(out.read_text(encoding="utf-8")) if expect == 0 else result

    def case(self, name: str) -> Path:
        case = self.build(name, "ingest", "--clean", "--activated")
        # Research material that nothing cites, and a lane folder a passage
        # points into: neither may travel.
        lane = case / "research" / "swarm" / "ownership-web"
        lane.mkdir(parents=True)
        (lane / "leads.jsonl").write_text('{"lead_id": "L1"}\n', encoding="utf-8")
        (case / "data" / "leads.json").write_text('{"leads": []}\n', encoding="utf-8")
        passages = json.loads((case / "data" / "source-expressions.json").read_text(encoding="utf-8"))
        extra = json.loads(json.dumps(passages["expressions"][0]))
        extra.update({"id": "SX2", "anchor_ref": {"path": "research/swarm/ownership-web/leads.jsonl", "line_start": 1, "line_end": 1}})
        extra["finding_links"][0]["relation"] = "context"
        passages["expressions"].append(extra)
        # Case state cited as if it were evidence, and a case variant of the
        # lane folder (the same folder on a case-insensitive disk).
        for sx, path in (("SX3", "data/orchestration.json"), ("SX4", "research/Swarm/ownership-web/leads.jsonl")):
            cited = json.loads(json.dumps(extra))
            cited.update({"id": sx, "anchor_ref": {"path": path, "line_start": 1, "line_end": 1},
                          "anchor_sha256": hashlib.sha256((case / path).read_bytes()).hexdigest() if (case / path).is_file() else None})
            passages["expressions"].append(cited)
        (case / "data" / "source-expressions.json").write_text(json.dumps(passages), encoding="utf-8")
        return case

    def test_the_package_carries_only_cited_evidence_with_hashes_matching_its_manifest(self) -> None:
        case = self.case("cited")
        built = self.build_package(case)
        package, files = built["package"], built["files"]
        manifest = package["evidence_manifest"]

        sent = sorted(f["path"] for f in files)
        self.assertEqual(sent, ["research/filing.bin", "research/filing.txt"])
        self.assertEqual([entry["path"] for entry in manifest["files"]], sent)
        for file, entry in zip(sorted(files, key=lambda f: f["path"]), manifest["files"]):
            data = base64.b64decode(file["content_base64"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
            self.assertEqual(file["sha256"], entry["sha256"])
            self.assertEqual(data, (case / file["path"]).read_bytes())
        filing = next(entry for entry in manifest["files"] if entry["path"] == "research/filing.txt")
        self.assertEqual(sorted(filing["roles"]), ["contextual", "supporting"])
        # Raw research stays behind: uncited files, the lane folder, the leads.
        text = json.dumps(built)
        for stays in ("primary-record.txt", "corroboration.txt", "L1", '"leads": []'):
            self.assertNotIn(stays, text)
        self.assertEqual(manifest["omissions"][0], {"path": "research/swarm/ownership-web/leads.jsonl", "expression_id": "SX2",
                                                    "reason": "research-phase material stays on this computer"})
        # A passage whose anchor stays behind stays behind too, recorded by id.
        self.assertEqual([e["id"] for e in package["passages"]["expressions"]], ["SX1"])
        self.assertEqual([(o["expression_id"], o["reason"]) for o in manifest["omissions"] if o["reason"].startswith("the passage")],
                         [(sx, "the passage stays behind: its anchor file did not travel") for sx in ("SX2", "SX3", "SX4")])
        self.assertEqual(sorted({o["path"] for o in manifest["omissions"][1:]}),
                         ["data/orchestration.json", "research/Swarm/ownership-web/leads.jsonl", "research/swarm/ownership-web/leads.jsonl"])
        self.assertEqual(package["journalist_input_units"], [])
        # The package is what the published contract says the server receives.
        self.assertEqual(schema_check.load(ROOT / "schemas" / "editorial-mcp" / "package.schema.json").errors(package), [])
        # Same case, same key: the idempotency key is stable.
        self.assertEqual(self.build_package(case)["package_sha256"], built["package_sha256"])

    def test_a_cited_file_changed_after_it_was_declared_stops_the_build(self) -> None:
        case = self.case("changed")
        (case / "research" / "filing.txt").write_text("Someone edited the filing.\n", encoding="utf-8")
        refused = self.build_package(case, expect=2)
        self.assertIn("research/filing.txt no longer matches its declared sha256", refused.stderr)

    def test_a_windows_absolute_path_never_travels(self) -> None:
        case = self.make_case() if hasattr(self, "make_case") else None
        mod = self.module() if hasattr(self, "module") else None
        if mod is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location("newsroom_package", ROOT / "scripts" / "newsroom-package.py")
            mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        for rel in ("C:/Users/alice/case/evidence/file.pdf", "C:evidence/file.pdf", "//host/share/evidence/f.pdf"):
            _, path, why = mod._case_file(Path("/tmp/no-such-case"), rel)
            self.assertIsNone(path, rel)
            self.assertEqual(why, "the path leads outside the case", rel)

    def test_the_package_cap_refuses_before_reading_the_file_that_would_cross_it(self) -> None:
        case = self.case("capped").resolve()
        spec = importlib.util.spec_from_file_location("newsroom_package_cap", PACKAGE)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        cited = [case / "research" / "filing.txt", case / "research" / "filing.bin"]
        mod.MAX_PACKAGE_BYTES = max(path.stat().st_size for path in cited)
        read: list[Path] = []
        original = Path.read_bytes

        def counting(path: Path) -> bytes:
            if path.resolve() in [c.resolve() for c in cited]:
                read.append(path)
            return original(path)

        with mock.patch.dict(os.environ, {"SPOTLIGHT_CONFIG": str(self.config)}), mock.patch.object(Path, "read_bytes", counting):
            with self.assertRaisesRegex(mod.Refused, "larger than"):
                mod.build(case)
        self.assertEqual(len(read), 1, read)

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_a_cited_path_through_a_symlinked_evidence_folder_never_travels(self) -> None:
        spec = importlib.util.spec_from_file_location("newsroom_package", PACKAGE)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        case = self.root / "linked"
        (case / "data").mkdir(parents=True)
        (case / "data" / "editorial-case.json").write_text('{"case_ref": "private"}', encoding="utf-8")
        (case / "research").mkdir()
        (case / "evidence").symlink_to(case / "data")
        (case / "research" / "inner").symlink_to(case / "data")
        for rel in ("evidence/editorial-case.json", "research/inner/editorial-case.json"):
            _, path, why = mod._case_file(case, rel)
            self.assertIsNone(path, rel)
            self.assertEqual(why, "the path passes through a symlink", rel)

    def test_an_rlm_distilled_lead_file_never_travels_as_evidence(self) -> None:
        spec = importlib.util.spec_from_file_location("newsroom_package_rlm", PACKAGE)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        case = self.root / "rlm"
        (case / "research").mkdir(parents=True)
        (case / "research" / "leads.md").write_text("# RLM-distilled leads from filing.pdf\n- Acme paid Doe\n", encoding="utf-8")
        _, path, why = mod._case_file(case, "research/leads.md")
        self.assertIsNone(path)
        self.assertIn("RLM-distilled lead file", why)

    def test_case_state_present_but_unreadable_refuses_instead_of_meaning_absent(self) -> None:
        case = self.case("unreadable-state")
        submission = case / "data" / "newsroom-submission.json"
        submission.write_text("[]", encoding="utf-8")
        self.assertIn("newsroom-submission.json is not a readable JSON object", self.run_seam("pending", str(case), expect=2).stderr)
        submission.unlink()
        passages = case / "data" / "source-expressions.json"
        passages.write_text('"not an object"', encoding="utf-8")
        self.assertIn("source-expressions.json is not a JSON object", self.build_package(case, expect=2).stderr)
        passages.unlink()
        self.assertIn("source-expressions.json is missing", self.build_package(case, expect=2).stderr)

    def test_a_solo_install_builds_nothing(self) -> None:
        case = self.case("solo")
        self.config.write_text(json.dumps({"knowledge_destination": {"mode": "solo"}}), encoding="utf-8")
        self.assertIn("not in newsroom mode", self.build_package(case, expect=2).stderr)

    def run_with(self, *args: str, payload: dict, expect: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(PACKAGE), *args], input=json.dumps(payload),
                                capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, expect, result.stderr or result.stdout)
        return result

    def request_ingest(self, case: Path) -> None:
        seam = ROOT / "scripts" / "spotlight-orchestration.py"
        view = json.loads(self.run_seam("review-view", "ingest", str(case), script=seam).stdout)
        nonce = json.loads(self.run_seam("issue-review-nonce", "ingest", str(case), script=seam).stdout)["nonce"]
        self.run_seam("decide-ingest", "requested", "--nonce", nonce, "--digest", view["digest"], *GATES.DECIDER, str(case), script=seam)

    def test_a_submission_starts_only_after_the_member_requested_ingestion_and_is_recorded_under_its_intent(self) -> None:
        case = self.build("requested", "ingest", "--clean", "--activated")
        key, other = "a" * 64, "b" * 64
        receipts = {"receipts": {"submission-sub-1.json": {"tool": "submit_case_review"}}, "review_state": "pending"}
        complete = ("complete", str(case), "--package-sha256", key, "--submission-id", "sub-1", "--review-id", "rev-1")
        # No member decision on the ingest page yet: nothing starts.
        self.assertIn("ingestion has not been requested", self.run_seam("begin", str(case), "--package-sha256", key, expect=2).stderr)
        self.request_ingest(case)
        # No recorded intent: nothing is recorded as submitted.
        self.assertIn("no submission of this package is pending", self.run_with(*complete, payload=receipts, expect=2).stderr)
        self.assertFalse(json.loads(self.run_seam("pending", str(case)).stdout)["pending"])
        self.run_seam("begin", str(case), "--package-sha256", key)
        self.assertEqual(json.loads(self.run_seam("pending", str(case)).stdout), {"pending": True, "idempotency_key": key})
        # A resumed start with the same key goes on; a different package waits.
        self.assertTrue(json.loads(self.run_seam("begin", str(case), "--package-sha256", key).stdout)["resumed"])
        self.assertIn("different version of this case is pending", self.run_seam("begin", str(case), "--package-sha256", other, expect=2).stderr)
        self.run_with(*complete, payload=receipts)
        marker = json.loads((case / "data" / "ingestion.json").read_text(encoding="utf-8"))
        self.assertEqual((marker["status"], marker["destination"], marker["submission_id"]), ("completed", "newsroom", "sub-1"))
        submission = json.loads((case / "data" / "newsroom-submission.json").read_text(encoding="utf-8"))
        self.assertEqual((submission["state"], submission["idempotency_key"]), ("submitted", key))
        self.assertEqual(json.loads((case / "receipts" / "submission-sub-1.json").read_text(encoding="utf-8")), {"tool": "submit_case_review"})
        seam = ROOT / "scripts" / "spotlight-orchestration.py"
        status = json.loads(self.run_seam("status", "--json", str(case), script=seam).stdout)
        self.assertEqual(status["ingest"], {"state": "completed", "resume_at": "seal"})
        # Recording the same outcome again changes nothing.
        self.assertTrue(json.loads(self.run_with(*complete, payload=receipts).stdout)["already_recorded"])

    def test_a_screenshot_that_is_the_only_original_is_checked_against_its_declared_hash(self) -> None:
        case = self.case("screenshot")
        bundle_path = case / "data" / "evidence-bundle.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        item = bundle["items"][0]
        item["screenshot_path"] = item.pop("raw_path")
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        self.build_package(case)
        (case / "research" / "filing.bin").write_bytes(b"a different capture\n")
        refused = self.build_package(case, expect=2)
        self.assertIn("research/filing.bin no longer matches its declared sha256 (screenshot_path)", refused.stderr)

    def test_a_recorded_original_that_is_missing_hands_the_hash_check_to_the_next_one(self) -> None:
        case = self.case("missing-download")
        bundle_path = case / "data" / "evidence-bundle.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        bundle["items"][0]["downloaded_document_path"] = "research/never-downloaded.pdf"
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        self.build_package(case)
        (case / "research" / "filing.bin").write_bytes(b"a different capture\n")
        refused = self.build_package(case, expect=2)
        self.assertIn("research/filing.bin no longer matches its declared sha256 (raw_path)", refused.stderr)

    def test_an_install_config_present_but_malformed_refuses_instead_of_meaning_solo(self) -> None:
        case = self.case("malformed-config")
        for content, reason in (({"knowledge_destination": "newsroom"}, "knowledge_destination must be an object"),
                                ({"knowledge_destination": {"mode": None}}, 'knowledge_destination.mode must be "solo" or "newsroom"'),
                                ("{not json", "the install config cannot be read")):
            self.config.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
            self.assertIn(reason, self.build_package(case, expect=2).stderr, content)

    def test_a_screenshot_beside_a_raw_capture_is_checked_against_its_anchor_hash(self) -> None:
        case = self.case("anchored-screenshot")
        bundle_path = case / "data" / "evidence-bundle.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        bundle["items"][0]["screenshot_path"] = "research/filing.png"
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        (case / "research" / "filing.png").write_bytes(b"the capture as cited\n")
        passages_path = case / "data" / "source-expressions.json"
        passages = json.loads(passages_path.read_text(encoding="utf-8"))
        passages["expressions"][0]["anchor_ref"] = {"path": "research/filing.png"}
        passages["expressions"][0]["anchor_sha256"] = hashlib.sha256(b"the capture as cited\n").hexdigest()
        passages_path.write_text(json.dumps(passages), encoding="utf-8")
        self.build_package(case)
        (case / "research" / "filing.png").write_bytes(b"an edited capture\n")
        refused = self.build_package(case, expect=2)
        self.assertIn("research/filing.png no longer matches its declared sha256 (anchor)", refused.stderr)

    def test_the_pages_show_the_newsroom_flags_and_a_refused_submissions_reasons(self) -> None:
        case = self.case("pages")
        seam = ROOT / "scripts" / "spotlight-orchestration.py"
        (case / "data" / "newsroom-checks.json").write_text(json.dumps({"schema_version": "1.0", "checks": {
            "report": {"kind": "report", "state": "unavailable", "reason": "the decision model is down", "flags": [], "input_sha256": "b" * 64, "checked_at": "x"},
        }}), encoding="utf-8")
        (case / "data" / "newsroom-submission.json").write_text(json.dumps({"schema_version": "1.0", "state": "blocked",
            "integrity_failures": [{"check": "file_hash", "reason": "research/filing.txt does not match the manifest hash"}]}), encoding="utf-8")
        ingest = json.loads(self.run_seam("review-view", "ingest", str(case), script=seam).stdout)
        self.assertEqual(ingest["content"]["submission"]["reasons"], ["research/filing.txt does not match the manifest hash"])
        report = json.loads(self.run_seam("review-view", "report", str(case), script=seam).stdout)
        self.assertEqual(report["content"]["newsroom_checks"], {"state": "unavailable", "reason": "the decision model is down", "flags": []})


if __name__ == "__main__":
    unittest.main()
