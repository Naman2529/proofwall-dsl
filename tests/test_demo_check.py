"""demo-check: one command that shows every Review 1 outcome on the demo/ examples."""
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from policyc.__main__ import main
from policyc.ai.demo_check import (DEMO_ROOT, NO_MODEL, fake_demo_client, format_report,
                                   load_requirements, run_demo_check)
from policyc.ai.llm_client import FakeClient


def by_objective(checks):
    out = {}
    for c in checks:
        out.setdefault(c.objective, []).append(c)
    return out


class DemoCheckTests(unittest.TestCase):
    def setUp(self):
        self.results = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.results)

    def test_every_outcome_passes_with_the_fake_model(self):
        checks = run_demo_check(fake_demo_client(), results_dir=self.results)
        groups = by_objective(checks)
        self.assertEqual(sorted(groups), [1, 2, 3, 4, 5, 6])
        failed = [c for c in checks if c.status == "FAIL"]
        self.assertEqual(failed, [], [(c.name, c.detail) for c in failed])
        self.assertEqual(sum(c.status == "PASS" for c in groups[4]), 6)   # 5 requirements + summary
        names = " ".join(c.name for c in groups[5])
        for part in ("compiler-only", "LLM", "safe", "unsafe", "stricter"):
            self.assertIn(part, names)

    def test_fake_model_shows_the_correction_loop_once(self):
        checks = run_demo_check(fake_demo_client(), results_dir=self.results)
        d01 = next(c for c in checks if c.name.startswith("d01"))
        self.assertIn("2 attempts", d01.detail)

    def test_garbage_model_fails_objectives_4_and_5_only(self):
        checks = run_demo_check(FakeClient(["this is not a policy"]), results_dir=self.results)
        failing = {c.objective for c in checks if c.status == "FAIL"}
        self.assertEqual(failing, {4, 5})

    def test_a_wrong_but_compiling_answer_warns_with_the_counterexample(self):
        wrong = FakeClient(["SUBJECT librarian, member\nRESOURCE catalogue SUPPORTS READ, WRITE\n"
                            "ALLOW member ACCESS catalogue\n"])
        checks = run_demo_check(wrong, results_dir=self.results)
        d01 = next(c for c in checks if c.name.startswith("d01"))
        self.assertEqual(d01.status, "WARN")
        self.assertRegex(d01.detail, r"\w+ (READ|WRITE) catalogue: reference says \w+, generated says \w+")

    def test_without_a_model_the_ai_checks_are_skipped_not_failed(self):
        checks = run_demo_check(None, results_dir=self.results)
        self.assertFalse([c for c in checks if c.status == "FAIL"])
        skipped = [c for c in checks if c.status == "WARN" and c.detail == NO_MODEL]
        self.assertEqual({c.objective for c in skipped}, {4, 5})

    def test_evaluation_results_are_graded(self):
        def obj6(run_json):
            if run_json is not None:
                with open(os.path.join(self.results, "run.json"), "w", encoding="utf-8") as f:
                    json.dump(run_json, f)
            checks = run_demo_check(fake_demo_client(), results_dir=self.results)
            return next(c for c in checks if c.objective == 6 and "evaluation" in c.name).status
        self.assertEqual(obj6(None), "WARN")
        self.assertEqual(obj6({"fake": True, "summary": {}}), "WARN")
        self.assertEqual(obj6({"fake": False, "summary": {"generation": {"run": 20}}}), "PASS")

    def test_requirements_and_references_line_up(self):
        reqs = load_requirements()
        self.assertEqual([r.id for r in reqs], ["d01", "d02", "d03", "d04", "d05"])
        for r in reqs:
            self.assertTrue(os.path.exists(r.reference_path), r.id)

    def test_report_has_a_verdict_line(self):
        checks = run_demo_check(fake_demo_client(), results_dir=self.results)
        text = format_report(checks, fake=True)
        self.assertIn("FAKE", text.splitlines()[0])
        self.assertIn("Objective 1", text)
        self.assertRegex(text, r"(ALL OUTCOMES DEMONSTRATED|outcomes need attention)")

    def test_cli_exit_codes(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()), \
                mock.patch("policyc.ai.demo_check.DEFAULT_RESULTS", self.results):
            code = main(["demo-check", "--llm", "fake"])
        self.assertEqual(code, 0, out.getvalue())
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), \
                mock.patch("policyc.ai.cli._demo_client",
                           lambda name: FakeClient(["this is not a policy"])):
            self.assertEqual(main(["demo-check"]), 1)

    def test_demo_folder_exists(self):
        self.assertTrue(os.path.isdir(DEMO_ROOT))


if __name__ == "__main__":
    unittest.main()
