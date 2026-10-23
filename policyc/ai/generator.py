"""Objective 4: English -> DSL with a compiler-driven correction loop.

    english --(LLM)--> DSL --compile--> ok? done
                         ^                 |
                         +-- JSON diagnostics (line, col, expected, hint)

The model's answer is only a candidate. compile_source() decides: if it has
errors, the structured diagnostics go back to the model for another attempt.
Warnings do not trigger a retry (the policy already has one meaning); they
are recorded for the evaluation.

Metrics the evaluation reads from GenerationResult: first-try success
(attempts[0].result.ok), success after correction (ok), correction cycles
(retries), tokens and latency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..compiler import CompileResult, compile_source
from .llm_client import Completion, as_client, default_client, strip_code_fence, total_tokens
from .prompts import Vocabulary, generation_fix_prompt, generation_prompt

__all__ = ["Attempt", "GenerationResult", "Vocabulary", "generate_policy"]


EMPTY_ANSWER = {"code": "G001", "phase": "generator", "severity": "error",
                "message": "the answer contains no policy statements", "line": 1, "col": 1,
                "hint": "output the SUBJECT / RESOURCE declarations and the ALLOW / DENY rules"}


@dataclass
class Attempt:
    dsl: str
    result: CompileResult
    completion: Completion

    @property
    def ok(self) -> bool:
        # An empty program compiles, but it is not an answer.
        return self.result.ok and bool(self.result.ast.statements)

    def errors(self) -> List[dict]:
        if self.result.errors:
            return [d.to_dict() for d in self.result.errors]
        return [] if self.ok else [dict(EMPTY_ANSWER)]


@dataclass
class GenerationResult:
    english: str
    attempts: List[Attempt] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].ok

    @property
    def dsl(self) -> Optional[str]:
        return self.attempts[-1].dsl if self.ok else None

    @property
    def retries(self) -> int:
        return max(len(self.attempts) - 1, 0)

    @property
    def tokens(self) -> Optional[int]:
        if any(total_tokens([a.completion]) is None for a in self.attempts):
            return None
        return total_tokens([a.completion for a in self.attempts])

    @property
    def latency_s(self) -> float:
        return sum(a.completion.latency_s for a in self.attempts)


def generate_policy(english: str, vocabulary: Optional[Vocabulary] = None, llm=None,
                    max_attempts: int = 3) -> GenerationResult:
    client = as_client(llm) if llm is not None else default_client()
    out = GenerationResult(english)
    prompt = generation_prompt(english, vocabulary)
    for _ in range(max_attempts):
        completion = client.complete(prompt)[0]
        dsl = strip_code_fence(completion.text)
        attempt = Attempt(dsl, compile_source(dsl), completion)
        out.attempts.append(attempt)
        if attempt.ok:
            break
        prompt = generation_fix_prompt(english, vocabulary, dsl, attempt.errors())
    return out
