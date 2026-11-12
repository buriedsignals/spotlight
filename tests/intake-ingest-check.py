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


def decided(case: Path, action: str, record_id: str) -> None:
    """The page's way: a one-time code for the material, then the decision with the digest the page shows."""
    nonce = INTAKE.issue_code(case, action, record_id)["nonce"]
    getattr(INTAKE, action)(case, record_id, "A. Reporter", nonce, INTAKE.view(case)["digests"][record_id])


def approved_material(case: Path, name: str, text: str) -> None:
    """One confirmed record whose kept claim the journalist approved."""
    source = case.parent / f"{name}.txt"
    source.write_text(text, encoding="utf-8")
    record = INTAKE.set_fields(case, INTAKE.add(case, source)["id"], COMPLETE)
    decided(case, "confirm", record["id"])
    stored = INTAKE.propose(case, record["id"], {"passages": [{"text": QUOTE, "language": "en", "direct_quote": True, "claims": [CLAIM]}]})
    INTAKE.set_rights(case, stored["passages"][-1]["id"], RIGHTS)
    INTAKE.decide(case, stored["claims"][-1]["id"], "keep")
    decided(case, "approve", record["id"])


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

        # A trace of an investigation rules the shortcut out, brief or no brief.
        (case / "data" / "findings.json").write_text("{}", encoding="utf-8")
        require(status()["phase"] == "brief", f"a case with findings took the material-only path: {status()}")
        (case / "data" / "findings.json").unlink()

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
        told = json.loads(run("spotlight-orchestration.py", "status", "--json", str(case), env=newsroom).stdout)
        require("newsroom_edition" not in told, f"a material-only case was told to draft and submit a batch: {told}")
        # The approval built the batch: no analyst runs in a case that holds only material.
        page = view()
        require(page["blocking"] == [] and page["content"]["batch"]["state"] == "final", f"the page does not show a final batch: {page}")
        require(any(r["type"] == "claims" and CLAIM["proposition"] in r["text"] for r in page["content"]["batch"]["records"]),
                f"the page does not list the claim: {page['content']}")
        # A claim from material shows what it rests on: the passage, the material and the passage's rights, in words.
        shown = next(r for r in page["content"]["batch"]["records"] if r["type"] == "claims")
        require(shown["basis"] == {"passage": QUOTE, "material": "interview, call.txt",
                                   "rights": ["Attribution: not for attribution",
                                              "Consent: content may be published, direct quotes may not be published",
                                              "Sensitivity: kept in the case only"]}, f"the claim does not show its basis: {shown}")

        # An approval without the page's code behind it is not an approval: the case is back at the start.
        unbacked = root / "unbacked" / "harbour"
        shutil.copytree(case, unbacked)
        (unbacked / "data" / "review-nonces.json").unlink()
        copied_status = json.loads(run("spotlight-orchestration.py", "status", "--json", str(unbacked), env=env).stdout)
        require(copied_status["phase"] == "brief", f"approvals with no ledger behind them took the material-only path: {copied_status}")

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
