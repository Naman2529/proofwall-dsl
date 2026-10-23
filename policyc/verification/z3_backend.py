"""Z3 backend: proves a candidate policy is no weaker than the original.

For each request (subject, action, resource) we build two Boolean formulas
over the attribute variables:

    allow(P) = (OR of the conditions of matching ALLOW rules)
               AND NOT (OR of the conditions of matching DENY rules)

which is exactly deny-overrides with default deny. Then we ask Z3:

    SAT?  allow(candidate) AND NOT allow(original)      -> candidate is WEAKER
    SAT?  allow(original)  AND NOT allow(candidate)     -> candidate is stricter
                                                           (only asked in "equivalent" mode)

UNSAT for every request is a proof. SAT gives a concrete counterexample.
Z3 returning `unknown` (e.g. a timeout) is NEVER treated as a proof.

Every counterexample is replayed through the plain-Python decide_rules() as a
self-check, so a bug in the encoding cannot silently produce a wrong verdict.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

try:
    import z3
except ImportError:  # optional: without it verify_z3() fails closed and layered falls back
    z3 = None

from .model import (Key, Rule, Tree, decide_rules, group_by_request, normalize,
                    request_attrs, signature)
from .result import Counterexample, VerificationResult

MODES = ("non-weakening", "equivalent")


def _encode(tree: Tree, atoms: Dict[str, "z3.BoolRef"]):
    if tree is None:
        return z3.BoolVal(True)
    tag = tree[0]
    if tag == "attr":
        name = tree[1]
        if name not in atoms:
            atoms[name] = z3.Bool("attr_" + name)
        return atoms[name]
    if tag == "not":
        return z3.Not(_encode(tree[1], atoms))
    parts = [_encode(x, atoms) for x in tree[1]]
    if len(parts) == 1:
        return parts[0]
    return z3.And(parts) if tag == "and" else z3.Or(parts)


def _any(exprs):
    if not exprs:
        return z3.BoolVal(False)
    return exprs[0] if len(exprs) == 1 else z3.Or(exprs)


def allow_formula(rules: List[Rule], atoms: Dict[str, "z3.BoolRef"]):
    """Z3 formula that is true exactly when the rules ALLOW the request."""
    allows = [_encode(r.cond, atoms) for r in rules if r.effect == "ALLOW"]
    denies = [_encode(r.cond, atoms) for r in rules if r.effect == "DENY"]
    return z3.And(_any(allows), z3.Not(_any(denies)))


def verify_z3(original, candidate, mode: str = "non-weakening", timeout_ms: int = 10000,
              find_all: bool = False, cond_adapter=None) -> VerificationResult:
    if mode not in MODES:
        raise ValueError("mode must be one of %s" % (MODES,))
    t0 = time.perf_counter()
    orig = group_by_request(normalize(original, cond_adapter))
    cand = group_by_request(normalize(candidate, cond_adapter))
    keys: List[Key] = sorted(set(orig) | set(cand))
    if z3 is None:
        return VerificationResult(proven=False, mode=mode, backend="z3", layer="z3",
                                  reason="z3-solver is not installed (pip install -r requirements.txt)",
                                  elapsed_s=time.perf_counter() - t0)

    cexs: List[Counterexample] = []
    unknown: List[Key] = []
    internal: List[str] = []
    queries = 0

    for key in keys:
        ro, rc = orig.get(key, []), cand.get(key, [])
        if signature(ro) == signature(rc):
            continue  # identical rule multiset => identical decisions, nothing to ask
        atoms: Dict[str, "z3.BoolRef"] = {}
        f_orig = allow_formula(ro, atoms)
        f_cand = allow_formula(rc, atoms)
        questions: List[Tuple[str, "z3.BoolRef"]] = [("weaker", z3.And(f_cand, z3.Not(f_orig)))]
        if mode == "equivalent":
            questions.append(("stricter", z3.And(f_orig, z3.Not(f_cand))))

        for direction, formula in questions:
            solver = z3.Solver()
            solver.set("timeout", int(timeout_ms))
            solver.add(formula)
            verdict = solver.check()
            queries += 1
            if verdict == z3.unsat:
                continue
            if verdict == z3.unknown:
                unknown.append(key)
                continue
            model = solver.model()
            names = request_attrs(ro, rc)
            env = {n: bool(z3.is_true(model.eval(atoms[n], model_completion=True))) for n in names if n in atoms}
            d_orig, d_cand = decide_rules(ro, env), decide_rules(rc, env)
            expected = ("DENY", "ALLOW") if direction == "weaker" else ("ALLOW", "DENY")
            if (d_orig, d_cand) != expected:
                internal.append("Z3 model for %s did not replay (encoding bug?)" % (key,))
                continue
            cexs.append(Counterexample(key[0], key[1], key[2], env, d_orig, d_cand))
            if not find_all:
                break
        if cexs and not find_all:
            break

    elapsed = time.perf_counter() - t0
    res = VerificationResult(proven=False, mode=mode, backend="z3", layer="z3",
                             requests_checked=len(keys), cases_checked=queries,
                             counterexamples=cexs, elapsed_s=elapsed)
    if cexs:
        return res
    if internal:
        res.reason = "internal error: " + "; ".join(internal)
    elif unknown:
        res.reason = "Z3 returned unknown (timeout %d ms) on %d request(s), e.g. %s %s %s" % (
            timeout_ms, len(unknown), *unknown[0])
    else:
        res.proven = True
    return res
