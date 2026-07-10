#!/usr/bin/env python3
"""Report CTI Expert upstream drift without activating upstream content."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "upstreams" / "cti-expert" / "source.lock.json"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def load_lock(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {"repository", "default_branch", "active_sha", "seen_sha"}
    missing = sorted(required - set(data))
    if missing:
        raise ValueError(f"lock missing required fields: {', '.join(missing)}")
    for field in ("active_sha", "seen_sha"):
        if not FULL_SHA.fullmatch(str(data[field])):
            raise ValueError(f"{field} must be a full lowercase 40-character commit SHA")
    return data


def github_api_url(lock: dict) -> str:
    match = re.fullmatch(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?", lock["repository"])
    if not match:
        raise ValueError("repository must be an https://github.com/<owner>/<repo> URL")
    owner, repo = match.groups()
    branch = lock["default_branch"]
    return f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}"


def fetch_head(lock: dict, timeout: float) -> str:
    request = urllib.request.Request(
        github_api_url(lock),
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Spotlight-CTI-Upstream-Check/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    sha = str(payload.get("sha", "")).lower()
    if not FULL_SHA.fullmatch(sha):
        raise ValueError("GitHub response did not contain a full commit SHA")
    return sha


def write_seen(path: Path, lock: dict, head_sha: str) -> None:
    updated = dict(lock)
    updated["seen_sha"] = head_sha
    updated["checked_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(updated, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def drift_status(active_sha: str, seen_sha: str, head_sha: str) -> str:
    if head_sha == active_sha:
        return "current"
    if head_sha == seen_sha:
        return "review_pending"
    return "upstream_changed"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--head-sha", help="Offline/test override; skips the GitHub request")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--update-seen", action="store_true", help="Record the observed SHA without changing active_sha")
    parser.add_argument("--strict", action="store_true", help="Exit 3 when upstream differs from active_sha")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of one-line text")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        lock_path = args.lock.resolve()
        lock = load_lock(lock_path)
        if args.head_sha:
            head_sha = args.head_sha.lower()
            if not FULL_SHA.fullmatch(head_sha):
                raise ValueError("--head-sha must be a full lowercase 40-character commit SHA")
        else:
            head_sha = fetch_head(lock, args.timeout)

        if args.update_seen:
            write_seen(lock_path, lock, head_sha)
            lock["seen_sha"] = head_sha

        status = drift_status(lock["active_sha"], lock["seen_sha"], head_sha)
        result = {
            "name": lock.get("name", "cti-expert"),
            "repository": lock["repository"],
            "branch": lock["default_branch"],
            "active_sha": lock["active_sha"],
            "seen_sha": lock["seen_sha"],
            "upstream_head": head_sha,
            "status": status,
            "runtime_activated": False,
            "next_action": "none" if status == "current" else "review upstream diff; do not auto-activate",
        }
    except Exception as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        else:
            print(f"CTI upstream check failed: {exc}")
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"cti-expert: {status} active={result['active_sha'][:12]} "
            f"seen={result['seen_sha'][:12]} head={head_sha[:12]}"
        )
    return 3 if args.strict and head_sha != lock["active_sha"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
