"""The compiler and the verification package must agree with each other.

policyc.verification was written before the compiler modules were in the repo,
so it carries its own .pol loader and reads the IR by duck typing. These tests
pin the two halves together on the real example files.
"""
import unittest

from policyc.compiler import compile_file
from policyc.optimizer import optimize
from policyc.verifier import verify

from helpers import example

try:
    from policyc.verification import load_policy, normalize, verify_candidate
    HAVE_Z3 = True
except ImportError:  # z3-solver not installed
    HAVE_Z3 = False

CLEAN = ["valid.pol", "optimizable.pol", "candidate_good.pol", "candidate_bad.pol",
         "candidate_drops_deny.pol", "candidate_stricter.pol"]
CANDIDATES = CLEAN[2:]


def rule_set(rules):
    return sorted(((r.effect, r.key, r.cond) for r in rules), key=repr)


def ir_of(name):
    res = compile_file(example(name))
    assert res.ir is not None, "%s did not compile" % name
    return res.ir


@unittest.skipUnless(HAVE_Z3, "z3-solver not installed (pip install -r requirements.txt)")
class TestCompilerVerifierAgree(unittest.TestCase):
    def test_loader_matches_compiler_ir(self):
        for name in CLEAN:
            with self.subTest(name):
                self.assertEqual(rule_set(normalize(ir_of(name))),
                                 rule_set(load_policy(example(name))))

    def test_rules_keep_source_lines(self):
        for rule in normalize(ir_of("valid.pol")):
            self.assertIsInstance(rule.line, int)

    def test_equivalent_mode_agrees_with_old_verifier(self):
        original = ir_of("optimizable.pol")
        for name in CANDIDATES:
            with self.subTest(name):
                candidate = ir_of(name)
                old = verify(original, candidate)
                new = verify_candidate(original, candidate, mode="equivalent")
                self.assertEqual(old.equivalent, new.proven)
                if not old.equivalent:
                    self.assertEqual(str(old.counterexample), str(new.counterexample))

    def test_expected_verdicts_on_compiled_ir(self):
        original = ir_of("optimizable.pol")
        expected = {"candidate_good.pol": True, "candidate_bad.pol": False,
                    "candidate_drops_deny.pol": False, "candidate_stricter.pol": True}
        for name, proven in expected.items():
            with self.subTest(name):
                self.assertEqual(verify_candidate(original, ir_of(name)).proven, proven)

    def test_compiler_optimizer_output_is_proven(self):
        for name in ["valid.pol", "optimizable.pol"]:
            with self.subTest(name):
                ir = ir_of(name)
                optimized = optimize(ir).optimized
                self.assertTrue(verify_candidate(ir, optimized, mode="equivalent").proven)


if __name__ == "__main__":
    unittest.main()
