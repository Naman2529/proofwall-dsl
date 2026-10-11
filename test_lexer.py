import unittest

from policyc.lexer import tokenize
from policyc.tokens import TokenType as T


def types(src):
    return [t.type for t in tokenize(src)[0]]


class LexerTests(unittest.TestCase):
    def test_keywords_and_identifiers(self):
        self.assertEqual(types("ALLOW admin READ database"),
                         [T.ALLOW, T.IDENT, T.READ, T.IDENT, T.EOF])

    def test_keywords_are_case_sensitive(self):
        toks, _ = tokenize("allow Allow ALLOW")
        self.assertEqual([t.type for t in toks[:3]], [T.IDENT, T.IDENT, T.ALLOW])

    def test_line_and_column_tracking(self):
        toks, _ = tokenize("SUBJECT a\n  DENY b")
        deny = toks[2]
        self.assertEqual((deny.type, deny.line, deny.col), (T.DENY, 2, 3))

    def test_comments_and_whitespace_skipped(self):
        self.assertEqual(types("# just a comment\n\tSUBJECT x # trailing\n"),
                         [T.SUBJECT, T.IDENT, T.EOF])

    def test_punctuation(self):
        self.assertEqual(types("( a , b )"),
                         [T.LPAREN, T.IDENT, T.COMMA, T.IDENT, T.RPAREN, T.EOF])

    def test_illegal_character_reported_and_skipped(self):
        toks, diags = tokenize("ALLOW a; READ b")
        self.assertEqual([d.code for d in diags], ["L001"])
        self.assertEqual((diags[0].line, diags[0].col), (1, 8))
        self.assertEqual([t.type for t in toks], [T.ALLOW, T.IDENT, T.READ, T.IDENT, T.EOF])


if __name__ == "__main__":
    unittest.main()
