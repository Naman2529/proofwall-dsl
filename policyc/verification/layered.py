"""The verification pipeline from the README, cheapest layer first:

    1. well-formedness  -> is the candidate even a valid rule list?
    2. differential test -> fast rejection of obviously broken candidates
    3. Z3 proof          -> the actual guarantee

verify_candidate() is the one function the optimizer loop should call.
"""
from __future__ import annotations

import time

from . import z3_backend
from .differential import verify_differential
from .enumerate_backend import verify_enumerate
from .model import MalformedPolicy, normalize
from .result import VerificationResult
from .z3_backend import verify_z3

BACKENDS = ("layered", "z3", "enumerate", "differential")


def verify_candidate(original, candidate, mode: str = "non-weakening", backend: str = "layered",
                     timeout_ms: int = 10000, max_attrs: int = 16, find_all: bool = False,
                     cond_adapter=None) -> VerificationResult:
    """Check `candidate` against `original`.

    mode "non-weakening": candidate may never ALLOW what the original DENIES
                          (the ProofWall guarantee; stricter candidates pass).
    mode "equivalent":    candidate must make exactly the same decisions.

    A result with proven=False means DO NOT ACCEPT, whether it carries a
    counterexample or not (timeouts, caps and malformed input also fail closed).
    """
    if backend not in BACKENDS:
        raise ValueError("backend must be one of %s" % (BACKENDS,))
    t0 = time.perf_counter()

    # Layer 1: well-formedness (fail closed on anything we cannot read).
    try:
        normalize(original, cond_adapter)
        normalize(candidate, cond_adapter)
    except MalformedPolicy as e:
        return VerificationResult(proven=False, mode=mode, backend="wellformed", layer="wellformed",
                                  reason="malformed policy: %s" % e, elapsed_s=time.perf_counter() - t0)

    if backend == "differential":
        return verify_differential(original, candidate, mode, find_all=find_all, cond_adapter=cond_adapter)
    if backend == "enumerate":
        return verify_enumerate(original, candidate, mode, max_attrs, find_all, cond_adapter)
    if backend == "z3":
        return verify_z3(original, candidate, mode, timeout_ms, find_all, cond_adapter)

    # Layer 2: differential testing as a fast reject.
    quick = verify_differential(original, candidate, mode, find_all=find_all, cond_adapter=cond_adapter)
    if quick.counterexamples:
        quick.elapsed_s = time.perf_counter() - t0
        return quick

    # Layer 3: the proof. Without Z3, exact enumeration (still fails closed past max_attrs).
    if z3_backend.z3 is None:
        proof = verify_enumerate(original, candidate, mode, max_attrs, find_all, cond_adapter)
        proof.layer = "enumerate (after differential, z3 not installed)"
    else:
        proof = verify_z3(original, candidate, mode, timeout_ms, find_all, cond_adapter)
        proof.layer = "z3 (after differential)"
    proof.elapsed_s = time.perf_counter() - t0
    return proof
