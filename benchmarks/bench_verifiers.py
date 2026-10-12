"""Benchmark: brute-force enumeration vs Z3 as attributes-per-request grows.

    python benchmarks/bench_verifiers.py                  # table + CSV + PNG in benchmarks/results/
    python benchmarks/bench_verifiers.py --quick          # smaller grid, a few seconds

Enumeration costs 2^k per request (k = attributes used by that request), so it
hits its cap at k=16 and refuses. Z3 stays flat. Verdicts are cross-checked
wherever enumeration is allowed to run.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from policyc.verification import Rule, verify_enumerate, verify_z3  # noqa: E402


def make_condition(rng, attrs, depth=2):
    """Random condition that mentions every attribute in `attrs` at least once."""
    if len(attrs) == 1:
        leaf = ("attr", attrs[0])
        return ("not", leaf) if rng.random() < 0.3 else leaf
    rng.shuffle(attrs)
    cut = rng.randint(1, len(attrs) - 1)
    left, right = make_condition(rng, attrs[:cut], depth), make_condition(rng, attrs[cut:], depth)
    tree = (rng.choice(["and", "or"]), (left, right))
    return ("not", tree) if rng.random() < 0.15 else tree


def make_policy(rng, k, n_rules, n_requests):
    """n_requests requests, each with n_rules rules whose conditions together use k attributes."""
    attrs = ["attr%d" % i for i in range(k)]
    rules = []
    for q in range(n_requests):
        for j in range(n_rules):
            # every rule gets a slice of the attributes; the union covers all k
            chunk = [attrs[(j * 3 + t) % k] for t in range(max(1, min(k, 3 + k // n_rules)))]
            chunk = list(dict.fromkeys(chunk))
            effect = "DENY" if j % 4 == 3 else "ALLOW"
            rules.append(Rule("R%d" % (len(rules) + 1), effect, "subj%d" % q, "READ", "res", make_condition(rng, chunk)))
    # make sure all k attributes appear in the first request
    rules.append(Rule("R%d" % (len(rules) + 1), "ALLOW", "subj0", "READ", "res", make_condition(rng, list(attrs))))
    return rules


def equivalent_rewrite(rng, rules):
    """Semantics-preserving change: shuffle, duplicate a rule, double-negate a condition."""
    out = []
    for r in rules:
        cond = r.cond
        if cond is not None and rng.random() < 0.3:
            cond = ("not", ("not", cond))
        out.append(Rule(r.id, r.effect, r.subject, r.action, r.resource, cond, r.line))
    out.append(out[rng.randrange(len(out))])
    rng.shuffle(out)
    return out


def weaken(rng, rules):
    """Make the policy weaker: drop one DENY rule, else make an ALLOW unconditional."""
    out = list(rules)
    denies = [i for i, r in enumerate(out) if r.effect == "DENY"]
    if denies:
        out.pop(rng.choice(denies))
    else:
        i = rng.choice([i for i, r in enumerate(out) if r.effect == "ALLOW"])
        r = out[i]
        out[i] = Rule(r.id, r.effect, r.subject, r.action, r.resource, None, r.line)
    return out


def timed(fn):
    t = time.perf_counter()
    res = fn()
    return res, time.perf_counter() - t


def run(ks, n_rules, n_requests, max_attrs, seed):
    rng = random.Random(seed)
    rows = []
    for k in ks:
        original = make_policy(rng, k, n_rules, n_requests)
        for label, candidate in (("equivalent rewrite", equivalent_rewrite(rng, original)),
                                 ("weakened", weaken(rng, original))):
            z, tz = timed(lambda: verify_z3(original, candidate, mode="equivalent"))
            e, te = timed(lambda: verify_enumerate(original, candidate, mode="equivalent", max_attrs=max_attrs))
            capped = (not e.proven) and not e.counterexamples
            agree = "n/a (enum capped)" if capped else ("yes" if z.proven == e.proven else "NO !!")
            rows.append({
                "attrs_per_request": k, "rules": len(original), "candidate": label,
                "z3_seconds": round(tz, 4), "z3_verdict": z.verdict,
                "enum_seconds": None if capped else round(te, 4),
                "enum_verdict": "CAP EXCEEDED" if capped else e.verdict,
                "agree": agree,
            })
    return rows


def print_table(rows):
    head = ["attrs/request", "rules", "candidate", "Z3 (s)", "Z3 verdict", "enumerate (s)", "enum verdict", "agree"]
    data = [[r["attrs_per_request"], r["rules"], r["candidate"], r["z3_seconds"], r["z3_verdict"],
             "-" if r["enum_seconds"] is None else r["enum_seconds"], r["enum_verdict"], r["agree"]] for r in rows]
    widths = [max(len(str(x)) for x in col) for col in zip(head, *data)]
    for line in [head] + data:
        print("  ".join(str(c).ljust(w) for c, w in zip(line, widths)))


def save(rows, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    with open(outdir / "bench_results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("\nwrote %s" % (outdir / "bench_results.csv"))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed; skipping the chart")
        return
    eq = [r for r in rows if r["candidate"] == "equivalent rewrite"]  # the full-proof case: worst for both
    ks = [r["attrs_per_request"] for r in eq]
    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.plot(ks, [r["z3_seconds"] for r in eq], "o-", label="Z3", color="#1f77b4")
    ek = [(r["attrs_per_request"], r["enum_seconds"]) for r in eq if r["enum_seconds"] is not None]
    ax.plot([a for a, _ in ek], [b for _, b in ek], "s-", label="enumeration", color="#d62728")
    capped = [r["attrs_per_request"] for r in eq if r["enum_seconds"] is None]
    if capped:
        ax.axvline(min(capped) - 0.5, ls="--", color="#d62728", alpha=0.5)
        ax.annotate("enumeration refuses\n(cap exceeded)", (min(capped), ax.get_ylim()[1] * 0.5), color="#d62728", fontsize=9)
    ax.set_yscale("log")
    ax.set_xlabel("attributes used by one request")
    ax.set_ylabel("time to prove equivalence (s, log scale)")
    ax.set_title("Proving an optimized policy equivalent: Z3 vs brute force")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(outdir / "bench_chart.png", dpi=150)
    print("wrote %s" % (outdir / "bench_chart.png"))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--quick", action="store_true")
    p.add_argument("--rules", type=int, default=8, help="rules per request")
    p.add_argument("--requests", type=int, default=4)
    p.add_argument("--max-enum-attrs", type=int, default=16)
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"))
    args = p.parse_args()
    ks = [4, 8, 12, 14] if args.quick else [4, 8, 10, 12, 14, 16, 18, 20, 24, 32, 48]
    rows = run(ks, args.rules, args.requests, args.max_enum_attrs, args.seed)
    print_table(rows)
    if any(r["agree"] == "NO !!" for r in rows):
        print("\nBACKENDS DISAGREE - investigate before trusting any result")
        return 1
    save(rows, Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
