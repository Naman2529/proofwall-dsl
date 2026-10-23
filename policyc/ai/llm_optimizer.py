"""Objective 5 (AI half): LLM-proposed, compiler-verified optimization.

STATUS: loop implemented; prompt is a placeholder and no real model is
connected (see llm_client.py).

The model proposes a rewritten policy. The compiler checks it compiles, then
verifier.verify() checks it makes the same decision for every request. Only
then is it accepted. On rejection the concrete counterexample is sent back.
If every attempt fails we keep the original: the AI can make a policy
smaller, never different.

TODO(AI part):
  * write the real prompt in build_optimize_prompt() (send the IR dump and
    the source; ask for a smaller equivalent policy);
  * metrics for the evaluation: accepted vs rejected proposals and rule
    reduction, both available on OptimizeResult.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..compiler import CompileResult, compile_source
from ..verifier import VerificationResult, verify
from .llm_client import LLMClient, default_client, strip_code_fence


@dataclass
class Proposal:
    dsl: str
    compiled: CompileResult
    verification: Optional[VerificationResult]   # None if it did not compile

    @property
    def accepted(self) -> bool:
        return self.verification is not None and self.verification.equivalent


@dataclass
class OptimizeResult:
    original: CompileResult
    proposals: List[Proposal] = field(default_factory=list)

    @property
    def accepted(self) -> Optional[Proposal]:
        return next((p for p in self.proposals if p.accepted), None)

    @property
    def rules_before(self) -> int:
        return len(self.original.ir.rules)

    @property
    def rules_after(self) -> int:
        best = self.accepted
        return len(best.compiled.ir.rules) if best else self.rules_before


def build_optimize_prompt(source: str, ir_dump: str, feedback: Optional[str]) -> str:
    # TODO(AI part): placeholder prompt. Replace with the real one.
    prompt = ("Rewrite this policy with fewer rules but exactly the same decisions. "
              "Output only DSL.\n\nPolicy:\n%s\n\nIR:\n%s\n" % (source, ir_dump))
    if feedback:
        prompt += "\nYour previous attempt was wrong: %s\n" % feedback
    return prompt


def optimize_with_llm(source: str, llm: Optional[LLMClient] = None,
                      max_attempts: int = 3) -> OptimizeResult:
    original = compile_source(source)
    if not original.ok:
        raise ValueError("the original policy does not compile")
    llm = llm or default_client()
    out = OptimizeResult(original)
    feedback = None
    for _ in range(max_attempts):
        dsl = strip_code_fence(llm(build_optimize_prompt(source, original.ir.dump(), feedback)))
        compiled = compile_source(dsl)
        if not compiled.ok:
            out.proposals.append(Proposal(dsl, compiled, None))
            feedback = "it does not compile: " + "; ".join(
                "line %d: %s" % (d.line, d.message) for d in compiled.errors)
            continue
        verdict = verify(original.ir, compiled.ir)
        out.proposals.append(Proposal(dsl, compiled, verdict))
        if verdict.equivalent:
            break
        feedback = str(verdict.counterexample)
    return out
