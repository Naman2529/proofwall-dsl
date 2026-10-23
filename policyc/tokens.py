"""Token kinds and the Token record produced by the lexer (phase 1)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TokenType(Enum):
    # statement keywords
    SUBJECT = "SUBJECT"
    RESOURCE = "RESOURCE"
    SUPPORTS = "SUPPORTS"
    ALLOW = "ALLOW"
    DENY = "DENY"
    WHEN = "WHEN"
    # actions
    READ = "READ"
    WRITE = "WRITE"
    EXECUTE = "EXECUTE"
    ACCESS = "ACCESS"
    # boolean operators
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    # everything else
    IDENT = "IDENT"
    COMMA = ","
    LPAREN = "("
    RPAREN = ")"
    EOF = "EOF"


# Words are scanned whole and then looked up here. Anything not in the
# table is an identifier, so keywords are case-sensitive: `allow` is an IDENT.
KEYWORDS = {
    t.value: t
    for t in (
        TokenType.SUBJECT, TokenType.RESOURCE, TokenType.SUPPORTS,
        TokenType.ALLOW, TokenType.DENY, TokenType.WHEN,
        TokenType.READ, TokenType.WRITE, TokenType.EXECUTE, TokenType.ACCESS,
        TokenType.AND, TokenType.OR, TokenType.NOT,
    )
}

ACTION_TOKENS = (TokenType.READ, TokenType.WRITE, TokenType.EXECUTE, TokenType.ACCESS)
EFFECT_TOKENS = (TokenType.ALLOW, TokenType.DENY)
# Tokens that can begin a statement. Panic-mode recovery skips to one of these.
STATEMENT_START = (TokenType.SUBJECT, TokenType.RESOURCE, TokenType.ALLOW, TokenType.DENY)

# The concrete actions. ACCESS is a wildcard meaning "every action the resource supports".
CONCRETE_ACTIONS = ("READ", "WRITE", "EXECUTE")


@dataclass(frozen=True)
class Token:
    type: TokenType
    lexeme: str
    line: int
    col: int

    @property
    def end_col(self) -> int:
        """Column just after the last character of this token."""
        return self.col + len(self.lexeme)

    def describe(self) -> str:
        """Human description used in error messages."""
        if self.type is TokenType.EOF:
            return "end of file"
        if self.type is TokenType.IDENT:
            return "identifier '%s'" % self.lexeme
        if self.type in (TokenType.COMMA, TokenType.LPAREN, TokenType.RPAREN):
            return "'%s'" % self.lexeme
        return "keyword %s" % self.lexeme

    def __str__(self) -> str:
        return "%4d:%-3d %-9s %s" % (self.line, self.col, self.type.name, self.lexeme)
