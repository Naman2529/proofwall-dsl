"""Standalone CLI:  python -m policyc.verification verify ORIGINAL.pol CANDIDATE.pol

Exit code 0 = proven, 1 = rejected or unproven, 2 = bad input.
"""
from __future__ import annotations

import argparse
import json
import sys

from .layered import BACKENDS, verify_candidate
from .loader import PolicyLoadError, load_policy


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m policyc.verification")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="check a candidate policy against an original")
    v.add_argument("original")
    v.add_argument("candidate")
    v.add_argument("--backend", choices=BACKENDS, default="layered")
    v.add_argument("--mode", choices=("non-weakening", "equivalent"), default="non-weakening")
    v.add_argument("--timeout-ms", type=int, default=10000)
    v.add_argument("--all", action="store_true", help="report every counterexample, not just the first")
    v.add_argument("--json", action="store_true", help="print the result as JSON")
    args = p.parse_args(argv)

    try:
        original, candidate = load_policy(args.original), load_policy(args.candidate)
    except (OSError, PolicyLoadError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2

    res = verify_candidate(original, candidate, mode=args.mode, backend=args.backend,
                           timeout_ms=args.timeout_ms, find_all=args.all)
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        print(res.summary())
        for extra in res.counterexamples[1:]:
            print("          %s" % extra)
    return 0 if res.proven else 1


if __name__ == "__main__":
    sys.exit(main())
