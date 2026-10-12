"""Security verification for policyc: prove an optimized policy is never weaker.

    from policyc.verification import verify_candidate
    result = verify_candidate(original_ir, candidate_ir)       # mode="non-weakening"
    if not result.proven:
        retry_prompt = result.feedback()                       # counterexample for the LLM
"""
from .differential import verify_differential
from .enumerate_backend import verify_enumerate
from .layered import verify_candidate
from .loader import PolicyLoadError, load_policy, load_policy_text
from .model import MalformedPolicy, Rule, decide, normalize
from .result import Counterexample, VerificationResult
from .z3_backend import verify_z3

__all__ = [
    "verify_candidate", "verify_z3", "verify_enumerate", "verify_differential",
    "load_policy", "load_policy_text", "PolicyLoadError",
    "normalize", "decide", "Rule", "MalformedPolicy",
    "VerificationResult", "Counterexample",
]
