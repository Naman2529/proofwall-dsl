"""demo-check: show every Review 1 outcome on the examples in demo/.

    python -m policyc demo-check [--llm gemini|fake]

Each objective gets one or more checks, graded PASS (outcome shown), WARN
(works, but something to look at: an answer that is not equivalent to the
reference, fake or missing evaluation results, no model configured) or FAIL
(the outcome is not met). Exit code 1 if anything FAILs.

The examples are deliberately different from the evaluation benchmark, so the
demo shows the compiler on policies it was not tuned on.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import List, Optional

from ..compiler import compile_file
from ..tokens import TokenType
from ..verifier import verify
from .evaluation.tasks import EVAL_ROOT, GenTask, parse_expectations
from .generator import generate_policy
from .llm_client import FakeClient, LLMError
from .llm_optimizer import optimize_with_llm
from .metrics import TIERS, tier
from .prompts import Vocabulary

DEMO_ROOT = os.path.join(os.path.dirname(EVAL_ROOT), "demo")
DEFAULT_RESULTS = os.path.join(EVAL_ROOT, "results")

OBJECTIVES = {
    1: "Language and formal grammar",
    2: "Lexer + recursive-descent parser: errors with line numbers and expected tokens",
    3: "Security semantic analysis: duplicates, conflicts, undefined names, invalid actions",
    4: "LLM: controlled English to DSL with a compiler-guided correction loop",
    5: "Optimization: compiler-only and LLM, accepted only through the verifier",
    6: "Target policy format and evaluation against the baseline",
}

KEYWORDS = {TokenType.SUBJECT, TokenType.RESOURCE, TokenType.SUPPORTS, TokenType.ALLOW,
            TokenType.DENY, TokenType.WHEN, TokenType.READ, TokenType.WRITE, TokenType.EXECUTE,
            TokenType.ACCESS, TokenType.AND, TokenType.OR, TokenType.NOT, TokenType.LPAREN,
            TokenType.RPAREN, TokenType.COMMA}

NO_MODEL = "skipped: no model (set GEMINI_API_KEY, or use --llm fake to rehearse)"


@dataclass
class Check:
    objective: int
    name: str
    status: str          # PASS | WARN | FAIL
    detail: str = ""


def _path(name: str, root: str) -> str:
    return os.path.join(root, name)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_requirements(root: str = DEMO_ROOT) -> List[GenTask]:
    with open(_path("requirements.json", root), encoding="utf-8") as f:
        raw = json.load(f)
    return [GenTask(r["id"], r["difficulty"], r["english"],
                    Vocabulary(r["vocabulary"]["subjects"], r["vocabulary"]["resources"],
                               r["vocabulary"].get("attributes", [])),
                    _path(r["reference"], root))
            for r in raw]


# ---- objectives 1-3: the trusted front end -------------------------------------

def _markers(name: str, root: str, objective: int, label: str) -> Check:
    path = _path(name, root)
    res = compile_file(path)
    expected = {(e.line, e.code) for e in parse_expectations(path)}
    reported = {(d.line, d.code) for d in res.diagnostics}
    missed, extra = sorted(expected - reported), sorted(reported - expected)
    status = "PASS" if not missed and not extra else "FAIL"
    detail = "%d/%d expected diagnostics reported" % (len(expected) - len(missed), len(expected))
    if missed:
        detail += "; missed %s" % ", ".join("line %d %s" % m for m in missed)
    if extra:
        detail += "; unexpected %s" % ", ".join("line %d %s" % m for m in extra)
    return Check(objective, "%s (%s)" % (label, name), status, detail)


def _objective_1(root: str) -> List[Check]:
    res = compile_file(_path("01_full_language.pol", root))
    used = {t.type for t in res.tokens}
    missing = sorted(t.value for t in KEYWORDS - used)
    ok = res.ok and not res.diagnostics and not missing
    return [Check(1, "every language feature compiles cleanly (01_full_language.pol)",
                  "PASS" if ok else "FAIL",
                  "%d statements -> %d IR rules, %d diagnostics%s" % (
                      len(res.ast.statements) if res.ast else 0, len(res.ir.rules) if res.ir else 0,
                      len(res.diagnostics), "; keywords not used: %s" % ", ".join(missing) if missing else
                      "; uses every keyword, ACCESS, AND/OR/NOT and parentheses"))]


def _objective_2(root: str) -> List[Check]:
    check = _markers("02_syntax_errors.pol", root, 2, "every syntax error, one run")
    res = compile_file(_path("02_syntax_errors.pol", root))
    no_expected = [d for d in res.diagnostics if d.code == "P001" and not d.expected]
    if no_expected:
        check.status = "FAIL"
        check.detail += "; %d parser errors without expected tokens" % len(no_expected)
    elif res.diagnostics:
        first = res.diagnostics[0]
        check.detail += "; e.g. line %d: %s (expected %s)" % (first.line, first.message,
                                                              ", ".join(first.expected))
    return [check]


def _objective_3(root: str) -> List[Check]:
    return [_markers("03_semantic_errors.pol", root, 3, "undefined names, invalid actions, redeclarations"),
            _markers("04_security_warnings.pol", root, 3, "duplicates, redundancy, conflicts, dead rules")]


# ---- objective 4: LLM generation ----------------------------------------------------

def _why(check) -> str:
    """The first request where the generated policy decides differently."""
    if not check or not check.counterexample:
        return ""
    c = check.counterexample
    attrs = ", ".join("%s=%s" % kv for kv in sorted(c.env.items()))
    return ": %s %s %s%s: reference says %s, generated says %s" % (
        c.subject, c.action, c.resource, " (%s)" % attrs if attrs else "", c.original, c.candidate)


def _objective_4(client, root: str, tiers: List[str]) -> List[Check]:
    if client is None:
        return [Check(4, "English to DSL on 5 new requirements", "WARN", NO_MODEL)]
    checks, first, compiled, correct, cycles = [], 0, 0, 0, []
    reqs = load_requirements(root)
    for r in reqs:
        try:
            res = generate_policy(r.english, r.vocabulary, llm=client)
        except LLMError as e:
            checks.append(Check(4, "%s (%s)" % (r.id, r.difficulty), "FAIL", "model call failed: %s" % e))
            continue
        check = verify(compile_file(r.reference_path).ir, res.attempts[-1].result.ir,
                       mode="equivalent") if res.ok else None
        equivalent = bool(check and check.proven)
        for a in res.attempts:
            tiers.append(tier(a.result, verified=(equivalent and a is res.attempts[-1]) or None)
                         if a.ok or not a.result.ok else "invalid")
        first += res.attempts[0].ok
        compiled += res.ok
        correct += equivalent
        cycles.append(res.retries)
        fed_back = sorted({e["code"] for a in res.attempts[:-1] for e in a.errors()})
        detail = "%d attempt%s%s, %.1f s; %s" % (
            len(res.attempts), "" if len(res.attempts) == 1 else "s",
            " (fed back: %s)" % ", ".join(fed_back) if fed_back else "", res.latency_s,
            "proven equivalent to the reference" if equivalent else
            "compiles but is NOT equivalent to the reference%s" % _why(check) if res.ok else
            "never compiled")
        checks.append(Check(4, "%s (%s): %s" % (r.id, r.difficulty, r.english[:60] +
                                                ("..." if len(r.english) > 60 else "")),
                            "PASS" if equivalent else "WARN" if res.ok else "FAIL", detail))
    n = len(reqs)
    worst = "FAIL" if any(c.status == "FAIL" for c in checks) else \
        "WARN" if any(c.status == "WARN" for c in checks) else "PASS"
    checks.append(Check(4, "summary", worst,
                        "compiled first try %d/%d, after correction %d/%d, proven correct %d/%d, "
                        "mean correction cycles %.1f" % (first, n, compiled, n, correct, n,
                                                         sum(cycles) / len(cycles) if cycles else 0)))
    return checks


# ---- objective 5: optimization and verification --------------------------------------

def _objective_5(client, root: str, tiers: List[str]) -> List[Check]:
    checks = []
    base = compile_file(_path("05_optimize_baseline.pol", root), optimize_ir=True)
    removed = len(base.ir.rules) - len(base.final_ir.rules)
    checks.append(Check(5, "compiler-only optimizer (05_optimize_baseline.pol)",
                        "PASS" if removed > 0 and base.verification.proven else "FAIL",
                        "%d -> %d IR rules, %s" % (len(base.ir.rules), len(base.final_ir.rules),
                                                   base.verification.summary())))
    llm_path = _path("06_optimize_llm.pol", root)
    if client is None:
        checks.append(Check(5, "LLM optimizer (06_optimize_llm.pol)", "WARN", NO_MODEL))
    else:
        baseline = compile_file(llm_path, optimize_ir=True)
        try:
            res = optimize_with_llm(_read(llm_path), llm=client)
            tiers.extend(p.tier for p in res.proposals)
            if res.accepted is None:
                checks.append(Check(5, "LLM optimizer (06_optimize_llm.pol)", "FAIL",
                                    "no proposal accepted after %d proposals (%s)" % (
                                        len(res.proposals), ", ".join(p.stage for p in res.proposals))))
            else:
                checks.append(Check(5, "LLM optimizer (06_optimize_llm.pol)", "PASS",
                                    "compiler-only %d -> %d, LLM %d -> %d IR rules (%d -> %d statements), "
                                    "%d proposal(s), %s" % (
                                        len(baseline.ir.rules), len(baseline.final_ir.rules),
                                        res.rules_before, res.rules_after, res.statements_before,
                                        res.statements_after, len(res.proposals),
                                        res.accepted.verification.summary())))
        except LLMError as e:
            checks.append(Check(5, "LLM optimizer (06_optimize_llm.pol)", "FAIL", "model call failed: %s" % e))

    original = compile_file(_path("07_verify_original.pol", root)).ir

    def against(name, mode="equivalent"):
        return verify(original, compile_file(_path(name, root)).ir, mode=mode)

    safe = against("07_verify_safe.pol")
    checks.append(Check(5, "verifier accepts a safe rewrite (07_verify_safe.pol)",
                        "PASS" if safe.proven else "FAIL", safe.summary()))
    unsafe = against("07_verify_unsafe.pol")
    weaker = [c for c in unsafe.counterexamples if c.direction == "weaker"]
    checks.append(Check(5, "verifier rejects an unsafe rewrite (07_verify_unsafe.pol)",
                        "PASS" if not unsafe.proven and weaker else "FAIL",
                        "counterexample: %s" % weaker[0] if weaker else unsafe.summary()))
    strict_eq, strict_nw = against("07_verify_stricter.pol"), against("07_verify_stricter.pol",
                                                                      "non-weakening")
    checks.append(Check(5, "stricter rewrite: rejected as equivalent, proven non-weakening",
                        "PASS" if not strict_eq.proven and strict_nw.proven else "FAIL",
                        "equivalent: %s | non-weakening: %s" % (strict_eq.verdict, strict_nw.verdict)))
    return checks


# ---- objective 6: target format and evaluation ------------------------------------------

def _objective_6(root: str, results_dir: str, tiers: List[str]) -> List[Check]:
    res = compile_file(_path("01_full_language.pol", root))
    target = res.target or {}
    ok = target.get("format") == "policyc-target/1" and len(target.get("rules", [])) == len(res.ir.rules)
    checks = [Check(6, "target JSON policy (01_full_language.pol)", "PASS" if ok else "FAIL",
                    "%s, %d rules, each with its source line" % (target.get("format"),
                                                                 len(target.get("rules", []))))]
    run_json = os.path.join(results_dir, "run.json")
    if not os.path.exists(run_json):
        checks.append(Check(6, "evaluation results", "WARN",
                            "none yet: run `python -m policyc evaluate` with GEMINI_API_KEY set"))
    else:
        with open(run_json, encoding="utf-8") as f:
            data = json.load(f)
        s = data.get("summary", {})
        if data.get("fake"):
            checks.append(Check(6, "evaluation results", "WARN",
                                "only FAKE results: rerun `python -m policyc evaluate` with a key"))
        else:
            g = (s.get("generation", {}).get("by_difficulty", {}) or {}).get("all", {})
            t = s.get("optimization", {}).get("totals", {})
            checks.append(Check(6, "evaluation results (%s)" % data.get("run", {}).get("llm", "model n/a"),
                                "PASS", "proven correct %s%%, LLM rule reduction %s%% vs compiler-only %s%%, "
                                "seeded issues detected %s%%" % (
                                    g.get("final_verified"), t.get("llm_reduction_pct"),
                                    t.get("baseline_reduction_pct"),
                                    s.get("semantic", {}).get("detection_rate"))))
    counts = ", ".join("%s %d" % (t.replace("_", " "), tiers.count(t)) for t in TIERS)
    checks.append(Check(6, "every AI output classified (this run)", "PASS",
                        counts if tiers else "no AI outputs in this run (no model)"))
    return checks


# ---- driver ---------------------------------------------------------------------------------

def run_demo_check(client, root: str = DEMO_ROOT, results_dir: Optional[str] = None) -> List[Check]:
    results_dir = results_dir or DEFAULT_RESULTS
    tiers: List[str] = []
    return (_objective_1(root) + _objective_2(root) + _objective_3(root)
            + _objective_4(client, root, tiers) + _objective_5(client, root, tiers)
            + _objective_6(root, results_dir, tiers))


def fake_demo_client(root: str = DEMO_ROOT) -> FakeClient:
    """Scripted answers: each requirement gets its reference policy (the first
    one with a typo first, to show the correction loop); the optimizer gets the
    hand-written rewrite of 06_optimize_llm.pol."""
    reqs = load_requirements(root)
    references = {r.english.strip(): "\n".join(l for l in _read(r.reference_path).splitlines()
                                               if not l.startswith("#")).strip() + "\n"
                  for r in reqs}
    source = _read(_path("06_optimize_llm.pol", root)).strip()
    rewrite = _read(_path("06_optimize_llm.rewrite.pol", root))
    typo_for = {reqs[0].english.strip()}

    def reply(prompt: str) -> str:
        if prompt.startswith("You optimize"):
            return rewrite if "Policy:\n%s\n" % source in prompt else ""
        for english, reference in references.items():
            if "Requirement:\n%s\n" % english in prompt:
                if english in typo_for:
                    typo_for.discard(english)
                    return re.sub(r"\b(READ|WRITE|EXECUTE|ACCESS)\b(?=\s+\w+\s*$)", "FLY", reference,
                                  count=1, flags=re.M)
                return reference
        return ""

    return FakeClient(reply)


def format_report(checks: List[Check], fake: bool = False) -> str:
    out = []
    if fake:
        out.append("FAKE MODEL: scripted answers for the AI checks, not real results")
    out.append("policyc demo-check: Review 1 outcomes on the demo/ examples")
    for n, title in OBJECTIVES.items():
        out.append("")
        out.append("Objective %d  %s" % (n, title))
        for c in (c for c in checks if c.objective == n):
            out.append("  [%s] %s" % (c.status, c.name))
            if c.detail:
                out.append("         %s" % c.detail)
    fails = sum(c.status == "FAIL" for c in checks)
    warns = sum(c.status == "WARN" for c in checks)
    passes = len(checks) - fails - warns
    out.append("")
    out.append("ALL OUTCOMES DEMONSTRATED (%d pass, %d warn)" % (passes, warns) if not fails else
               "%d outcomes need attention (%d fail, %d warn, %d pass)" % (fails, fails, warns, passes))
    return "\n".join(out) + "\n"
