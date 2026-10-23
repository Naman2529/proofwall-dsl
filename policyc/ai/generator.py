"""Objective 4: English -> DSL with a compiler-driven correction loop.

STATUS: loop implemented and tested with a fake LLM; prompts are placeholders
and no real model is connected (see llm_client.py).

    english --(LLM)--> DSL --compile--> ok? done
                         ^                 |
                         +-- JSON diagnostics (line, col, expected, hint)

TODO(AI part):
  * write the real prompts in build_prompt() / build_fix_prompt()
    (include the grammar from the README and one or two examples);
  * pick max_attempts from experiments;
  * the metrics the README promises (first-try parse rate, rate after
    correction, average retries) can be computed from GenerationResult.attempts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

from ..compiler import CompileResult, compile_source
from .llm_client import LLMClient, default_client, strip_code_fence


@dataclass
class Attempt:
    dsl: str
    result: CompileResult


@dataclass
class GenerationResult:
    english: str
    attempts: List[Attempt] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].result.ok

    @property
    def dsl(self) -> Optional[str]:
        return self.attempts[-1].dsl if self.ok else None

    @property
    def retries(self) -> int:
        return max(len(self.attempts) - 1, 0)


def build_prompt(english: str) -> str:
    # TODO(AI part): placeholder prompt. Replace with the real one.
    return ("Translate this access-control requirement into the policyc DSL. "
            "Output only DSL.\n\nRequirement:\n%s\n" % english)


def build_fix_prompt(english: str, dsl: str, diagnostics: List[dict]) -> str:
    # TODO(AI part): placeholder prompt. Replace with the real one.
    return ("Your policy did not compile. Fix it and output only DSL.\n\n"
            "Requirement:\n%s\n\nYour policy:\n%s\n\nCompiler errors (JSON):\n%s\n"
            % (english, dsl, json.dumps(diagnostics, indent=2)))


def generate_policy(english: str, llm: Optional[LLMClient] = None,
                    max_attempts: int = 3) -> GenerationResult:
    llm = llm or default_client()
    out = GenerationResult(english)
    prompt = build_prompt(english)
    for _ in range(max_attempts):
        dsl = strip_code_fence(llm(prompt))
        result = compile_source(dsl)
        out.attempts.append(Attempt(dsl, result))
        if result.ok:
            break
        prompt = build_fix_prompt(english, dsl, [d.to_dict() for d in result.errors])
    return out
