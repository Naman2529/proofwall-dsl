"""Runs the evaluation suites and keeps raw, resumable records.

    run_generation    objective 4: English -> DSL, correctness against the reference
    run_optimization  objective 5: compiler-only baseline vs LLM optimizer
    run_semantic      objective 3: seeded mistakes vs what semantic analysis reports

AI suites append one JSON record per task to a .jsonl file as soon as the
task finishes. A record is keyed by (task, settings, prompt version); a rerun
skips tasks that already have an "ok" record for the same key, so hitting the
free-tier quota only means running the same command again later. A failed
model call (quota, auth, network) is recorded as status "not_run", never as a
model failure, and is retried on the next run.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from ... import ast_nodes as A
from ...compiler import compile_file, compile_source
from ...ir import IRProgram
from ...verifier import verify
from ..generator import generate_policy
from ..llm_client import FakeClient, LLMError
from ..llm_optimizer import optimize_with_llm
from ..metrics import size, tier
from ..prompts import PROMPT_VERSION
from .tasks import (EVAL_ROOT, load_generation_tasks, load_optimization_policies,
                    load_semantic_cases)


@dataclass
class RunConfig:
    llm_name: str                    # client.name, e.g. "gemini:gemini-flash-lite-latest"
    samples: int = 1
    attempts: int = 3
    mode: str = "equivalent"

    def key_fields(self) -> dict:
        d = asdict(self)
        d["prompt_version"] = PROMPT_VERSION
        return d

    def key(self) -> str:
        return json.dumps(self.key_fields(), sort_keys=True)


# ---- raw records --------------------------------------------------------------

def read_raw(path: str) -> List[dict]:
    """Every decodable record; a line cut off mid-write is ignored."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def append_raw(path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()


def _done(path: str, cfg: RunConfig) -> Dict[str, dict]:
    key = cfg.key()
    return {r["task"]: r for r in read_raw(path)
            if r.get("config_key") == key and r.get("status") == "ok"}


def _base(suite: str, task: str, cfg: RunConfig) -> dict:
    return {"suite": suite, "task": task, "config": cfg.key_fields(), "config_key": cfg.key(),
            "fake": cfg.llm_name == "fake", "status": "ok", "error_kind": None}


# ---- generation -----------------------------------------------------------------

def _gen_record(task, cfg: RunConfig, client) -> dict:
    rec = _base("generation", task.id, cfg)
    rec["difficulty"] = task.difficulty
    try:
        res = generate_policy(task.english, task.vocabulary, llm=client, max_attempts=cfg.attempts)
    except LLMError as e:
        rec.update(status="not_run", error_kind=e.kind, error=str(e))
        return rec
    reference = compile_file(task.reference_path).ir
    attempts = []
    for a in res.attempts:
        verified = verify(reference, a.result.ir, mode="equivalent").proven if a.ok else None
        attempts.append({
            "dsl": a.dsl,
            "tier": tier(a.result, verified) if (a.ok or not a.result.ok) else "invalid",
            "errors": [e["code"] for e in a.errors()],
            "warnings": [d.code for d in a.result.warnings] if a.result.ok else [],
            "verified": verified,
            "tokens": (a.completion.prompt_tokens or 0) + (a.completion.output_tokens or 0)
            if a.completion.prompt_tokens is not None or a.completion.output_tokens is not None
            else None,
            "latency_s": a.completion.latency_s,
        })
    rec.update(attempts=attempts, ok=res.ok, retries=res.retries,
               first_try_ok=res.attempts[0].ok,
               first_try_verified=bool(attempts[0]["verified"]),
               verified=bool(attempts[-1]["verified"]), final_tier=attempts[-1]["tier"],
               tokens=res.tokens, latency_s=res.latency_s, final_dsl=res.dsl)
    return rec


def run_generation(client, cfg: RunConfig, raw_path: str, root: str = EVAL_ROOT,
                   limit: Optional[int] = None) -> List[dict]:
    tasks = load_generation_tasks(root)[:limit]
    done = _done(raw_path, cfg)
    records = []
    for task in tasks:
        if task.id not in done:
            done[task.id] = _gen_record(task, cfg, client)
            append_raw(raw_path, done[task.id])
        records.append(done[task.id])
    return records


# ---- optimization -----------------------------------------------------------------

def _baseline(path: str) -> dict:
    start = time.perf_counter()
    res = compile_file(path, optimize_ir=True)
    ms = (time.perf_counter() - start) * 1000
    return {"rules_before": len(res.ir.rules), "rules_after": len(res.final_ir.rules),
            "statements_before": len(res.ast.policies), "verified": res.verification.equivalent,
            "removed": [rm.rule.id for rm in res.optimization.removed], "ms": round(ms, 3),
            "tokens": 0}


def _llm_section(source: str, cfg: RunConfig, client) -> dict:
    if client is None:
        return {"status": "skipped"}
    try:
        res = optimize_with_llm(source, llm=client, max_attempts=cfg.attempts,
                                samples=cfg.samples, mode=cfg.mode)
    except LLMError as e:
        return {"status": "not_run", "error_kind": e.kind, "error": str(e)}
    proposals = [{
        "attempt": p.attempt, "stage": p.stage, "tier": p.tier, "size": p.size,
        "drops_deny_guard": p.drops_deny_guard, "note": p.note,
        "verifier": p.verification.summary() if p.verification else None,
        "tokens": (p.completion.prompt_tokens or 0) + (p.completion.output_tokens or 0)
        if p.completion.prompt_tokens is not None or p.completion.output_tokens is not None
        else None,
        "dsl": p.dsl,
    } for p in res.proposals]
    return {"status": "ok", "accepted": res.accepted is not None,
            "rules_before": res.rules_before, "rules_after": res.rules_after,
            "statements_before": res.statements_before, "statements_after": res.statements_after,
            "size_before": size(res.original),
            "size_after": res.accepted.size if res.accepted else size(res.original),
            "stage_counts": res.stage_counts, "proposals": proposals, "tokens": res.tokens,
            "latency_s": res.latency_s, "final_source": res.final_source}


def run_optimization(client, cfg: RunConfig, raw_path: str, root: str = EVAL_ROOT,
                     limit: Optional[int] = None) -> List[dict]:
    policies = load_optimization_policies(root)[:limit]
    done = _done(raw_path, cfg)
    records = []
    for name, path in policies:
        if name not in done:
            with open(path, encoding="utf-8") as f:
                source = f.read()
            rec = _base("optimization", name, cfg)
            rec["baseline"] = _baseline(path)
            rec["llm"] = _llm_section(source, cfg, client)
            if rec["llm"]["status"] == "not_run":
                rec.update(status="not_run", error_kind=rec["llm"]["error_kind"])
            done[name] = rec
            append_raw(raw_path, rec)
        records.append(done[name])
    return records


# ---- semantic -----------------------------------------------------------------------

def run_semantic(root: str = EVAL_ROOT) -> List[dict]:
    """Deterministic and free, so it is recomputed every run (no raw file)."""
    records = []
    for path, expectations in load_semantic_cases(root):
        with open(path, encoding="utf-8") as f:
            res = compile_source(f.read())
        reported = sorted({(d.line, d.code) for d in res.diagnostics})
        expected = sorted({(e.line, e.code) for e in expectations})
        reasons = {(e.line, e.code): e.known_miss for e in expectations}
        records.append({
            "suite": "semantic", "task": os.path.relpath(path, root).replace(os.sep, "/"),
            "status": "ok", "fake": False,
            "expected": [[l, c, reasons[(l, c)]] for l, c in expected],
            "reported": [[l, c] for l, c in reported],
            "matched": [[l, c] for l, c in expected if (l, c) in set(reported)],
            "missed": [[l, c, reasons[(l, c)]] for l, c in expected if (l, c) not in set(reported)],
            "false_alarms": [[l, c] for l, c in reported if (l, c) not in set(expected)],
        })
    return records


# ---- fake model for demos and tests --------------------------------------------------

def render_source(ir: IRProgram) -> str:
    """DSL text for an IR: declarations, then one statement per IR rule."""
    lines = ["SUBJECT %s" % ", ".join(ir.subjects)]
    lines += ["RESOURCE %s SUPPORTS %s" % (r, ", ".join(a)) for r, a in ir.resources.items()]
    for rule in ir.rules:
        when = "" if rule.condition is None else " WHEN %s" % A.cond_to_str(rule.condition)
        lines.append("%s %s %s %s%s" % (rule.effect, rule.subject, rule.action, rule.resource, when))
    return "\n".join(lines) + "\n"


def fake_client_for(root: str = EVAL_ROOT) -> FakeClient:
    """Scripted answers: generation gets the reference policy (the first task is
    answered wrongly once, to exercise the correction loop); optimization gets
    the compiler-only optimizer's output, or the policy unchanged when the
    baseline removes nothing. Every output made with it is marked FAKE."""
    references = {}
    for task in load_generation_tasks(root):
        with open(task.reference_path, encoding="utf-8") as f:
            references[task.english.strip()] = (task.id, f.read())
    optimized = {}
    for _, path in load_optimization_policies(root):
        with open(path, encoding="utf-8") as f:
            source = f.read()
        res = compile_file(path, optimize_ir=True)
        changed = res.final_ir is not res.ir
        optimized[source.strip()] = render_source(res.final_ir) if changed else source
    first_task = load_generation_tasks(root)[0].id
    wrong_once = set()

    def reply(prompt: str) -> str:
        if prompt.startswith("You optimize"):
            for source, answer in optimized.items():
                if "Policy:\n%s\n" % source in prompt:
                    return answer
            return ""
        for english, (task_id, reference) in references.items():
            if "Requirement:\n%s\n" % english in prompt:
                if task_id == first_task and task_id not in wrong_once:
                    wrong_once.add(task_id)
                    return reference.replace(" ACCESS ", " FULL ", 1)
                return reference
        return ""

    return FakeClient(reply)
