import unittest

from policyc.ai.llm_client import FakeClient
from policyc.ai.llm_optimizer import STAGES, optimize_with_llm

from helpers import read_example

ORIGINAL = read_example("optimizable.pol")
GOOD = read_example("candidate_good.pol")
BAD = read_example("candidate_bad.pol")


def stages(result):
    return [p.stage for p in result.proposals]


class OptimizerTests(unittest.TestCase):
    def test_stage_names(self):
        self.assertEqual(STAGES, ("compile", "declarations", "verifier", "not_smaller", "accepted"))

    def test_counterexample_fed_back_then_good_proposal_accepted(self):
        fake = FakeClient([BAD, GOOD])
        res = optimize_with_llm(ORIGINAL, llm=fake)
        self.assertEqual(stages(res), ["verifier", "accepted"])
        self.assertIn("authenticated=False", fake.prompts[1])
        self.assertEqual((res.rules_before, res.rules_after), (9, 6))
        self.assertEqual((res.statements_before, res.statements_after), (7, 4))
        self.assertEqual(res.final_source, GOOD)
        self.assertEqual(res.accepted.tier, "improving")
        self.assertEqual(res.proposals[0].tier, "semantically_valid")

    def test_non_compiling_proposal(self):
        fake = FakeClient(["ALLOW admin FLY database", GOOD])
        res = optimize_with_llm(ORIGINAL, llm=fake)
        self.assertEqual(stages(res), ["compile", "accepted"])
        self.assertIn("FLY", fake.prompts[1])
        self.assertEqual(res.proposals[0].tier, "invalid")

    def test_changed_declarations_rejected(self):
        dropped = GOOD.replace("SUBJECT admin, analyst, guest", "SUBJECT admin, analyst, guest, intern")
        fake = FakeClient([dropped, GOOD])
        res = optimize_with_llm(ORIGINAL, llm=fake)
        self.assertEqual(stages(res), ["declarations", "accepted"])
        self.assertIn("intern", fake.prompts[1])

    def test_proven_but_not_smaller(self):
        fake = FakeClient([ORIGINAL, GOOD])
        res = optimize_with_llm(ORIGINAL, llm=fake)
        self.assertEqual(stages(res), ["not_smaller", "accepted"])
        self.assertIn("not smaller", fake.prompts[1])
        self.assertEqual(res.proposals[0].tier, "verified")

    def test_best_of_n_in_one_call(self):
        fake = FakeClient([BAD, ORIGINAL, GOOD])
        res = optimize_with_llm(ORIGINAL, llm=fake, samples=3)
        self.assertEqual(len(fake.prompts), 1)
        self.assertEqual(stages(res), ["verifier", "not_smaller", "accepted"])
        self.assertEqual(res.accepted.dsl, GOOD.strip())
        self.assertEqual(res.stage_counts["accepted"], 1)

    def test_smallest_accepted_proposal_wins(self):
        bigger = GOOD + "ALLOW admin READ reports\n"
        res = optimize_with_llm(ORIGINAL, llm=FakeClient([bigger, GOOD]), samples=2)
        self.assertEqual(stages(res), ["accepted", "accepted"])
        self.assertEqual(res.accepted.dsl, GOOD.strip())

    def test_original_kept_when_nothing_is_accepted(self):
        res = optimize_with_llm(ORIGINAL, llm=FakeClient([BAD]), max_attempts=2)
        self.assertIsNone(res.accepted)
        self.assertEqual(res.final_source, ORIGINAL)
        self.assertEqual(res.rules_after, res.rules_before)
        self.assertEqual(len(res.proposals), 2)

    def test_stricter_needs_non_weakening_mode(self):
        stricter = read_example("candidate_stricter.pol")
        self.assertIsNone(optimize_with_llm(ORIGINAL, llm=FakeClient([stricter]), max_attempts=1).accepted)
        relaxed = optimize_with_llm(ORIGINAL, llm=FakeClient([stricter]), max_attempts=1,
                                    mode="non-weakening")
        self.assertIsNotNone(relaxed.accepted)

    def test_non_weakening_prefers_equivalent_and_flags_stricter(self):
        deny_all = "SUBJECT admin, analyst, guest\nRESOURCE database SUPPORTS READ, WRITE\n" \
                   "RESOURCE reports  SUPPORTS READ, WRITE\n"
        res = optimize_with_llm(ORIGINAL, llm=FakeClient([deny_all, GOOD]), samples=2,
                                mode="non-weakening")
        self.assertEqual(stages(res), ["accepted", "accepted"])
        self.assertTrue(res.proposals[0].stricter)
        self.assertFalse(res.proposals[1].stricter)
        self.assertEqual(res.accepted.dsl, GOOD.strip())       # equivalent beats smaller-but-stricter
        alone = optimize_with_llm(ORIGINAL, llm=FakeClient([deny_all]), mode="non-weakening")
        self.assertTrue(alone.accepted.stricter)
        self.assertEqual(alone.accepted.tier, "verified")      # never "improving" if it changes decisions
        self.assertIsNone(optimize_with_llm(ORIGINAL, llm=FakeClient([GOOD])).accepted.stricter)

    def test_dropping_a_redundant_deny_is_flagged_but_accepted(self):
        source = "SUBJECT g\nRESOURCE r SUPPORTS READ\nALLOW g READ r WHEN x\nDENY g READ r WHEN NOT x\n"
        res = optimize_with_llm(source, llm=FakeClient(["SUBJECT g\nRESOURCE r SUPPORTS READ\n"
                                                        "ALLOW g READ r WHEN x\n"]))
        self.assertTrue(res.accepted.drops_deny_guard)

    def test_empty_reply_is_a_compile_failure(self):
        res = optimize_with_llm(ORIGINAL, llm=FakeClient(["", GOOD]))
        self.assertEqual(stages(res), ["compile", "accepted"])

    def test_original_must_compile(self):
        with self.assertRaises(ValueError):
            optimize_with_llm("ALLOW nobody READ nothing", llm=FakeClient([GOOD]))

    def test_tokens_none_for_fake(self):
        res = optimize_with_llm(ORIGINAL, llm=FakeClient([GOOD]))
        self.assertIsNone(res.tokens)
        self.assertGreaterEqual(res.latency_s, 0)


if __name__ == "__main__":
    unittest.main()
