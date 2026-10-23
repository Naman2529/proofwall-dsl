"""AST node classes produced by the parser (phase 2).

The AST mirrors what the user wrote. Lowering to the flatter IR happens in ir.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Union


@dataclass
class Ident:
    name: str
    line: int
    col: int


@dataclass
class ActionRef:
    name: str          # READ | WRITE | EXECUTE | ACCESS
    line: int
    col: int


# ---- conditions -----------------------------------------------------------

@dataclass(frozen=True)
class Attr:
    name: str


@dataclass(frozen=True)
class Not:
    operand: "Cond"


@dataclass(frozen=True)
class And:
    left: "Cond"
    right: "Cond"


@dataclass(frozen=True)
class Or:
    left: "Cond"
    right: "Cond"


Cond = Union[Attr, Not, And, Or]


# ---- statements -----------------------------------------------------------

@dataclass
class SubjectDecl:
    names: List[Ident]
    line: int


@dataclass
class ResourceDecl:
    name: Ident
    actions: List[ActionRef]
    line: int


@dataclass
class Policy:
    effect: str                 # ALLOW | DENY
    subject: Ident
    action: ActionRef
    resource: Ident
    condition: Optional[Cond]   # None means "always"
    line: int


Statement = Union[SubjectDecl, ResourceDecl, Policy]


@dataclass
class Program:
    statements: List[Statement] = field(default_factory=list)

    @property
    def policies(self) -> List[Policy]:
        return [s for s in self.statements if isinstance(s, Policy)]


# ---- pretty printing (used by --emit ast) ----------------------------------

def cond_to_str(c: Optional[Cond]) -> str:
    """Fully parenthesised form, so precedence is visible."""
    if c is None:
        return "TRUE"
    if isinstance(c, Attr):
        return c.name
    if isinstance(c, Not):
        return "(NOT %s)" % cond_to_str(c.operand)
    if isinstance(c, And):
        return "(%s AND %s)" % (cond_to_str(c.left), cond_to_str(c.right))
    return "(%s OR %s)" % (cond_to_str(c.left), cond_to_str(c.right))


def _cond_tree(c: Cond, indent: str, out: List[str]) -> None:
    if isinstance(c, Attr):
        out.append("%sAttr %s" % (indent, c.name))
    elif isinstance(c, Not):
        out.append("%sNot" % indent)
        _cond_tree(c.operand, indent + "  ", out)
    else:
        out.append("%s%s" % (indent, type(c).__name__))
        _cond_tree(c.left, indent + "  ", out)
        _cond_tree(c.right, indent + "  ", out)


def dump(program: Program) -> str:
    out = ["Program"]
    for s in program.statements:
        if isinstance(s, SubjectDecl):
            out.append("  SubjectDecl (line %d): %s" % (s.line, ", ".join(i.name for i in s.names)))
        elif isinstance(s, ResourceDecl):
            out.append("  ResourceDecl (line %d): %s SUPPORTS %s"
                       % (s.line, s.name.name, ", ".join(a.name for a in s.actions)))
        else:
            out.append("  Policy (line %d)" % s.line)
            out.append("    effect:   %s" % s.effect)
            out.append("    subject:  Ident %s" % s.subject.name)
            out.append("    action:   %s" % s.action.name)
            out.append("    resource: Ident %s" % s.resource.name)
            if s.condition is not None:
                out.append("    when:")
                _cond_tree(s.condition, "      ", out)
    return "\n".join(out)
