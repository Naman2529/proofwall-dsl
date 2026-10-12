"""Brute-force backend: check every attribute assignment for every request.

This is the baseline the Z3 backend is compared against. It is exact, but the
cost is 2^(attributes used by one request), so it refuses past `max_attrs`
instead of running forever. Refusing is reported as UNPROVEN, never as a pass.
"""
from __future__ import annotations

import itertools
import time
from typing import List

from .model import (Key, decide_rules, group_by_request, normalize, request_attrs)
from .result import Counterexample, VerificationResult

MODES = ("non-weakening", "equivalent")


def verify_enumerate(original, candidate, mode: str = "non-weakening", max_attrs: int = 16,
                     find_all: bool = False, cond_adapter=None) -> VerificationResult:
    if mode not in MODES:
        raise ValueError("mode must be one of %s" % (MODES,))
    t0 = time.perf_counter()
    orig = group_by_request(normalize(original, cond_adapter))
    cand = group_by_request(normalize(candidate, cond_adapter))
    keys: List[Key] = sorted(set(orig) | set(cand))

    cexs: List[Counterexample] = []
    capped: List[Key] = []
    cases = 0

    for key in keys:
        ro, rc = orig.get(key, []), cand.get(key, [])
        names = request_attrs(ro, rc)
        if len(names) > max_attrs:
            capped.append(key)
            continue
        found = False
        for bits in itertools.product((False, True), repeat=len(names)):
            env = dict(zip(names, bits))
            cases += 1
            d_orig, d_cand = decide_rules(ro, env), decide_rules(rc, env)
            if d_orig == d_cand:
                continue
            weaker = d_cand == "ALLOW" and d_orig == "DENY"
            if mode == "non-weakening" and not weaker:
                continue
            cexs.append(Counterexample(key[0], key[1], key[2], env, d_orig, d_cand))
            found = True
            if not find_all:
                break
        if found and not find_all:
            break

    res = VerificationResult(proven=False, mode=mode, backend="enumerate", layer="enumerate",
                             requests_checked=len(keys), cases_checked=cases,
                             counterexamples=cexs, elapsed_s=time.perf_counter() - t0)
    if cexs:
        return res
    if capped:
        res.reason = ("enumeration cap exceeded: %s %s %s uses more than %d attributes "
                      "(%d request(s) skipped); use the z3 backend" % (*capped[0], max_attrs, len(capped)))
    else:
        res.proven = True
    return res
