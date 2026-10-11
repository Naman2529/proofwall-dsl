import unittest

from policyc import logic
from policyc.compiler import compile_source
from policyc.ir import decide
from policyc.lexer import tokenize
from policyc.parser import parse

from helpers import read_example

SRC = """SUBJECT admin, guest
RESOURCE database SUPPORTS READ, WRITE
ALLOW admin ACCESS database
ALLOW guest READ database WHEN vpn
DENY guest READ database WHEN on_public_wifi
"""


def cond(text):
    program, _ = parse(tokenize("ALLOW a READ r WHEN " + text)[0])
    return program.policies[0].condition


class IRTests(unittest.TestCase):
    def setUp(self):
        self.ir = compile_source(SRC).ir

    def test_access_is_expanded_with_ids_and_lines(self):
        first_two = [(r.id, r.action, r.line) for r in self.ir.rules[:2]]
        self.assertEqual(first_two, [("R1", "READ", 3), ("R2", "WRITE", 3)])
        self.assertEqual(len(self.ir.rules), 4)

    def test_deny_overrides(self):
        env = {"vpn": True, "on_public_wifi": True}
        self.assertEqual(decide(self.ir, "guest", "READ", "database", env), "DENY")
        self.assertEqual(decide(self.ir, "guest", "READ", "database", {"vpn": True}), "ALLOW")

    def test_default_deny_and_missing_attributes_are_false(self):
        self.assertEqual(decide(self.ir, "guest", "WRITE", "database", {}), "DENY")
        self.assertEqual(decide(self.ir, "guest", "READ", "database", {}), "DENY")
        self.assertEqual(decide(self.ir, "admin", "WRITE", "database", {}), "ALLOW")

    def test_canonical_condition_keys(self):
        self.assertEqual(logic.canonical(cond("a AND (b AND c)")),
                         logic.canonical(cond("c AND b AND a")))
        self.assertNotEqual(logic.canonical(cond("a AND b")), logic.canonical(cond("a OR b")))

    def test_codegen_json(self):
        target = compile_source(read_example("valid.pol")).target
        self.assertEqual(target["combining_algorithm"], "deny-overrides")
        self.assertEqual(target["default_decision"], "DENY")
        self.assertEqual(target["resources"]["internal_api"], ["READ", "EXECUTE"])
        rule = next(r for r in target["rules"] if r["subject"] == "analyst")
        self.assertEqual(rule["source_line"], 11)
        self.assertEqual(rule["condition"],
                         {"and": [{"attr": "authenticated"}, {"not": {"attr": "on_public_wifi"}}]})


if __name__ == "__main__":
    unittest.main()
