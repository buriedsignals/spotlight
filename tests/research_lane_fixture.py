"""Offline research cases for the research checks and the merge: an approved
methodology with a research plan, and lane folders written the way the lane
runner writes them (lane.json, leads.jsonl, sources/ named by sha256)."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_checks as checks  # noqa: E402


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


merge = load_script("spotlight_research_merge", ROOT / "scripts" / "research-merge.py")

CONSENT = {"swarm": True, "decided_by": "journalist:fixture", "decided_at": "2026-10-23T09:00:00Z"}


def topic(topic_id: str, direction: str, lane_type: str, **extra: Any) -> dict[str, Any]:
    return {"topic_id": topic_id, "direction_id": direction, "lane_type": lane_type, "route": "swarm",
            "questions": [f"What do {lane_type} show about {direction}?"], "token_budget": 100000, **extra}


def methodology(*topics: dict[str, Any], **plan: Any) -> dict[str, Any]:
    entries = list(topics) or [topic("ownership-registries", "ownership", "registries"),
                                topic("ownership-web", "ownership", "web"),
                                topic("contracts-news-courts", "contracts", "news-courts")]
    return {
        "schema_version": "1.0", "project": "northwind",
        "lead": "Who owns Northwind Holdings, and did it win the harbour contract?",
        "investigation_plan": [
            {"direction": "Ownership of Northwind Holdings", "direction_id": "ownership",
             "questions": ["Who owns Northwind Holdings?"], "steps": [{"order": 1, "action": "Read the register", "tool": "fetch"}]},
            {"direction": "The harbour contract", "direction_id": "contracts",
             "questions": ["Who won the harbour contract?"], "steps": [{"order": 1, "action": "Read the award", "tool": "fetch"}]},
        ],
        "research_plan": {
            "round_budget_usd": 1, "case_budget_usd": 5, "mode": "openrouter_swarm", "consent": dict(CONSENT),
            "keys": [], "egress": [{"topic_id": t["topic_id"], "destination": "openrouter"} for t in entries],
            "topics": entries, **plan,
        },
    }


class Case:
    """A case folder with data/methodology.json and lane folders."""

    def __init__(self, root: Path, method: dict[str, Any] | None = None) -> None:
        self.root = root
        (root / "data").mkdir(parents=True, exist_ok=True)
        self.methodology = method or methodology()
        self.write_json(root / "data" / "methodology.json", self.methodology)
        self.lanes: dict[str, list[dict[str, Any]]] = {}

    @staticmethod
    def write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def brief(self, topic_id: str) -> dict[str, Any]:
        return next(t for t in self.methodology["research_plan"]["topics"] if t["topic_id"] == topic_id)

    def folder(self, topic_id: str) -> Path:
        return self.root / "research" / "swarm" / topic_id

    def store(self, topic_id: str, data: bytes, suffix: str) -> tuple[str, str]:
        sha = hashlib.sha256(data).hexdigest()
        path = self.folder(topic_id) / "sources" / f"{sha}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"sources/{sha}{suffix}", sha

    def page_lead(self, topic_id: str, lead_id: str, *, page_text: str, excerpt: str, summary: str | None = None,
                  url: str | None = None, subjects: list[str] | None = None, **extra: Any) -> dict[str, Any]:
        """A stored-page lead: the HTML original and its Markdown extraction are written to sources/."""
        brief = self.brief(topic_id)
        html_ref, html_sha = self.store(topic_id, f"<html><body><p>{page_text}</p></body></html>".encode(), ".html")
        md_ref, md_sha = self.store(topic_id, page_text.encode(), ".md")
        lead = {
            "schema_version": "1.0", "lead_id": lead_id, "topic_id": topic_id, "direction_id": brief["direction_id"],
            "summary": summary or excerpt, "subjects": subjects or ["Northwind Holdings"], "absence": False,
            "status": "candidate", "stored": "page",
            "capture": {"route": "api", "tool": "firecrawl", "source_url": url or f"https://{lead_id}.example.org/page",
                        "captured_at": "2026-10-23T09:14:02Z"},
            "content_ref": html_ref, "content_sha256": html_sha,
            "derivatives": [{"id": "d1", "type": "extracted_text", "content_ref": md_ref, "content_sha256": md_sha}],
            "provenance": {"method": "agent_candidate", "model": "deepseek/deepseek-v4-flash"},
            "excerpt": {"text": excerpt, "language": "en", "direct_quote": True, "derivative_id": "d1"},
        }
        lead.update(extra)
        self.lanes.setdefault(topic_id, []).append(lead)
        return lead

    def snippet_lead(self, topic_id: str, lead_id: str, text: str, url: str, **extra: Any) -> dict[str, Any]:
        brief = self.brief(topic_id)
        lead = {
            "schema_version": "1.0", "lead_id": lead_id, "topic_id": topic_id, "direction_id": brief["direction_id"],
            "summary": text, "subjects": ["Northwind Holdings"], "absence": False, "status": "candidate", "stored": "snippet",
            "capture": {"route": "api", "tool": "firecrawl", "source_url": url, "captured_at": "2026-10-23T09:15:00Z"},
            "provenance": {"method": "agent_candidate", "model": "deepseek/deepseek-v4-flash"},
            "excerpt": {"text": text, "language": "en", "direct_quote": True}, **extra,
        }
        self.lanes.setdefault(topic_id, []).append(lead)
        return lead

    def absence_lead(self, topic_id: str, lead_id: str, subjects: list[str], queries: list[str]) -> dict[str, Any]:
        brief = self.brief(topic_id)
        lead = {"schema_version": "1.0", "lead_id": lead_id, "topic_id": topic_id, "direction_id": brief["direction_id"],
                "summary": f"No record for {', '.join(subjects)}.", "subjects": subjects, "absence": True,
                "status": "candidate", "queries_tried": queries}
        self.lanes.setdefault(topic_id, []).append(lead)
        return lead

    def finish(self, topic_id: str, status: str = "completed", **lane: Any) -> dict[str, Any]:
        """Write lane.json and leads.jsonl for one topic."""
        brief = self.brief(topic_id)
        document = {"schema_version": "1.0", "topic_id": topic_id, "direction_id": brief["direction_id"],
                    "lane_type": brief["lane_type"], "route": "swarm", "status": status,
                    "started_at": "2026-10-23T09:10:00Z", "finished_at": "2026-10-23T09:20:00Z",
                    "spend": {"usd": 0.01, "tokens": 1000}, "queries_tried": [f"{topic_id} query"],
                    "lead_count": len(self.lanes.get(topic_id, []))}
        if status == "not_started":
            document.update(started_at=None, finished_at=None, spend={"usd": 0}, lead_count=0)
        document.update(lane)
        folder = self.folder(topic_id)
        folder.mkdir(parents=True, exist_ok=True)
        self.write_json(folder / "lane.json", document)
        if self.lanes.get(topic_id):
            (folder / "leads.jsonl").write_text("".join(json.dumps(l) + "\n" for l in self.lanes[topic_id]), encoding="utf-8")
        return document

    def finish_all(self) -> None:
        for brief in self.methodology["research_plan"]["topics"]:
            if not (self.folder(brief["topic_id"]) / "lane.json").exists():
                self.finish(brief["topic_id"])

    def data(self, name: str) -> Any:
        return json.loads((self.root / "data" / name).read_text(encoding="utf-8"))


def clone(value: Any) -> Any:
    return copy.deepcopy(value)
