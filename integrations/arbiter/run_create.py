#!/usr/bin/env python3
"""Build and summarize files for Arbiter's external case-study create flow.

The helper is offline and stdlib-only. It keeps user-provided query, title, and
search phrases out of shell command strings while producing the JSON bodies
consumed by curl, and renders the two payloads a reviewer has to read: the
generated search plan and the run's progress.

Usage:
  python3 integrations/arbiter/run_create.py build-create \
    --case-dir CASE_DIR --query-file CASE_DIR/research/query.txt \
    --platforms reddit,youtube \
    --from 2026-07-10T00:00:00Z --to 2026-07-24T00:00:00Z \
    --title-file CASE_DIR/research/title.txt --out CASE_DIR/research/create-body.json
  python3 integrations/arbiter/run_create.py plan-summary \
    --case-dir CASE_DIR --plan-file CASE_DIR/research/search-plan.json
  python3 integrations/arbiter/run_create.py plan-options --mode remove \
    --case-dir CASE_DIR --plan-file CASE_DIR/research/search-plan.json \
    --out CASE_DIR/research/plan-options.json
  python3 integrations/arbiter/run_create.py build-finalize \
    --case-dir CASE_DIR --plan-file CASE_DIR/research/search-plan.json --remove 2,5 \
    --phrases-file CASE_DIR/research/added-phrases.txt \
    --out CASE_DIR/research/finalize-body.json
  python3 integrations/arbiter/run_create.py progress-summary \
    --case-dir CASE_DIR --progress-file CASE_DIR/research/progress.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Appended, never prepended: prepending would let a file in integrations/ shadow
# a stdlib module for every later import in this process.
sys.path.append(str(Path(__file__).resolve().parents[1]))
from _runner import write_json_atomic  # noqa: E402
from arbiter.client import open_research_file, safe_research_path  # noqa: E402


def contained_path(args: argparse.Namespace, value: str, label: str) -> Path:
    """Keep every file-backed input and output under case research."""
    path = Path(value)
    case_dir = getattr(args, "case_dir", None)
    if not case_dir:
        raise ValueError("--case-dir is required for file-backed operations")
    case_path = Path(case_dir)
    safe_research_path(case_path, "__case-boundary-check__.json")
    research = (case_path / "research").resolve(strict=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"{label} must be a regular file under case research")
    if path.resolve(strict=False).parent != research:
        raise ValueError(f"{label} must stay under case research")
    return path


# Creation accepts nine platforms. The posts filter accepts a tenth
# (google_news), which is a read-side value and never valid here.
ALLOWED_PLATFORMS = frozenset(
    {
        "youtube",
        "twitter",
        "bluesky",
        "reddit",
        "facebook",
        "linkedin",
        "fourchan",
        "instagram",
        "tiktok",
    }
)
MAX_QUERY_LENGTH = 500
MAX_TITLE_LENGTH = 200
MAX_FINALIZE_PHRASES = 50
MAX_FINALIZE_ENTITIES = 200
MAX_FINALIZE_PHRASE_LENGTH = 200
MAX_FINALIZE_ENTITY_LENGTH = 500


def _read_research_text(path: Path) -> str:
    descriptor = open_research_file(path, os.O_RDONLY)
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        return stream.read()


def read_text(path: Path, label: str) -> str:
    """Read and trim a UTF-8 text input file."""
    try:
        return _read_research_text(path).strip()
    except OSError as exc:
        raise ValueError(f"cannot read {label} file at {path}: {exc}") from exc


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    """Load a JSON object and surface an Arbiter error envelope clearly."""
    try:
        payload = json.loads(_read_research_text(path))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} JSON at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON must be an object")
    error = payload.get("error")
    if isinstance(error, dict):
        code = str(error.get("code", "unknown"))
        message = str(error.get("message", "Arbiter request failed"))
        raise ValueError(f"Arbiter error {code}: {message}")
    return payload


def parse_datetime(value: str, label: str) -> datetime:
    """Parse a timezone-aware ISO-8601 timestamp."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


def parse_platforms(value: str) -> list[str]:
    """Validate a comma-separated list against Arbiter's create platform set."""
    platforms = [entry.strip().lower() for entry in value.split(",") if entry.strip()]
    if not platforms:
        raise ValueError("platforms must contain at least one platform")
    if len(platforms) != len(set(platforms)):
        raise ValueError("platforms must not contain duplicates")
    unsupported = [platform for platform in platforms if platform not in ALLOWED_PLATFORMS]
    if unsupported:
        raise ValueError(f"unsupported platform(s): {', '.join(unsupported)}")
    return platforms


def parse_index_list(value: str, phrase_count: int, label: str) -> list[int]:
    """Parse comma/space-separated one-based phrase numbers against the plan's list."""
    entries = [entry.strip() for entry in value.replace(" ", ",").split(",") if entry.strip()]
    if not entries:
        raise ValueError(f"{label} must contain at least one phrase number")
    if not all(entry.isdecimal() for entry in entries):
        raise ValueError(f"{label} must be a comma-separated list of phrase numbers")
    indexes = [int(entry) for entry in entries]
    if len(indexes) != len(set(indexes)):
        raise ValueError(f"{label} must not contain duplicate phrase numbers")
    if any(index < 1 or index > phrase_count for index in indexes):
        raise ValueError(f"{label} indexes must be between 1 and {phrase_count}")
    return indexes


def build_create_body(args: argparse.Namespace) -> dict[str, Any]:
    """Build a validated POST /case-studies request body from file inputs."""
    query = read_text(contained_path(args, args.query_file, "query"), "query")
    if not query or len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"query must contain 1..{MAX_QUERY_LENGTH} characters")
    start = parse_datetime(args.from_date, "from")
    end = parse_datetime(args.to_date, "to")
    if start >= end:
        raise ValueError("from must be earlier than to")
    if end > datetime.now(timezone.utc):
        raise ValueError("to must not be in the future")

    body: dict[str, Any] = {
        "search_query": query,
        "platforms": parse_platforms(args.platforms),
        "date_range": {"from": args.from_date, "to": args.to_date},
    }
    if args.title_file:
        title = read_text(contained_path(args, args.title_file, "title"), "title")
        if title:
            if len(title) > MAX_TITLE_LENGTH:
                raise ValueError(f"title must contain at most {MAX_TITLE_LENGTH} characters")
            body["title"] = title
    return body


def plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract and minimally validate the plan object from a search-plan response."""
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        raise ValueError("search-plan JSON is missing plan")
    phrases = plan.get("search_phrases")
    if (
        not isinstance(phrases, list)
        or not phrases
        or not all(isinstance(item, str) and item.strip() for item in phrases)
    ):
        raise ValueError("search-plan JSON is missing a valid plan.search_phrases array")
    return plan


def added_phrases(path: Path | None) -> list[str]:
    """Read optional added phrases as one non-empty phrase per line."""
    if path is None:
        return []
    text = read_text(path, "phrases")
    return [line.strip() for line in text.splitlines() if line.strip()]


def validate_finalize_values(
    phrases: list[str], entities: list[str]
) -> tuple[list[str], list[str]]:
    """Validate protocol ceilings, lengths, and case-insensitive uniqueness."""
    if not phrases or len(phrases) > MAX_FINALIZE_PHRASES:
        raise ValueError(
            f"search_phrases must contain 1..{MAX_FINALIZE_PHRASES} items"
        )
    if len(entities) > MAX_FINALIZE_ENTITIES:
        raise ValueError(
            f"final_entities must contain at most {MAX_FINALIZE_ENTITIES} items"
        )
    for phrase in phrases:
        if not 1 <= len(phrase) <= MAX_FINALIZE_PHRASE_LENGTH:
            raise ValueError(
                f"search phrases must contain 1..{MAX_FINALIZE_PHRASE_LENGTH} characters"
            )
    for entity in entities:
        if not 1 <= len(entity) <= MAX_FINALIZE_ENTITY_LENGTH:
            raise ValueError(
                f"entities must contain 1..{MAX_FINALIZE_ENTITY_LENGTH} characters"
            )
    phrase_keys = [phrase.casefold() for phrase in phrases]
    if len(phrase_keys) != len(set(phrase_keys)):
        raise ValueError("search_phrases must be unique after trimming and case normalization")
    entity_keys = [entity.casefold() for entity in entities]
    if len(entity_keys) != len(set(entity_keys)):
        raise ValueError("final_entities must be unique after trimming and case normalization")
    return phrases, entities

def build_finalize_body(args: argparse.Namespace) -> dict[str, Any]:
    """Build a POST /case-studies/{id}/finalize body from reviewed plan phrases."""
    plan = plan_payload(load_json_object(contained_path(args, args.plan_file, "search-plan"), "search-plan"))
    source_phrases = [phrase.strip() for phrase in plan["search_phrases"]]
    if getattr(args, "remove", None):
        # Removal is the shape the review loop speaks: the reviewer names the
        # phrases to drop and everything else survives, so the kept list can
        # never drift from the numbering they were shown.
        dropped = set(parse_index_list(args.remove, len(source_phrases), "remove"))
        indexes = [index for index in range(1, len(source_phrases) + 1) if index not in dropped]
        if not indexes:
            raise ValueError("remove must leave at least one phrase")
    else:
        indexes = parse_index_list(args.keep, len(source_phrases), "keep")
    phrases = [source_phrases[index - 1] for index in indexes]
    phrases.extend(added_phrases(contained_path(args, args.phrases_file, "phrases") if args.phrases_file else None))

    deduplicated = list(dict.fromkeys(phrases))
    entities = plan.get("entities", [])
    if not isinstance(entities, list) or not all(isinstance(item, str) for item in entities):
        raise ValueError("plan.entities must be an array of strings when present")
    normalized_entities = [entity.strip() for entity in entities if entity.strip()]
    deduplicated, normalized_entities = validate_finalize_values(
        deduplicated, normalized_entities
    )
    return {
        "search_phrases": deduplicated,
        "final_entities": normalized_entities,
    }


def build_plan_options(args: argparse.Namespace) -> dict[str, Any]:
    """Build the selectable removal or addition options for one review round.

    Every option carries the phrase's original one-based plan number, so a
    caller can accumulate selections across rounds without renumbering or
    parsing labels. `--mode remove` enumerates the plan's own phrases;
    `--mode add` enumerates locally generated suggestions.
    """
    plan = plan_payload(load_json_object(contained_path(args, args.plan_file, "search-plan"), "search-plan"))
    phrases = [phrase.strip() for phrase in plan["search_phrases"]]
    if args.mode == "remove":
        if args.suggestions_file:
            raise ValueError("remove mode does not accept --suggestions-file")
        removed_numbers = (
            sorted(parse_index_list(args.removed, len(phrases), "removed"))
            if args.removed
            else []
        )
        removed = set(removed_numbers)
        options = [
            {
                "label": f"{index}. {phrase}",
                "description": (
                    "Already removed; selecting again has no effect."
                    if index in removed
                    else "Currently kept; select to remove."
                ),
                "original_number": index,
                "phrase": phrase,
                "removed": index in removed,
            }
            for index, phrase in enumerate(phrases, start=1)
        ]
        prompt = "Select the search phrases that should be removed."
        mode_fields: dict[str, Any] = {"removed_numbers": removed_numbers}
    else:
        if args.removed:
            raise ValueError("add mode does not accept --removed")
        if not args.suggestions_file:
            raise ValueError("add mode requires --suggestions-file")
        candidates = list(dict.fromkeys(added_phrases(contained_path(args, args.suggestions_file, "suggestions"))))
        source_phrase_set = set(phrases)
        suggestions = [phrase for phrase in candidates if phrase not in source_phrase_set]
        if not suggestions:
            raise ValueError("suggestions file contains no new phrases")
        disclosure = (
            "Generated by Spotlight from Arbiter plan context; these are not search "
            "phrases returned by Arbiter."
        )
        options = [
            {
                "label": phrase,
                "description": disclosure,
                "suggestion_number": index,
                "phrase": phrase,
            }
            for index, phrase in enumerate(suggestions, start=1)
        ]
        prompt = (
            "Select any Spotlight-generated phrases to add. A reviewer may also "
            "supply their own phrase."
        )
        mode_fields = {
            "source": {
                "producer": "spotlight_model",
                "basis": "the available plan summary, categories, and entities",
                "disclosure": disclosure,
            }
        }
    result: dict[str, Any] = {
        "mode": args.mode,
        "prompt": prompt,
        "multi_select": True,
        "option_count": len(options),
        "options": options,
    }
    result.update(mode_fields)
    return result


def render_plan_summary(path: Path, removed: str | None = None) -> str:
    """Render the summary, grounded sources, and the numbered search phrases.

    Phrase numbers are the plan's original one-based positions and never shift,
    so a caller can print this block again after each review round and the
    numbers a reviewer named still mean the same phrases.

    Args:
        path: Saved `POST /case-studies/{id}/search-plan` response.
        removed: Optional comma-separated phrase numbers to mark as dropped.

    Returns:
        The multi-line review block.
    """
    plan = plan_payload(load_json_object(path, "search-plan"))
    summary = str(plan.get("summary", "")).strip() or "(no summary returned)"
    lines = ["SUMMARY", summary, "", "SOURCES"]
    sources = plan.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("plan.sources must be an array when present")
    valid_sources = [source for source in sources if isinstance(source, dict)]
    if valid_sources:
        for index, source in enumerate(valid_sources, start=1):
            title = str(source.get("title") or "Untitled source").strip()
            url = str(source.get("url", "")).strip()
            lines.append(f"{index}. {title} - {url}")
    else:
        lines.append("(none returned)")

    phrases = plan["search_phrases"]
    removed_indexes = (
        set(parse_index_list(removed, len(phrases), "removed")) if removed else set()
    )
    lines.extend(["", "SEARCH PHRASES"])
    for index, phrase in enumerate(phrases, start=1):
        marker = "  [removed]" if index in removed_indexes else ""
        lines.append(f"{index:>2}. {phrase.strip()}{marker}")
    kept = len(phrases) - len(removed_indexes)
    lines.append("")
    lines.append(f"{kept} of {len(phrases)} phrases kept.")
    if removed_indexes:
        dropped = ", ".join(str(index) for index in sorted(removed_indexes))
        lines.append(f"Removed: {dropped}")
    lines.append("Phrase numbers are the plan's original positions and stay fixed.")
    return "\n".join(lines)


STALL_WARNING_MINUTES = 10
PLAN_GENERATION_PENDING_MESSAGE = (
    "Search plan is still being generated; collection has not started yet."
)


def parse_optional_timestamp(value: Any) -> datetime | None:
    """Parse an optional ISO-8601 timestamp, tolerating missing or malformed values."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def format_age(updated_at: Any, now: datetime) -> str:
    """Render how long ago an activity timestamp was, for stall detection."""
    parsed = parse_optional_timestamp(updated_at)
    if parsed is None:
        return "no activity yet"
    minutes = int((now - parsed).total_seconds() // 60)
    if minutes < 0:
        return "just now"
    if minutes < 1:
        return "updated just now"
    if minutes < 60:
        return f"updated {minutes}m ago"
    return f"updated {minutes // 60}h{minutes % 60:02d}m ago"


def render_progress_summary(
    path: Path, now: datetime | None = None, *, phase: str = "collection"
) -> str:
    """Render a per-poll progress block: headline, per-platform lines, and analysis.

    The headline carries the run status, collected posts, module readiness, and
    how stale the newest activity is, so a caller polling every few seconds can
    print something real every time and notice a stalled run instead of waiting
    silently.

    Args:
        path: Saved `GET /case-studies/{id}/progress` response.
        now: Clock used for activity ages; defaults to the current UTC time.
        phase: Plan liveness or full collection progress rendering.

    Returns:
        The multi-line summary block.
    """
    payload = load_json_object(path, "progress")
    moment = now or datetime.now(timezone.utc)
    status = str(payload.get("status", "unknown"))
    collection = payload.get("collection")
    if not isinstance(collection, dict):
        collection = {}
    platform_rows = collection.get("platforms", [])
    if not isinstance(platform_rows, list):
        platform_rows = []
    platforms = [row for row in platform_rows if isinstance(row, dict)]
    total_posts = collection.get("total_posts") or 0

    analysis = payload.get("analysis")
    if not isinstance(analysis, dict):
        analysis = {}
    module_rows = analysis.get("modules", [])
    modules = (
        [module for module in module_rows if isinstance(module, dict)]
        if isinstance(module_rows, list)
        else []
    )
    ready_modules = sum(1 for module in modules if module.get("status") == "ready")
    failed_modules = sum(1 for module in modules if module.get("status") == "failed")

    updated_at = payload.get("updated_at")
    age = format_age(updated_at, moment)
    if (
        phase == "plan"
        and status not in {"processing", "completed", "failed"}
        and total_posts == 0
        and ready_modules == 0
    ):
        lines = [f"{PLAN_GENERATION_PENDING_MESSAGE} | {age}"]
        step_rows = payload.get("steps", [])
        if isinstance(step_rows, list):
            for step in step_rows:
                if not isinstance(step, dict):
                    continue
                title = str(step.get("title") or step.get("id") or "Unknown step")
                step_status = str(step.get("status") or "unknown")
                lines.append(f"  plan: {title} [{step_status}]")
        return "\n".join(lines)

    headline = (
        f"status={status} | posts={total_posts} | "
        f"modules={ready_modules}/{len(modules)} ready"
    )
    if failed_modules:
        headline += f" ({failed_modules} failed)"
    headline += f" | {age}"
    parsed_updated = parse_optional_timestamp(updated_at)
    if parsed_updated is not None:
        stalled_minutes = int((moment - parsed_updated).total_seconds() // 60)
        if stalled_minutes >= STALL_WARNING_MINUTES:
            headline += f"  [!] no activity for {stalled_minutes}m - run may be stalled"

    lines = [headline]
    for row in platforms:
        platform = str(row.get("platform", "unknown"))
        posts = row.get("posts", 0)
        platform_status = str(row.get("status", "unknown"))
        detail = f"  {platform:<10} {posts:>6} posts  {platform_status}"
        message = row.get("message")
        if isinstance(message, str) and message.strip():
            detail += f" - {message.strip()}"
        lines.append(detail)

    stage_rows = collection.get("stages", [])
    if isinstance(stage_rows, list):
        for stage in stage_rows:
            if not isinstance(stage, dict):
                continue
            message = stage.get("message")
            if stage.get("status") == "active" and isinstance(message, str) and message.strip():
                lines.append(f"  stage {stage.get('stage', 'unknown')}: {message.strip()}")

    activity_rows = analysis.get("activity", [])
    if isinstance(activity_rows, list):
        recent = [row for row in activity_rows if isinstance(row, dict)][-3:]
        for row in recent:
            message = row.get("message")
            if isinstance(message, str) and message.strip():
                lines.append(f"  analysis: {message.strip()}")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Create the frozen subcommand parser for the create-flow helper."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    create = subcommands.add_parser("build-create", help="build a create request body")
    create.add_argument("--query-file", required=True)
    create.add_argument("--platforms", required=True)
    create.add_argument("--from", dest="from_date", required=True)
    create.add_argument("--to", dest="to_date", required=True)
    create.add_argument("--title-file")
    create.add_argument("--out", required=True)
    create.add_argument("--case-dir", help="case directory containing research files")

    finalize = subcommands.add_parser("build-finalize", help="build a finalize request body")
    finalize.add_argument("--plan-file", required=True)
    keep_group = finalize.add_mutually_exclusive_group(required=True)
    keep_group.add_argument("--keep", help="phrase numbers to keep")
    keep_group.add_argument("--remove", help="phrase numbers to drop; keeps the rest")
    finalize.add_argument("--phrases-file")
    finalize.add_argument("--out", required=True)
    finalize.add_argument("--case-dir", help="case directory containing research files")

    summary = subcommands.add_parser("plan-summary", help="render a saved search plan")
    summary.add_argument("--plan-file", required=True)
    summary.add_argument("--removed", help="phrase numbers already marked for removal")
    summary.add_argument("--case-dir", help="case directory containing research files")

    options = subcommands.add_parser("plan-options", help="build phrase-review options")
    options.add_argument("--mode", choices=("remove", "add"), required=True)
    options.add_argument("--plan-file", required=True)
    options.add_argument("--removed", help="phrase numbers already marked for removal")
    options.add_argument("--suggestions-file", help="one Spotlight suggestion per line")
    options.add_argument("--out", required=True)
    options.add_argument("--case-dir", help="case directory containing research files")

    progress = subcommands.add_parser("progress-summary", help="render saved progress")
    progress.add_argument("--progress-file", required=True)
    progress.add_argument("--case-dir", help="case directory containing research files")
    progress.add_argument("--now", help="ISO-8601 clock override for activity ages")
    progress.add_argument(
        "--phase",
        choices=("plan", "collection"),
        default="collection",
        help="render plan liveness or full collection progress (default: collection)",
    )
    return parser


def main() -> int:
    """Run one create-flow helper subcommand."""
    args = build_parser().parse_args()
    try:
        if args.command == "build-create":
            write_json_atomic(contained_path(args, args.out, "output"), build_create_body(args))
            print(f"wrote {args.out}")
        elif args.command == "build-finalize":
            write_json_atomic(contained_path(args, args.out, "output"), build_finalize_body(args))
            print(f"wrote {args.out}")
        elif args.command == "plan-summary":
            print(
                render_plan_summary(
                    contained_path(args, args.plan_file, "search-plan"),
                    getattr(args, "removed", None),
                )
            )
        elif args.command == "plan-options":
            write_json_atomic(contained_path(args, args.out, "output"), build_plan_options(args))
            print(f"wrote {args.out}")
        elif args.command == "progress-summary":
            override = parse_optional_timestamp(getattr(args, "now", None))
            if getattr(args, "now", None) and override is None:
                raise ValueError("--now must be an ISO-8601 timestamp")
            print(
                render_progress_summary(
                    contained_path(args, args.progress_file, "progress"),
                    override,
                    phase=args.phase,
                )
            )
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
