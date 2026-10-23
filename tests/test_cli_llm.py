import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from policyc.__main__ import main
from policyc.ai.evaluation.report import BANNER
from policyc.compiler import compile_file

from helpers import example

NO_KEY = {k: v for k, v in os.environ.items() if k not in ("GEMINI_API_KEY", "POLICYC_LLM")}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_generate_task_with_fake_writes_compiling_utf8_file(self):
        path = os.path.join(self.tmp, "out.pol")
        code, out, _ = run(["generate", "--task", "g08", "--llm", "fake", "-o", path])
        self.assertEqual(code, 0, out)
        self.assertIn(BANNER, out)
        self.assertTrue(compile_file(path).ok)

    def test_generate_with_inline_vocabulary(self):
        code, out, _ = run(["generate", "analysts may read reports", "--subjects", "analyst",
                            "--resource", "reports=READ,WRITE", "--attributes", "authenticated",
                            "--llm", "fake"])
        self.assertEqual(code, 0, out)
        self.assertIn("SUBJECT analyst", out)

    def test_generate_without_key_explains_and_exits_2(self):
        with mock.patch.dict(os.environ, NO_KEY, clear=True):
            code, out, err = run(["generate", "admins can read the db"])
        self.assertEqual(code, 2)
        self.assertIn("GEMINI_API_KEY", err)
        self.assertNotIn("Traceback", err)

    def test_generate_bad_resource_syntax(self):
        code, _, err = run(["generate", "x", "--resource", "reports", "--llm", "fake"])
        self.assertEqual(code, 2)
        self.assertIn("NAME=ACTION", err)

    def test_generate_needs_english_or_task(self):
        code, _, err = run(["generate", "--llm", "fake"])
        self.assertEqual(code, 2)

    def test_optimize_with_fake_reports_each_proposal(self):
        code, out, _ = run(["optimize", example("optimizable.pol"), "--llm", "fake", "--attempts", "2"])
        self.assertEqual(code, 1)
        self.assertEqual(out.count("not_smaller"), 2)
        self.assertIn("keeping the original", out)

    def test_optimize_input_must_compile(self):
        code, _, err = run(["optimize", example("syntax_errors.pol"), "--llm", "fake"])
        self.assertEqual(code, 1)
        self.assertIn("P001", err)

    def test_evaluate_fake_writes_every_report(self):
        code, out, _ = run(["evaluate", "--llm", "fake", "--out", self.tmp])
        self.assertEqual(code, 0, out)
        for name in ("summary.txt", "generation.csv", "optimization.csv", "proposals.csv",
                     "semantic.csv", "results.md", "run.json"):
            self.assertTrue(os.path.exists(os.path.join(self.tmp, name)), name)
        self.assertIn(BANNER, out)

    def test_evaluate_semantic_only_needs_no_key(self):
        with mock.patch.dict(os.environ, NO_KEY, clear=True):
            code, out, _ = run(["evaluate", "--suite", "semantic", "--out", self.tmp])
        self.assertEqual(code, 0)
        self.assertIn("seeded semantic issues", out)

    def test_evaluate_without_key_skips_ai_suites(self):
        with mock.patch.dict(os.environ, NO_KEY, clear=True):
            code, out, err = run(["evaluate", "--out", self.tmp])
        self.assertEqual(code, 0)
        self.assertIn("skipped", err)
        self.assertIn("GEMINI_API_KEY", err)
        self.assertIn("baseline on all policies", out)

    def test_evaluate_fresh_removes_old_raw_records(self):
        run(["evaluate", "--llm", "fake", "--suite", "generation", "--limit", "2", "--out", self.tmp])
        raw = os.path.join(self.tmp, "raw", "generation.jsonl")
        self.assertTrue(os.path.exists(raw))
        run(["evaluate", "--llm", "fake", "--suite", "generation", "--limit", "1", "--out", self.tmp,
             "--fresh"])
        with open(raw, encoding="utf-8") as f:
            self.assertEqual(len(f.readlines()), 1)


if __name__ == "__main__":
    unittest.main()
