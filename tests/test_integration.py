"""The compiler and the verification package must agree with each other.

policyc.verification was written before the compiler modules were in the repo,
so it carries its own .pol loader and reads the IR by duck typing. These tests
pin the two halves together on the real example files, and cover the wiring
(compiler gate, CLIs, LLM optimizer loop). None of them need z3-solver: without
it the verifier falls back to exact enumeration.
"""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from policyc.__main__ import main as policyc_main
from policyc.ai.llm_optimizer import optimize_with_llm
from policyc.compiler import compile_file, compile_source
from policyc.optimizer import optimize
from policyc.verification import load_policy, normalize, verify_candidate
from policyc.verification.__main__ import main as verification_main
from policyc.verifier import verify

from helpers import example, read_example

CLEAN = ["valid.pol", "optimizable.pol", "candidate_good.pol", "candidate_bad.pol",
         "candidate_drops_deny.pol", "candidate_stricter.pol"]
CANDIDATES = CLEAN[2:]


def rule_set(rules):
    return sorted(((r.effect, r.key, r.cond) for r in rules), key=repr)


def ir_of(name):
    res = compile_file(example(name))
    assert res.ir is not None, "%s did not compile" % name
    return res.ir


def run(main, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class TestCompilerVerifierAgree(unittest.TestCase):
    def test_loader_matches_compiler_ir(self):
        for name in CLEAN:
            with self.subTest(name):
                self.assertEqual(rule_set(normalize(ir_of(name))),
                                 rule_set(load_policy(example(name))))

    def test_rules_keep_source_lines(self):
        for rule in normalize(ir_of("valid.pol")):
            self.assertIsInstance(rule.line, int)

    def test_every_backend_agrees_on_compiled_ir(self):
        original = ir_of("optimizable.pol")
        for name in CANDIDATES:
            for mode in ("equivalent", "non-weakening"):
                with self.subTest(name=name, mode=mode):
                    verdicts = {verify(original, ir_of(name), mode=mode, backend=b).proven
                                for b in ("layered", "enumerate")}
                    self.assertEqual(len(verdicts), 1)

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
                self.assertTrue(verify(ir, optimize(ir).optimized).proven)


class TestManyAttributes(unittest.TestCase):
    BIG1 = " AND ".join("x%d" % i for i in range(9))
    BIG2 = " AND ".join("y%d" % i for i in range(9))
    HEADER = "SUBJECT a\nRESOURCE r SUPPORTS READ\n"

    def test_semantic_pair_check_warns_instead_of_crashing(self):
        res = compile_source(self.HEADER + "ALLOW a READ r WHEN %s\nDENY a READ r WHEN %s\n"
                             % (self.BIG1, self.BIG2))
        self.assertTrue(res.ok)
        self.assertEqual([d.code for d in res.warnings], ["W107"])

    def test_optimize_with_18_attributes_does_not_crash(self):
        res = compile_source(self.HEADER + "ALLOW a READ r WHEN %s AND %s\n"
                             % (self.BIG1, self.BIG2), optimize_ir=True)
        self.assertTrue(res.ok)
        self.assertIsNotNone(res.verification)


class TestCli(unittest.TestCase):
    def test_policyc_verify_modes(self):
        orig, stricter = example("optimizable.pol"), example("candidate_stricter.pol")
        code, out, _ = run(policyc_main, ["verify", orig, stricter])
        self.assertEqual(code, 1)
        self.assertIn("admin READ reports", out)
        code, out, _ = run(policyc_main, ["verify", orig, stricter, "--mode", "non-weakening"])
        self.assertEqual(code, 0)
        self.assertIn("PROVEN", out)

    def test_verification_cli_reads_multiline_statements(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "multiline.pol")
            with open(path, "w", encoding="utf-8") as f:
                f.write(read_example("optimizable.pol").replace(" WHEN ", "\n    WHEN "))
            code, out, _ = run(verification_main,
                               ["verify", example("optimizable.pol"), path, "--mode", "equivalent"])
        self.assertEqual(code, 0, out)

    def test_verification_cli_rejects_what_the_compiler_rejects(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "dup.pol")
            with open(path, "w", encoding="utf-8") as f:
                f.write("SUBJECT a\nSUBJECT a\nRESOURCE r SUPPORTS READ\nALLOW a READ r\n")
            code, _, err = run(verification_main, ["verify", path, path])
        self.assertEqual(code, 2)
        self.assertIn("S004", err)


class TestLlmOptimizerLoop(unittest.TestCase):
    def test_bad_proposal_rejected_then_good_one_accepted(self):
        prompts = []
        replies = iter([read_example("candidate_bad.pol"), read_example("candidate_good.pol")])

        def fake_llm(prompt):
            prompts.append(prompt)
            return next(replies)

        result = optimize_with_llm(read_example("optimizable.pol"), llm=fake_llm)
        self.assertEqual([p.accepted for p in result.proposals], [False, True])
        self.assertIn("authenticated=False", prompts[1])
        self.assertEqual((result.rules_before, result.rules_after), (9, 6))

    def test_stricter_proposal_needs_non_weakening_mode(self):
        stricter = read_example("candidate_stricter.pol")
        source = read_example("optimizable.pol")
        default = optimize_with_llm(source, llm=lambda p: stricter, max_attempts=1)
        relaxed = optimize_with_llm(source, llm=lambda p: stricter, max_attempts=1,
                                    mode="non-weakening")
        self.assertIsNone(default.accepted)
        self.assertIsNotNone(relaxed.accepted)


if __name__ == "__main__":
    unittest.main()
