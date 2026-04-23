#!/usr/bin/env python3
"""Manage Spotlight's external-monitor case registry."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "cases"
SCHEMA_VERSION = "2"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _case_dir(project: str) -> Path:
    return CASES_DIR / project


def _data_dir(project: str) -> Path:
    return _case_dir(project) / "data"


def _monitoring_path(project: str) -> Path:
    return _data_dir(project) / "monitoring.json"


def default_registry(project: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "case_ref": f"spotlight:{project}",
        "mycroft": {
            "topic_slugs": [],
            "last_checked_at": None,
        },
        "cojournalist": {
            "project_id": None,
            "project_name": None,
            "scouts": [],
            "last_checked_at": None,
        },
        "fallback_routines": [],
        "checks": [],
    }


def _read_registry(path: Path) -> dict | None:
    if not path.exists():
        return None
    with open(path) as fh:
        return json.load(fh)


def _write_registry(project: str, registry: dict) -> Path:
    path = _monitoring_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(registry, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        deduped.append(cleaned)
        seen.add(cleaned)
    return deduped


def _normalize_checks(checks: object) -> list[dict]:
    normalized: list[dict] = []
    if not isinstance(checks, list):
        return normalized
    for check in checks:
        if not isinstance(check, dict):
            continue
        normalized.append(
            {
                "checked_at": check.get("checked_at") or _now_iso(),
                "source": check.get("source", "unknown"),
                "summary": check.get("summary", ""),
                "items": check.get("items", []),
            }
        )
    return normalized


def _normalize_scouts(scouts: object) -> list[dict]:
    normalized: list[dict] = []
    if not isinstance(scouts, list):
        return normalized
    for scout in scouts:
        if not isinstance(scout, dict):
            continue
        scout_id = scout.get("scout_id") or scout.get("id")
        if not scout_id:
            continue
        normalized.append(
            {
                "scout_id": scout_id,
                "monitor_kind": scout.get("monitor_kind") or scout.get("scout_type") or "web",
                "target": scout.get("target", ""),
                "criteria": scout.get("criteria", ""),
                "created_at": scout.get("created_at") or _now_iso(),
                "status": scout.get("status"),
            }
        )
    return normalized


def _normalize_fallbacks(fallbacks: object) -> list[dict]:
    normalized: list[dict] = []
    if not isinstance(fallbacks, list):
        return normalized
    for routine in fallbacks:
        if not isinstance(routine, dict):
            continue
        runtime = routine.get("runtime")
        handle = routine.get("handle")
        if not runtime or not handle:
            continue
        normalized.append(
            {
                "runtime": runtime,
                "handle": handle,
                "monitor_kind": routine.get("monitor_kind") or routine.get("scout_type") or "web",
                "target": routine.get("target", ""),
                "criteria": routine.get("criteria", ""),
                "created_at": routine.get("created_at") or _now_iso(),
            }
        )
    return normalized


def normalize_registry(project: str, payload: dict | None) -> dict:
    registry = default_registry(project)
    if not isinstance(payload, dict):
        return registry

    if payload.get("schema_version") != SCHEMA_VERSION:
        return migrate_legacy_registry(project, payload)

    registry["case_ref"] = payload.get("case_ref") or registry["case_ref"]

    mycroft = payload.get("mycroft", {})
    if isinstance(mycroft, dict):
        registry["mycroft"]["topic_slugs"] = _dedupe_strings(mycroft.get("topic_slugs", []))
        registry["mycroft"]["last_checked_at"] = mycroft.get("last_checked_at")

    cojournalist = payload.get("cojournalist", {})
    if isinstance(cojournalist, dict):
        registry["cojournalist"]["project_id"] = cojournalist.get("project_id")
        registry["cojournalist"]["project_name"] = cojournalist.get("project_name")
        registry["cojournalist"]["scouts"] = _normalize_scouts(cojournalist.get("scouts", []))
        registry["cojournalist"]["last_checked_at"] = cojournalist.get("last_checked_at")

    registry["fallback_routines"] = _normalize_fallbacks(payload.get("fallback_routines", []))
    registry["checks"] = _normalize_checks(payload.get("checks", []))
    if "migration" in payload and isinstance(payload["migration"], dict):
        registry["migration"] = payload["migration"]
    if "legacy_snapshot" in payload and isinstance(payload["legacy_snapshot"], dict):
        registry["legacy_snapshot"] = payload["legacy_snapshot"]
    return registry


def migrate_legacy_registry(project: str, payload: dict) -> dict:
    registry = default_registry(project)
    registry["migration"] = {
        "migrated_at": _now_iso(),
        "source_schema_version": payload.get("schema_version"),
        "strategy": "preserve-legacy-snapshot",
    }

    topic_slugs: list[str] = []
    for key in ("topic_slugs", "topics"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    topic_slugs.append(item)
                elif isinstance(item, dict) and item.get("slug"):
                    topic_slugs.append(item["slug"])
    registry["mycroft"]["topic_slugs"] = _dedupe_strings(topic_slugs)

    if isinstance(payload.get("checks"), list):
        registry["checks"] = _normalize_checks(payload.get("checks"))

    cojo_project_id = payload.get("project_id")
    cojo_project_name = payload.get("project_name")
    if isinstance(payload.get("cojournalist"), dict):
        cojo_project_id = payload["cojournalist"].get("project_id") or cojo_project_id
        cojo_project_name = payload["cojournalist"].get("project_name") or cojo_project_name
    registry["cojournalist"]["project_id"] = cojo_project_id
    registry["cojournalist"]["project_name"] = cojo_project_name

    scouts = payload.get("scouts")
    if isinstance(payload.get("cojournalist"), dict):
        scouts = payload["cojournalist"].get("scouts", scouts)
    registry["cojournalist"]["scouts"] = _normalize_scouts(scouts)

    fallbacks = payload.get("fallback_routines")
    if fallbacks is None and isinstance(payload.get("routines"), list):
        fallbacks = payload.get("routines")
    registry["fallback_routines"] = _normalize_fallbacks(fallbacks or [])

    registry["legacy_snapshot"] = deepcopy(payload)
    return registry


def load_registry(project: str) -> dict:
    payload = _read_registry(_monitoring_path(project))
    return normalize_registry(project, payload)


def cmd_schema(args: argparse.Namespace) -> None:
    print(json.dumps(default_registry(args.project or "example-project"), indent=2, ensure_ascii=False))


def cmd_show(args: argparse.Namespace) -> None:
    registry = load_registry(args.project)
    if args.json:
        print(json.dumps(registry, indent=2, ensure_ascii=False))
        return
    print(_monitoring_path(args.project))
    print(json.dumps(registry, indent=2, ensure_ascii=False))


def cmd_init(args: argparse.Namespace) -> None:
    registry = load_registry(args.project)
    path = _write_registry(args.project, registry)
    print(path)


def cmd_migrate(args: argparse.Namespace) -> None:
    path = _monitoring_path(args.project)
    original = _read_registry(path)
    registry = normalize_registry(args.project, original)
    path = _write_registry(args.project, registry)
    if args.json:
        print(json.dumps({"path": str(path), "registry": registry}, indent=2, ensure_ascii=False))
        return
    print(path)


def cmd_link_mycroft_topic(args: argparse.Namespace) -> None:
    registry = load_registry(args.project)
    slugs = registry["mycroft"]["topic_slugs"]
    slugs.append(args.slug)
    registry["mycroft"]["topic_slugs"] = _dedupe_strings(slugs)
    registry["mycroft"]["last_checked_at"] = args.last_checked_at
    path = _write_registry(args.project, registry)
    print(path)


def cmd_link_cojournalist_project(args: argparse.Namespace) -> None:
    registry = load_registry(args.project)
    registry["cojournalist"]["project_id"] = args.project_id
    if args.project_name:
        registry["cojournalist"]["project_name"] = args.project_name
    path = _write_registry(args.project, registry)
    print(path)


def cmd_link_cojournalist_scout(args: argparse.Namespace) -> None:
    registry = load_registry(args.project)
    scouts = registry["cojournalist"]["scouts"]
    scout = {
        "scout_id": args.scout_id,
        "monitor_kind": args.monitor_kind,
        "target": args.target,
        "criteria": args.criteria,
        "created_at": args.created_at or _now_iso(),
        "status": args.status,
    }
    scouts = [existing for existing in scouts if existing.get("scout_id") != args.scout_id]
    scouts.append(scout)
    registry["cojournalist"]["scouts"] = scouts
    path = _write_registry(args.project, registry)
    print(path)


def cmd_link_fallback(args: argparse.Namespace) -> None:
    registry = load_registry(args.project)
    routine = {
        "runtime": args.runtime,
        "handle": args.handle,
        "monitor_kind": args.monitor_kind,
        "target": args.target,
        "criteria": args.criteria,
        "created_at": args.created_at or _now_iso(),
    }
    fallbacks = [
        existing
        for existing in registry["fallback_routines"]
        if not (existing.get("runtime") == args.runtime and existing.get("handle") == args.handle)
    ]
    fallbacks.append(routine)
    registry["fallback_routines"] = fallbacks
    path = _write_registry(args.project, registry)
    print(path)


def cmd_record_check(args: argparse.Namespace) -> None:
    registry = load_registry(args.project)
    item_payload = []
    if args.items_json:
        item_payload = json.loads(args.items_json)
        if not isinstance(item_payload, list):
            raise ValueError("--items-json must decode to a list")
    registry["checks"].append(
        {
            "checked_at": args.checked_at or _now_iso(),
            "source": args.source,
            "summary": args.summary,
            "items": item_payload,
        }
    )
    if args.source == "mycroft":
        registry["mycroft"]["last_checked_at"] = args.checked_at or registry["checks"][-1]["checked_at"]
    elif args.source == "cojournalist":
        registry["cojournalist"]["last_checked_at"] = args.checked_at or registry["checks"][-1]["checked_at"]
    path = _write_registry(args.project, registry)
    print(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Spotlight monitoring registry files.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_schema = sub.add_parser("schema", help="Print the v2 monitoring registry shape.")
    p_schema.add_argument("--project", default="example-project")
    p_schema.add_argument("--json", action="store_true", help="Accepted for CLI symmetry.")
    p_schema.set_defaults(func=cmd_schema)

    p_show = sub.add_parser("show", help="Read a case registry without modifying it.")
    p_show.add_argument("--project", required=True)
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)

    p_init = sub.add_parser("init", help="Create or normalize a case registry on disk.")
    p_init.add_argument("--project", required=True)
    p_init.set_defaults(func=cmd_init)

    p_migrate = sub.add_parser("migrate", help="Migrate a legacy registry to schema v2.")
    p_migrate.add_argument("--project", required=True)
    p_migrate.add_argument("--json", action="store_true")
    p_migrate.set_defaults(func=cmd_migrate)

    p_topic = sub.add_parser("link-mycroft-topic", help="Attach a Mycroft topic slug to a case.")
    p_topic.add_argument("--project", required=True)
    p_topic.add_argument("--slug", required=True)
    p_topic.add_argument("--last-checked-at")
    p_topic.set_defaults(func=cmd_link_mycroft_topic)

    p_cojo_project = sub.add_parser("link-cojournalist-project", help="Attach a coJournalist project to a case.")
    p_cojo_project.add_argument("--project", required=True)
    p_cojo_project.add_argument("--project-id", required=True)
    p_cojo_project.add_argument("--project-name")
    p_cojo_project.set_defaults(func=cmd_link_cojournalist_project)

    p_cojo_scout = sub.add_parser("link-cojournalist-scout", help="Record a coJournalist scout under the linked case.")
    p_cojo_scout.add_argument("--project", required=True)
    p_cojo_scout.add_argument("--scout-id", required=True)
    p_cojo_scout.add_argument("--monitor-kind", required=True, choices=["web", "pulse", "social", "civic"])
    p_cojo_scout.add_argument("--target", required=True)
    p_cojo_scout.add_argument("--criteria", required=True)
    p_cojo_scout.add_argument("--created-at")
    p_cojo_scout.add_argument("--status")
    p_cojo_scout.set_defaults(func=cmd_link_cojournalist_scout)

    p_fallback = sub.add_parser("link-fallback-routine", help="Record a runtime-native monitoring fallback.")
    p_fallback.add_argument("--project", required=True)
    p_fallback.add_argument("--runtime", required=True, choices=["codex", "claude", "hermes", "openclaw"])
    p_fallback.add_argument("--handle", required=True)
    p_fallback.add_argument("--monitor-kind", required=True, choices=["web", "pulse", "social", "civic"])
    p_fallback.add_argument("--target", required=True)
    p_fallback.add_argument("--criteria", required=True)
    p_fallback.add_argument("--created-at")
    p_fallback.set_defaults(func=cmd_link_fallback)

    p_check = sub.add_parser("record-check", help="Append a monitoring check entry for a case.")
    p_check.add_argument("--project", required=True)
    p_check.add_argument("--source", required=True, choices=["mycroft", "cojournalist", "runtime-routine"])
    p_check.add_argument("--summary", required=True)
    p_check.add_argument("--items-json")
    p_check.add_argument("--checked-at")
    p_check.set_defaults(func=cmd_record_check)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
