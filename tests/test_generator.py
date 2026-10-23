import unittest

from policyc.ai.generator import Vocabulary, generate_policy
from policyc.ai.llm_client import Completion, FakeClient, LLMError, strip_code_fence

VOCAB = Vocabulary(subjects=["admin"], resources={"db": ["READ"]}, attributes=["mfa"])
BAD = "SUBJECT admin\nRESOURCE db SUPPORTS READ\nALLOW admin FLY db"
GOOD = "SUBJECT admin\nRESOURCE db SUPPORTS READ\nALLOW admin READ db WHEN mfa"


class CountingClient:
    name = "counting"

    def __init__(self, replies):
        self.replies = list(replies)

    def complete(self, prompt, n=1):
        return [self.replies.pop(0)]


class GeneratorTests(unittest.TestCase):
    def test_errors_fed_back_until_it_compiles(self):
        fake = FakeClient([BAD, GOOD])
        res = generate_policy("admins may read the db with mfa", VOCAB, llm=fake)
        self.assertTrue(res.ok)
        self.assertEqual(res.retries, 1)
        self.assertEqual(res.dsl, GOOD)
        self.assertIn('"code": "P001"', fake.prompts[1])
        self.assertIn("  3| ALLOW admin FLY db", fake.prompts[1])
        self.assertIn("RESOURCE db SUPPORTS READ", fake.prompts[0])
        self.assertIn("mfa", fake.prompts[0])

    def test_gives_up_after_max_attempts(self):
        res = generate_policy("x", VOCAB, llm=FakeClient([BAD]), max_attempts=2)
        self.assertFalse(res.ok)
        self.assertEqual(len(res.attempts), 2)
        self.assertIsNone(res.dsl)

    def test_warnings_do_not_trigger_a_retry(self):
        warned = GOOD + "\nALLOW admin READ db WHEN mfa\n"   # W101 duplicate
        res = generate_policy("x", VOCAB, llm=FakeClient([warned, GOOD]))
        self.assertTrue(res.ok)
        self.assertEqual(len(res.attempts), 1)
        self.assertEqual([d.code for d in res.attempts[0].result.warnings], ["W101"])

    def test_tokens_and_latency_are_summed(self):
        client = CountingClient([Completion(BAD, 100, 20, 0.5), Completion(GOOD, 150, 25, 0.25)])
        res = generate_policy("x", VOCAB, llm=client)
        self.assertEqual(res.tokens, 295)
        self.assertAlmostEqual(res.latency_s, 0.75)
        partial = generate_policy("x", VOCAB, llm=CountingClient([Completion(GOOD)]))
        self.assertIsNone(partial.tokens)

    def test_chatty_or_empty_replies_are_failed_attempts(self):
        fenced = "Here you go:\n```\n%s\n```\nHope this helps" % GOOD
        res = generate_policy("x", VOCAB, llm=FakeClient(["", "Sure! Here is the policy:", fenced]))
        self.assertTrue(res.ok)
        self.assertEqual(len(res.attempts), 3)
        self.assertEqual(res.dsl, GOOD)
        self.assertFalse(res.attempts[0].ok)
        self.assertEqual(res.attempts[0].errors()[0]["code"], "G001")
        self.assertFalse(res.attempts[1].ok)

    def test_non_ascii_reply_is_fed_back_as_lexer_error(self):
        fake = FakeClient([GOOD.replace("mfa", "m’fa"), GOOD])
        res = generate_policy("x", VOCAB, llm=fake)
        self.assertTrue(res.ok)
        self.assertIn('"code": "L001"', fake.prompts[1])

    def test_llm_error_propagates(self):
        def boom(prompt):
            raise LLMError("quota", "HTTP 429")
        with self.assertRaises(LLMError):
            generate_policy("x", VOCAB, llm=boom)

    def test_plain_function_still_works_without_vocabulary(self):
        res = generate_policy("admins may read the db", llm=lambda p: GOOD)
        self.assertTrue(res.ok)

    def test_strip_code_fence_extracts_block_from_prose(self):
        self.assertEqual(strip_code_fence("Sure:\n```dsl\nA\nB\n```\nDone."), "A\nB")
        self.assertEqual(strip_code_fence("no fence here"), "no fence here")


if __name__ == "__main__":
    unittest.main()
