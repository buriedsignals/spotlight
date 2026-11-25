#!/usr/bin/env python3
"""Case storage works on this platform and refuses linked directories and files.

Runs in the Windows CI job (path-held storage: Windows has no dir_fd) and in
smoke on Linux/macOS (descriptor-anchored storage). Spotlight 2.36.0 failed on
Windows at the first locked write with "case storage directory is not safely
accessible", which stopped every run at the first review page.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from spotlight_orchestration.case_writer import (  # noqa: E402
    atomic_write_files,
    open_case_directory,
    restore_files_on_error,
)
from spotlight_orchestration.contract import OrchestrationError  # noqa: E402
from spotlight_orchestration.storage import read_data_bytes, transaction  # noqa: E402


def link_directory(link: Path, target: Path) -> None:
    """A junction on Windows (needs no privilege), a symlink elsewhere."""
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


class CaseStorage(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.case = Path(self.temporary.name).resolve() / "case"
        (self.case / "data").mkdir(parents=True)

    def test_locked_writes_read_back_replace_and_roll_back(self) -> None:
        with transaction(self.case) as directory:
            atomic_write_files(self.case, {"data/a.json": b"one", "data/b.json": b"two"},
                               data_descriptor=directory)
            atomic_write_files(self.case, {"data/a.json": b"three"}, data_descriptor=directory)
            self.assertEqual(read_data_bytes(self.case, "a.json", directory), b"three")
            self.assertIsNone(read_data_bytes(self.case, "absent.json", directory))
            with self.assertRaises(RuntimeError):
                with restore_files_on_error(self.case, ("data/a.json", "data/c.json"),
                                            data_descriptor=directory):
                    atomic_write_files(self.case, {"data/a.json": b"lost", "data/c.json": b"new"},
                                       data_descriptor=directory)
                    raise RuntimeError("fail after publishing")
        self.assertEqual((self.case / "data/a.json").read_bytes(), b"three")
        self.assertFalse((self.case / "data/c.json").exists())
        leftovers = [p.name for p in (self.case / "data").iterdir() if p.name.startswith(".a.json")]
        self.assertEqual(leftovers, [])

    def test_unlocked_writes_reach_other_case_folders(self) -> None:
        with open_case_directory(self.case, ()) as root:
            root.mkdir("receipts")
        atomic_write_files(self.case, {"receipts/r.json": b"{}"})
        self.assertEqual((self.case / "receipts/r.json").read_bytes(), b"{}")

    def test_a_linked_data_directory_is_refused(self) -> None:
        elsewhere = Path(self.temporary.name).resolve() / "elsewhere"
        elsewhere.mkdir()
        (self.case / "data").rmdir()
        link_directory(self.case / "data", elsewhere)
        with self.assertRaisesRegex(OrchestrationError, "not safely accessible"):
            with transaction(self.case):
                pass
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_a_linked_output_file_is_neither_read_nor_replaced(self) -> None:
        outside = Path(self.temporary.name).resolve() / "outside.json"
        outside.write_bytes(b"outside")
        try:
            (self.case / "data/a.json").symlink_to(outside)
        except OSError as exc:  # Windows without the symlink privilege
            self.skipTest(f"cannot create a file symlink here: {exc}")
        with transaction(self.case) as directory:
            with self.assertRaises(OrchestrationError):
                read_data_bytes(self.case, "a.json", directory)
            with self.assertRaises(OrchestrationError):
                atomic_write_files(self.case, {"data/a.json": b"new"}, data_descriptor=directory)
        self.assertEqual(outside.read_bytes(), b"outside")


if __name__ == "__main__":
    unittest.main(verbosity=2)
