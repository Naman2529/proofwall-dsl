"""Phase 5a: compiler-only optimizer (the baseline the LLM optimizer is compared against).

Removes IR rules that cannot change any decision:
  * exact duplicates (same effect, subject, action, resource, condition key);
  * ALLOW rules beaten by an unconditional DENY for the same request;
  * conditional rules already covered by an unconditional rule of the same effect.

DENY rules are never dropped in a way that leaves a request without an
explicit DENY: they are guard rails if someone later adds a broader ALLOW.

The output of this module is NOT trusted. compiler.py runs the verifier on
(original, optimized) and only keeps the result if it is proven equivalent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from .ir import ALLOW, DENY, IRProgram, IRRule


@dataclass
class Removal:
    rule: IRRule
    reason: str


@dataclass
class OptimizationResult:
    original: IRProgram
    optimized: IRProgram
    removed: List[Removal] = field(default_factory=list)

    def summary(self) -> str:
        out = ["optimizer: %d IR rules -> %d" % (len(self.original.rules), len(self.optimized.rules))]
        for rm in self.removed:
            out.append("  removed %s (line %d): %s" % (rm.rule.id, rm.rule.line, rm.reason))
        return "\n".join(out)


def optimize(ir: IRProgram) -> OptimizationResult:
    kept: List[IRRule] = []
    removed: List[Removal] = []

    def triple(r: IRRule) -> Tuple[str, str, str]:
        return (r.subject, r.action, r.resource)

    for rule in ir.rules:
        same_request = [r for r in ir.rules if triple(r) == triple(rule) and r is not rule]
        reason = None

        dup = next((r for r in kept if r.effect == rule.effect and triple(r) == triple(rule)
                    and r.cond_key == rule.cond_key), None)
        if dup is not None:
            reason = "duplicate of %s" % dup.id
        elif rule.effect == ALLOW:
            killer = next((r for r in same_request if r.effect == DENY and r.condition is None), None)
            if killer is not None:
                reason = "can never take effect, %s always denies" % killer.id
        if reason is None and rule.condition is not None:
            cover = next((r for r in same_request if r.effect == rule.effect
                          and r.condition is None), None)
            if cover is not None:
                reason = "covered by unconditional %s" % cover.id

        if reason is None:
            kept.append(rule)
        else:
            removed.append(Removal(rule, reason))

    return OptimizationResult(ir, ir.with_rules(kept), removed)
