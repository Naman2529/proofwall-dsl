"""The one place the project talks to a language model.

A client has a `name` and `complete(prompt, n=1) -> List[Completion]`. A
Completion carries the text plus token counts and latency, which the
evaluation reports. Anything else (a plain `prompt -> str` function, as the
tests use) is wrapped by as_client().

Providers:
  gemini  GeminiClient, Google's REST API through the standard library.
          Key from GEMINI_API_KEY, model from POLICYC_MODEL.
  fake    FakeClient, scripted replies for tests and offline demos.
POLICYC_LLM picks the provider for default_client(). Adding a provider means
adding one class with `name` and `complete()`.

The API key is never printed, logged, stored or put in an exception message.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Union

try:
    from typing import Protocol
except ImportError:  # Python 3.7
    Protocol = object

DEFAULT_MODEL = "gemini-3.5-flash-lite"   # fast (about 1 s) and accurate on the benchmark
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


@dataclass
class Completion:
    text: str
    prompt_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_s: float = 0.0


class LLMClient(Protocol):
    name: str

    def complete(self, prompt: str, n: int = 1) -> List[Completion]: ...


class LLMError(RuntimeError):
    """A model call failed. kind: quota | auth | network | bad_response."""

    def __init__(self, kind: str, message: str):
        super().__init__("%s: %s" % (kind, message))
        self.kind = kind


def total_tokens(completions: Sequence[Completion]) -> Optional[int]:
    """Prompt + output tokens over all completions; None if no count was reported."""
    counts = [n for c in completions for n in (c.prompt_tokens, c.output_tokens) if n is not None]
    return sum(counts) if counts else None


class FunctionClient:
    """Wraps a plain `prompt -> str` function."""

    name = "function"

    def __init__(self, fn: Callable[[str], str]):
        self.fn = fn

    def complete(self, prompt: str, n: int = 1) -> List[Completion]:
        out = []
        for _ in range(n):
            start = time.perf_counter()
            text = self.fn(prompt)
            out.append(Completion(text, latency_s=time.perf_counter() - start))
        return out


def as_client(llm):
    """Return `llm` if it is already a client, else wrap the function."""
    return llm if hasattr(llm, "complete") else FunctionClient(llm)


class FakeClient:
    """Scripted replies. A list is consumed in order (the last one repeats);
    a callable gets the prompt. Every prompt is recorded in `.prompts`."""

    def __init__(self, replies: Union[List[str], Callable[[str], str]], name: str = "fake"):
        self.replies = replies
        self.name = name
        self.prompts: List[str] = []
        self._next = 0

    def _reply(self, prompt: str) -> str:
        if callable(self.replies):
            return self.replies(prompt)
        text = self.replies[min(self._next, len(self.replies) - 1)]
        self._next += 1
        return text

    def complete(self, prompt: str, n: int = 1) -> List[Completion]:
        self.prompts.append(prompt)
        return [Completion(self._reply(prompt)) for _ in range(n)]


class GeminiClient:
    """Gemini generateContent over HTTPS with retries on 429/503.

    Every failure leaves complete() as an LLMError whose message never
    contains the key: quota (429/503 after retries), auth (missing or
    malformed key, 401/403, or a 400 that names the key), bad_response
    (unparseable or empty answer) and network (everything else).
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 temperature: float = 0.2, base_url: str = DEFAULT_BASE_URL,
                 max_retries: int = 3, backoff_s: float = 2.0, timeout_s: float = 60.0,
                 max_wait_s: float = 60.0):
        raw = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY")
        # Keys pasted from files often end in a newline; anything else odd is rejected.
        self._key = raw.strip() if raw else ""
        self._key_ok = bool(self._key) and all(33 <= ord(ch) < 127 for ch in self._key)
        self.model = model or os.environ.get("POLICYC_MODEL") or DEFAULT_MODEL
        self.temperature = temperature
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.timeout_s = timeout_s
        self.max_wait_s = max_wait_s
        self.name = "gemini:%s" % self.model

    def __repr__(self) -> str:
        return "GeminiClient(model=%r)" % self.model

    def complete(self, prompt: str, n: int = 1) -> List[Completion]:
        if not self._key:
            raise LLMError("auth", "no API key: set GEMINI_API_KEY")
        if not self._key_ok:
            raise LLMError("auth", "GEMINI_API_KEY contains whitespace or control characters")
        if n == 1:
            return self._request(prompt, 1)
        try:
            return self._request(prompt, n)
        except _CandidateCountRejected:
            return [c for _ in range(n) for c in self._request(prompt, 1)]

    # ---- internals ----------------------------------------------------------

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    def _request(self, prompt: str, n: int) -> List[Completion]:
        config = {"temperature": self.temperature}
        if n > 1:
            config["candidateCount"] = n
        body = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                           "generationConfig": config}).encode("utf-8")
        url = "%s/models/%s:generateContent" % (self.base_url, self.model)
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "Content-Type": "application/json", "x-goog-api-key": self._key})
            start = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    raw = resp.read()
            except urllib.error.HTTPError as e:
                message, body_text = self._error_info(e)
                if e.code in (429, 503) and attempt < self.max_retries:
                    time.sleep(self._wait(e, attempt, body_text))
                    continue
                if e.code in (429, 503):
                    raise LLMError("quota", "HTTP %d after %d retries: %s"
                                   % (e.code, self.max_retries, message)) from None
                if e.code in (401, 403) or (e.code == 400 and (
                        "api_key_invalid" in body_text or "api key" in body_text)):
                    raise LLMError("auth", "HTTP %d: %s" % (e.code, message)) from None
                if e.code == 400 and n > 1:
                    raise _CandidateCountRejected() from None
                raise LLMError("network", "HTTP %d: %s" % (e.code, message)) from None
            except urllib.error.URLError as e:
                raise LLMError("network", self._scrub(str(e.reason))) from None
            except (OSError, http.client.HTTPException, ValueError) as e:
                # Connection reset or closed, timeout, truncated body, bad header value.
                raise LLMError("network", self._scrub("%s: %s" % (type(e).__name__, e))) from None
            return self._parse(raw, n, time.perf_counter() - start)
        raise LLMError("quota", "retries exhausted")  # pragma: no cover

    def _wait(self, e: urllib.error.HTTPError, attempt: int, body_text: str = "") -> float:
        """Seconds to wait before retrying: Retry-After header, else Gemini's
        RetryInfo.retryDelay or "retry in Ns" (per-minute free-tier limits),
        else exponential backoff. Always capped at max_wait_s."""
        try:
            wait = float(e.headers.get("Retry-After"))
        except (TypeError, ValueError):
            m = (re.search(r'"retrydelay":\s*"([\d.]+)s"', body_text)
                 or re.search(r"retry in ([\d.]+)\s*s", body_text))
            wait = float(m.group(1)) if m else self.backoff_s * (2 ** attempt)
        return max(0.0, min(wait, self.max_wait_s))

    def _error_info(self, e: urllib.error.HTTPError):
        """(scrubbed message, lowercased body) of an HTTP error response."""
        try:
            text = e.read().decode("utf-8", "replace")
        except (OSError, http.client.HTTPException):
            text = ""
        try:
            message = str(json.loads(text).get("error", {}).get("message", ""))
        except (ValueError, AttributeError):
            message = ""
        return self._scrub(message) or (e.reason or ""), self._scrub(text).lower()

    def _parse(self, raw: bytes, n: int, latency: float) -> List[Completion]:
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise LLMError("bad_response", "response is not JSON") from None
        try:
            candidates = data.get("candidates") if isinstance(data, dict) else None
            if not candidates or not isinstance(candidates, list):
                feedback = (data.get("promptFeedback") if isinstance(data, dict) else None) or {}
                reason = feedback.get("blockReason")
                raise LLMError("bad_response", "no candidates in response%s"
                               % (" (blocked: %s)" % reason if reason else ""))
            usage = data.get("usageMetadata") or {}
            prompt_tokens = usage.get("promptTokenCount")
            output = usage.get("candidatesTokenCount")
            per_candidate = output // len(candidates) if output is not None else None
            out = []
            for i, cand in enumerate(candidates):
                parts = (cand.get("content") or {}).get("parts") or []
                text = "".join(p.get("text", "") for p in parts)
                out.append(Completion(text, prompt_tokens if i == 0 else None, per_candidate, latency))
            return out
        except (AttributeError, TypeError, KeyError):
            raise LLMError("bad_response", "unexpected response shape") from None


class _CandidateCountRejected(Exception):
    """The model does not accept candidateCount > 1; fall back to n calls."""


def default_client(name: Optional[str] = None):
    name = name or os.environ.get("POLICYC_LLM") or "gemini"
    if name == "gemini":
        return GeminiClient()
    if name == "fake":
        return FakeClient(["SUBJECT nobody\n"])
    raise ValueError("unknown LLM provider %r (use gemini or fake)" % name)


def strip_code_fence(text: str) -> str:
    """Models like to wrap code in ``` fences, sometimes with prose around
    them. Keep only the inside of the first fenced block; text without a
    fence is returned as is (the compiler will judge it)."""
    text = text.strip()
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip().startswith("```")]
    if not starts:
        return text
    body = lines[starts[0] + 1:]
    end = next((i for i, line in enumerate(body) if line.strip().startswith("```")), None)
    return "\n".join(body if end is None else body[:end])
