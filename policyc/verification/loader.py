"""Tiny standalone loader for .pol files, so the verifier can be run and tested
without the rest of the compiler.

It is NOT the compiler front end (no error recovery, no semantic warnings).
When the full pipeline is available, feed the verifier its IR instead:

    from policyc.verification import verify_candidate
    verify_candidate(compile_file(a).ir, compile_file(b).ir)
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .model import Rule, Tree

_TOKEN = re.compile(r"\(|\)|,|[A-Za-z_][A-Za-z0-9_]*|\S")
_ACTIONS = ("READ", "WRITE", "EXECUTE", "ACCESS")


class PolicyLoadError(ValueError):
    pass


class _Cond:
    """Recursive descent: or_expr -> and_expr -> not_expr -> primary."""

    def __init__(self, toks: List[str], line: int):
        self.t, self.i, self.line = toks, 0, line

    def _peek(self) -> Optional[str]:
        return self.t[self.i] if self.i < len(self.t) else None

    def _next(self) -> str:
        tok = self._peek()
        if tok is None:
            raise PolicyLoadError("line %d: condition ends unexpectedly" % self.line)
        self.i += 1
        return tok

    def parse(self) -> Tree:
        tree = self._or()
        if self._peek() is not None:
            raise PolicyLoadError("line %d: unexpected %r in condition" % (self.line, self._peek()))
        return tree

    def _or(self) -> Tree:
        parts = [self._and()]
        while self._peek() == "OR":
            self._next()
            parts.append(self._and())
        return parts[0] if len(parts) == 1 else ("or", tuple(parts))

    def _and(self) -> Tree:
        parts = [self._not()]
        while self._peek() == "AND":
            self._next()
            parts.append(self._not())
        return parts[0] if len(parts) == 1 else ("and", tuple(parts))

    def _not(self) -> Tree:
        if self._peek() == "NOT":
            self._next()
            return ("not", self._not())
        return self._primary()

    def _primary(self) -> Tree:
        tok = self._next()
        if tok == "(":
            inner = self._or()
            if self._next() != ")":
                raise PolicyLoadError("line %d: missing ')'" % self.line)
            return inner
        if re.fullmatch(r"[a-z_][A-Za-z0-9_]*", tok):
            return ("attr", tok)
        raise PolicyLoadError("line %d: expected an attribute name, found %r" % (self.line, tok))


def load_policy_text(text: str) -> List[Rule]:
    subjects: set = set()
    resources: Dict[str, List[str]] = {}
    rules: List[Rule] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        toks = _TOKEN.findall(raw.split("#", 1)[0])
        if not toks:
            continue
        head = toks[0]
        if head == "SUBJECT":
            subjects.update(t for t in toks[1:] if t != ",")
        elif head == "RESOURCE":
            if len(toks) < 4 or toks[2] != "SUPPORTS":
                raise PolicyLoadError("line %d: expected RESOURCE name SUPPORTS actions" % lineno)
            resources[toks[1]] = [t for t in toks[3:] if t != ","]
        elif head in ("ALLOW", "DENY"):
            if len(toks) < 4:
                raise PolicyLoadError("line %d: expected %s subject ACTION resource" % (lineno, head))
            subj, action, res = toks[1], toks[2], toks[3]
            if subj not in subjects:
                raise PolicyLoadError("line %d: undefined subject %r" % (lineno, subj))
            if res not in resources:
                raise PolicyLoadError("line %d: undefined resource %r" % (lineno, res))
            if action not in _ACTIONS:
                raise PolicyLoadError("line %d: expected an action, found %r" % (lineno, action))
            if action != "ACCESS" and action not in resources[res]:
                raise PolicyLoadError("line %d: %s does not support %s" % (lineno, res, action))
            cond: Tree = None
            if len(toks) > 4:
                if toks[4] != "WHEN":
                    raise PolicyLoadError("line %d: expected WHEN, found %r" % (lineno, toks[4]))
                cond = _Cond(toks[5:], lineno).parse()
            for a in (resources[res] if action == "ACCESS" else [action]):
                rules.append(Rule("R%d" % (len(rules) + 1), head, subj, a, res, cond, lineno))
        else:
            raise PolicyLoadError("line %d: unknown statement %r" % (lineno, head))
    return rules


def load_policy(path: str) -> List[Rule]:
    with open(path, encoding="utf-8") as f:
        return load_policy_text(f.read())
