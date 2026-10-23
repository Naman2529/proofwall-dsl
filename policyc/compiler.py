"""Runs the phases in order. This is the API the CLI, the tests and the AI
hooks (policyc/ai/) call.

    lexer -> parser -> semantic -> IR -> [optimizer -> verifier] -> codegen

Lexer and parser errors are collected together (both phases always run, so a
single run reports every syntax error). Any error stops compilation before
the next phase. Warnings never stop it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import ast_nodes as A
from .codegen import generate
from .diagnostics import Diagnostic, has_errors, sort_key
from .ir import IRProgram, lower
from .lexer import tokenize
from .optimizer import OptimizationResult, optimize
from .parser import parse
from .semantic import SymbolTable, analyze
from .tokens import Token
from .verifier import VerificationResult, verify


@dataclass
class CompileResult:
    source: str
    tokens: List[Token] = field(default_factory=list)
    ast: Optional[A.Program] = None
    symbols: Optional[SymbolTable] = None
    ir: Optional[IRProgram] = None              # unoptimized IR
    optimization: Optional[OptimizationResult] = None
    verification: Optional[VerificationResult] = None
    final_ir: Optional[IRProgram] = None        # what codegen used
    target: Optional[dict] = None
    diagnostics: List[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not has_errors(self.diagnostics)

    @property
    def errors(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.is_error]

    @property
    def warnings(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if not d.is_error]


def compile_source(source: str, optimize_ir: bool = False) -> CompileResult:
    res = CompileResult(source)

    res.tokens, lex_diags = tokenize(source)
    res.ast, parse_diags = parse(res.tokens)
    res.diagnostics = sorted(lex_diags + parse_diags, key=sort_key)
    if not res.ok:
        return res

    res.symbols, sem_diags = analyze(res.ast)
    res.diagnostics.extend(sem_diags)
    if not res.ok:
        return res

    res.ir = lower(res.ast, res.symbols)
    res.final_ir = res.ir

    if optimize_ir:
        res.optimization = optimize(res.ir)
        # The optimizer is not trusted: keep its output only if proven equivalent.
        res.verification = verify(res.ir, res.optimization.optimized)
        if res.verification.equivalent:
            res.final_ir = res.optimization.optimized

    res.target = generate(res.final_ir)
    return res


def compile_file(path: str, optimize_ir: bool = False) -> CompileResult:
    with open(path, encoding="utf-8") as f:
        return compile_source(f.read(), optimize_ir)
