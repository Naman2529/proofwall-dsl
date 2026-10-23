"""The one error/warning object every phase reports.

Diagnostics are structured on purpose: `to_dict()` is what `--json-diagnostics`
prints, and it is what the LLM correction loop (policyc/ai/generator.py) will
send back to the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

ERROR = "error"
WARNING = "warning"


@dataclass
class Diagnostic:
    code: str               # L001, P001, S001..S006, W101..W106
    phase: str              # lexer | parser | semantic
    severity: str           # error | warning
    message: str
    line: int
    col: int
    expected: List[str] = field(default_factory=list)
    hint: Optional[str] = None

    @property
    def is_error(self) -> bool:
        return self.severity == ERROR

    def to_dict(self) -> dict:
        d = {
            "code": self.code,
            "phase": self.phase,
            "severity": self.severity,
            "message": self.message,
            "line": self.line,
            "col": self.col,
        }
        if self.expected:
            d["expected"] = list(self.expected)
        if self.hint:
            d["hint"] = self.hint
        return d

    def format(self, filename: str = "<input>", source: Optional[str] = None) -> str:
        out = ["%s:%d:%d: %s[%s] %s" % (filename, self.line, self.col, self.severity,
                                        self.code, self.message)]
        if source is not None:
            lines = source.splitlines()
            if 1 <= self.line <= len(lines):
                text = lines[self.line - 1]
                out.append("    %s" % text)
                out.append("    %s^" % (" " * max(self.col - 1, 0)))
        if self.expected:
            out.append("    expected: %s" % ", ".join(self.expected))
        if self.hint:
            out.append("    hint: %s" % self.hint)
        return "\n".join(out)


def sort_key(d: Diagnostic):
    return (d.line, d.col, d.code)


def has_errors(diags: List[Diagnostic]) -> bool:
    return any(d.is_error for d in diags)
