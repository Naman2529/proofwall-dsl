import unittest

from policyc.ai.metrics import TIERS, drops_deny_guard, same_declarations, size, tier
from policyc.compiler import compile_file, compile_source

from helpers import example, read_example


class MetricsTests(unittest.TestCase):
    def test_size_counts_ir_rules_statements_and_condition_nodes(self):
        self.assertEqual(size(compile_file(example("optimizable.pol"))), (9, 7, 3))
        res = compile_source("SUBJECT a\nRESOURCE r SUPPORTS READ\n"
                             "ALLOW a READ r WHEN x AND NOT (y OR z)\n")
        self.assertEqual(size(res), (1, 1, 6))

    def test_sizes_compare_lexicographically(self):
        good = size(compile_file(example("candidate_good.pol")))
        self.assertLess(good, size(compile_file(example("optimizable.pol"))))

    def test_tiers(self):
        self.assertEqual(TIERS, ("invalid", "syntactically_valid", "semantically_valid",
                                 "verified", "improving"))
        self.assertEqual(tier(compile_source("SUBJECT a;\n")), "invalid")
        self.assertEqual(tier(compile_source("SUBJECT a\nRESOURCE r SUPPORTS READ\nALLOW a FLY r\n")),
                         "invalid")
        undefined = compile_source("SUBJECT a\nRESOURCE r SUPPORTS READ\nALLOW b READ r\n")
        self.assertEqual(tier(undefined), "syntactically_valid")
        ok = compile_source("SUBJECT a\nRESOURCE r SUPPORTS READ\nALLOW a READ r\n")
        self.assertEqual(tier(ok), "semantically_valid")
        self.assertEqual(tier(ok, verified=False), "semantically_valid")
        self.assertEqual(tier(ok, verified=True), "verified")
        self.assertEqual(tier(ok, verified=True, smaller=False), "verified")
        self.assertEqual(tier(ok, verified=True, smaller=True), "improving")
        self.assertEqual(tier(undefined, verified=True, smaller=True), "syntactically_valid")

    def test_same_declarations(self):
        original = compile_file(example("optimizable.pol")).ir
        self.assertIsNone(same_declarations(original, compile_file(example("candidate_good.pol")).ir))
        dropped = compile_source(read_example("optimizable.pol").replace(
            "SUBJECT admin, analyst, guest", "SUBJECT admin, analyst").replace(
            "DENY guest ACCESS database", "").replace("ALLOW guest READ database", "")).ir
        diff = same_declarations(original, dropped)
        self.assertIn("guest", diff)
        widened = compile_source(read_example("optimizable.pol").replace(
            "RESOURCE reports  SUPPORTS READ, WRITE", "RESOURCE reports SUPPORTS READ, WRITE, EXECUTE")).ir
        self.assertIn("reports", same_declarations(original, widened))

    def test_drops_deny_guard(self):
        original = compile_file(example("optimizable.pol")).ir
        self.assertTrue(drops_deny_guard(original, compile_file(example("candidate_drops_deny.pol")).ir))
        self.assertFalse(drops_deny_guard(original, compile_file(example("candidate_good.pol")).ir))


if __name__ == "__main__":
    unittest.main()
