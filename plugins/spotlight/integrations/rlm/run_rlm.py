#!/usr/bin/env python3
"""Run optional Spotlight RLM case-corpus analysis."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
URL_RE = re.compile(r"https?://[^\s<>)\"']+")
URL_TRAILING_PUNCTUATION = ".,;:!?]}"
HANDLE_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{2,30}\b")
DATE_RE = re.compile(r"\b(?:20\d{2}|19\d{2})[-/](?:0[1-9]|1[0-2])[-/](?:0[1-9]|[12]\d|3[01])\b")
FORBIDDEN_STATUSES = {"verified", "confirmed", "publishable"}
MODES = {"off", "lite", "local_gemma4_e4b"}
MODEL = "gemma4:e4b"
ARTIFACT_KINDS = {"entity", "timeline_event", "contradiction", "lead", "discarded"}
DEFAULT_NUM_CTX = 32768
DEFAULT_NUM_PREDICT = 0
PREFILTER_NUM_CTX = 4096
PREFILTER_NUM_PREDICT = 0
PREFILTER_KEYWORDS = {
    "award",
    "awarded",
    "vendor",
    "memo",
    "addendum",
    "contact",
    "contradict",
    "contradicting",
    "not final",
    "ratification",
}
NEGATIVE_CONTEXT_MARKERS = {"not part of the current lead", "unrelated", "noise line"}
SEMANTIC_KEYWORDS = {"contradict", "contradicting", "disputed", "conflict", "not final", "ratification"}


class RLMError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_token(value: Any, regex: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not regex.match(value) or ".." in value:
        raise RLMError(f"invalid {label}")
    return value


def resolve_case_path(case_dir: Path, relative_path: str) -> Path:
    raw = Path(relative_path)
    if raw.is_absolute():
        raise RLMError("corpus path must be relative")
    if any(part in {"", ".."} or part.startswith("-") for part in raw.parts):
        raise RLMError("corpus path contains unsafe segment")
    base = case_dir.resolve()
    resolved = (base / raw).resolve(strict=False)
    if base != resolved and base not in resolved.parents:
        raise RLMError(f"corpus path escapes case directory: {relative_path}")
    return resolved


def case_workspace_root() -> Path:
    configured = os.environ.get("SPOTLIGHT_CASES_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return ROOT / "cases"


def get_case_dir(project: str) -> Path:
    return resolve_case_path(case_workspace_root(), project)


def load_request(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RLMError("request must be a JSON object")
    project = validate_token(data.get("project"), SLUG_RE, "project")
    run_id = validate_token(data.get("run_id"), RUN_ID_RE, "run_id")
    mode = data.get("mode", os.environ.get("SPOTLIGHT_RLM_MODE", "off"))
    if mode not in MODES:
        raise RLMError(f"unsupported mode: {mode!r}")
    corpus_paths = data.get("corpus_paths", [])
    if mode != "off" and (not isinstance(corpus_paths, list) or not corpus_paths):
        raise RLMError("corpus_paths must be a non-empty list when RLM is enabled")
    default_optimized = mode == "local_gemma4_e4b"
    prefilter = bool(data.get("prefilter", default_optimized))
    hybrid = bool(data.get("hybrid", prefilter))
    num_ctx = data.get("num_ctx", PREFILTER_NUM_CTX if prefilter else DEFAULT_NUM_CTX)
    num_predict = data.get("num_predict", PREFILTER_NUM_PREDICT if prefilter else DEFAULT_NUM_PREDICT)
    if not isinstance(num_ctx, int) or num_ctx < 1024 or num_ctx > 65536:
        raise RLMError("num_ctx must be an integer between 1024 and 65536")
    if not isinstance(num_predict, int) or num_predict < 0 or num_predict > 4096:
        raise RLMError("num_predict must be an integer between 0 and 4096")
    if num_predict and num_predict < 128:
        raise RLMError("num_predict must be 0 or at least 128")
    return {
        "project": project,
        "run_id": run_id,
        "mode": mode,
        "corpus_paths": corpus_paths,
        "prefilter": prefilter,
        "hybrid": hybrid,
        "num_ctx": num_ctx,
        "num_predict": num_predict,
    }


def read_corpus(project: str, corpus_paths: list[str]) -> list[dict[str, Any]]:
    case_dir = get_case_dir(project)
    chunks: list[dict[str, Any]] = []
    for rel in corpus_paths:
        if not isinstance(rel, str):
            raise RLMError("corpus path must be a string")
        path = resolve_case_path(case_dir, rel)
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for index, line in enumerate(lines, start=1):
            if line.strip():
                chunks.append({"path": rel, "line": index, "text": line})
    return chunks


def source_ref(chunk: dict[str, Any]) -> dict[str, Any]:
    return {"path": chunk["path"], "line_start": chunk["line"], "line_end": chunk["line"]}


def trim_terminal_url_punctuation(value: str) -> str:
    if not value.startswith(("http://", "https://")):
        return value
    return value.rstrip(URL_TRAILING_PUNCTUATION)


def artifact(kind: str, text: str, chunk: dict[str, Any], index: int, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": f"{kind}-{index:04d}",
        "kind": kind,
        "text": text,
        "source_refs": [source_ref(chunk)],
        "verification_status": "needs_verification",
        "metadata": metadata or {},
    }


def lite_extract(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for chunk in chunks:
        candidates: list[tuple[str, str, dict[str, Any]]] = []
        candidates.extend(("entity", email, {"entity_type": "email"}) for email in EMAIL_RE.findall(chunk["text"]))
        candidates.extend(("entity", trim_terminal_url_punctuation(url), {"entity_type": "url"}) for url in URL_RE.findall(chunk["text"]))
        candidates.extend(("entity", handle, {"entity_type": "handle"}) for handle in HANDLE_RE.findall(chunk["text"]))
        candidates.extend(("timeline_event", date, {"entity_type": "date"}) for date in DATE_RE.findall(chunk["text"]))
        for kind, text, metadata in candidates:
            key = (kind, text, chunk["path"])
            if key in seen:
                continue
            seen.add(key)
            artifacts.append(artifact(kind, text, chunk, len(artifacts) + 1, metadata))
    return artifacts


def chunk_has_candidate_signal(chunk: dict[str, Any]) -> bool:
    text = chunk["text"]
    lowered = text.lower()
    if any(marker in lowered for marker in NEGATIVE_CONTEXT_MARKERS):
        return False
    return (
        bool(EMAIL_RE.search(text))
        or bool(URL_RE.search(text))
        or bool(HANDLE_RE.search(text))
        or bool(DATE_RE.search(text))
        or any(keyword in lowered for keyword in PREFILTER_KEYWORDS)
    )


def chunk_has_semantic_signal(chunk: dict[str, Any]) -> bool:
    lowered = chunk["text"].lower()
    if any(marker in lowered for marker in NEGATIVE_CONTEXT_MARKERS):
        return False
    return any(keyword in lowered for keyword in SEMANTIC_KEYWORDS)


def select_candidate_chunks(chunks: list[dict[str, Any]], *, window: int = 0, max_chunks: int = 80) -> list[dict[str, Any]]:
    if not chunks:
        return []
    selected_indexes: set[int] = set()
    for index, chunk in enumerate(chunks):
        if not chunk_has_candidate_signal(chunk):
            continue
        start = max(0, index - window)
        end = min(len(chunks), index + window + 1)
        selected_indexes.update(range(start, end))
    if not selected_indexes:
        return chunks[:max_chunks]
    return [chunks[index] for index in sorted(selected_indexes)[:max_chunks]]


def select_semantic_chunks(chunks: list[dict[str, Any]], *, window: int = 0, max_chunks: int = 40) -> list[dict[str, Any]]:
    selected_indexes: set[int] = set()
    for index, chunk in enumerate(chunks):
        if not chunk_has_semantic_signal(chunk):
            continue
        start = max(0, index - window)
        end = min(len(chunks), index + window + 1)
        selected_indexes.update(range(start, end))
    return [chunks[index] for index in sorted(selected_indexes)[:max_chunks]]


def dedupe_artifacts(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int, int]] = set()
    for item in artifacts:
        refs = item.get("source_refs") if isinstance(item.get("source_refs"), list) else []
        first_ref = refs[0] if refs and isinstance(refs[0], dict) else {}
        key = (
            str(item.get("kind", "")),
            str(item.get("text", "")),
            str(first_ref.get("path", "")),
            int(first_ref.get("line_start", 0) or 0),
            int(first_ref.get("line_end", 0) or 0),
        )
        if key in seen:
            continue
        seen.add(key)
        item = dict(item)
        item["id"] = f"{item.get('kind', 'artifact')}-{len(deduped) + 1:04d}"
        deduped.append(item)
    return deduped


def validate_analysis(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("schema_version", "project", "run_id", "mode", "provider", "created_at", "artifacts"):
        if key not in data:
            errors.append(f"missing {key}")
    if data.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if data.get("mode") not in {"lite", "local_gemma4_e4b"}:
        errors.append("mode must be lite or local_gemma4_e4b")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list):
        return errors + ["artifacts must be a list"]
    for i, item in enumerate(artifacts):
        if not isinstance(item, dict):
            errors.append(f"artifacts[{i}] must be object")
            continue
        for key in ("id", "kind", "text", "source_refs", "verification_status"):
            if key not in item:
                errors.append(f"artifacts[{i}] missing {key}")
        if item.get("kind") not in ARTIFACT_KINDS:
            errors.append(f"artifacts[{i}] invalid kind {item.get('kind')!r}")
        if not isinstance(item.get("id"), str) or not item.get("id", "").strip():
            errors.append(f"artifacts[{i}] invalid id")
        if not isinstance(item.get("text"), str) or not item.get("text", "").strip():
            errors.append(f"artifacts[{i}] invalid text")
        status = str(item.get("verification_status", "")).lower()
        if status in FORBIDDEN_STATUSES or status != "needs_verification":
            errors.append(f"artifacts[{i}] invalid verification_status {status!r}")
        source_refs = item.get("source_refs")
        if item.get("kind") != "discarded" and not source_refs:
            errors.append(f"artifacts[{i}] missing source_refs")
        if source_refs is not None:
            if not isinstance(source_refs, list):
                errors.append(f"artifacts[{i}] source_refs must be list")
            else:
                for j, ref in enumerate(source_refs):
                    if not isinstance(ref, dict):
                        errors.append(f"artifacts[{i}].source_refs[{j}] must be object")
                        continue
                    if not isinstance(ref.get("path"), str) or not ref.get("path", "").strip():
                        errors.append(f"artifacts[{i}].source_refs[{j}] invalid path")
                    for key in ("line_start", "line_end"):
                        if not isinstance(ref.get(key), int) or ref.get(key) < 1:
                            errors.append(f"artifacts[{i}].source_refs[{j}] invalid {key}")
    return errors


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def ollama_model_available(model: str = MODEL) -> bool:
    proc = subprocess.run(["ollama", "list"], text=True, capture_output=True, check=False)
    return proc.returncode == 0 and any(line.split()[0] == model for line in proc.stdout.splitlines()[1:] if line.split())


def normalize_model_artifact(raw: dict[str, Any], index: int) -> dict[str, Any]:
    kind = raw.get("kind")
    if kind not in ARTIFACT_KINDS:
        kind = "lead"
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        text = raw.get("lead", raw.get("description", ""))
    if isinstance(text, str):
        text = trim_terminal_url_punctuation(text.strip())
    source_refs: list[dict[str, Any]] = []
    for ref in raw.get("source_refs", []) if isinstance(raw.get("source_refs"), list) else []:
        if not isinstance(ref, dict):
            continue
        line_start = ref.get("line_start", ref.get("line"))
        line_end = ref.get("line_end", line_start)
        path = ref.get("path", "")
        # Drop malformed refs here instead of emitting None/empty values for
        # downstream validation to reject — an artifact stripped of all refs
        # still fails the non-discarded-needs-refs check, visibly.
        if not (isinstance(path, str) and path.strip()):
            continue
        if not (isinstance(line_start, int) and line_start >= 1):
            continue
        if not (isinstance(line_end, int) and line_end >= line_start):
            line_end = line_start
        source_refs.append({
            "path": path,
            "line_start": line_start,
            "line_end": line_end,
        })
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    return {
        "id": raw.get("id") if isinstance(raw.get("id"), str) and raw.get("id", "").strip() else f"{kind}-{index:04d}",
        "kind": kind,
        "text": text,
        "source_refs": source_refs,
        "verification_status": "needs_verification",
        "metadata": metadata,
    }


def local_gemma_extract(
    chunks: list[dict[str, Any]],
    *,
    base_url: str = "http://127.0.0.1:11434",
    num_ctx: int = DEFAULT_NUM_CTX,
    num_predict: int = DEFAULT_NUM_PREDICT,
    semantic_only: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if os.environ.get("SPOTLIGHT_RLM_FAKE_LOCAL") == "1":
        if semantic_only:
            artifacts = [
                artifact("contradiction", chunk["text"], chunk, index)
                for index, chunk in enumerate(chunks, start=1)
                if chunk_has_semantic_signal(chunk)
            ]
            return artifacts, {"fake_local": True, "semantic_only": True}
        return lite_extract(chunks), {"fake_local": True}
    # Configurable capable extractor: point at the main model's llama-server (sovereign local
    # tier) or OpenRouter (cloud tier) instead of the weak local gemma4:e4b. Env-gated; default
    # stays ollama/e4b. The 12B/31B emit valid JSON where e4b fails.
    _openai_base = os.environ.get("SPOTLIGHT_RLM_OPENAI_BASE_URL")
    if not _openai_base and not ollama_model_available(MODEL):
        raise RLMError(f"Ollama model {MODEL} is not available; run benchmark with --pull-missing only after explicit approval")
    prompt = {
        "task": (
            "Extract source-linked contradiction or relationship artifacts only. Return strict JSON with artifacts[]."
            if semantic_only
            else "Extract source-linked investigation artifacts only. Return strict JSON with artifacts[]."
        ),
        "artifact_schema": {
            "id": "string, unique",
            "kind": "entity | timeline_event | contradiction | lead | discarded",
            "text": "exact entity, date/event, contradiction, or concise lead text",
            "source_refs": [{"path": "one of the provided chunk paths", "line_start": 1, "line_end": 1}],
            "verification_status": "needs_verification",
            "metadata": "object",
        },
        "kind_guidance": [
            "Use kind=entity for emails, handles, URLs, names, organizations, and other concrete identifiers.",
            "Use kind=timeline_event for dated events.",
            "Use kind=contradiction only when provided chunks conflict.",
            "Use kind=lead only for useful source-linked observations that are not an entity, event, or contradiction.",
        ],
        "rules": [
            "Every artifact must have verification_status needs_verification.",
            "Every artifact must cite source_refs from provided chunks.",
            "Every source_ref must use line_start and line_end integer fields.",
            "Do not invent sources, entities, dates, or IDs.",
            "Never use verified, confirmed, or publishable.",
        ],
        "chunks": chunks[:200],
    }
    if semantic_only:
        prompt["rules"].append("Only emit kind=contradiction or kind=lead artifacts; skip simple emails, URLs, handles, and dates.")
    if _openai_base:
        _model = os.environ.get("SPOTLIGHT_RLM_MODEL", MODEL)
        _headers = {"Content-Type": "application/json"}
        _key = os.environ.get("SPOTLIGHT_RLM_API_KEY")
        if _key:
            _headers["Authorization"] = f"Bearer {_key}"
        _oai_body = json.dumps({
            "model": _model,
            "temperature": 0,
            "max_tokens": num_predict or 8192,  # thinking models need room to reason THEN emit JSON
            "messages": [
                {"role": "system", "content": 'You produce strict JSON for source-linked OSINT lead extraction. Output ONLY a JSON object {"artifacts":[...]}.'},
                {"role": "user", "content": json.dumps(prompt)},
            ],
        }).encode("utf-8")
        _base = _openai_base.rstrip("/")
        _url = _base + ("/chat/completions" if _base.endswith("/v1") else "/v1/chat/completions")
        _oai_req = urllib.request.Request(_url, data=_oai_body, headers=_headers)
        with urllib.request.urlopen(_oai_req, timeout=300) as _resp:
            _oai_raw = json.loads(_resp.read().decode("utf-8"))
        _content = ((_oai_raw.get("choices") or [{}])[0].get("message", {}) or {}).get("content") or ""
        _s, _e = _content.find("{"), _content.rfind("}")
        if _s == -1 or _e == -1:
            raise RLMError(f"Extractor returned no JSON object; preview={_content[:200]!r}")
        _parsed = json.loads(_content[_s:_e + 1])
        _raw_artifacts = _parsed.get("artifacts", [])
        if not isinstance(_raw_artifacts, list):
            raise RLMError("Extractor response missing artifacts list")
        _artifacts = [normalize_model_artifact(item, index) for index, item in enumerate(_raw_artifacts, start=1) if isinstance(item, dict)]
        return _artifacts, {"extractor": "openai", "extractor_model": _model, "num_ctx": num_ctx, "semantic_only": semantic_only}
    options: dict[str, Any] = {"temperature": 0, "num_ctx": num_ctx}
    if num_predict:
        options["num_predict"] = num_predict
    body = json.dumps(
        {
            "model": MODEL,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": "You produce strict JSON for source-linked OSINT lead extraction."},
                {"role": "user", "content": json.dumps(prompt)},
            ],
            "options": options,
        }
    ).encode("utf-8")
    req = urllib.request.Request(f"{base_url}/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    content = raw.get("message", {}).get("content", "{}")
    if not isinstance(content, str) or not content.strip():
        raise RLMError("Gemma response content was empty")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        preview = content[:200].replace("\n", "\\n")
        raise RLMError(f"Gemma response was not valid JSON: {exc.msg}; preview={preview!r}") from exc
    raw_artifacts = parsed.get("artifacts", [])
    if not isinstance(raw_artifacts, list):
        raise RLMError("Gemma response missing artifacts list")
    artifacts = [normalize_model_artifact(item, index) for index, item in enumerate(raw_artifacts, start=1) if isinstance(item, dict)]
    return artifacts, {
        "ollama_total_duration": raw.get("total_duration"),
        "num_ctx": num_ctx,
        "num_predict": num_predict,
        "semantic_only": semantic_only,
    }


def build_analysis(request: dict[str, Any], artifacts: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    provider = "deterministic" if request["mode"] == "lite" else "ollama"
    model = None if request["mode"] == "lite" else MODEL
    analysis = {
        "schema_version": "1.0",
        "project": request["project"],
        "run_id": request["run_id"],
        "mode": request["mode"],
        "provider": provider,
        "model": model,
        "created_at": utc_now(),
        "artifacts": artifacts,
        "metrics": metrics,
    }
    errors = validate_analysis(analysis)
    if errors:
        raise RLMError("; ".join(errors))
    return analysis


def run(request_path: str | Path) -> dict[str, Any]:
    request = load_request(request_path)
    if request["mode"] == "off":
        return {"status": "off", "analysis_path": None, "artifact_count": 0}
    start = time.time()
    chunks = read_corpus(request["project"], request["corpus_paths"])
    if request["mode"] == "lite":
        artifacts = lite_extract(chunks)
        extra_metrics: dict[str, Any] = {"input_chunk_count": len(chunks), "rlm_chunk_count": 0, "prefilter": False}
    else:
        if os.environ.get("SPOTLIGHT_RLM_FAKE_LOCAL") != "1" and not ollama_model_available(MODEL):
            raise RLMError(f"Ollama model {MODEL} is not available; run benchmark with --pull-missing only after explicit approval")
        rlm_chunks = select_candidate_chunks(chunks) if request["prefilter"] else chunks
        if request["hybrid"]:
            deterministic_artifacts = lite_extract(rlm_chunks)
            semantic_chunks = select_semantic_chunks(rlm_chunks)
            if semantic_chunks:
                semantic_artifacts, extra_metrics = local_gemma_extract(
                    semantic_chunks,
                    num_ctx=request["num_ctx"],
                    num_predict=request["num_predict"],
                    semantic_only=True,
                )
            else:
                semantic_artifacts = []
                extra_metrics = {"ollama_skipped": True, "num_ctx": request["num_ctx"], "num_predict": request["num_predict"]}
            artifacts = dedupe_artifacts([*deterministic_artifacts, *semantic_artifacts])
            extra_metrics = {"semantic_chunk_count": len(semantic_chunks), "hybrid": True, **extra_metrics}
        else:
            artifacts, extra_metrics = local_gemma_extract(
                rlm_chunks,
                num_ctx=request["num_ctx"],
                num_predict=request["num_predict"],
            )
            extra_metrics = {"semantic_chunk_count": 0, "hybrid": False, **extra_metrics}
        extra_metrics = {
            "input_chunk_count": len(chunks),
            "rlm_chunk_count": len(rlm_chunks),
            "prefilter": request["prefilter"],
            **extra_metrics,
        }
    metrics = {
        "chunk_count": len(chunks),
        "artifact_count": len(artifacts),
        "wall_time_seconds": round(time.time() - start, 3),
        **extra_metrics,
    }
    analysis = build_analysis(request, artifacts, metrics)
    output_path = get_case_dir(request["project"]) / "data" / "rlm-analysis.json"
    write_json(output_path, analysis)
    return {"status": "ok", "analysis_path": str(output_path), "artifact_count": len(artifacts)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Spotlight RLM analysis.")
    parser.add_argument("request_json")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.request_json), indent=2))
        return 0
    except (OSError, RLMError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
