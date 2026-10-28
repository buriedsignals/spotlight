"""The Investigation gate's Anomaly check.

When the harness's decision model added Anomaly to the plan
(scripts/anomaly-decision.py, data/anomaly-decision.json says yes) and the
member approved that plan, every table the decision named needs at least one
anomaly_run in data/anomaly-signals.json before the gate is offered. A case
without that record, or whose approved plan no longer has the section, is not
affected: the model's own plan stands.
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


def problems(case: Path, approvals: dict[str, Any]) -> list[str]:
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
    return [f"anomaly: {table} has no anomaly_run yet. The approved methodology runs Anomaly on it: run a core detector "
            f"on it with anomaly_run (the ANOMALY block in the investigator's task gives the call)"
            for table in decision.get("tables") or [] if table not in done and (case / table).is_file()]
