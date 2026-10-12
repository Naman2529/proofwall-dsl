"""Neutral policy model for verification.

Every backend works on a flat list of `Rule` objects, one per
(subject, action, resource), with a condition tree built from tuples:

    None                      no condition (always true)
    ("attr", "name")          an attribute (missing attributes are False)
    ("not", tree)
    ("and", (tree, tree, ...))
    ("or",  (tree, tree, ...))

`normalize()` converts the compiler's IR (or any rule-like objects) into that
form, so the verifier does not depend on the exact AST classes.

Semantics (same as ir.decide): if any matching DENY rule is true -> DENY,
else if any matching ALLOW rule is true -> ALLOW, else DENY.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

Tree = Optional[tuple]
Key = Tuple[str, str, str]  # (subject, action, resource)


class MalformedPolicy(ValueError):
    """The policy (or candidate) is not something the verifier can reason about."""


@dataclass(frozen=True)
class Rule:
    id: str
    effect: str          # "ALLOW" or "DENY"
    subject: str
    action: str          # one concrete action; ACCESS must already be expanded
    resource: str
    cond: Tree = None
    line: Optional[int] = None

    @property
    def key(self) -> Key:
        return (self.subject, self.action, self.resource)


# --------------------------------------------------------------------------
# condition trees
# --------------------------------------------------------------------------

_ATTR = {"attr", "ident", "identifier", "name", "attribute", "var", "variable", "atom"}
_NOT = {"not", "negation", "notexpr"}
_AND = {"and", "andexpr", "conjunction"}
_OR = {"or", "orexpr", "disjunction"}
_NAME_FIELDS = ("name", "value", "ident", "text", "lexeme", "attr", "id")
_LIST_FIELDS = ("operands", "terms", "args", "children", "items", "exprs")
_NOT_FIELDS = ("operand", "expr", "inner", "child", "value", "arg", "expression")


def _check_tree(t: Any) -> tuple:
    """Validate and freeze an already-tuple condition tree."""
    if not isinstance(t, tuple) or not t:
        raise MalformedPolicy("bad condition tree: %r" % (t,))
    tag = t[0]
    if tag == "attr" and len(t) == 2 and isinstance(t[1], str) and t[1]:
        return t
    if tag == "not" and len(t) == 2:
        return ("not", _check_tree(t[1]))
    if tag in ("and", "or") and len(t) == 2 and isinstance(t[1], (tuple, list)) and t[1]:
        return (tag, tuple(_check_tree(x) for x in t[1]))
    raise MalformedPolicy("bad condition tree: %r" % (t,))


def cond_to_tree(c: Any, adapter: Optional[Callable[[Any], Any]] = None) -> Tree:
    """Convert a condition from any supported representation into a tree.

    Supported: None, a tree tuple, a bare string (attribute), the codegen JSON
    form ({"and": [...]}, {"or": [...]}, {"not": x}, {"attr": "x"}), and AST
    node objects (matched by class name: Attr/Ident, And, Or, Not).
    `adapter(node)` may return a tree (or NotImplemented) to override this.
    """
    if c is None:
        return None
    if adapter is not None:
        out = adapter(c)
        if out is not NotImplemented:
            return None if out is None else _check_tree(out)
    if isinstance(c, tuple):
        return _check_tree(c)
    if isinstance(c, str):
        return ("attr", c)
    if isinstance(c, dict):
        if "attr" in c:
            return ("attr", c["attr"])
        if "not" in c:
            return ("not", cond_to_tree(c["not"], adapter))
        for tag in ("and", "or"):
            if tag in c:
                return (tag, tuple(cond_to_tree(x, adapter) for x in c[tag]))
        raise MalformedPolicy("unknown condition dict: %r" % (c,))
    return _from_object(c, adapter)


def _first_attr(obj: Any, fields: Iterable[str]) -> Any:
    for f in fields:
        if hasattr(obj, f):
            return getattr(obj, f)
    return None


def _from_object(c: Any, adapter: Optional[Callable[[Any], Any]]) -> tuple:
    kind = type(c).__name__.lower()
    if kind in _NOT:
        child = _first_attr(c, _NOT_FIELDS)
        if child is None:
            raise MalformedPolicy("NOT node without an operand: %r" % (c,))
        return ("not", cond_to_tree(child, adapter))
    if kind in _AND or kind in _OR:
        tag = "and" if kind in _AND else "or"
        kids = _first_attr(c, _LIST_FIELDS)
        if not isinstance(kids, (list, tuple)):
            if hasattr(c, "left") and hasattr(c, "right"):
                kids = [c.left, c.right]
            else:
                raise MalformedPolicy("%s node without operands: %r" % (kind, c))
        return (tag, tuple(cond_to_tree(k, adapter) for k in kids))
    if kind in _ATTR:
        name = _first_attr(c, _NAME_FIELDS)
        if isinstance(name, str) and name:
            return ("attr", name)
    raise MalformedPolicy(
        "unsupported condition node %s; pass cond_adapter=... to normalize() "
        "to teach the verifier about it" % type(c).__name__)


def tree_attrs(t: Tree) -> Set[str]:
    if t is None:
        return set()
    if t[0] == "attr":
        return {t[1]}
    if t[0] == "not":
        return tree_attrs(t[1])
    out: Set[str] = set()
    for x in t[1]:
        out |= tree_attrs(x)
    return out


def eval_tree(t: Tree, env: Dict[str, bool]) -> bool:
    if t is None:
        return True
    tag = t[0]
    if tag == "attr":
        return bool(env.get(t[1], False))
    if tag == "not":
        return not eval_tree(t[1], env)
    if tag == "and":
        return all(eval_tree(x, env) for x in t[1])
    return any(eval_tree(x, env) for x in t[1])


# --------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------

def _text(x: Any) -> str:
    if isinstance(x, str):
        return x
    for f in ("value", "name"):
        v = getattr(x, f, None)
        if isinstance(v, str):
            return v
    return str(x)


def normalize(policy: Any, cond_adapter: Optional[Callable[[Any], Any]] = None) -> List[Rule]:
    """Turn an IR object (with .rules), a list of rule-like objects, or a list
    of `Rule`s into a list of `Rule`s. Raises MalformedPolicy on bad input."""
    raw = getattr(policy, "rules", policy)
    try:
        items = list(raw)
    except TypeError:
        raise MalformedPolicy("expected an IR with .rules or a list of rules, got %r" % (type(policy).__name__,))
    out: List[Rule] = []
    for i, r in enumerate(items, 1):
        if isinstance(r, Rule):
            rule = r
        else:
            try:
                rule = Rule(
                    id=str(getattr(r, "id", None) or "R%d" % i),
                    effect=_text(r.effect).upper(),
                    subject=_text(r.subject),
                    action=_text(r.action).upper(),
                    resource=_text(r.resource),
                    cond=cond_to_tree(getattr(r, "condition", getattr(r, "cond", None)), cond_adapter),
                    line=getattr(r, "line", None),
                )
            except AttributeError as e:
                raise MalformedPolicy("rule %d is missing a field: %s" % (i, e))
        out.append(rule)
    problems = check_wellformed(out)
    if problems:
        raise MalformedPolicy("; ".join(problems))
    return out


def check_wellformed(rules: List[Rule]) -> List[str]:
    problems = []
    for r in rules:
        if r.effect not in ("ALLOW", "DENY"):
            problems.append("%s: effect must be ALLOW or DENY, got %r" % (r.id, r.effect))
        if r.action == "ACCESS":
            problems.append("%s: ACCESS must be expanded into one rule per action" % r.id)
        if not (r.subject and r.action and r.resource):
            problems.append("%s: subject, action and resource must be non-empty" % r.id)
    return problems


def group_by_request(rules: List[Rule]) -> Dict[Key, List[Rule]]:
    groups: Dict[Key, List[Rule]] = {}
    for r in rules:
        groups.setdefault(r.key, []).append(r)
    return groups


def request_attrs(*rule_lists: List[Rule]) -> List[str]:
    names: Set[str] = set()
    for rules in rule_lists:
        for r in rules:
            names |= tree_attrs(r.cond)
    return sorted(names)


def decide_rules(rules: List[Rule], env: Dict[str, bool]) -> str:
    """Decision for ONE request, given the rules that match it."""
    allowed = False
    for r in rules:
        if eval_tree(r.cond, env):
            if r.effect == "DENY":
                return "DENY"
            allowed = True
    return "ALLOW" if allowed else "DENY"


def decide(rules: List[Rule], subject: str, action: str, resource: str,
           env: Optional[Dict[str, bool]] = None) -> str:
    matching = [r for r in rules if r.key == (subject, action, resource)]
    return decide_rules(matching, env or {})


def signature(rules: List[Rule]) -> tuple:
    """Order- and id-independent fingerprint. Equal signatures => same decisions."""
    return tuple(sorted((r.effect, repr(r.cond)) for r in rules))
