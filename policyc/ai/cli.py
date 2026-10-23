"""CLI commands for the AI stages, registered by policyc/__main__.py.

    python -m policyc generate "ENGLISH" [--task ID | --subjects a,b --resource db=READ,WRITE
                                          --attributes x,y] [--attempts N] [--llm gemini|fake] [-o OUT]
    python -m policyc optimize FILE [--samples N] [--attempts N] [--mode ...] [--llm ...] [-o OUT]
    python -m policyc evaluate [--suite ...] [--llm ...] [--samples N] [--attempts N] [--mode ...]
                              [--limit N] [--out DIR] [--fresh]

Exit codes: 0 success, 1 not compiled / nothing accepted, 2 bad usage, I/O or
a failed model call (the message names the kind of failure, never the key).
"""

from __future__ import annotations

import datetime
import os
import platform
import sys

from ..compiler import compile_source
from .evaluation import charts
from .evaluation.report import BANNER, summarize, write_reports
from .evaluation.run import (RunConfig, fake_client_for, latest_records, run_generation,
                             run_optimization, run_semantic)
from .evaluation.tasks import EVAL_ROOT, load_generation_tasks
from .generator import generate_policy
from .llm_client import FakeClient, LLMError, default_client
from .llm_optimizer import optimize_with_llm
from .prompts import PROMPT_VERSION, Vocabulary

LLMS = ("gemini", "fake")


class UsageError(Exception):
    pass


def _strip_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#")).strip() + "\n"


def _vocabulary(args):
    if args.task:
        tasks = {t.id: t for t in load_generation_tasks()}
        if args.task not in tasks:
            raise UsageError("unknown task %r (g01 .. g%02d)" % (args.task, len(tasks)))
        return tasks[args.task].english, tasks[args.task].vocabulary, tasks[args.task]
    if not args.english:
        raise UsageError("give a requirement in English, or --task ID")
    if not args.subjects and not args.resource:
        return args.english, None, None
    resources = {}
    for spec in args.resource or []:
        name, sep, actions = spec.partition("=")
        if not sep or not name or not actions:
            raise UsageError("--resource must look like NAME=ACTION[,ACTION], got %r" % spec)
        resources[name.strip()] = [a.strip() for a in actions.split(",") if a.strip()]
    split = lambda s: [x.strip() for x in (s or "").split(",") if x.strip()]
    return args.english, Vocabulary(split(args.subjects), resources, split(args.attributes)), None


def _client(name, fake_reply):
    if (name or os.environ.get("POLICYC_LLM") or "gemini") == "fake":
        return FakeClient(fake_reply)
    return default_client(name)


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("wrote %s" % path, file=sys.stderr)


def cmd_generate(args) -> int:
    try:
        english, vocab, task = _vocabulary(args)
    except UsageError as e:
        print("policyc generate: %s" % e, file=sys.stderr)
        return 2
    if task is not None:
        with open(task.reference_path, encoding="utf-8") as f:
            fake_answer = _strip_comments(f.read())
    else:
        fake_answer = vocab.declarations() + "\n" if vocab else "SUBJECT nobody\n"
    client = _client(args.llm, lambda prompt: fake_answer)
    if client.name == "fake":
        print(BANNER)
    try:
        res = generate_policy(english, vocab, llm=client, max_attempts=args.attempts)
    except LLMError as e:
        print("policyc generate: model call failed (%s)" % e, file=sys.stderr)
        return 2
    for i, a in enumerate(res.attempts, 1):
        errors = a.errors()
        print("attempt %d: %s" % (i, "compiles" if a.ok else "%d error(s): %s" % (
            len(errors), ", ".join("%s line %s" % (e["code"], e["line"]) for e in errors))))
    if not res.ok:
        print("no policy compiled after %d attempt(s)" % len(res.attempts))
        return 1
    if args.output:            # before printing: the console may not encode every character
        _write(args.output, res.dsl.rstrip() + "\n")
    print("\n" + res.dsl)
    for d in res.attempts[-1].result.warnings:
        print(d.format("<generated>", res.dsl), file=sys.stderr)
    return 0


def cmd_optimize(args) -> int:
    try:
        with open(args.file, encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        print("policyc optimize: %s" % e, file=sys.stderr)
        return 2
    original = compile_source(source)
    if not original.ok:
        for d in original.errors:
            print(d.format(args.file, source), file=sys.stderr)
        print("the input policy must compile before it can be optimized", file=sys.stderr)
        return 1
    client = _client(args.llm, lambda prompt: source)
    if client.name == "fake":
        print(BANNER)
    try:
        res = optimize_with_llm(source, llm=client, max_attempts=args.attempts,
                                samples=args.samples, mode=args.mode)
    except LLMError as e:
        print("policyc optimize: model call failed (%s)" % e, file=sys.stderr)
        return 2
    for i, p in enumerate(res.proposals, 1):
        detail = p.verification.summary() if p.verification and p.stage != "accepted" else p.note
        print("attempt %d, proposal %d: %s (%s)%s" % (p.attempt, i, p.stage, p.tier,
                                                      "  " + detail if detail else ""))
    if res.accepted is None:
        print("no proposal accepted after %d attempt(s); keeping the original" % args.attempts)
        return 1
    if args.output:
        _write(args.output, res.final_source)
    print("accepted: IR rules %d -> %d, statements %d -> %d%s" % (
        res.rules_before, res.rules_after, res.statements_before, res.statements_after,
        " (drops a DENY rule)" if res.accepted.drops_deny_guard else ""))
    print("  " + res.accepted.verification.summary())
    if res.accepted.stricter:
        print("  WARNING: STRICTER than the original: it denies some requests the original "
              "allowed (check with: python -m policyc verify ORIGINAL NEW)")
    print("\n" + res.final_source, end="")
    return 0


def cmd_evaluate(args) -> int:
    suites = ("generation", "optimization", "semantic") if args.suite == "all" else (args.suite,)
    raw = os.path.join(args.out, "raw")
    gen_raw, opt_raw = os.path.join(raw, "generation.jsonl"), os.path.join(raw, "optimization.jsonl")
    if args.fresh:             # only the suites being run; other results stay
        for suite, path in (("generation", gen_raw), ("optimization", opt_raw)):
            if suite in suites and os.path.exists(path):
                os.remove(path)
    llm = args.llm or os.environ.get("POLICYC_LLM") or "gemini"
    client = None
    if llm == "fake":
        client = fake_client_for(EVAL_ROOT)
    elif os.environ.get("GEMINI_API_KEY"):
        client = default_client(llm)
    elif set(suites) & {"generation", "optimization"}:
        print("AI suites skipped: set GEMINI_API_KEY (or use --llm fake). Reports show earlier "
              "results where they exist%s." % (
                  ", and the compiler-only baseline" if "optimization" in suites else ""),
              file=sys.stderr)
    name = client.name if client else "none"
    cfg = RunConfig(llm_name=name, samples=args.samples, attempts=args.attempts, mode=args.mode)
    progress = lambda line: print(line, file=sys.stderr)
    # Suites not run now keep their latest earlier results in the reports.
    if "generation" in suites and client:
        gen = run_generation(client, cfg, gen_raw, limit=args.limit, progress=progress)
    else:
        gen = latest_records(gen_raw)
    if "optimization" in suites and client:
        opt = run_optimization(client, cfg, opt_raw, limit=args.limit, progress=progress)
    else:
        opt = latest_records(opt_raw)
        if not opt and "optimization" in suites:
            opt = run_optimization(None, cfg, opt_raw, limit=args.limit)
    sem = run_semantic() if "semantic" in suites else []
    info = {"date": datetime.datetime.now().isoformat(timespec="seconds"), "llm": name,
            "prompt_version": PROMPT_VERSION, "attempts": args.attempts, "samples": args.samples,
            "mode": args.mode, "suites": list(suites), "limit": args.limit,
            "python": platform.python_version()}
    summary = summarize(gen, opt, sem)
    paths = write_reports(args.out, summary, gen, opt, sem, info)
    paths += charts.write_charts(args.out, summary)
    with open(os.path.join(args.out, "summary.txt"), encoding="utf-8") as f:
        print(f.read(), end="")
    print("\nwrote %d files to %s" % (len(paths), args.out), file=sys.stderr)
    return 0


def add_commands(sub) -> None:
    g = sub.add_parser("generate", help="English requirement -> DSL policy, with the LLM")
    g.add_argument("english", nargs="?")
    g.add_argument("--task", help="use a benchmark task's requirement and vocabulary (g01..g20)")
    g.add_argument("--subjects", help="comma-separated subject names")
    g.add_argument("--resource", action="append", help="NAME=ACTION[,ACTION]; repeat per resource")
    g.add_argument("--attributes", help="comma-separated attribute names")
    g.add_argument("--attempts", type=int, default=3)
    g.add_argument("--llm", choices=LLMS)
    g.add_argument("-o", "--output", help="write the generated policy here")
    g.set_defaults(func=cmd_generate)

    o = sub.add_parser("optimize", help="LLM-proposed, verifier-checked optimization")
    o.add_argument("file")
    o.add_argument("--samples", type=int, default=1)
    o.add_argument("--attempts", type=int, default=3)
    o.add_argument("--mode", choices=["equivalent", "non-weakening"], default="equivalent")
    o.add_argument("--llm", choices=LLMS)
    o.add_argument("-o", "--output", help="write the optimized policy here")
    o.set_defaults(func=cmd_optimize)

    e = sub.add_parser("evaluate", help="run the evaluation and write the reports")
    e.add_argument("--suite", choices=["all", "generation", "optimization", "semantic"], default="all")
    e.add_argument("--llm", choices=LLMS)
    e.add_argument("--samples", type=int, default=1)
    e.add_argument("--attempts", type=int, default=3)
    e.add_argument("--mode", choices=["equivalent", "non-weakening"], default="equivalent")
    e.add_argument("--limit", type=int, help="only the first N tasks / policies per suite")
    e.add_argument("--out", default=os.path.join("evaluation", "results"))
    e.add_argument("--fresh", action="store_true", help="discard earlier raw results first")
    e.set_defaults(func=cmd_evaluate)
