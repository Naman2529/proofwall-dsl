"""Objective 5 (AI half): LLM-proposed, compiler-verified optimization.

The model proposes rewritten policies. Each proposal is only a candidate and
goes through these stages in order, stopping at the first failure:

    compile       it must compile (and contain at least one statement)
    declarations  SUBJECT / RESOURCE declarations must be unchanged
    verifier      verifier.verify() must prove it: mode "equivalent" (default,
                  the same decision for every request) or "non-weakening"
    not_smaller   it must be strictly smaller (IR rules, then statements,
                  then condition nodes)
    accepted      otherwise

Each attempt asks for `samples` proposals; the smallest accepted one wins and
the loop stops. Failures go back to the model as feedback (compiler errors,
counterexamples, "not smaller"). If nothing is accepted the original policy
is kept. In the default "equivalent" mode the AI can make a policy smaller,
never different. In "non-weakening" mode an accepted rewrite may deny more
than the original: such proposals are marked `stricter`, never count as
"improving", and lose to an equivalent proposal of any size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..compiler import CompileResult, compile_source
from ..verifier import VerificationResult, verify
from .llm_client import Completion, as_client, default_client, strip_code_fence, total_tokens
from .metrics import Size, drops_deny_guard, same_declarations, size, tier
from .prompts import optimization_prompt

STAGES = ("compile", "declarations", "verifier", "not_smaller", "accepted")


@dataclass
class Proposal:
    dsl: str
    attempt: int
    stage: str
    compiled: CompileResult
    verification: Optional[VerificationResult]
    size: Optional[Size]
    drops_deny_guard: bool
    completion: Completion
    note: str = ""                  # declaration difference, for feedback and reports
    # mode "non-weakening" only: True if proven safe but it denies some request
    # the original allowed. None when not checked.
    stricter: Optional[bool] = None

    @property
    def accepted(self) -> bool:
        return self.stage == "accepted"

    @property
    def tier(self) -> str:
        if self.stage == "compile":
            return tier(self.compiled) if not self.compiled.ok else "invalid"
        # A stricter rewrite changes decisions, so it is never "improving".
        return tier(self.compiled, verified=self.stage in ("not_smaller", "accepted"),
                    smaller=self.accepted and not self.stricter)


@dataclass
class OptimizeResult:
    original: CompileResult
    proposals: List[Proposal] = field(default_factory=list)
    mode: str = "equivalent"
    chosen: Optional[Proposal] = None
    call_latencies: List[float] = field(default_factory=list)   # one per model call

    @property
    def accepted(self) -> Optional[Proposal]:
        return self.chosen

    @property
    def rules_before(self) -> int:
        return len(self.original.ir.rules)

    @property
    def rules_after(self) -> int:
        return len(self.chosen.compiled.ir.rules) if self.chosen else self.rules_before

    @property
    def statements_before(self) -> int:
        return len(self.original.ast.policies)

    @property
    def statements_after(self) -> int:
        return len(self.chosen.compiled.ast.policies) if self.chosen else self.statements_before

    @property
    def stage_counts(self) -> Dict[str, int]:
        return {s: sum(p.stage == s for p in self.proposals) for s in STAGES}

    @property
    def tokens(self) -> Optional[int]:
        return total_tokens([p.completion for p in self.proposals])

    @property
    def latency_s(self) -> float:
        return sum(self.call_latencies)

    @property
    def final_source(self) -> str:
        return self.chosen.dsl.rstrip() + "\n" if self.chosen else self.original.source


def _check(original: CompileResult, dsl: str, attempt: int, completion: Completion,
           mode: str) -> Proposal:
    compiled = compile_source(dsl)

    def proposal(stage, verification=None, note=""):
        ok = compiled.ok
        return Proposal(dsl, attempt, stage, compiled, verification,
                        size(compiled) if ok else None,
                        drops_deny_guard(original.ir, compiled.ir) if ok else False,
                        completion, note)

    if not compiled.ok or not compiled.ast.statements:
        return proposal("compile")
    diff = same_declarations(original.ir, compiled.ir)
    if diff:
        return proposal("declarations", note=diff)
    verification = verify(original.ir, compiled.ir, mode=mode)
    if not verification.proven:
        return proposal("verifier", verification)
    stage = "not_smaller" if size(compiled) >= size(original) else "accepted"
    p = proposal(stage, verification)
    if mode != "equivalent":
        p.stricter = not verify(original.ir, compiled.ir, mode="equivalent").proven
    return p


def _feedback(p: Proposal, original: CompileResult, label: str) -> str:
    if p.stage == "compile":
        if not p.compiled.errors:
            return "%s was empty: output the whole rewritten policy." % label
        return "%s does not compile: %s" % (label, "; ".join(
            "line %d: %s" % (d.line, d.message) for d in p.compiled.errors))
    if p.stage == "declarations":
        return ("%s changed the declarations (%s). Keep the SUBJECT and RESOURCE lines "
                "unchanged." % (label, p.note))
    if p.stage == "verifier":
        return "%s: %s" % (label, p.verification.feedback(limit=3))
    rules, statements, _ = size(original)
    return ("%s is correct but not smaller than the original (%d IR rules, %d statements). "
            "Look for duplicates, rules an unconditional rule already covers, and rules "
            "that can be merged with OR or ACCESS." % (label, rules, statements))


def optimize_with_llm(source: str, llm=None, max_attempts: int = 3, samples: int = 1,
                      mode: str = "equivalent") -> OptimizeResult:
    original = compile_source(source)
    if not original.ok:
        raise ValueError("the original policy does not compile")
    client = as_client(llm) if llm is not None else default_client()
    out = OptimizeResult(original, mode=mode)
    warnings = [d.to_dict() for d in original.warnings]
    feedback = None
    for attempt in range(1, max_attempts + 1):
        prompt = optimization_prompt(source, original.ir.dump(), warnings, feedback)
        completions = client.complete(prompt, n=samples)
        # Candidates from one request share its latency; sequential fallbacks add up.
        out.call_latencies.append(max(c.latency_s for c in completions)
                                  if len({c.latency_s for c in completions}) == 1
                                  else sum(c.latency_s for c in completions))
        batch = [_check(original, strip_code_fence(c.text), attempt, c, mode)
                 for c in completions]
        out.proposals.extend(batch)
        accepted = [p for p in batch if p.accepted]
        if accepted:
            # Same meaning beats smaller-but-stricter; then the smallest wins.
            out.chosen = min(accepted, key=lambda p: (bool(p.stricter), p.size))
            break
        feedback = "\n".join(_feedback(p, original, "Proposal %d" % i)
                             for i, p in enumerate(batch, 1))
    return out
