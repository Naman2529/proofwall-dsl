import json
import os
import shutil
import tempfile
import unittest

from policyc.ai.evaluation.run import (RunConfig, fake_client_for, read_raw, render_source,
                                       run_generation, run_optimization, run_semantic)
from policyc.ai.evaluation.tasks import EVAL_ROOT
from policyc.ai.llm_client import FakeClient, LLMError
from policyc.compiler import compile_file, compile_source
from policyc.verifier import verify


class QuotaOnThird:
    """Answers like the fake, but the third task's first call hits the quota."""
    name = "quota-test"

    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def complete(self, prompt, n=1):
        self.calls += 1
        if self.calls >= 4:          # g01 takes two calls (wrong once), g02 one, g03 is next
            raise LLMError("quota", "HTTP 429 after 3 retries")
        return self.inner.complete(prompt, n)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.raw = os.path.join(self.tmp, "raw.jsonl")
        self.cfg = RunConfig(llm_name="fake")

    def test_generation_with_fake_client(self):
        records = run_generation(fake_client_for(EVAL_ROOT), self.cfg, self.raw)
        self.assertEqual(len(records), 20)
        self.assertTrue(all(r["fake"] for r in records))
        self.assertTrue(all(r["status"] == "ok" and r["verified"] for r in records))
        g01 = records[0]
        self.assertEqual((g01["task"], g01["first_try_ok"], g01["retries"]), ("g01", False, 1))
        self.assertEqual([a["tier"] for a in g01["attempts"]], ["invalid", "verified"])
        self.assertEqual(len(read_raw(self.raw)), 20)

    def test_rerun_with_same_config_is_resumed(self):
        run_generation(fake_client_for(EVAL_ROOT), self.cfg, self.raw)
        fake = fake_client_for(EVAL_ROOT)
        again = run_generation(fake, self.cfg, self.raw)
        self.assertEqual(len(again), 20)
        self.assertEqual(fake.prompts, [])
        self.assertEqual(len(read_raw(self.raw)), 20)

    def test_changed_settings_start_new_records(self):
        run_generation(fake_client_for(EVAL_ROOT), self.cfg, self.raw, limit=3)
        run_generation(fake_client_for(EVAL_ROOT), RunConfig(llm_name="fake", attempts=2), self.raw,
                       limit=3)
        self.assertEqual(len(read_raw(self.raw)), 6)

    def test_truncated_last_line_is_rerun(self):
        run_generation(fake_client_for(EVAL_ROOT), self.cfg, self.raw, limit=3)
        with open(self.raw, encoding="utf-8") as f:
            lines = f.readlines()
        with open(self.raw, "w", encoding="utf-8") as f:
            f.writelines(lines[:2])
            f.write(lines[2][:40])          # killed mid-write
        fake = fake_client_for(EVAL_ROOT)
        records = run_generation(fake, self.cfg, self.raw, limit=3)
        self.assertEqual([r["task"] for r in records], ["g01", "g02", "g03"])
        self.assertEqual(len(fake.prompts), 1)   # only g03 ran again
        third = fake_client_for(EVAL_ROOT)
        run_generation(third, self.cfg, self.raw, limit=3)
        self.assertEqual(third.prompts, [])      # the rerun result was kept
        self.assertEqual(sorted(r["task"] for r in read_raw(self.raw)), ["g01", "g02", "g03"])

    def test_cut_inside_a_multibyte_character(self):
        run_generation(fake_client_for(EVAL_ROOT), self.cfg, self.raw, limit=2)
        with open(self.raw, "ab") as f:
            f.write('{"task": "g03", "dsl": "é'.encode("utf-8")[:-1] + "é".encode("utf-8")[:1])
        records = run_generation(fake_client_for(EVAL_ROOT), self.cfg, self.raw, limit=3)
        self.assertEqual([r["task"] for r in records], ["g01", "g02", "g03"])

    def test_progress_is_reported_per_task(self):
        lines = []
        run_generation(fake_client_for(EVAL_ROOT), self.cfg, self.raw, limit=3, progress=lines.append)
        self.assertEqual(len(lines), 3)
        self.assertIn("g01", lines[0])

    def test_quota_error_marks_the_rest_not_run_without_calling(self):
        client = QuotaOnThird(fake_client_for(EVAL_ROOT))
        records = run_generation(client, RunConfig(llm_name="quota-test"), self.raw, limit=5)
        self.assertEqual([r["status"] for r in records], ["ok", "ok", "not_run", "not_run", "not_run"])
        self.assertEqual(records[2]["error_kind"], "quota")
        self.assertEqual(client.calls, 4)            # no calls after the quota ran out
        retry = run_generation(fake_client_for(EVAL_ROOT), RunConfig(llm_name="quota-test"),
                               self.raw, limit=5)
        self.assertEqual(retry[2]["status"], "ok")   # not_run tasks are retried on resume

    def test_limit(self):
        self.assertEqual(len(run_generation(fake_client_for(EVAL_ROOT), self.cfg, self.raw, limit=5)), 5)

    def test_generation_correctness_uses_the_reference(self):
        wrong = FakeClient(["SUBJECT admin, guest\nRESOURCE customer_db SUPPORTS READ, WRITE\n"
                            "ALLOW admin READ customer_db\n"])
        [record] = run_generation(wrong, RunConfig(llm_name="wrong"), self.raw, limit=1)
        self.assertTrue(record["ok"])
        self.assertFalse(record["verified"])
        self.assertEqual(record["final_tier"], "semantically_valid")

    def test_optimization_records_baseline_and_llm(self):
        records = run_optimization(fake_client_for(EVAL_ROOT), self.cfg, self.raw)
        self.assertEqual(len(records), 8)
        by_task = {r["task"]: r for r in records}
        o1 = by_task["o1_duplicates"]
        self.assertEqual((o1["baseline"]["rules_before"], o1["baseline"]["rules_after"]), (7, 5))
        self.assertTrue(o1["baseline"]["verified"])
        self.assertEqual(o1["llm"]["status"], "ok")
        self.assertTrue(o1["llm"]["accepted"])
        self.assertEqual(by_task["o4_or_merge"]["llm"]["proposals"][0]["stage"], "not_smaller")

    def test_optimization_without_llm_runs_baseline_only(self):
        records = run_optimization(None, RunConfig(llm_name="none"), self.raw)
        self.assertEqual(len(records), 8)
        self.assertTrue(all(r["llm"]["status"] == "skipped" for r in records))

    def test_semantic_suite_matches_markers(self):
        records = {os.path.basename(r["task"]): r for r in run_semantic(EVAL_ROOT)}
        self.assertEqual(len(records), 10)
        s10 = records["s10_hard_cases.pol"]
        self.assertEqual(sorted(code for _, code, _ in s10["missed"]), ["W102", "W104"])
        self.assertEqual(s10["false_alarms"], [])
        self.assertEqual(records["s01_undefined_names.pol"]["missed"], [])

    def test_render_source_round_trips(self):
        res = compile_file(os.path.join(EVAL_ROOT, "optimization", "o7_mixed.pol"))
        again = compile_source(render_source(res.ir))
        self.assertTrue(again.ok)
        self.assertTrue(verify(res.ir, again.ir).proven)


if __name__ == "__main__":
    unittest.main()
