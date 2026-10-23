"""Command-line interface.

    python -m policyc compile FILE [--emit tokens|ast|ir|json|all] [--optimize]
                                   [-o OUT.json] [--json-diagnostics]
    python -m policyc verify ORIGINAL CANDIDATE [--mode equivalent|non-weakening]
                                                [--backend layered|z3|enumerate|differential]

    python -m policyc generate | optimize | evaluate ...   (AI stages, see policyc/ai/cli.py)

Exit codes: 0 success / proven, 1 compile errors / rejected / unproven, 2 bad usage or I/O.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import ast_nodes
from .codegen import generate
from .compiler import CompileResult, compile_file
from .verification.layered import BACKENDS
from .verifier import verify


def _print_diagnostics(res: CompileResult, filename: str, as_json: bool) -> None:
    if as_json:
        # On failure the diagnostics are the output; on success keep stdout for the target.
        out = sys.stdout if not res.ok else sys.stderr
        print(json.dumps([d.to_dict() for d in res.diagnostics], indent=2), file=out)
        return
    for d in res.diagnostics:
        print(d.format(filename, res.source), file=sys.stderr)
    if res.diagnostics:
        print("%d error(s), %d warning(s)" % (len(res.errors), len(res.warnings)), file=sys.stderr)


def _section(title: str) -> None:
    print("\n== %s ==" % title)


def _cmd_compile(args) -> int:
    try:
        res = compile_file(args.file, optimize_ir=args.optimize)
    except OSError as e:
        print("policyc: %s" % e, file=sys.stderr)
        return 2

    emit = args.emit
    show_all = emit == "all"
    # Tokens are still useful when there are syntax errors, so show them first.
    if show_all or emit == "tokens":
        _section("TOKENS")
        for t in res.tokens:
            print(t)

    _print_diagnostics(res, args.file, args.json_diagnostics)
    if not res.ok:
        return 1

    if show_all or emit == "ast":
        _section("AST")
        print(ast_nodes.dump(res.ast))
    if show_all or emit == "ir":
        _section("IR")
        print(res.ir.dump())
        if res.optimization is not None:
            _section("OPTIMIZER")
            print(res.optimization.summary())
            print(res.verification.summary())
            if res.verification.equivalent:
                _section("OPTIMIZED IR")
                print(res.final_ir.dump())
            else:
                print("optimized IR rejected, keeping the original")
    elif res.optimization is not None:
        print("%s | %s" % (res.optimization.summary().splitlines()[0],
                           res.verification.summary()), file=sys.stderr)
    if show_all or emit == "json":
        if show_all:
            _section("TARGET JSON")
        print(json.dumps(res.target, indent=2))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(res.target, f, indent=2)
            f.write("\n")
        print("wrote %s" % args.output, file=sys.stderr)
    return 0


def _cmd_verify(args) -> int:
    results = []
    for path in (args.original, args.candidate):
        try:
            res = compile_file(path)
        except OSError as e:
            print("policyc: %s" % e, file=sys.stderr)
            return 2
        if not res.ok:
            _print_diagnostics(res, path, False)
            print("REJECTED: %s does not compile" % path)
            return 1
        results.append(res)
    original, candidate = results
    print("original:  %d IR rules" % len(original.ir.rules))
    print("candidate: %d IR rules" % len(candidate.ir.rules))
    result = verify(original.ir, candidate.ir, mode=args.mode, backend=args.backend)
    print(result.summary())
    return 0 if result.proven else 1


def main(argv=None) -> int:
    # Model output can contain any character; a redirected Windows console
    # (cp1252) must not crash on one.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(prog="policyc",
                                 description="Cybersecurity policy DSL compiler")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compile", help="compile a .pol file")
    c.add_argument("file")
    c.add_argument("--emit", choices=["tokens", "ast", "ir", "json", "all"], default="json")
    c.add_argument("--optimize", action="store_true",
                   help="run the compiler-only optimizer, gated by the verifier")
    c.add_argument("-o", "--output", help="write the target JSON policy to this file")
    c.add_argument("--json-diagnostics", action="store_true",
                   help="print errors and warnings as JSON (for the LLM correction loop)")
    c.set_defaults(func=_cmd_compile)

    v = sub.add_parser("verify", help="check a candidate policy against the original")
    v.add_argument("original")
    v.add_argument("candidate")
    v.add_argument("--mode", choices=["equivalent", "non-weakening"], default="equivalent",
                   help="equivalent: same decisions (default); non-weakening: never allows more")
    v.add_argument("--backend", choices=BACKENDS, default="layered")
    v.set_defaults(func=_cmd_verify)

    from .ai.cli import add_commands
    add_commands(sub)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
