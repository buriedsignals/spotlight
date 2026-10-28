"""The Investigation gate's Anomaly note.

When the harness's decision model added Anomaly to the plan
(scripts/anomaly-decision.py, data/anomaly-decision.json says yes) and the
member approved that plan, the gate page lists each table the decision named
that has no anomaly_run in data/anomaly-signals.json. It is a note for the
member, never a reason to withhold the gate: Anomaly is optional (Tom,
2026-10-28). A case without that record, or whose approved plan no longer has
the section, gets no note.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DECISION_FILE = "data/anomaly-decision.json"


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def tables_without_run(case: Path, approvals: dict[str, Any]) -> list[str]:
    decision = _read(case / DECISION_FILE)
    if not (isinstance(decision, dict) and decision.get("decision") is True):
        return []
    methodology_approval = approvals.get("methodology")
    if not (isinstance(methodology_approval, dict) and methodology_approval.get("anomaly_methods")):
        return []
    from integrations.anomaly import adapter as anomaly  # only for a case the decision touched

    methodology = _read(case / "data" / "methodology.json")
    if not (isinstance(methodology, dict) and anomaly.plan_domains(methodology)):
        return []
    done = {table.get("path") for run in anomaly.signal_runs(case) for table in (run.get("tables") or {}).values()
            if isinstance(table, dict)}
    return [table for table in decision.get("tables") or [] if table not in done and (case / table).is_file()]
