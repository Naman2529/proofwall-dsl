import unittest

from policyc.lexer import tokenize
from policyc.parser import parse
from policyc.semantic import analyze

from helpers import codes, read_example

HEADER = """SUBJECT admin, guest, analyst
RESOURCE database SUPPORTS READ, WRITE
RESOURCE reports SUPPORTS READ
"""


def check(src, header=HEADER):
    program, diags = parse(tokenize(header + src)[0])
    assert not diags, diags
    return analyze(program)[1]


def without_unused(diags):
    return [d for d in diags if d.code != "W106"]


class SemanticTests(unittest.TestCase):
    def test_valid_example_is_clean(self):
        program, _ = parse(tokenize(read_example("valid.pol"))[0])
        self.assertEqual(analyze(program)[1], [])

    def test_undefined_subject_with_suggestion(self):
        d = without_unused(check("ALLOW admni READ database"))[0]
        self.assertEqual(d.code, "S001")
        self.assertEqual(d.hint, "did you mean 'admin'?")

    def test_undefined_resource(self):
        self.assertIn("S002", codes(check("ALLOW admin READ logs")))

    def test_unsupported_action(self):
        d = without_unused(check("ALLOW analyst WRITE reports"))[0]
        self.assertEqual((d.code, d.expected), ("S003", ["READ"]))

    def test_declared_twice(self):
        diags = check("SUBJECT admin\nRESOURCE reports SUPPORTS READ\nALLOW admin READ reports")
        self.assertIn("S004", codes(diags))
        self.assertIn("S005", codes(diags))

    def test_access_not_allowed_in_supports(self):
        diags = check("RESOURCE api SUPPORTS ACCESS\nALLOW admin ACCESS api", header="SUBJECT admin\n")
        self.assertIn("S006", codes(diags))

    def test_duplicate_ignores_condition_order(self):
        diags = check("ALLOW admin READ database WHEN a AND b\n"
                      "ALLOW admin READ database WHEN b AND a")
        self.assertEqual(codes(without_unused(diags)), ["W101"])

    def test_access_makes_narrower_rule_redundant(self):
        diags = check("ALLOW admin ACCESS database\nALLOW admin READ database")
        self.assertEqual(codes(without_unused(diags)), ["W102"])
        self.assertEqual(without_unused(diags)[0].line, 5)

    def test_conflict(self):
        diags = check("ALLOW analyst READ reports WHEN authenticated\n"
                      "DENY analyst READ reports WHEN on_public_wifi")
        self.assertEqual(codes(without_unused(diags)), ["W103"])

    def test_no_conflict_when_conditions_cannot_both_hold(self):
        diags = check("ALLOW analyst READ reports WHEN vpn\n"
                      "DENY analyst READ reports WHEN NOT vpn")
        self.assertEqual(without_unused(diags), [])

    def test_shadowed_allow_and_unused(self):
        diags = check("DENY guest ACCESS database\nALLOW guest READ database WHEN x")
        self.assertIn("W104", codes(diags))
        unused = sorted(d.message for d in diags if d.code == "W106")
        self.assertEqual(len(unused), 3)   # admin, analyst, reports


if __name__ == "__main__":
    unittest.main()
