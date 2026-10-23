import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from policyc.__main__ import main
from policyc.ai.generator import generate_policy

from helpers import example


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CliAndAiHookTests(unittest.TestCase):
    def test_cli_exit_codes_and_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "out.json")
            code, _, _ = run(["compile", example("valid.pol"), "-o", target])
            self.assertEqual(code, 0)
            with open(target) as f:
                self.assertEqual(json.load(f)["format"], "policyc-target/1")

        code, out, _ = run(["compile", example("syntax_errors.pol"), "--json-diagnostics"])
        self.assertEqual(code, 1)
        self.assertEqual(len(json.loads(out)), 5)

        code, out, _ = run(["verify", example("optimizable.pol"), example("candidate_bad.pol")])
        self.assertEqual(code, 1)
        self.assertIn("REJECTED", out)

    def test_generator_loop_feeds_diagnostics_back(self):
        # Fake LLM: wrong on the first try, fixed once it sees the diagnostics.
        prompts = []
        replies = iter([
            "SUBJECT admin\nRESOURCE db SUPPORTS READ\nALLOW admin FLY db",
            "```\nSUBJECT admin\nRESOURCE db SUPPORTS READ\nALLOW admin READ db\n```",
        ])

        def fake_llm(prompt):
            prompts.append(prompt)
            return next(replies)

        result = generate_policy("admins may read the db", llm=fake_llm)
        self.assertTrue(result.ok)
        self.assertEqual(result.retries, 1)
        self.assertIn('"code": "P001"', prompts[1])
        self.assertIn("FLY", prompts[1])


if __name__ == "__main__":
    unittest.main()
