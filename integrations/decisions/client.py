"""Stdlib-only boundary for typed decision models on the OpenRouter Decisions API.

A decision provider answers typed questions (noul / choice / score) about a
JSON state and returns probabilities. It never generates text. Spotlight uses
the answers only as review signals that can lower confidence or request human
attention; they are never evidence, verification, or corroboration.

The OpenRouter provider always sends ``provider: {"zdr": true,
"allow_fallbacks": false}``. OpenRouter enforces provider filters on the
Decisions endpoint, so a request that cannot be served under zero data
retention fails instead of silently routing elsewhere. Sensitive mode blocks
all egress before a request is built.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.13"
KEY_ENV = "OPENROUTER_API_KEY"
REQUIRED_PROVIDER = {"zdr": True, "allow_fallbacks": False}
RETRYABLE = {429, 500, 502, 503, 529}


class DecisionUnavailable(RuntimeError):
    """The provider could not answer; callers record 'unavailable', never a guess."""


class DecisionProvider(Protocol):
    model: str

    def decide(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        """Return the raw response: {"model", "answers", "usage", ...}."""


class JevOpenRouterProvider:
    """OpenRouter Decisions API (default model typesafe/jev-1.13), pinned to zero data retention."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        sensitive: bool = False,
        timeout: float = 30.0,
        retries: int = 2,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        if sensitive:
            raise DecisionUnavailable("sensitive mode blocks decision-model egress")
        if not api_key:
            raise DecisionUnavailable(f"{KEY_ENV} is not set")
        self._key = api_key
        self.model = model
        self._timeout = timeout
        self._retries = retries
        self._open = opener or urlopen

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **kwargs: Any) -> "JevOpenRouterProvider":
        values = os.environ if env is None else env
        return cls(values.get(KEY_ENV, ""), **kwargs)

    def request_body(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        return {"model": self.model, "provider": dict(REQUIRED_PROVIDER), "state": state, "questions": dict(questions)}

    def decide(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        body = self.request_body(state, questions)
        if body.get("provider") != REQUIRED_PROVIDER:
            raise DecisionUnavailable("refusing to send without zero-data-retention routing")
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last_error = "unknown error"
        for attempt in range(self._retries + 1):
            request = Request(
                DECISIONS_URL,
                data=raw,
                headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
                method="POST",
            )
            started = time.monotonic()
            try:
                with self._open(request, timeout=self._timeout) as response:
                    payload = json.load(response)
            except HTTPError as exc:
                last_error = f"HTTP {exc.code}"
                if exc.code in RETRYABLE and attempt < self._retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise DecisionUnavailable(last_error) from None
            except (URLError, TimeoutError, OSError, ValueError) as exc:
                last_error = type(exc).__name__
                if attempt < self._retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise DecisionUnavailable(last_error) from None
            if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
                raise DecisionUnavailable("response has no answers object")
            problems = [f"{key}: {problem}" for key, question in questions.items()
                        if (problem := answer_problem(question, payload["answers"].get(key)))]
            if problems:
                raise DecisionUnavailable("malformed answers: " + "; ".join(problems[:5]))
            payload["latency_ms"] = round((time.monotonic() - started) * 1000)
            return payload
        raise DecisionUnavailable(last_error)


def _probability(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= float(value) <= 1.0


def answer_problem(question: Mapping[str, Any], answer: Any) -> str | None:
    """Why an answer does not fit its question's primitive and criteria, or None when it does."""
    if not isinstance(answer, dict):
        return "missing"
    kind = question.get("type")
    if kind == "noul":
        return None if _probability(answer.get("noul")) else "noul is not a probability"
    if kind == "choice":
        options = set(question.get("criteria") or {})
        probabilities = answer.get("probabilities")
        choice = answer.get("choice")
        if not isinstance(choice, str) or choice not in options:
            return "choice is not one of the criteria"
        if not isinstance(probabilities, dict) or not probabilities or not set(probabilities) <= options:
            return "probabilities do not match the criteria"
        if choice not in probabilities:
            return "the selected choice has no probability"
        if not all(_probability(v) for v in probabilities.values()):
            return "probabilities are not numbers between 0 and 1"
        return None
    return f"unsupported question type {kind!r}"


class FakeProvider:
    """Deterministic in-process provider for tests: answers come from a callback."""

    def __init__(self, answer: Callable[[Any, str, Mapping[str, Any]], dict[str, Any]], model: str = "fake/decisions-1") -> None:
        self._answer = answer
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def decide(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append({"state": state, "questions": dict(questions)})
        answers = {qid: self._answer(state, qid, question) for qid, question in questions.items()}
        return {"model": self.model, "answers": answers, "usage": {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}}
