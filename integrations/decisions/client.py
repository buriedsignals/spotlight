"""Stdlib-only boundary for typed decision models on the OpenRouter Decisions API.

A decision provider answers typed questions (noul / choice / score) about a
JSON state and returns probabilities. It never generates text. Spotlight uses
the answers only as review signals that can lower confidence or request human
attention; they are never evidence, verification, or corroboration.

The OpenRouter provider always sends ``provider: {"zdr": true,
"allow_fallbacks": false}``. OpenRouter enforces provider filters on the
Decisions endpoint, so a request that cannot be served under zero data
retention fails instead of silently routing elsewhere. Sensitive mode blocks
all egress before a request is built. With Engine installed, RedactingProvider
masks the member's protected sources (``bsig wiki redact``) in every text
before it is sent.

Another endpoint (a newsroom's own inference, or a later deployment) may serve
the same questions if it speaks the same Decisions API. It is named only in
the install config Engine writes (``endpoint_from_config``): https, or plain
http on loopback, with its own named credential. The OpenRouter key and the
zero-retention provider body go only to openrouter.ai. A server with another
API needs an adapter here, not just a URL.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _engine import bsig_path  # noqa: E402

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.13"
KEY_ENV = "OPENROUTER_API_KEY"
REQUIRED_PROVIDER = {"zdr": True, "allow_fallbacks": False}
RETRYABLE = {429, 500, 502, 503, 529}
OPENROUTER_HOST = "openrouter.ai"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
ADAPTERS = ("openrouter-decisions",)


def is_openrouter(host: str) -> bool:
    return host == OPENROUTER_HOST or host.endswith("." + OPENROUTER_HOST)


def endpoint_from_config(block: Mapping[str, Any] | None) -> tuple[str, str]:
    """(url, credential env name) for integrations.decisions.endpoint, or the
    OpenRouter default. Raises DecisionUnavailable for anything it cannot use."""
    if not block:
        return DECISIONS_URL, KEY_ENV
    if not isinstance(block, Mapping) or set(block) - {"url", "credential_env", "api"}:
        raise DecisionUnavailable("integrations.decisions.endpoint must give url, credential_env and api only")
    url, api, credential = block.get("url"), block.get("api"), block.get("credential_env")
    if api not in ADAPTERS:
        raise DecisionUnavailable(f"decision endpoint api {api!r} has no adapter; supported: {', '.join(ADAPTERS)}")
    try:
        parsed = urlparse(url if isinstance(url, str) else "")
        host = (parsed.hostname or "").lower()
        parsed.port  # an invalid port raises here
    except ValueError:
        raise DecisionUnavailable("the decision endpoint url is malformed") from None
    if parsed.username or parsed.password:
        raise DecisionUnavailable("a decision endpoint url must not carry credentials")
    if not (parsed.scheme == "https" or (parsed.scheme == "http" and host in LOOPBACK)) or not host:
        raise DecisionUnavailable("a decision endpoint must be https, or http on loopback")
    if is_openrouter(host):
        return url, KEY_ENV
    if not isinstance(credential, str) or not credential or credential == KEY_ENV:
        raise DecisionUnavailable(f"a non-OpenRouter decision endpoint needs its own credential_env (never {KEY_ENV})")
    return url, credential


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
        url: str = DECISIONS_URL,
    ) -> None:
        if sensitive:
            raise DecisionUnavailable("sensitive mode blocks decision-model egress")
        if not api_key:
            raise DecisionUnavailable(f"{KEY_ENV} is not set" if url == DECISIONS_URL else "the decision endpoint's credential is not set")
        self._url = url
        self._openrouter = is_openrouter((urlparse(url).hostname or "").lower())
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
        body = {"model": self.model, "state": state, "questions": dict(questions)}
        if self._openrouter:
            body["provider"] = dict(REQUIRED_PROVIDER)  # OpenRouter's routing filter; meaningless elsewhere
        return body

    def decide(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        body = self.request_body(state, questions)
        if self._openrouter and body.get("provider") != REQUIRED_PROVIDER:
            raise DecisionUnavailable("refusing to send without zero-data-retention routing")
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last_error = "unknown error"
        for attempt in range(self._retries + 1):
            request = Request(
                self._url,
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


def _strings(value: Any, out: list[str], skip_keys: frozenset[str] = frozenset()) -> None:
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in skip_keys:
                _strings(item, out, skip_keys)
    elif isinstance(value, list):
        for item in value:
            _strings(item, out, skip_keys)


def _replace(value: Any, texts: list[str], skip_keys: frozenset[str] = frozenset()) -> Any:
    if isinstance(value, str):
        return texts.pop(0)
    if isinstance(value, dict):
        return {key: item if key in skip_keys else _replace(item, texts, skip_keys) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, texts, skip_keys) for item in value]
    return value


# A question's type is the API's vocabulary, and criteria keys are the options
# answers name: neither is case text.
QUESTION_SKIP = frozenset({"type"})


class RedactingProvider:
    """Masks protected sources in everything a provider would send.

    Every string in the state, and every question text and criterion
    description, goes through ``redact`` in one call before ``decide``; dict
    keys stay, so the answers still match the criteria."""

    def __init__(self, provider: Any, redact: Callable[[list[str]], list[str]]) -> None:
        self._provider = provider
        self._redact = redact
        self.model = provider.model

    def decide(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        texts: list[str] = []
        _strings(state, texts)
        for question in questions.values():
            _strings(question, texts, QUESTION_SKIP)
        masked = self._redact(texts) if texts else []
        if len(masked) != len(texts) or not all(isinstance(t, str) for t in masked):
            raise DecisionUnavailable("redaction returned a different number of texts")
        state = _replace(state, masked)
        questions = {key: _replace(question, masked, QUESTION_SKIP) for key, question in questions.items()}
        return self._provider.decide(state, questions)


def wiki_redactor(workspace: Path | None) -> Callable[[list[str]], list[str]] | None:
    """`bsig wiki redact` over the member's wiki. Without a wiki in the
    install config there is no protected-source register and nothing to mask:
    None. With one, a redaction that cannot run or fails stops the request
    rather than sending unmasked text."""
    if workspace is None:
        return None  # no wiki in the install config: no protected-source register
    bsig = bsig_path()
    if not bsig:
        def unavailable(_texts: list[str]) -> list[str]:
            raise DecisionUnavailable("Engine's bsig was not found, so protected sources cannot be masked")
        return unavailable
    command = [bsig, "--json", "wiki", "redact", "--workspace", str(workspace)]

    def redact(texts: list[str]) -> list[str]:
        try:
            done = subprocess.run(command, input=json.dumps({"texts": texts}), capture_output=True,
                                  text=True, encoding="utf-8", timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DecisionUnavailable(f"protected-source redaction failed: {type(exc).__name__}") from None
        if done.returncode != 0:
            raise DecisionUnavailable(f"protected-source redaction exited {done.returncode}")
        for line in reversed(done.stdout.splitlines()):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict) or event.get("event") != "result":
                continue
            result = (event.get("data") or {}).get("result") if isinstance(event.get("data"), dict) else None
            masked = result.get("texts") if isinstance(result, dict) else None
            if isinstance(masked, list):
                return masked
        raise DecisionUnavailable("protected-source redaction returned no texts")

    return redact


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
