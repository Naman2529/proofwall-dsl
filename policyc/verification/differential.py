"""Differential testing: the cheap first filter before Z3.

Runs original and candidate on many generated attribute assignments per
request and compares decisions. It can REJECT a bad candidate very quickly,
but it can never prove a good one safe, so it never returns proven=True.
"""
from __future__ import annotations

import itertools
import random
import time
from typing import List

from .model import Key, decide_rules, group_by_request, normalize, request_attrs
from .result import Counterexample, VerificationResult

MODES = ("non-weakening", "equivalent")


def verify_differential(original, candidate, mode: str = "non-weakening", samples: int = 128,
                        seed: int = 0, find_all: bool = False, cond_adapter=None) -> VerificationResult:
    if mode not in MODES:
        raise ValueError("mode must be one of %s" % (MODES,))
    t0 = time.perf_counter()
    rng = random.Random(seed)
    orig = group_by_request(normalize(original, cond_adapter))
    cand = group_by_request(normalize(candidate, cond_adapter))
    keys: List[Key] = sorted(set(orig) | set(cand))

    cexs: List[Counterexample] = []
    cases = 0
    for key in keys:
        ro, rc = orig.get(key, []), cand.get(key, [])
        names = request_attrs(ro, rc)
        if 2 ** len(names) <= samples:
            envs = (dict(zip(names, bits)) for bits in itertools.product((False, True), repeat=len(names)))
        else:
            fixed = [dict.fromkeys(names, False), dict.fromkeys(names, True)]
            rand = ({n: rng.random() < 0.5 for n in names} for _ in range(samples))
            envs = itertools.chain(fixed, rand)
        for env in envs:
            cases += 1
            d_orig, d_cand = decide_rules(ro, env), decide_rules(rc, env)
            if d_orig == d_cand:
                continue
            if mode == "non-weakening" and not (d_cand == "ALLOW" and d_orig == "DENY"):
                continue
            cexs.append(Counterexample(key[0], key[1], key[2], env, d_orig, d_cand))
            if not find_all:
                break
        if cexs and not find_all:
            break

    res = VerificationResult(proven=False, mode=mode, backend="differential", layer="differential",
                             requests_checked=len(keys), cases_checked=cases,
                             counterexamples=cexs, elapsed_s=time.perf_counter() - t0)
    if not cexs:
        res.reason = "no difference found in %d sampled cases (testing cannot prove safety)" % cases
    return res
