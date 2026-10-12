"""Result objects shared by every verification backend.

The field names mirror what the compiler, the CLI and ai/llm_optimizer.py
already expect from verifier.VerificationResult (equivalent, counterexample),
plus the extra detail the Z3 backend can give.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Counterexample:
    """One concrete request on which the candidate differs from the original."""

    subject: str
    action: str
    resource: str
    env: Dict[str, bool]
    original: str   # "ALLOW" or "DENY"
    candidate: str  # "ALLOW" or "DENY"

    @property
    def direction(self) -> str:
        """'weaker' = candidate allows what the original denies (the dangerous one)."""
        if self.candidate == "ALLOW" and self.original == "DENY":
            return "weaker"
        return "stricter"

    def __str__(self) -> str:
        where = "%s %s %s" % (self.subject, self.action, self.resource)
        attrs = ", ".join("%s=%s" % (k, v) for k, v in sorted(self.env.items()))
        if attrs:
            where += " (%s)" % attrs
        return "%s: original says %s, candidate says %s" % (where, self.original, self.candidate)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject": self.subject,
            "action": self.action,
            "resource": self.resource,
            "attributes": dict(self.env),
            "original": self.original,
            "candidate": self.candidate,
            "direction": self.direction,
        }


@dataclass
class VerificationResult:
    proven: bool                      # True only if the whole check finished and passed
    mode: str                         # "non-weakening" or "equivalent"
    backend: str                      # "z3", "enumerate", "differential", "wellformed"
    layer: str = ""                   # which layer of the pipeline produced this verdict
    requests_checked: int = 0         # distinct (subject, action, resource) requests covered
    cases_checked: int = 0            # z3: solver queries; enumerate/differential: attribute assignments
    counterexamples: List[Counterexample] = field(default_factory=list)
    reason: str = ""                  # why it is not proven when there is no counterexample
    elapsed_s: float = 0.0

    # --- convenience -------------------------------------------------------

    @property
    def counterexample(self) -> Optional[Counterexample]:
        return self.counterexamples[0] if self.counterexamples else None

    @property
    def equivalent(self) -> bool:
        """Drop-in for the old verifier: true only for a proven full equivalence."""
        return self.proven and self.mode == "equivalent"

    @property
    def verdict(self) -> str:
        if self.proven:
            return "PROVEN"
        if self.counterexamples:
            return "REJECTED"
        return "UNPROVEN"

    def summary(self) -> str:
        head = "%s (%s, %s)" % (self.verdict, self.mode, self.layer or self.backend)
        if self.proven:
            return "%s: %d requests, %d %s in %.3fs" % (
                head, self.requests_checked, self.cases_checked,
                "solver queries" if self.backend == "z3" else "cases", self.elapsed_s)
        if self.counterexamples:
            return "%s: %s" % (head, self.counterexamples[0])
        return "%s: %s" % (head, self.reason or "no verdict")

    def feedback(self, limit: int = 3) -> str:
        """Text to hand back to the LLM optimizer for its retry."""
        if self.proven:
            return "The proposal was proven safe."
        if not self.counterexamples:
            return "The proposal could not be proven safe: %s" % (self.reason or "unknown")
        lines = ["The proposal is NOT safe. Concrete requests where it differs from the original:"]
        for cex in self.counterexamples[:limit]:
            lines.append("  - %s" % cex)
        lines.append("Keep every condition needed to preserve these decisions, then try again.")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "proven": self.proven,
            "mode": self.mode,
            "backend": self.backend,
            "layer": self.layer,
            "requests_checked": self.requests_checked,
            "cases_checked": self.cases_checked,
            "counterexamples": [c.to_dict() for c in self.counterexamples],
            "reason": self.reason,
            "elapsed_s": round(self.elapsed_s, 6),
        }
