#!/usr/bin/env python3
"""A case that holds only material goes from intake to Ingest with no
investigation: a pending ingest decision, a page-backed request, the wiki
written, the case complete. A hand-written decision does not count, and an
approval changed after the request takes the request back."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("intake", ROOT / "scripts" / "intake.py")
INTAKE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(INTAKE)

DECIDER = ("--decided-by", "journalist:fixture", "--decided-at", "2026-08-23T12:00:00Z")
COMPLETE = {"kind": "interview", "captured_by": "A. Reporter", "source": {"pseudonym": "Dock worker 1"},
            "rights_holder": {"label": "A. Reporter"}}
RIGHTS = {"attribution": "not_for_attribution", "sensitivity": "case_only",
          "consent": {"publish_content": True, "publish_direct_quotes": False, "obtained_via": "recorded_oral"}}
QUOTE = "the supervisor told the night shift to stop opening containers"
CLAIM = {"proposition": "Dock worker 1 says the night shift was told to stop opening containers.",
         "statement": {"subject": {"text": "the supervisor"}, "predicate": "told the night shift to stop opening containers"}}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(script: str, *args: str, env: dict[str, str], expect: int = 0) -> subprocess.CompletedProcess[str]:
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args], capture_output=True, text=True, env=env)
    require(done.returncode == expect, f"{script} {' '.join(args[:2])}: exit {done.returncode}, wanted {expect}: {done.stderr or done.stdout}")
    return done


def approved_material(case: Path, name: str, text: str) -> None:
    """One confirmed record whose kept claim the journalist approved."""
    source = case.parent / f"{name}.txt"
    source.write_text(text, encoding="utf-8")
    record = INTAKE.set_fields(case, INTAKE.add(case, source)["id"], COMPLETE)
    INTAKE.confirm(case, record["id"], "A. Reporter")
    stored = INTAKE.propose(case, record["id"], {"passages": [{"text": QUOTE, "language": "en", "direct_quote": True, "claims": [CLAIM]}]})
    INTAKE.set_rights(case, stored["passages"][-1]["id"], RIGHTS)
    INTAKE.decide(case, stored["claims"][-1]["id"], "keep")
    INTAKE.approve(case, record["id"], "A. Reporter")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        env = {**os.environ, "SPOTLIGHT_CONFIG": str(root / "no-config.json")}  # no install config: the solo edition
        os.environ["SPOTLIGHT_CONFIG"] = env["SPOTLIGHT_CONFIG"]
        case = root / "cases_protected" / "harbour"
        (case / "data").mkdir(parents=True)

        def seam(*args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
            return run("spotlight-orchestration.py", *args, str(case), env=env, expect=expect)

        def status() -> dict:
            return json.loads(seam("status", "--json").stdout)

        def view() -> dict:
            return json.loads(seam("review-view", "ingest").stdout)

        # Nothing approved yet: the case is at the start.
        require(status()["phase"] == "brief", f"a case with no approved material left the start: {status()}")
        approved_material(case, "call", f"In March {QUOTE}.\n")

        # Approved material alone puts the case at the ingest decision.
        pending = status()
        require(pending["phase"] == "ingest" and pending["ingest"] == {"state": "pending", "resume_at": "decision"},
                f"approved material did not reach the ingest decision: {pending}")
        # The newsroom edition keeps material in the case: it says so, and builds nothing.
        (root / "newsroom.json").write_text(json.dumps({"knowledge_destination": {"mode": "newsroom"}}), encoding="utf-8")
        newsroom = {**env, "SPOTLIGHT_CONFIG": str(root / "newsroom.json")}
        refused = run("build-knowledge-batch.py", str(case), env=newsroom, expect=2)
        require("keeps in the case" in refused.stderr, f"a newsroom build gave no reason: {refused.stderr}")
        shown = json.loads(run("spotlight-orchestration.py", "review-view", "ingest", str(case), env=newsroom).stdout)
        require(any("nothing to ingest" in line for line in shown["blocking"]), f"the newsroom page did not say why: {shown['blocking']}")
        require("not been built" in " ".join(view()["blocking"]), f"a page with no batch did not block: {view()['blocking']}")
        run("build-knowledge-batch.py", str(case), "--review", env=env)
        page = view()
        require(page["blocking"] == [] and page["content"]["batch"]["state"] == "final", f"the page does not show a final batch: {page}")
        require(any(r["type"] == "claims" and CLAIM["proposition"] in r["text"] for r in page["content"]["batch"]["records"]),
                f"the page does not list the claim: {page['content']}")

        # A request needs the page's own nonce; a hand-written decision does not advance the case.
        forged = root / "forged" / "harbour"
        shutil.copytree(case, forged)
        nonce = json.loads(seam("issue-review-nonce", "ingest").stdout)["nonce"]
        seam("decide-ingest", "requested", "--nonce", "0" * 64, "--digest", page["digest"], *DECIDER, expect=3)
        seam("decide-ingest", "requested", "--nonce", nonce, "--digest", page["digest"], *DECIDER)
        require(status()["ingest"] == {"state": "requested", "resume_at": "ingest"}, f"the request did not count: {status()}")
        (forged / "data" / "orchestration.json").write_bytes((case / "data" / "orchestration.json").read_bytes())
        (forged / "data" / "ingestion.json").write_bytes((case / "data" / "ingestion.json").read_bytes())
        copied = json.loads(run("spotlight-orchestration.py", "status", "--json", str(forged), env=env).stdout)
        require(copied["ingest"] == {"state": "pending", "resume_at": "decision"}, f"a hand-written request advanced the case: {copied}")

        # The wiki: a protected case is written to its own folder's wiki, and the case completes.
        written = run("wiki-destination.py", str(case), env=env)
        wiki = root / "cases_protected" / "wiki" / "spotlight"
        notes = list((wiki / "claims").glob("*.md"))
        require(json.loads(written.stdout)["ok"] and len(notes) == 1 and "Dock worker 1 asserts" in notes[0].read_text(encoding="utf-8"),
                f"the claim's note was not written to the protected wiki: {written.stdout}")
        require(status()["ingest"] == {"state": "completed", "resume_at": "seal"}, f"a written wiki was not seen: {status()}")
        seam("decide-ingest", "completed")
        require(status()["phase"] == "complete", f"the case did not complete: {status()}")

        # Approving more material changes what the request covered: it no longer counts.
        approved_material(case, "second-call", f"Later the same week {QUOTE}.\n")
        changed = status()
        require(changed["phase"] == "ingest" and changed["ingest"] == {"state": "pending", "resume_at": "decision"},
                f"an approval changed after the request left it standing: {changed}")
        run("wiki-destination.py", str(case), env=env, expect=2)

    print("intake to ingest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
