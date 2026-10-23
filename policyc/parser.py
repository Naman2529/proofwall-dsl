"""Phase 2: recursive-descent parser. Tokens in, AST out.

One method per grammar rule:

    program       -> statement* EOF
    statement     -> subject_decl | resource_decl | policy
    subject_decl  -> SUBJECT IDENT ( ',' IDENT )*
    resource_decl -> RESOURCE IDENT SUPPORTS action ( ',' action )*
    policy        -> effect IDENT action IDENT ( WHEN or_expr )?
    effect        -> ALLOW | DENY
    action        -> READ | WRITE | EXECUTE | ACCESS
    or_expr       -> and_expr ( OR and_expr )*
    and_expr      -> not_expr ( AND not_expr )*
    not_expr      -> NOT not_expr | primary
    primary       -> IDENT | '(' or_expr ')'

Every statement starts with a different keyword, so one token of lookahead
picks the rule (LL(1), no backtracking). Precedence NOT > AND > OR comes from
the layering of or_expr / and_expr / not_expr.

Errors use panic-mode recovery: report, skip to the next token that can start
a statement (SUBJECT, RESOURCE, ALLOW, DENY), and keep parsing.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from . import ast_nodes as A
from .diagnostics import ERROR, Diagnostic
from .tokens import (ACTION_TOKENS, EFFECT_TOKENS, KEYWORDS, STATEMENT_START, Token,
                     TokenType)


class _ParseError(Exception):
    """Internal signal: abandon the current statement and resynchronise."""


def _names(types: Sequence[TokenType]) -> List[str]:
    return [t.value if t is not TokenType.IDENT else "identifier" for t in types]


class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0
        self.diagnostics: List[Diagnostic] = []
        self._stmt_start = 0

    # ---- token helpers ----------------------------------------------------

    @property
    def cur(self) -> Token:
        return self.tokens[self.pos]

    def _prev(self) -> Optional[Token]:
        return self.tokens[self.pos - 1] if self.pos > 0 else None

    def _advance(self) -> Token:
        tok = self.cur
        if tok.type is not TokenType.EOF:
            self.pos += 1
        return tok

    def _check(self, *types: TokenType) -> bool:
        return self.cur.type in types

    def _error(self, what: str, expected: Sequence[str], hint: Optional[str] = None):
        tok = self.cur
        line, col = tok.line, tok.col
        prev = self._prev()
        # If the statement ended early, point at the end of that line rather
        # than at the start of whatever comes next.
        if prev is not None and self.pos > self._stmt_start and tok.line > prev.line:
            line, col = prev.line, prev.end_col
        if hint is None and tok.type is TokenType.IDENT and tok.lexeme.upper() in KEYWORDS:
            hint = "keywords are uppercase: did you mean %s?" % tok.lexeme.upper()
        self.diagnostics.append(Diagnostic(
            code="P001", phase="parser", severity=ERROR,
            message="expected %s but found %s" % (what, tok.describe()),
            line=line, col=col, expected=list(expected), hint=hint,
        ))
        raise _ParseError()

    def _expect(self, ttype: TokenType, what: str) -> Token:
        if self.cur.type is ttype:
            return self._advance()
        self._error(what, _names([ttype]))

    def _synchronize(self) -> None:
        while not self._check(TokenType.EOF, *STATEMENT_START):
            self._advance()

    # ---- grammar rules ----------------------------------------------------

    def parse(self) -> Tuple[A.Program, List[Diagnostic]]:
        program = A.Program()
        while not self._check(TokenType.EOF):
            start = self._stmt_start = self.pos
            try:
                program.statements.append(self._statement())
            except _ParseError:
                if self.pos == start:      # always make progress
                    self._advance()
                self._synchronize()
        return program, self.diagnostics

    def _statement(self) -> A.Statement:
        if self._check(TokenType.SUBJECT):
            return self._subject_decl()
        if self._check(TokenType.RESOURCE):
            return self._resource_decl()
        if self._check(*EFFECT_TOKENS):
            return self._policy()
        self._error("a statement", _names(STATEMENT_START))

    def _ident(self, what: str) -> A.Ident:
        tok = self._expect(TokenType.IDENT, what)
        return A.Ident(tok.lexeme, tok.line, tok.col)

    def _subject_decl(self) -> A.SubjectDecl:
        kw = self._advance()
        names = [self._ident("a subject name")]
        while self._check(TokenType.COMMA):
            self._advance()
            names.append(self._ident("a subject name"))
        return A.SubjectDecl(names, kw.line)

    def _resource_decl(self) -> A.ResourceDecl:
        kw = self._advance()
        name = self._ident("a resource name")
        self._expect(TokenType.SUPPORTS, "SUPPORTS")
        actions = [self._action()]
        while self._check(TokenType.COMMA):
            self._advance()
            actions.append(self._action())
        return A.ResourceDecl(name, actions, kw.line)

    def _action(self) -> A.ActionRef:
        if self._check(*ACTION_TOKENS):
            tok = self._advance()
            return A.ActionRef(tok.lexeme, tok.line, tok.col)
        self._error("an action", _names(ACTION_TOKENS))

    def _policy(self) -> A.Policy:
        effect = self._advance()
        subject = self._ident("a subject name")
        action = self._action()
        resource = self._ident("a resource name")
        condition = None
        if self._check(TokenType.WHEN):
            self._advance()
            condition = self._or_expr()
        return A.Policy(effect.lexeme, subject, action, resource, condition, effect.line)

    def _or_expr(self) -> A.Cond:
        left = self._and_expr()
        while self._check(TokenType.OR):
            self._advance()
            left = A.Or(left, self._and_expr())
        return left

    def _and_expr(self) -> A.Cond:
        left = self._not_expr()
        while self._check(TokenType.AND):
            self._advance()
            left = A.And(left, self._not_expr())
        return left

    def _not_expr(self) -> A.Cond:
        if self._check(TokenType.NOT):
            self._advance()
            return A.Not(self._not_expr())
        return self._primary()

    def _primary(self) -> A.Cond:
        if self._check(TokenType.IDENT):
            return A.Attr(self._advance().lexeme)
        if self._check(TokenType.LPAREN):
            self._advance()
            inner = self._or_expr()
            self._expect(TokenType.RPAREN, "')' to close the condition")
            return inner
        self._error("an attribute name or '('", ["identifier", "(", "NOT"])


def parse(tokens: List[Token]) -> Tuple[A.Program, List[Diagnostic]]:
    return Parser(tokens).parse()
