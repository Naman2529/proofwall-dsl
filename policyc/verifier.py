"""Phase 5b: verifier (translation validation, Necula PLDI 2000).

The compiler, the CLI and the AI optimizer hook (policyc/ai/llm_optimizer.py)
call verify() here. The work is done by policyc.verification (the security
verification part): a cheap differential test, then a Z3 proof, or exact
enumeration if z3-solver is not installed. See docs/VERIFICATION.md.

Modes:
  equivalent     (default) the candidate makes the same decision for every
                 request. This is what an optimizer must preserve.
  non-weakening  the candidate never ALLOWs what the original DENIES; it may
                 deny more.

Anything short of a full proof (timeout, enumeration cap, malformed input) is
a rejection: check `result.proven`, never the absence of a counterexample.
"""

from __future__ import annotations

from .verification import Counterexample, VerificationResult, verify_candidate

__all__ = ["Counterexample", "VerificationResult", "verify"]


def verify(original, candidate, mode: str = "equivalent", backend: str = "layered",
           **options) -> VerificationResult:
    return verify_candidate(original, candidate, mode=mode, backend=backend, **options)
