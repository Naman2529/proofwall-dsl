import re
import unittest
from unittest import mock

from policyc.ai import prompts
from policyc.ai.prompts import (PROMPT_VERSION, Vocabulary, generation_fix_prompt,
                                generation_prompt, optimization_prompt)
from policyc.compiler import compile_source

VOCAB = Vocabulary(subjects=["analyst", "guest"],
                   resources={"reports": ["READ", "WRITE"], "api": ["EXECUTE"]},
                   attributes=["authenticated", "on_vpn"])


class PromptTests(unittest.TestCase):
    def test_vocabulary_declarations(self):
        self.assertEqual(VOCAB.declarations(),
                         "SUBJECT analyst, guest\n"
                         "RESOURCE reports SUPPORTS READ, WRITE\n"
                         "RESOURCE api SUPPORTS EXECUTE")
        self.assertTrue(compile_source(VOCAB.declarations()).ok)

    def test_generation_prompt_contents(self):
        p = generation_prompt("analysts may read reports when authenticated", VOCAB)
        self.assertIn("policy        -> effect IDENT action IDENT ( WHEN or_expr )?", p)
        self.assertIn("DENY", p)
        self.assertIn("RESOURCE reports SUPPORTS READ, WRITE", p)
        self.assertIn("authenticated, on_vpn", p)
        self.assertEqual(p.count("Example "), 2)
        self.assertIn("analysts may read reports when authenticated", p)
        self.assertIn("Output only", p)

    def test_generation_prompt_without_vocabulary(self):
        p = generation_prompt("admins can read the database", None)
        self.assertIn("admins can read the database", p)
        self.assertIn("choose", p.lower())

    def test_examples_compile(self):
        for dsl in prompts.EXAMPLE_POLICIES:
            self.assertTrue(compile_source(dsl).ok, dsl)

    def test_fix_prompt_numbers_attempt_and_includes_diagnostics(self):
        err = {"code": "P001", "phase": "parser", "severity": "error",
               "message": "expected an action but found identifier 'FLY'", "line": 2, "col": 13}
        p = generation_fix_prompt("x", VOCAB, "SUBJECT analyst\nALLOW analyst FLY reports", [err])
        self.assertIn("  1| SUBJECT analyst", p)
        self.assertIn("  2| ALLOW analyst FLY reports", p)
        self.assertIn('"code": "P001"', p)
        self.assertIn("RESOURCE reports SUPPORTS READ, WRITE", p)

    def test_optimization_prompt_contents(self):
        warn = {"code": "W101", "message": "duplicate rule", "line": 4}
        p = optimization_prompt("SUBJECT a\n", "IR: 0 rules", [warn], None)
        for text in ("SUBJECT a", "IR: 0 rules", "W101", "ACCESS", " OR ",
                     "keep the SUBJECT and RESOURCE lines unchanged", "keep every DENY rule"):
            self.assertIn(text, p)
        self.assertNotIn("Previous attempt", p)
        with_feedback = optimization_prompt("SUBJECT a\n", "IR", [], "it does not compile")
        self.assertIn("it does not compile", with_feedback)

    def test_prompt_version_is_a_fingerprint(self):
        self.assertRegex(PROMPT_VERSION, r"^[0-9a-f]{12}$")
        with mock.patch.object(prompts, "GRAMMAR", "changed"):
            self.assertNotEqual(prompts.fingerprint(), PROMPT_VERSION)


if __name__ == "__main__":
    unittest.main()
