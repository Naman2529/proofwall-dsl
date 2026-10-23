"""Phase 4: intermediate representation, and the one definition of meaning.

The AST mirrors what the user typed. The IR is flat and normalised:
  * ACCESS is expanded (desugared) into one rule per supported action, so every
    IR rule is about exactly one (subject, action, resource);
  * every rule gets an id (R1, R2, ...) and keeps its source line;
  * every condition gets a canonical key so equal conditions compare equal.

`decide()` is the semantics of the language. The verifier, the optimizer and
anything else that reasons about meaning must go through it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import ast_nodes as A
from . import logic
from .semantic import SymbolTable

ALLOW = "ALLOW"
DENY = "DENY"


@dataclass(frozen=True)
class IRRule:
    id: str
    effect: str
    subject: str
    action: str
    resource: str
    condition: Optional[A.Cond]
    cond_key: str
    line: int

    def matches(self, subject: str, action: str, resource: str) -> bool:
        return (self.subject, self.action, self.resource) == (subject, action, resource)

    def __str__(self) -> str:
        when = "" if self.condition is None else " WHEN %s" % self.cond_key
        return "%-4s %-5s %s %s %s%s   (line %d)" % (
            self.id, self.effect, self.subject, self.action, self.resource, when, self.line)


@dataclass
class IRProgram:
    subjects: List[str]
    resources: Dict[str, List[str]]          # resource -> supported concrete actions
    rules: List[IRRule] = field(default_factory=list)

    def with_rules(self, rules: List[IRRule]) -> "IRProgram":
        return IRProgram(list(self.subjects), dict(self.resources), list(rules))

    def rules_for(self, subject: str, action: str, resource: str) -> List[IRRule]:
        return [r for r in self.rules if r.matches(subject, action, resource)]

    def requests(self):
        """Every (subject, action, resource) this policy can be asked about."""
        for s in self.subjects:
            for r, actions in self.resources.items():
                for a in actions:
                    yield s, a, r

    def dump(self) -> str:
        out = ["IR: %d rules, combining = deny-overrides, default = DENY" % len(self.rules)]
        out.extend("  " + str(r) for r in self.rules)
        return "\n".join(out)


def lower(program: A.Program, symbols: SymbolTable) -> IRProgram:
    """AST -> IR. Assumes semantic analysis reported no errors."""
    ir = IRProgram(
        subjects=list(symbols.subjects),
        resources={name: list(actions) for name, (_, actions) in symbols.resources.items()},
    )
    n = 0
    for p in program.policies:
        key = logic.canonical(p.condition)
        for action in symbols.expand(p.action.name, p.resource.name):
            n += 1
            ir.rules.append(IRRule("R%d" % n, p.effect, p.subject.name, action,
                                   p.resource.name, p.condition, key, p.line))
    return ir


def decide(ir: IRProgram, subject: str, action: str, resource: str,
           env: Dict[str, bool]) -> str:
    """Deny-overrides with default deny:
      1. any matching DENY whose condition is true  -> DENY
      2. else any matching ALLOW whose condition is true -> ALLOW
      3. else DENY
    Attributes missing from `env` count as false."""
    allowed = False
    for rule in ir.rules_for(subject, action, resource):
        if logic.evaluate(rule.condition, env):
            if rule.effect == DENY:
                return DENY
            allowed = True
    return ALLOW if allowed else DENY
