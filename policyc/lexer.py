"""Phase 1: hand-written scanner. Characters in, tokens out.

Walks the source one character at a time, tracking line and column for
every token. Whitespace and `#` comments are skipped. An illegal character
is reported as L001, skipped, and scanning continues, so one typo does not
hide later errors.
"""

from __future__ import annotations

from typing import List, Tuple

from .diagnostics import ERROR, Diagnostic
from .tokens import KEYWORDS, Token, TokenType

_PUNCT = {",": TokenType.COMMA, "(": TokenType.LPAREN, ")": TokenType.RPAREN}


def _is_ident_start(ch: str) -> bool:
    return ch.isascii() and (ch.isalpha() or ch == "_")


def _is_ident_part(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch == "_")


class Lexer:
    def __init__(self, source: str):
        self.src = source
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens: List[Token] = []
        self.diagnostics: List[Diagnostic] = []

    def _peek(self) -> str:
        return self.src[self.pos] if self.pos < len(self.src) else ""

    def _advance(self) -> str:
        ch = self.src[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def tokenize(self) -> Tuple[List[Token], List[Diagnostic]]:
        while self.pos < len(self.src):
            ch = self._peek()

            if ch in " \t\r\n":
                self._advance()
            elif ch == "#":
                while self.pos < len(self.src) and self._peek() != "\n":
                    self._advance()
            elif ch in _PUNCT:
                self.tokens.append(Token(_PUNCT[ch], ch, self.line, self.col))
                self._advance()
            elif _is_ident_start(ch):
                line, col, start = self.line, self.col, self.pos
                while self.pos < len(self.src) and _is_ident_part(self._peek()):
                    self._advance()
                word = self.src[start:self.pos]
                self.tokens.append(Token(KEYWORDS.get(word, TokenType.IDENT), word, line, col))
            else:
                self.diagnostics.append(Diagnostic(
                    code="L001", phase="lexer", severity=ERROR,
                    message="illegal character %r" % ch,
                    line=self.line, col=self.col,
                    hint="remove it; statements end at the end of the line, no ';' needed"
                    if ch == ";" else None,
                ))
                self._advance()

        self.tokens.append(Token(TokenType.EOF, "", self.line, self.col))
        return self.tokens, self.diagnostics


def tokenize(source: str) -> Tuple[List[Token], List[Diagnostic]]:
    return Lexer(source).tokenize()
