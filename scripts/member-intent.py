#!/usr/bin/env python3
"""What the member is doing with their latest chat message, read by the
decision model on this computer (plan step 4, reports/step4-routing-design.md).

    scripts/spotlight-python scripts/member-intent.py [--config PATH] < message.txt

Prints one JSON line: {"intent": "<choice>", "probability": p} or
{"unavailable": "<reason>"}. Only a decision endpoint on a literal loopback
address is asked: a member's words never go to a cloud model here. The
question wording is the one the step1 intent test scored (R2), unchanged.
The harness's code router uses the answer to choose between resolving and
handing the turn to the orchestrator model; an unavailable answer hands it
to the model.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "integrations" / "decisions"))

from integrations import _credentials as credentials  # noqa: E402
from integrations.decisions import client  # noqa: E402

QUESTION_KEY = "intent"
QUESTION = {
    "type": "choice",
    "instructions": "Read the member's latest chat message to the investigation assistant. What is the member doing?",
    "criteria": {
        "change_request": "The member asks for something in the case to be added, removed, changed, checked again or redone (a research topic, a direction, a finding, a source check), or says something they asked for is missing.",
        "question": "The member asks the agent to explain or tell them something, without asking for a change.",
        "approval_talk": "The member agrees or tells the agent to go ahead, picks an option the agent offered, or says they have decided or acted on the review page. (Approval itself only counts on the page; this is talk about it.)",
        "other": "Anything else: a new investigation request, a plain request to resume, a status note that they have not decided, thanks, or off-topic chat.",
    },
}
MESSAGE_CHARS = 2000
# The router waits this long, then hands the turn to the model (design: 5 s).
TIMEOUT_S = 5.0


def ask(message: str, config: dict[str, Any]) -> dict[str, Any]:
    block = (config.get("integrations") or {}).get("decisions") or {}
    try:
        url, _key_env = client.endpoint_from_config(block.get("endpoint"))
    except client.DecisionUnavailable as exc:
        return {"unavailable": str(exc)}
    if not client.is_local(url):
        return {"unavailable": "the decision endpoint is not on this computer; a member message is not sent to a cloud model"}
    try:
        provider = client.JevOpenRouterProvider("", model=block.get("model") or client.DEFAULT_MODEL, sensitive=True, url=url,
                                                timeout=TIMEOUT_S, retries=0)
        answer = provider.decide({"member_message": message[:MESSAGE_CHARS]}, {QUESTION_KEY: QUESTION})["answers"][QUESTION_KEY]
    except (client.DecisionUnavailable, KeyError, TypeError) as exc:
        return {"unavailable": str(exc) or type(exc).__name__}
    choice = answer.get("choice")
    if choice not in QUESTION["criteria"]:
        return {"unavailable": f"the decision model chose {choice!r}"}
    return {"intent": choice, "probability": float((answer.get("probabilities") or {}).get(choice, 0.0))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    path = Path(args.config) if args.config else credentials.config_path()
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        config = {}
    message = sys.stdin.read().strip()
    print(json.dumps(ask(message, config if isinstance(config, dict) else {}) if message else {"unavailable": "no message"}))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONNOUSERSITE", "1")
    sys.exit(main())
