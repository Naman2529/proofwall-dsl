"""Small boolean-logic helpers over condition trees.

Shared by semantic analysis (implication / overlap checks), the IR
(canonical keys) and decide(). Conditions are tiny, so implication and
satisfiability are decided by truth table over the attributes involved.
`None` stands for "no WHEN clause", i.e. always true.
"""

from __future__ import annotations

from itertools import product
from typing import Dict, FrozenSet, Iterator, List, Optional

from .ast_nodes import And, Attr, Cond, Not, Or

MAX_ATTRS = 16   # 2**16 rows is the most we are willing to enumerate


def attrs(c: Optional[Cond]) -> FrozenSet[str]:
    if c is None:
        return frozenset()
    if isinstance(c, Attr):
        return frozenset([c.name])
    if isinstance(c, Not):
        return attrs(c.operand)
    return attrs(c.left) | attrs(c.right)


def evaluate(c: Optional[Cond], env: Dict[str, bool]) -> bool:
    if c is None:
        return True
    if isinstance(c, Attr):
        return bool(env.get(c.name, False))
    if isinstance(c, Not):
        return not evaluate(c.operand, env)
    if isinstance(c, And):
        return evaluate(c.left, env) and evaluate(c.right, env)
    return evaluate(c.left, env) or evaluate(c.right, env)


def assignments(names) -> Iterator[Dict[str, bool]]:
    """Every true/false assignment to `names`, in a fixed order (False first)."""
    names = sorted(names)
    if len(names) > MAX_ATTRS:
        raise ValueError("too many attributes to enumerate (%d > %d)" % (len(names), MAX_ATTRS))
    for values in product((False, True), repeat=len(names)):
        yield dict(zip(names, values))


def implies(a: Optional[Cond], b: Optional[Cond]) -> bool:
    """Is a -> b valid? (every assignment making a true also makes b true)"""
    return all(evaluate(b, env) for env in assignments(attrs(a) | attrs(b)) if evaluate(a, env))


def satisfiable_together(a: Optional[Cond], b: Optional[Cond]) -> bool:
    """Is there an assignment making both a and b true?"""
    return any(evaluate(a, env) and evaluate(b, env) for env in assignments(attrs(a) | attrs(b)))


def _flatten(c: Cond, kind) -> List[Cond]:
    if isinstance(c, kind):
        return _flatten(c.left, kind) + _flatten(c.right, kind)
    return [c]


def _by_attr(text: str):
    # order operands by attribute name, so NOT x sorts next to x
    return (text.lstrip("(").replace("NOT ", ""), text)


def canonical(c: Optional[Cond]) -> str:
    """Canonical text for a condition: AND/OR chains are flattened, operands
    sorted and de-duplicated, so `a AND b` and `b AND a` get the same key.
    This is a cheap syntactic normal form, not full logical equivalence."""
    if c is None:
        return "TRUE"
    if isinstance(c, Attr):
        return c.name
    if isinstance(c, Not):
        return "NOT %s" % canonical(c.operand)
    kind = And if isinstance(c, And) else Or
    parts = sorted(set(canonical(x) for x in _flatten(c, kind)), key=_by_attr)
    if len(parts) == 1:
        return parts[0]
    return "(%s)" % (" %s " % kind.__name__.upper()).join(parts)


def to_tree(c: Optional[Cond]):
    """JSON-friendly condition tree, used by codegen."""
    if c is None:
        return None
    if isinstance(c, Attr):
        return {"attr": c.name}
    if isinstance(c, Not):
        return {"not": to_tree(c.operand)}
    kind = And if isinstance(c, And) else Or
    return {kind.__name__.lower(): [to_tree(x) for x in _flatten(c, kind)]}
