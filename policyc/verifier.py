"""Phase 5b: equivalence verifier (translation validation, Necula PLDI 2000).

OWNERSHIP NOTE
    This file is the hand-off point to the security verification part of the
    team. What is here is a complete, working baseline so the compiler can
    gate its own optimizer and so `python -m policyc verify` works. The
    verification owner can keep it as is, extend it (more reports, metrics,
    a "candidate is stricter / looser" mode), or swap the enumeration for an
    SMT backend (Z3) when policies get too big. Keep the `verify()` signature
    and `VerificationResult` fields stable: compiler.py, the CLI and the AI
    optimizer hook (policyc/ai/llm_optimizer.py) all depend on them.

The question: do two IRs make the same decision for every possible request?
The DSL is small, so the request space is finite: every subject, every
resource, every action it supports, and every true/false combination of the
attributes used by that request's rules (in either policy). We check them all
with ir.decide(). Any difference is a rejection with a concrete counterexample.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from . import logic
from .ir import IRProgram, decide


@dataclass
class Counterexample:
    subject: str
    action: str
    resource: str
    env: Dict[str, bool]
    original: str
    candidate: str

    def __str__(self) -> str:
        attrs = ""
        if self.env:
            attrs = " (%s)" % ", ".join("%s=%s" % kv for kv in sorted(self.env.items()))
        return "%s %s %s%s: original says %s, candidate says %s" % (
            self.subject, self.action, self.resource, attrs, self.original, self.candidate)


@dataclass
class VerificationResult:
    equivalent: bool
    cases_checked: int
    counterexample: Optional[Counterexample] = None

    def __str__(self) -> str:
        if self.equivalent:
            return "VERIFIED: same decision in all %d cases checked" % self.cases_checked
        return "REJECTED: %s" % self.counterexample


def _union(a: List[str], b: List[str]) -> List[str]:
    return a + [x for x in b if x not in a]


def verify(original: IRProgram, candidate: IRProgram) -> VerificationResult:
    subjects = _union(original.subjects, candidate.subjects)
    resources = _union(list(original.resources), list(candidate.resources))
    cases = 0
    for s in subjects:
        for r in resources:
            actions = _union(original.resources.get(r, []), candidate.resources.get(r, []))
            for a in actions:
                rules = original.rules_for(s, a, r) + candidate.rules_for(s, a, r)
                names = set()
                for rule in rules:
                    names |= logic.attrs(rule.condition)
                for env in logic.assignments(names):
                    cases += 1
                    d1 = decide(original, s, a, r, env)
                    d2 = decide(candidate, s, a, r, env)
                    if d1 != d2:
                        return VerificationResult(False, cases,
                                                  Counterexample(s, a, r, env, d1, d2))
    return VerificationResult(True, cases)
