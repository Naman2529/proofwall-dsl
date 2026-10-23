"""Turns raw evaluation records into the numbers Review 1 promised.

summarize()      every metric, as one dict (rates exclude tasks that were not run)
write_reports()  summary.txt, generation.csv, optimization.csv, proposals.csv,
                 semantic.csv, results.md, run.json

Outputs from the fake model start with BANNER. The API key is scrubbed from
everything written, whatever a caller passes in.
"""

from __future__ import annotations

import csv
import io
import json
import os
from collections import Counter
from typing import Dict, List, Optional

from ..llm_optimizer import STAGES
from ..metrics import TIERS

BANNER = "FAKE - not real results"
DIFFICULTIES = ("easy", "medium", "hard")


def _pct(part: int, whole: int) -> Optional[float]:
    return round(100.0 * part / whole, 1) if whole else None


def _mean(values) -> Optional[float]:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 3) if values else None


# ---- summary ----------------------------------------------------------------------

def _gen_block(records: List[dict]) -> dict:
    n = len(records)
    return {"tasks": n,
            "first_try_compile": _pct(sum(r["first_try_ok"] for r in records), n),
            "final_compile": _pct(sum(r["ok"] for r in records), n),
            "first_try_verified": _pct(sum(r["first_try_verified"] for r in records), n),
            "final_verified": _pct(sum(r["verified"] for r in records), n),
            "mean_cycles": _mean([r["retries"] for r in records])}


def _summarize_generation(gen: List[dict]) -> dict:
    run = [r for r in gen if r["status"] == "ok"]
    by_difficulty = {d: _gen_block([r for r in run if r["difficulty"] == d]) for d in DIFFICULTIES}
    by_difficulty["all"] = _gen_block(run)
    return {"tasks": len(gen), "run": len(run), "not_run": len(gen) - len(run),
            "by_difficulty": by_difficulty,
            "mean_cycles_compiled": _mean([r["retries"] for r in run if r["ok"]]),
            "mean_tokens": _mean([r["tokens"] for r in run]),
            "mean_latency_s": _mean([r["latency_s"] for r in run])}


def _summarize_optimization(opt: List[dict]) -> dict:
    run = [r for r in opt if r["llm"]["status"] == "ok"]
    proposals = [p for r in run for p in r["llm"]["proposals"]]
    by_stage = Counter(p["stage"] for p in proposals)
    accepted = by_stage["accepted"]
    b_before = sum(r["baseline"]["rules_before"] for r in run)
    b_after = sum(r["baseline"]["rules_after"] for r in run)
    l_after = sum(r["llm"]["rules_after"] for r in run)
    rows = [{"task": r["task"], "baseline_before": r["baseline"]["rules_before"],
             "baseline_after": r["baseline"]["rules_after"], "baseline_ms": r["baseline"]["ms"],
             "llm_status": r["llm"]["status"], "llm_after": r["llm"].get("rules_after"),
             "statements_before": r["baseline"]["statements_before"],
             "statements_after": r["llm"].get("statements_after"),
             "accepted": r["llm"].get("accepted"), "tokens": r["llm"].get("tokens"),
             "latency_s": r["llm"].get("latency_s")} for r in opt]
    return {"policies": rows, "run": len(run), "not_run": sum(r["llm"]["status"] == "not_run" for r in opt),
            "skipped": sum(r["llm"]["status"] == "skipped" for r in opt),
            "baseline_all": {"rules_before": sum(r["baseline"]["rules_before"] for r in opt),
                             "rules_after": sum(r["baseline"]["rules_after"] for r in opt),
                             "reduction_pct": _pct(sum(r["baseline"]["rules_before"] - r["baseline"]["rules_after"]
                                                       for r in opt),
                                                   sum(r["baseline"]["rules_before"] for r in opt))},
            "totals": {
                "baseline_rules_before": b_before, "baseline_rules_after": b_after,
                "llm_rules_after": l_after,
                "baseline_reduction_pct": _pct(b_before - b_after, b_before),
                "llm_reduction_pct": _pct(b_before - l_after, b_before),
                "llm_statements_before": sum(r["llm"]["statements_before"] for r in run),
                "llm_statements_after": sum(r["llm"]["statements_after"] for r in run),
                "proposals": len(proposals), "accepted": accepted,
                "rejected": len(proposals) - accepted, "accepted_pct": _pct(accepted, len(proposals)),
                "by_stage": {s: by_stage[s] for s in STAGES},
                "deny_guards_dropped_proposed": sum(bool(p["drops_deny_guard"]) for p in proposals),
                "deny_guards_dropped_accepted": sum(bool(p["drops_deny_guard"]) for p in proposals
                                                    if p["stage"] == "accepted"),
                "mean_tokens": _mean([r["llm"]["tokens"] for r in run]),
                "mean_latency_s": _mean([r["llm"]["latency_s"] for r in run]),
                "baseline_mean_ms": _mean([r["baseline"]["ms"] for r in opt]),
            }}


def _summarize_semantic(sem: List[dict]) -> dict:
    by_code: Dict[str, Dict[str, int]] = {}

    def bump(code, key):
        entry = by_code.setdefault(code, {"expected": 0, "detected": 0, "missed": 0, "false_alarms": 0})
        entry[key] += 1

    known = []
    for r in sem:
        for _, code, _ in r["expected"]:
            bump(code, "expected")
        for _, code in r["matched"]:
            bump(code, "detected")
        for line, code, reason in r["missed"]:
            bump(code, "missed")
            if reason:
                known.append({"file": r["task"], "line": line, "code": code, "reason": reason})
        for _, code in r["false_alarms"]:
            bump(code, "false_alarms")
    expected = sum(len(r["expected"]) for r in sem)
    detected = sum(len(r["matched"]) for r in sem)
    return {"files": len(sem), "expected": expected, "detected": detected,
            "missed": sum(len(r["missed"]) for r in sem),
            "false_alarms": sum(len(r["false_alarms"]) for r in sem),
            "detection_rate": _pct(detected, expected),
            "by_code": dict(sorted(by_code.items())), "known_misses": known}


def summarize(gen: List[dict], opt: List[dict], sem: List[dict]) -> dict:
    gen_tiers = Counter(a["tier"] for r in gen if r["status"] == "ok" for a in r["attempts"])
    opt_tiers = Counter(p["tier"] for r in opt if r["llm"]["status"] == "ok"
                        for p in r["llm"]["proposals"])
    return {"fake": any(r.get("fake") for r in gen + opt),
            "generation": _summarize_generation(gen),
            "optimization": _summarize_optimization(opt),
            "semantic": _summarize_semantic(sem),
            "tiers": {"generation": {t: gen_tiers[t] for t in TIERS},
                      "optimization": {t: opt_tiers[t] for t in TIERS}}}


# ---- writing ----------------------------------------------------------------------

def _fmt(v) -> str:
    if v is None:
        return "n/a"
    return "%.1f" % v if isinstance(v, float) else str(v)


def _rate(v) -> str:
    return "n/a" if v is None else "%.1f%%" % v


def _csv(rows: List[dict], fields: List[str], fake: bool) -> str:
    buf = io.StringIO()
    if fake:
        buf.write("# %s\n" % BANNER)
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k) for k in fields})
    return buf.getvalue()


def _generation_rows(gen: List[dict]) -> List[dict]:
    rows = []
    for r in gen:
        if r["status"] != "ok":
            rows.append({"task": r["task"], "difficulty": r.get("difficulty"), "status": r["status"],
                         "error_kind": r.get("error_kind")})
            continue
        for i, a in enumerate(r["attempts"], 1):
            rows.append({"task": r["task"], "difficulty": r["difficulty"], "status": "ok",
                         "attempt": i, "tier": a["tier"], "errors": " ".join(a["errors"]),
                         "verified": a["verified"], "tokens": a["tokens"], "latency_s": a["latency_s"]})
    return rows


def _proposal_rows(opt: List[dict]) -> List[dict]:
    return [dict(p, task=r["task"]) for r in opt if r["llm"]["status"] == "ok"
            for p in r["llm"]["proposals"]]


def _semantic_rows(sem: List[dict]) -> List[dict]:
    rows = []
    for r in sem:
        matched = {(l, c) for l, c in r["matched"]}
        for line, code, reason in r["expected"]:
            rows.append({"file": r["task"], "line": line, "code": code,
                         "result": "detected" if (line, code) in matched else "missed",
                         "known_miss": reason})
        for line, code in r["false_alarms"]:
            rows.append({"file": r["task"], "line": line, "code": code, "result": "false_alarm"})
    return rows


def _summary_text(s: dict, info: dict) -> str:
    g, o, sem = s["generation"], s["optimization"], s["semantic"]
    out = []
    if s["fake"]:
        out.append(BANNER)
    out.append("policyc evaluation  (model: %s, prompts: %s)" % (info.get("llm", "n/a"),
                                                                 info.get("prompt_version", "n/a")))
    out.append("")
    out.append("Objective 4  English -> DSL  (%d run, %d not run)" % (g["run"], g["not_run"]))
    out.append("  %-7s %5s  %-14s %-14s %-14s %-14s %s" % ("", "tasks", "compile 1st", "compile final",
                                                         "correct 1st", "correct final", "cycles"))
    for d in DIFFICULTIES + ("all",):
        b = g["by_difficulty"][d]
        out.append("  %-7s %5d  %-14s %-14s %-14s %-14s %s" % (
            d, b["tasks"], _rate(b["first_try_compile"]), _rate(b["final_compile"]),
            _rate(b["first_try_verified"]), _rate(b["final_verified"]), _fmt(b["mean_cycles"])))
    out.append("  tokens/task %s, latency/task %s s" % (_fmt(g["mean_tokens"]), _fmt(g["mean_latency_s"])))
    t = o["totals"]
    out.append("")
    out.append("Objective 5  optimization  (%d policies with LLM results, %d not run, %d skipped)"
               % (o["run"], o["not_run"], o["skipped"]))
    out.append("  baseline IR rules %d -> %d (%s)   LLM IR rules %d -> %d (%s)" % (
        t["baseline_rules_before"], t["baseline_rules_after"], _rate(t["baseline_reduction_pct"]),
        t["baseline_rules_before"], t["llm_rules_after"], _rate(t["llm_reduction_pct"])))
    out.append("  baseline on all policies: %d -> %d (%s), %s ms/policy, 0 tokens" % (
        o["baseline_all"]["rules_before"], o["baseline_all"]["rules_after"],
        _rate(o["baseline_all"]["reduction_pct"]), _fmt(t["baseline_mean_ms"])))
    out.append("  proposals %d: accepted %d (%s), rejected %d  %s" % (
        t["proposals"], t["accepted"], _rate(t["accepted_pct"]), t["rejected"],
        ", ".join("%s %d" % kv for kv in t["by_stage"].items())))
    out.append("  DENY guards dropped: %d proposed, %d accepted" % (
        t["deny_guards_dropped_proposed"], t["deny_guards_dropped_accepted"]))
    out.append("  tokens/policy %s, latency/policy %s s" % (_fmt(t["mean_tokens"]), _fmt(t["mean_latency_s"])))
    out.append("")
    out.append("Objective 3  seeded semantic issues: %d/%d detected (%s), %d missed, %d false alarms"
               % (sem["detected"], sem["expected"], _rate(sem["detection_rate"]), sem["missed"],
                  sem["false_alarms"]))
    out.append("")
    out.append("AI output tiers   " + "  ".join("%s" % t for t in TIERS))
    for stage in ("generation", "optimization"):
        out.append("  %-14s  %s" % (stage, "  ".join("%d" % s["tiers"][stage][t] for t in TIERS)))
    return "\n".join(out) + "\n"


def _results_md(s: dict, info: dict) -> str:
    g, o, sem = s["generation"], s["optimization"], s["semantic"]
    t = o["totals"]
    out = []
    if s["fake"]:
        out += ["> **%s**" % BANNER, ""]
    out += ["# policyc evaluation results", "",
            "- Model: `%s`" % info.get("llm", "n/a"),
            "- Date: %s" % info.get("date", "n/a"),
            "- Prompt version: `%s`" % info.get("prompt_version", "n/a"),
            "- Settings: attempts %s, samples %s, mode %s" % (info.get("attempts", "n/a"),
                                                              info.get("samples", "n/a"),
                                                              info.get("mode", "n/a")), ""]
    out += ["## Objective 4: English to DSL", ""]
    if g["run"]:
        out += ["| Difficulty | Tasks | Compiles (1st try) | Compiles (final) | Correct (1st try) "
                "| Correct (final) | Mean correction cycles |", "|---|---|---|---|---|---|---|"]
        for d in DIFFICULTIES + ("all",):
            b = g["by_difficulty"][d]
            out.append("| %s | %d | %s | %s | %s | %s | %s |" % (
                d, b["tasks"], _rate(b["first_try_compile"]), _rate(b["final_compile"]),
                _rate(b["first_try_verified"]), _rate(b["final_verified"]), _fmt(b["mean_cycles"])))
        out += ["", "Correct = proven equivalent to the hand-written reference policy by the verifier.",
                "Mean tokens per task: %s. Mean latency per task: %s s. Tasks not run: %d." % (
                    _fmt(g["mean_tokens"]), _fmt(g["mean_latency_s"]), g["not_run"]), ""]
    else:
        out += ["Generation suite not run (no LLM results).", ""]
    out += ["## Objective 5: optimization, LLM vs compiler-only baseline", ""]
    out += ["| Policy | Baseline IR rules | LLM IR rules | LLM statements | LLM accepted | Tokens |",
            "|---|---|---|---|---|---|"]
    for row in o["policies"]:
        llm = ("%d -> %d" % (row["baseline_before"], row["llm_after"])
               if row["llm_status"] == "ok" else row["llm_status"].replace("_", " "))
        stm = ("%d -> %d" % (row["statements_before"], row["statements_after"])
               if row["llm_status"] == "ok" else "")
        out.append("| %s | %d -> %d | %s | %s | %s | %s |" % (
            row["task"], row["baseline_before"], row["baseline_after"], llm, stm,
            "" if row["accepted"] is None else ("yes" if row["accepted"] else "no"),
            _fmt(row["tokens"]) if row["llm_status"] == "ok" else ""))
    out += ["", "Baseline: always valid by construction, 0 tokens, %s ms per policy." % _fmt(t["baseline_mean_ms"])]
    if o["run"]:
        out.append("Proposals: %d, accepted %d (%s), rejected %d (%s)." % (
            t["proposals"], t["accepted"], _rate(t["accepted_pct"]), t["rejected"],
            ", ".join("%s %d" % kv for kv in t["by_stage"].items() if kv[0] != "accepted")))
    else:
        out.append("LLM optimizer not run.")
    out += ["", "## Classification of every AI output", "",
            "| Stage | " + " | ".join(TIERS) + " |", "|---|" + "---|" * len(TIERS)]
    for stage in ("generation", "optimization"):
        out.append("| %s | %s |" % (stage, " | ".join(str(s["tiers"][stage][x]) for x in TIERS)))
    out += ["", "## Objective 3: seeded semantic issues", "",
            "| Code | Expected | Detected | Missed | False alarms |", "|---|---|---|---|---|"]
    for code, c in sem["by_code"].items():
        out.append("| %s | %d | %d | %d | %d |" % (code, c["expected"], c["detected"], c["missed"],
                                                    c["false_alarms"]))
    out += ["", "Detected %d of %d (%s)." % (sem["detected"], sem["expected"], _rate(sem["detection_rate"]))]
    for k in sem["known_misses"]:
        out.append("- Known miss: %s line %d, %s: %s" % (k["file"], k["line"], k["code"], k["reason"]))
    out += ["", "## Findings", ""] + ["- " + f for f in _findings(s)]
    return "\n".join(out) + "\n"


def _findings(s: dict) -> List[str]:
    g, t, sem = s["generation"], s["optimization"]["totals"], s["semantic"]
    out = []
    a = g["by_difficulty"]["all"]
    if g["run"]:
        out.append("%s of generated policies compiled on the first try and %s after correction, "
                   "with %s correction cycles on average." % (_rate(a["first_try_compile"]),
                                                              _rate(a["final_compile"]), _fmt(a["mean_cycles"])))
        out.append("%s matched the reference policy exactly in meaning." % _rate(a["final_verified"]))
    if s["optimization"]["run"]:
        out.append("The compiler-only optimizer removed %s of IR rules; with the LLM the reduction was %s."
                   % (_rate(t["baseline_reduction_pct"]), _rate(t["llm_reduction_pct"])))
        out.append("%d of %d LLM proposals were accepted; every accepted one was proven by the verifier."
                   % (t["accepted"], t["proposals"]))
        if t["by_stage"]["verifier"]:
            out.append("The verifier rejected %d proposal(s) that would have changed a decision."
                       % t["by_stage"]["verifier"])
    out.append("Semantic analysis detected %d of %d seeded issues (%s) with %d false alarm(s)."
               % (sem["detected"], sem["expected"], _rate(sem["detection_rate"]), sem["false_alarms"]))
    return out


def _scrub(text: str) -> str:
    key = os.environ.get("GEMINI_API_KEY")
    return text.replace(key, "***") if key else text


def write_reports(out_dir: str, summary: dict, gen: List[dict], opt: List[dict], sem: List[dict],
                  run_info: dict) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    fake = summary["fake"]
    run_json = {"fake": fake, "run": run_info, "summary": summary}
    if fake:
        run_json["banner"] = BANNER
    files = {
        "summary.txt": _summary_text(summary, run_info),
        "generation.csv": _csv(_generation_rows(gen), ["task", "difficulty", "status", "error_kind",
                                                       "attempt", "tier", "errors", "verified",
                                                       "tokens", "latency_s"], fake),
        "optimization.csv": _csv(summary["optimization"]["policies"],
                                 ["task", "baseline_before", "baseline_after", "baseline_ms",
                                  "llm_status", "llm_after", "statements_before", "statements_after",
                                  "accepted", "tokens", "latency_s"], fake),
        "proposals.csv": _csv(_proposal_rows(opt), ["task", "attempt", "stage", "tier",
                                                    "drops_deny_guard", "tokens", "verifier", "dsl"], fake),
        "semantic.csv": _csv(_semantic_rows(sem), ["file", "line", "code", "result", "known_miss"], fake),
        "results.md": _results_md(summary, run_info),
        "run.json": json.dumps(run_json, indent=2, ensure_ascii=False) + "\n",
    }
    paths = []
    for name, text in files.items():
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(_scrub(text))
        paths.append(path)
    return paths
