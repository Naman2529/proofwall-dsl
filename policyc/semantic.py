"""Phase 3: semantic analysis. The parser checked shape; this checks meaning.

Four passes:
  1. Build the symbol table from SUBJECT / RESOURCE declarations
     (S004, S005 declared twice; S006 ACCESS listed inside SUPPORTS).
  2. Check each policy against it
     (S001 undefined subject, S002 undefined resource, S003 unsupported action).
  3. Security checks between pairs of rules that share a subject and resource
     and have overlapping actions
     (W101 duplicate, W102 redundant, W103 conflict, W104 shadowed ALLOW;
     W107 when a pair is too large to check by truth table).
  4. W106 unused declarations.

Errors stop compilation. Warnings do not: under deny-overrides the policy
still has exactly one meaning.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

from . import ast_nodes as A
from . import logic
from .diagnostics import ERROR, WARNING, Diagnostic, sort_key
from .tokens import CONCRETE_ACTIONS


@dataclass
class SymbolTable:
    subjects: Dict[str, A.Ident] = field(default_factory=dict)
    # resource name -> (declaring Ident, supported concrete actions in declared order)
    resources: Dict[str, Tuple[A.Ident, List[str]]] = field(default_factory=dict)

    def supported(self, resource: str) -> List[str]:
        return list(self.resources[resource][1])

    def expand(self, action: str, resource: str) -> List[str]:
        """ACCESS means every action the resource supports."""
        return self.supported(resource) if action == "ACCESS" else [action]


def _did_you_mean(name: str, candidates) -> Optional[str]:
    match = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.6)
    return "did you mean '%s'?" % match[0] if match else None


class SemanticAnalyzer:
    def __init__(self, program: A.Program):
        self.program = program
        self.symbols = SymbolTable()
        self.diags: List[Diagnostic] = []

    def _err(self, code, msg, line, col, expected=(), hint=None):
        self.diags.append(Diagnostic(code, "semantic", ERROR, msg, line, col, list(expected), hint))

    def _warn(self, code, msg, line, col, hint=None):
        self.diags.append(Diagnostic(code, "semantic", WARNING, msg, line, col, [], hint))

    def analyze(self) -> Tuple[SymbolTable, List[Diagnostic]]:
        self._build_symbols()
        valid = self._check_policies()
        self._check_pairs(valid)
        self._check_unused()
        self.diags.sort(key=sort_key)
        return self.symbols, self.diags

    # ---- pass 1 -------------------------------------------------------------

    def _build_symbols(self) -> None:
        for s in self.program.statements:
            if isinstance(s, A.SubjectDecl):
                for ident in s.names:
                    if ident.name in self.symbols.subjects:
                        first = self.symbols.subjects[ident.name]
                        self._err("S004", "subject '%s' is already declared on line %d"
                                  % (ident.name, first.line), ident.line, ident.col)
                    else:
                        self.symbols.subjects[ident.name] = ident
            elif isinstance(s, A.ResourceDecl):
                if s.name.name in self.symbols.resources:
                    first = self.symbols.resources[s.name.name][0]
                    self._err("S005", "resource '%s' is already declared on line %d"
                              % (s.name.name, first.line), s.name.line, s.name.col)
                    continue
                actions: List[str] = []
                for a in s.actions:
                    if a.name == "ACCESS":
                        self._err("S006", "ACCESS cannot be listed in SUPPORTS", a.line, a.col,
                                  expected=CONCRETE_ACTIONS,
                                  hint="ACCESS already means 'every supported action'")
                    elif a.name not in actions:
                        actions.append(a.name)
                self.symbols.resources[s.name.name] = (s.name, actions)

    # ---- pass 2 -------------------------------------------------------------

    def _check_policies(self) -> List[A.Policy]:
        valid = []
        for p in self.program.policies:
            ok = True
            if p.subject.name not in self.symbols.subjects:
                ok = False
                self._err("S001", "undefined subject '%s'" % p.subject.name,
                          p.subject.line, p.subject.col,
                          expected=list(self.symbols.subjects),
                          hint=_did_you_mean(p.subject.name, self.symbols.subjects)
                          or "declare it with SUBJECT %s" % p.subject.name)
            if p.resource.name not in self.symbols.resources:
                ok = False
                self._err("S002", "undefined resource '%s'" % p.resource.name,
                          p.resource.line, p.resource.col,
                          expected=list(self.symbols.resources),
                          hint=_did_you_mean(p.resource.name, self.symbols.resources)
                          or "declare it with RESOURCE %s SUPPORTS ..." % p.resource.name)
            elif p.action.name != "ACCESS":
                supported = self.symbols.supported(p.resource.name)
                if p.action.name not in supported:
                    ok = False
                    self._err("S003", "resource '%s' does not support %s"
                              % (p.resource.name, p.action.name),
                              p.action.line, p.action.col, expected=supported)
            if ok:
                valid.append(p)
        return valid

    # ---- pass 3 -------------------------------------------------------------

    def _actions(self, p: A.Policy) -> FrozenSet[str]:
        return frozenset(self.symbols.expand(p.action.name, p.resource.name))

    def _check_pairs(self, policies: List[A.Policy]) -> None:
        for i, p in enumerate(policies):
            for q in policies[i + 1:]:
                if p.subject.name != q.subject.name or p.resource.name != q.resource.name:
                    continue
                pa, qa = self._actions(p), self._actions(q)
                if not (pa & qa):
                    continue
                try:
                    if p.effect == q.effect:
                        self._same_effect(p, pa, q, qa)
                    else:
                        self._opposite_effect(p, pa, q, qa)
                except logic.TooManyAttributes:
                    self._warn("W107", "not checked against line %d for conflicts or redundancy: "
                               "the two conditions use more than %d attributes"
                               % (p.line, logic.MAX_ATTRS), q.line, 1)

    def _same_effect(self, p, pa, q, qa) -> None:
        if pa == qa and logic.canonical(p.condition) == logic.canonical(q.condition):
            self._warn("W101", "duplicate rule: same as line %d" % p.line, q.line, 1,
                       hint="delete one of them")
        elif qa <= pa and logic.implies(q.condition, p.condition):
            self._warn("W102", "redundant rule: line %d already covers it" % p.line, q.line, 1)
        elif pa <= qa and logic.implies(p.condition, q.condition):
            self._warn("W102", "redundant rule: line %d already covers it" % q.line, p.line, 1)

    def _opposite_effect(self, p, pa, q, qa) -> None:
        allow, aa, deny, da = (p, pa, q, qa) if p.effect == "ALLOW" else (q, qa, p, pa)
        if deny.condition is None and aa <= da:
            self._warn("W104", "this ALLOW can never take effect: the unconditional DENY on "
                       "line %d always wins" % deny.line, allow.line, 1)
        elif logic.satisfiable_together(allow.condition, deny.condition):
            later = q
            other = p
            actions = ", ".join(sorted(aa & da))
            self._warn("W103", "conflict with line %d: %s %s %s is both allowed and denied "
                       "when %s holds; DENY wins"
                       % (other.line, allow.subject.name, actions, allow.resource.name,
                          _both(allow.condition, deny.condition)),
                       later.line, 1, hint="if this is intended, narrow one of the conditions")

    # ---- pass 4 -------------------------------------------------------------

    def _check_unused(self) -> None:
        used_s = {p.subject.name for p in self.program.policies}
        used_r = {p.resource.name for p in self.program.policies}
        for name, ident in self.symbols.subjects.items():
            if name not in used_s:
                self._warn("W106", "subject '%s' is declared but never used" % name,
                           ident.line, ident.col)
        for name, (ident, _) in self.symbols.resources.items():
            if name not in used_r:
                self._warn("W106", "resource '%s' is declared but never used" % name,
                           ident.line, ident.col)


def _both(a, b) -> str:
    parts = [A.cond_to_str(c) for c in (a, b) if c is not None]
    return " AND ".join(parts) if parts else "always"


def analyze(program: A.Program) -> Tuple[SymbolTable, List[Diagnostic]]:
    return SemanticAnalyzer(program).analyze()
