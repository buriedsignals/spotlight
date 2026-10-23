#!/usr/bin/env python3
"""scripts/spotlight-python picks the Spotlight interpreter every skill step runs:
SPOTLIGHT_PYTHON (absolute), else the checkout's .venv, else python3 on PATH.
Each case runs the real launcher against fake interpreters that print a tag."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "spotlight-python"


def fake_python(path: Path, tag: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho "{tag} $*"\n', encoding="utf-8")
    path.chmod(0o755)
    return path


def run(launcher: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(launcher), "scripts/x.py", "--flag"], text=True, capture_output=True, env=env, check=False)


def main() -> int:
    failures: list[str] = []

    def expect(name: str, result: subprocess.CompletedProcess[str], code: int, stdout: str = "") -> None:
        if result.returncode != code or (stdout and result.stdout.strip() != stdout):
            failures.append(f"{name}: rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}")

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        checkout = tmp / "checkout"
        launcher = checkout / "scripts" / "spotlight-python"
        launcher.parent.mkdir(parents=True)
        shutil.copy2(LAUNCHER, launcher)
        path_dir = tmp / "path"
        fake_python(path_dir / "python3", "path-python3")
        base = {"PATH": f"{path_dir}{os.pathsep}/usr/bin:/bin"}

        expect("python3 on PATH without a venv or SPOTLIGHT_PYTHON", run(launcher, base), 0, "path-python3 scripts/x.py --flag")
        fake_python(checkout / ".venv" / "bin" / "python", "venv-python")
        expect("the checkout venv before PATH", run(launcher, base), 0, "venv-python scripts/x.py --flag")
        pinned = fake_python(tmp / "pinned" / "python", "pinned-python")
        expect("SPOTLIGHT_PYTHON before the venv", run(launcher, {**base, "SPOTLIGHT_PYTHON": str(pinned)}), 0, "pinned-python scripts/x.py --flag")
        expect("a relative SPOTLIGHT_PYTHON is refused", run(launcher, {**base, "SPOTLIGHT_PYTHON": "python3"}), 127)
        expect("a missing SPOTLIGHT_PYTHON is refused, not replaced", run(launcher, {**base, "SPOTLIGHT_PYTHON": str(tmp / "missing")}), 127)

    if failures:
        for failure in failures:
            print(f"FAIL  {failure}", file=sys.stderr)
        return 1
    print("spotlight-python checks: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
