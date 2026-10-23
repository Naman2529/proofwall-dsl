import unittest

from policyc import ast_nodes as A
from policyc.compiler import compile_source
from policyc.lexer import tokenize
from policyc.parser import parse

from helpers import codes, read_example


def parse_src(src):
    tokens, lex = tokenize(src)
    program, diags = parse(tokens)
    return program, lex + diags


def cond_of(text):
    program, diags = parse_src("ALLOW a READ r WHEN " + text)
    assert not diags, diags
    return A.cond_to_str(program.policies[0].condition)


class ParserTests(unittest.TestCase):
    def test_valid_example_parses(self):
        program, diags = parse_src(read_example("valid.pol"))
        self.assertEqual(diags, [])
        self.assertEqual(len(program.statements), 8)
        self.assertEqual(len(program.policies), 4)

    def test_resource_declaration(self):
        program, _ = parse_src("RESOURCE api SUPPORTS READ, EXECUTE")
        decl = program.statements[0]
        self.assertIsInstance(decl, A.ResourceDecl)
        self.assertEqual(decl.name.name, "api")
        self.assertEqual([a.name for a in decl.actions], ["READ", "EXECUTE"])

    def test_operator_precedence(self):
        self.assertEqual(cond_of("x OR y AND NOT z"), "(x OR (y AND (NOT z)))")

    def test_parentheses_override_precedence(self):
        self.assertEqual(cond_of("(x OR y) AND z"), "((x OR y) AND z)")

    def test_expected_tokens_in_error(self):
        _, diags = parse_src("ALLOW admin FLY database")
        d = diags[0]
        self.assertEqual(d.code, "P001")
        self.assertEqual((d.line, d.col), (1, 13))
        self.assertIn("expected an action but found identifier 'FLY'", d.message)
        self.assertEqual(d.expected, ["READ", "WRITE", "EXECUTE", "ACCESS"])

    def test_missing_token_points_at_end_of_line(self):
        _, diags = parse_src("DENY guest WRITE\nALLOW a READ b")
        self.assertEqual(len(diags), 1)
        self.assertEqual((diags[0].line, diags[0].col), (1, 17))

    def test_missing_close_paren(self):
        _, diags = parse_src("ALLOW a READ b WHEN (x AND y\n")
        self.assertEqual(diags[0].expected, [")"])

    def test_lowercase_keyword_hint(self):
        _, diags = parse_src("allow a READ b")
        self.assertIn("did you mean ALLOW?", diags[0].hint)

    def test_recovery_keeps_parsing_after_error(self):
        program, diags = parse_src("ALLOW a FLY b\nDENY c READ d")
        self.assertEqual(len(diags), 1)
        self.assertEqual(len(program.policies), 1)
        self.assertEqual(program.policies[0].subject.name, "c")

    def test_syntax_errors_example_reports_five_errors(self):
        res = compile_source(read_example("syntax_errors.pol"))
        self.assertEqual(sorted(codes(res.errors)), ["L001", "P001", "P001", "P001", "P001"])
        self.assertEqual([d.line for d in res.errors], [6, 7, 8, 9, 10])


if __name__ == "__main__":
    unittest.main()
