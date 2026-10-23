import csv
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from policyc.ai.evaluation import charts
from policyc.ai.evaluation.report import BANNER, summarize, write_reports

GEN = [
    {"suite": "generation", "task": "g1", "difficulty": "easy", "status": "ok", "fake": False,
     "first_try_ok": True, "ok": True, "first_try_verified": True, "verified": True, "retries": 0,
     "attempts": [{"tier": "verified", "errors": [], "verified": True, "tokens": 100, "latency_s": 1.0}],
     "tokens": 100, "latency_s": 1.0},
    {"suite": "generation", "task": "g2", "difficulty": "easy", "status": "ok", "fake": False,
     "first_try_ok": False, "ok": True, "first_try_verified": False, "verified": False, "retries": 1,
     "attempts": [{"tier": "invalid", "errors": ["P001"], "verified": None, "tokens": 120, "latency_s": 1.0},
                  {"tier": "semantically_valid", "errors": [], "verified": False, "tokens": 180,
                   "latency_s": 1.0}],
     "tokens": 300, "latency_s": 2.0},
    {"suite": "generation", "task": "g3", "difficulty": "hard", "status": "ok", "fake": False,
     "first_try_ok": False, "ok": False, "first_try_verified": False, "verified": False, "retries": 2,
     "attempts": [{"tier": "invalid", "errors": ["L001"], "verified": None, "tokens": None, "latency_s": 1.0},
                  {"tier": "invalid", "errors": ["P001"], "verified": None, "tokens": None, "latency_s": 1.0},
                  {"tier": "syntactically_valid", "errors": ["S001"], "verified": None, "tokens": None,
                   "latency_s": 1.0}],
     "tokens": None, "latency_s": 3.0},
    {"suite": "generation", "task": "g4", "difficulty": "hard", "status": "not_run", "fake": False,
     "error_kind": "quota"},
]


def proposal(stage, tier, drops=False, tokens=None):
    return {"attempt": 1, "stage": stage, "tier": tier, "drops_deny_guard": drops, "tokens": tokens,
            "verifier": None, "size": None, "note": "", "dsl": "x ’"}


OPT = [
    {"suite": "optimization", "task": "o1", "status": "ok", "fake": False,
     "baseline": {"rules_before": 7, "rules_after": 5, "statements_before": 6, "verified": True, "ms": 1.0},
     "llm": {"status": "ok", "accepted": True, "rules_before": 7, "rules_after": 5,
             "statements_before": 6, "statements_after": 4,
             "proposals": [proposal("accepted", "improving", tokens=50)], "tokens": 50, "latency_s": 1.5}},
    {"suite": "optimization", "task": "o2", "status": "ok", "fake": False,
     "baseline": {"rules_before": 4, "rules_after": 4, "statements_before": 4, "verified": True, "ms": 2.0},
     "llm": {"status": "ok", "accepted": False, "rules_before": 4, "rules_after": 4,
             "statements_before": 4, "statements_after": 4,
             "proposals": [proposal("verifier", "semantically_valid", tokens=60),
                           proposal("not_smaller", "verified", drops=True, tokens=60)],
             "tokens": 120, "latency_s": 2.5}},
    {"suite": "optimization", "task": "o3", "status": "not_run", "fake": False, "error_kind": "quota",
     "baseline": {"rules_before": 3, "rules_after": 2, "statements_before": 3, "verified": True, "ms": 3.0},
     "llm": {"status": "not_run", "error_kind": "quota"}},
]

SEM = [{"suite": "semantic", "task": "semantic/s1.pol", "status": "ok", "fake": False,
        "expected": [[1, "S001", ""], [2, "W104", "two DENYs"]], "reported": [[1, "S001"], [3, "W103"]],
        "matched": [[1, "S001"]], "missed": [[2, "W104", "two DENYs"]], "false_alarms": [[3, "W103"]]}]


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.s = summarize(GEN, OPT, SEM)

    def test_generation_rates_exclude_not_run(self):
        g = self.s["generation"]
        self.assertEqual((g["tasks"], g["run"], g["not_run"]), (4, 3, 1))
        overall = g["by_difficulty"]["all"]
        self.assertEqual((overall["first_try_compile"], overall["final_compile"]), (33.3, 66.7))
        self.assertEqual((overall["first_try_verified"], overall["final_verified"]), (33.3, 33.3))
        self.assertEqual(overall["mean_cycles"], 1.0)
        self.assertEqual(g["mean_cycles_compiled"], 0.5)
        self.assertEqual(g["by_difficulty"]["easy"]["final_compile"], 100.0)
        self.assertEqual(g["by_difficulty"]["hard"]["tasks"], 1)
        self.assertEqual((g["mean_tokens"], g["mean_latency_s"]), (200.0, 2.0))

    def test_tiers(self):
        self.assertEqual(self.s["tiers"]["generation"],
                         {"invalid": 3, "syntactically_valid": 1, "semantically_valid": 1,
                          "verified": 1, "improving": 0})
        self.assertEqual(self.s["tiers"]["optimization"],
                         {"invalid": 0, "syntactically_valid": 0, "semantically_valid": 1,
                          "verified": 1, "improving": 1})

    def test_optimization_totals(self):
        t = self.s["optimization"]["totals"]
        self.assertEqual((t["baseline_rules_before"], t["baseline_rules_after"]), (11, 9))
        self.assertEqual(t["llm_rules_after"], 9)
        self.assertEqual((t["baseline_reduction_pct"], t["llm_reduction_pct"]), (18.2, 18.2))
        self.assertEqual((t["llm_statements_before"], t["llm_statements_after"]), (10, 8))
        self.assertEqual((t["proposals"], t["accepted"], t["rejected"], t["accepted_pct"]),
                         (3, 1, 2, 33.3))
        self.assertEqual(t["by_stage"], {"compile": 0, "declarations": 0, "verifier": 1,
                                         "not_smaller": 1, "accepted": 1})
        self.assertEqual((t["deny_guards_dropped_proposed"], t["deny_guards_dropped_accepted"]), (1, 0))
        self.assertEqual((t["mean_tokens"], t["mean_latency_s"], t["baseline_mean_ms"]), (85.0, 2.0, 2.0))
        self.assertEqual(self.s["optimization"]["not_run"], 1)

    def test_semantic(self):
        sem = self.s["semantic"]
        self.assertEqual((sem["expected"], sem["detected"], sem["missed"], sem["false_alarms"]),
                         (2, 1, 1, 1))
        self.assertEqual(sem["detection_rate"], 50.0)
        self.assertEqual(sem["by_code"]["W104"], {"expected": 1, "detected": 0, "missed": 1,
                                                  "false_alarms": 0})
        self.assertEqual(sem["by_code"]["W103"]["false_alarms"], 1)
        self.assertEqual(len(sem["known_misses"]), 1)

    def test_empty_inputs(self):
        s = summarize([], [], SEM)
        self.assertEqual(s["generation"]["run"], 0)
        self.assertIsNone(s["generation"]["by_difficulty"]["all"]["final_compile"])


class WriteTests(unittest.TestCase):
    FILES = {"summary.txt", "generation.csv", "optimization.csv", "proposals.csv", "semantic.csv",
             "results.md", "run.json"}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def write(self, gen, opt, sem, info=None):
        return write_reports(self.tmp, summarize(gen, opt, sem), gen, opt, sem,
                             info or {"llm": "gemini:test", "date": "2026-10-23"})

    def test_every_file_is_written_and_parses(self):
        paths = self.write(GEN, OPT, SEM)
        self.assertEqual({os.path.basename(p) for p in paths}, self.FILES)
        with open(os.path.join(self.tmp, "generation.csv"), encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 7)           # 6 attempts + 1 not_run row
        with open(os.path.join(self.tmp, "run.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertFalse(data["fake"])
        self.assertIn("summary", data)
        with open(os.path.join(self.tmp, "proposals.csv"), encoding="utf-8") as f:
            self.assertEqual(len(list(csv.DictReader(f))), 3)

    def test_only_rates_are_formatted_as_percentages(self):
        self.write(GEN, OPT, SEM)
        with open(os.path.join(self.tmp, "summary.txt"), encoding="utf-8") as f:
            text = f.read()
        all_row = next(line for line in text.splitlines() if line.strip().startswith("all"))
        self.assertTrue(all_row.rstrip().endswith(" 1.0"), all_row)
        self.assertIn("33.3%", all_row)
        self.assertIn("latency/task 2.0 s", text)
        self.assertIn("2.0 ms/policy", text)
        with open(os.path.join(self.tmp, "results.md"), encoding="utf-8") as f:
            md = f.read()
        self.assertIn("| all | 3 | 33.3% | 66.7% | 33.3% | 33.3% | 1.0 |", md)

    def test_partial_input_still_writes_everything(self):
        paths = self.write([], [], SEM)
        self.assertEqual({os.path.basename(p) for p in paths}, self.FILES)
        with open(os.path.join(self.tmp, "results.md"), encoding="utf-8") as f:
            self.assertIn("not run", f.read())

    def test_fake_banner_on_every_file(self):
        fake = [dict(r, fake=True) for r in GEN]
        self.write(fake, OPT, SEM)
        for name in self.FILES - {"run.json"}:
            with open(os.path.join(self.tmp, name), encoding="utf-8") as f:
                self.assertIn(BANNER, f.readline(), name)
        with open(os.path.join(self.tmp, "run.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertTrue(data["fake"])
        self.assertEqual(data["banner"], BANNER)

    def test_api_key_never_written(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "SENTINEL-KEY-123"}):
            self.write(GEN, OPT, SEM, {"llm": "gemini:test", "note": "SENTINEL-KEY-123"})
        for name in self.FILES:
            with open(os.path.join(self.tmp, name), encoding="utf-8") as f:
                self.assertNotIn("SENTINEL-KEY-123", f.read(), name)

    def test_non_ascii_survives(self):
        self.write(GEN, OPT, SEM)
        with open(os.path.join(self.tmp, "proposals.csv"), encoding="utf-8") as f:
            self.assertIn("’", f.read())


class ChartTests(unittest.TestCase):
    def test_fake_charts_carry_the_banner(self):
        try:
            from matplotlib.figure import Figure
        except ImportError:
            self.skipTest("matplotlib not installed")
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        figures = []
        real_savefig = Figure.savefig

        def capture(fig, *args, **kwargs):
            figures.append([t.get_text() for t in fig.texts])
            return real_savefig(fig, *args, **kwargs)

        with mock.patch.object(Figure, "savefig", capture):
            charts.write_charts(tmp, summarize([dict(r, fake=True) for r in GEN], OPT, SEM))
        self.assertEqual(len(figures), 4)
        self.assertTrue(all(BANNER in texts for texts in figures))

    def test_no_matplotlib_means_no_charts(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        with mock.patch.dict(sys.modules, {"matplotlib": None}):
            self.assertEqual(charts.write_charts(tmp, summarize(GEN, OPT, SEM)), [])

    def test_charts_written_when_matplotlib_is_available(self):
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            self.skipTest("matplotlib not installed")
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        paths = charts.write_charts(tmp, summarize(GEN, OPT, SEM))
        self.assertEqual(len(paths), 4)
        self.assertTrue(all(p.endswith(".png") and os.path.getsize(p) > 0 for p in paths))


if __name__ == "__main__":
    unittest.main()
