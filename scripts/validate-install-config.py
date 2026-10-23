#!/usr/bin/env python3
"""Validate the knowledge destination in .spotlight-config.json, read-only.

Engine writes `knowledge_destination` with `type`, `workspace_path` (the member
wiki), `namespace` and `mode` (`solo` or `newsroom`). Only `workspace_path` is
required. Keys an older Engine wrote for the retired 1.0 graph
(`project_id`, `destination_id`, `projection_namespace`, `story_namespace`,
`graph_database_path`, `provider_policy`, `legacy_vault_path`, `legacy_mode`)
are tolerated and ignored.

Prints one JSON status line; exits 0 when valid, 2 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integrations._credentials import ConfigPathError, config_path  # noqa: E402

SCHEMA = "spotlight-install-config-status/v1"
MODES = {"solo", "newsroom"}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def validate(config: Path) -> dict[str, Any]:
    blockers: list[str] = []
    workspace: Path | None = None
    mode = "solo"
    try:
        destination = _load(config).get("knowledge_destination")
        if not isinstance(destination, dict):
            raise ValueError("knowledge_destination is missing or not an object")
    except (OSError, ValueError) as exc:
        return {"schema_version": SCHEMA, "status": "invalid", "mode": None, "workspace_path": None, "blockers": [str(exc)]}

    raw_workspace = destination.get("workspace_path")
    if not isinstance(raw_workspace, str) or not raw_workspace.strip():
        blockers.append("knowledge_destination.workspace_path is missing")
    else:
        workspace = Path(raw_workspace).expanduser().resolve()
        if not workspace.is_dir():
            blockers.append(f"knowledge_destination.workspace_path is not a directory: {workspace}")
    if "mode" in destination:
        if destination["mode"] in MODES:
            mode = destination["mode"]
        else:
            blockers.append("knowledge_destination.mode must be solo or newsroom")
    for key in ("type", "namespace"):
        if key in destination and not isinstance(destination[key], str):
            blockers.append(f"knowledge_destination.{key} must be a string")

    # Engine's knowledge router seals the Spotlight route; when both exist they
    # must agree, so a hand-edited namespace is caught.
    namespace = destination.get("namespace")
    routes_path = workspace / ".knowledge-workspace" / "routes.json" if workspace else None
    if isinstance(namespace, str) and routes_path is not None and routes_path.is_file():
        try:
            routes = _load(routes_path)
            sealed = routes.get("routes", {}).get("spotlight_verified") if routes.get("schema_version") == "knowledge-routes/v1" else None
            if sealed is not None and sealed != namespace:
                blockers.append("configured namespace differs from the sealed Spotlight route")
        except (OSError, ValueError, AttributeError) as exc:
            blockers.append(f"knowledge routes are unreadable: {exc}")

    return {
        "schema_version": SCHEMA,
        "status": "valid" if not blockers else "invalid",
        "mode": mode,
        "workspace_path": str(workspace) if workspace else None,
        "blockers": blockers,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, help="override the config path (default: config_path())")
    args = parser.parse_args(argv)
    try:
        config = args.config.expanduser() if args.config else config_path()
    except ConfigPathError as exc:
        result = {"schema_version": SCHEMA, "status": "invalid", "mode": None, "workspace_path": None, "blockers": [str(exc)]}
    else:
        result = validate(config)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "valid" else 2


if __name__ == "__main__":
    raise SystemExit(main())
