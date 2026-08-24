#!/usr/bin/env python3
"""Focused contract for Spotlight's importable portable resolver."""
from __future__ import annotations

import fcntl

import importlib
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXED_APPROVAL = {
    "approved_by": "journalist:fixture",
    "approved_at": "2026-08-23T12:00:00Z",
}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_execution_outputs(case: Path, *, include_gate1_summaries: bool) -> None:
    write_json(case / "data/findings.json", {"schema_version": "1.0", "findings": []})
    write_json(case / "data/fact-check.json", {"schema_version": "1.0", "claims": []})
    write_json(
        case / "data/evidence-bundle.json",
        {"schema_version": "1.0", "items": []},
    )
    write_json(
        case / "data/investigation-log.json",
        {"schema_version": "1.0", "cycles": []},
    )
    if include_gate1_summaries:
        (case / "summary.md").write_text("# Mature fixture\n", encoding="utf-8")
        write_json(case / "data/summary.json", {"schema_version": "1.0"})


def byte_snapshot(case: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(case).as_posix(): None if path.is_dir() else path.read_bytes()
        for path in sorted(case.rglob("*"))
    }

@contextmanager
def portable_module():
    sys.path.insert(0, str(SCRIPTS))
    prior_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "spotlight_orchestration" or name.startswith("spotlight_orchestration.")
    }
    for name in prior_modules:
        sys.modules.pop(name, None)
    try:
        yield importlib.import_module("spotlight_orchestration")
    finally:
        for name in tuple(sys.modules):
            if name == "spotlight_orchestration" or name.startswith("spotlight_orchestration."):
                sys.modules.pop(name, None)
        sys.modules.update(prior_modules)
        sys.path.remove(str(SCRIPTS))


class PortableResolverContract(unittest.TestCase):
    def make_case(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory(prefix="spotlight-portable-resolver-")
        case = Path(temporary.name) / "offline-case"
        (case / "data").mkdir(parents=True)
        return temporary, case

    def test_resolve_preserves_every_byte_of_a_mature_case(self) -> None:
        temporary, case = self.make_case()
        self.addCleanup(temporary.cleanup)
        (case / "brief-directions.txt").write_text(
            "Verify the mature offline fixture.\n", encoding="utf-8"
        )
        write_json(case / "data/methodology.json", {"schema_version": "1.0"})

        with portable_module() as orchestration:
            orchestration.approve(case, "methodology", **FIXED_APPROVAL)
            write_execution_outputs(case, include_gate1_summaries=True)
            before = byte_snapshot(case)
            resolution = orchestration.resolve(case)

        self.assertEqual(byte_snapshot(case), before)
        self.assertEqual(resolution["phase"], "gate1_approval")
        self.assertEqual(resolution["status"], "pending")
        self.assertEqual(resolution["owner"], "phase-gate1")
        self.assertIn("Gate 1 approval", resolution["missing"])
        self.assertIsInstance(resolution["attempts"], dict)
        self.assertIn("resume", resolution)

    def test_execution_completion_selects_gate1_to_author_its_summaries(self) -> None:
        temporary, case = self.make_case()
        self.addCleanup(temporary.cleanup)
        (case / "brief-directions.txt").write_text(
            "Verify the offline fixture.\n", encoding="utf-8"
        )
        write_json(case / "data/methodology.json", {"schema_version": "1.0"})

        with portable_module() as orchestration:
            orchestration.approve(case, "methodology", **FIXED_APPROVAL)
            write_execution_outputs(case, include_gate1_summaries=False)
            resolution = orchestration.resolve(case)

        self.assertEqual(resolution["phase"], "gate1_approval")
        self.assertEqual(resolution["owner"], "phase-gate1")
        self.assertCountEqual(
            resolution["missing"],
            ["summary.md", "data/summary.json"],
        )

    def test_resolve_rejects_a_case_outside_the_authorized_root(self) -> None:
        temporary, case = self.make_case()
        self.addCleanup(temporary.cleanup)
        authorized_root = Path(temporary.name) / "cases"
        authorized_root.mkdir()

        with portable_module() as orchestration:
            with self.assertRaises(orchestration.OrchestrationError):
                orchestration.resolve(case, authorized_cases_root=authorized_root)

    def test_resolve_rejects_a_preexisting_symlinked_data_directory(self) -> None:
        temporary, case = self.make_case()
        self.addCleanup(temporary.cleanup)
        real_data = case / "real-data"
        (case / "data").rename(real_data)
        (case / "data").symlink_to(real_data, target_is_directory=True)

        with portable_module() as orchestration:
            with self.assertRaises(orchestration.OrchestrationError):
                orchestration.resolve(case, authorized_cases_root=Path(temporary.name))

    def test_transaction_flocks_and_yields_the_anchored_data_descriptor(self) -> None:
        temporary, case = self.make_case()
        self.addCleanup(temporary.cleanup)

        with portable_module():
            storage = importlib.import_module("spotlight_orchestration.storage")
            contender = os.open(case / "data", os.O_RDONLY)
            try:
                with storage.transaction(case) as descriptor:
                    anchored = os.fstat(descriptor)
                    visible = os.stat(case / "data", follow_symlinks=False)
                    self.assertEqual(
                        (anchored.st_dev, anchored.st_ino),
                        (visible.st_dev, visible.st_ino),
                    )
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                fcntl.flock(contender, fcntl.LOCK_UN)
                os.close(contender)



if __name__ == "__main__":
    unittest.main(verbosity=2)
