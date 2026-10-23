"""Objective 6: evaluation of the AI pipeline against the compiler-only baseline.

STATUS: baseline half done (baseline_metrics). The AI comparison is not
started.

TODO(AI part / whoever runs the evaluation):
  * build a small benchmark set: English requirements + .pol files;
  * implement run_evaluation() to produce one table:
      validity (first try / after correction), rule reduction (baseline vs
      LLM), accepted vs rejected LLM proposals, tokens used, latency;
  * add `python -m policyc.ai.evaluation` output to the final report.
"""

from __future__ import annotations

import time

from ..compiler import compile_file


def baseline_metrics(path: str) -> dict:
    """Compiler-only optimizer numbers for one policy file."""
    start = time.perf_counter()
    res = compile_file(path, optimize_ir=True)
    elapsed_ms = (time.perf_counter() - start) * 1000
    if not res.ok:
        return {"file": path, "compiles": False}
    return {
        "file": path,
        "compiles": True,
        "rules_before": len(res.ir.rules),
        "rules_after": len(res.final_ir.rules),
        "verified": res.verification.equivalent,
        "cases_checked": res.verification.cases_checked,
        "ms": round(elapsed_ms, 2),
    }


def run_evaluation():
    raise NotImplementedError("TODO(AI part): AI vs baseline evaluation, see module docstring")


if __name__ == "__main__":
    import glob
    for f in sorted(glob.glob("examples/*.pol")):
        print(baseline_metrics(f))
