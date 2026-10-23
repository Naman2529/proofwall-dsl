"""Measurements the AI stages and the evaluation share.

size()               how big a compiled policy is, compared lexicographically:
                     (IR rules, policy statements, condition nodes)
tier()               the Review 1 classification of one AI output
same_declarations()  did a rewrite keep SUBJECT / RESOURCE declarations?
drops_deny_guard()   did a rewrite remove an explicit DENY rule?
"""

from __future__ import annotations

from typing import Optional, Tuple

from .. import ast_nodes as A
from ..compiler import CompileResult
from ..ir import DENY, IRProgram

Size = Tuple[int, int, int]

TIERS = ("invalid", "syntactically_valid", "semantically_valid", "verified", "improving")


def _nodes(c: Optional[A.Cond]) -> int:
    if c is None:
        return 0
    if isinstance(c, A.Attr):
        return 1
    if isinstance(c, A.Not):
        return 1 + _nodes(c.operand)
    return 1 + _nodes(c.left) + _nodes(c.right)


def size(res: CompileResult) -> Size:
    """Only meaningful for a policy that compiled."""
    policies = res.ast.policies
    return (len(res.ir.rules), len(policies), sum(_nodes(p.condition) for p in policies))


def tier(res: CompileResult, verified: Optional[bool] = None,
         smaller: Optional[bool] = None) -> str:
    """invalid: lexer/parser errors; syntactically_valid: parses, semantic errors;
    semantically_valid: compiles; verified: proven (or matches the reference);
    improving: verified and strictly smaller."""
    if any(d.phase in ("lexer", "parser") for d in res.errors):
        return "invalid"
    if not res.ok:
        return "syntactically_valid"
    if not verified:
        return "semantically_valid"
    return "improving" if smaller else "verified"


def same_declarations(a: IRProgram, b: IRProgram) -> Optional[str]:
    """None if both declare the same subjects and resources (with the same
    supported actions), else a one-line description of the difference."""
    diffs = []
    missing, extra = set(a.subjects) - set(b.subjects), set(b.subjects) - set(a.subjects)
    if missing:
        diffs.append("subjects removed: %s" % ", ".join(sorted(missing)))
    if extra:
        diffs.append("subjects added: %s" % ", ".join(sorted(extra)))
    for name in sorted(set(a.resources) | set(b.resources)):
        if name not in b.resources:
            diffs.append("resource removed: %s" % name)
        elif name not in a.resources:
            diffs.append("resource added: %s" % name)
        elif set(a.resources[name]) != set(b.resources[name]):
            diffs.append("resource %s SUPPORTS changed from %s to %s" % (
                name, ", ".join(a.resources[name]), ", ".join(b.resources[name])))
    return "; ".join(diffs) or None


def _denies(ir: IRProgram):
    return {(r.subject, r.action, r.resource, r.cond_key) for r in ir.rules if r.effect == DENY}


def drops_deny_guard(original: IRProgram, candidate: IRProgram) -> bool:
    return bool(_denies(original) - _denies(candidate))
