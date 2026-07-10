#!/usr/bin/env python3
"""Functional contract for the local launcher's model-identity probe."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts" / "verify-openai-model.py"


def run_probe(base_url: str, expected: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PROBE), base_url, expected],
        text=True,
        capture_output=True,
        check=False,
    )


def main() -> int:
    if not PROBE.is_file():
        print(f"FAIL: missing model identity probe: {PROBE}", file=sys.stderr)
        return 1

    model_id = "spotlight-12b-model.gguf"
    with tempfile.TemporaryDirectory(prefix="spotlight-model-probe.") as tmp:
        root = Path(tmp)
        (root / "v1").mkdir()
        (root / "v1" / "models").write_text(
            json.dumps({"object": "list", "data": [{"id": model_id}]}),
            encoding="utf-8",
        )
        base = root.as_uri()

        matching = run_probe(base, model_id)
        assert matching.returncode == 0, matching.stderr

        wrong = run_probe(base, "spotlight-26b-other.gguf")
        assert wrong.returncode == 2, (wrong.returncode, wrong.stderr)
        assert model_id in wrong.stderr and "spotlight-26b-other.gguf" in wrong.stderr

        unavailable = run_probe((root / "missing").as_uri(), model_id)
        assert unavailable.returncode != 0
        assert "could not query" in unavailable.stderr

    print("local model identity contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
