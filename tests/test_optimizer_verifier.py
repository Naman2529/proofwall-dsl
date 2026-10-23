import unittest
from unittest import mock

from policyc import compiler
from policyc.compiler import compile_file, compile_source
from policyc.optimizer import OptimizationResult, optimize
from policyc.verifier import verify

from helpers import example


class OptimizerVerifierTests(unittest.TestCase):
    def setUp(self):
        self.original = compile_file(example("optimizable.pol")).ir

    def test_optimizer_removes_three_rules(self):
        result = optimize(self.original)
        self.assertEqual((len(self.original.rules), len(result.optimized.rules)), (9, 6))
        self.assertEqual(sorted(rm.rule.id for rm in result.removed), ["R3", "R5", "R8"])

    def test_compile_with_optimize_is_verified(self):
        res = compile_file(example("optimizable.pol"), optimize_ir=True)
        self.assertTrue(res.verification.equivalent)
        self.assertEqual(res.verification.cases_checked, 14)
        self.assertEqual(len(res.target["rules"]), 6)

    def test_good_candidate_accepted(self):
        candidate = compile_file(example("candidate_good.pol")).ir
        self.assertTrue(verify(self.original, candidate).equivalent)

    def test_bad_candidate_rejected_with_counterexample(self):
        candidate = compile_file(example("candidate_bad.pol")).ir
        result = verify(self.original, candidate)
        self.assertFalse(result.equivalent)
        self.assertEqual(str(result.counterexample),
                         "analyst READ reports (authenticated=False): "
                         "original says DENY, candidate says ALLOW")

    def test_optimizer_keeps_deny_rules(self):
        ir = compile_source("SUBJECT g\nRESOURCE r SUPPORTS READ\n"
                            "DENY g READ r\nDENY g READ r WHEN x\n").ir
        kept = optimize(ir).optimized.rules
        self.assertEqual([(r.effect, r.condition) for r in kept], [("DENY", None)])

    def test_unverified_optimization_is_discarded(self):
        def broken_optimizer(ir):
            return OptimizationResult(ir, ir.with_rules(ir.rules[:1]), [])

        with mock.patch.object(compiler, "optimize", broken_optimizer):
            res = compile_file(example("optimizable.pol"), optimize_ir=True)
        self.assertFalse(res.verification.equivalent)
        self.assertIs(res.final_ir, res.ir)
        self.assertEqual(len(res.target["rules"]), 9)


if __name__ == "__main__":
    unittest.main()
