"""Objective 6: evaluation of the AI pipeline against the compiler-only baseline.

    tasks.py   loads the benchmark data in evaluation/
    run.py     runs the suites (generation, optimization, semantic), resumable
    report.py  turns raw records into summary.txt, CSVs, results.md, run.json
    charts.py  optional PNG charts (needs matplotlib)

`python -m policyc evaluate` drives all of it; see docs/EVALUATION.md.
"""

from __future__ import annotations

import time

from ...compiler import compile_file


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
