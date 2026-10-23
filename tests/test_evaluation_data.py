"""Integrity of the benchmark data in evaluation/. Detection rates are the
evaluation's job; these tests only check the data is well-formed."""
import unittest
from collections import Counter

from policyc import logic
from policyc.ai.evaluation import baseline_metrics
from policyc.ai.evaluation.tasks import (Expectation, load_generation_tasks,
                                         load_optimization_policies, load_semantic_cases)
from policyc.compiler import compile_file
from policyc.optimizer import optimize

from helpers import example

ALL_CODES = {"L001", "P001", "S001", "S002", "S003", "S004", "S005", "S006",
             "W101", "W102", "W103", "W104", "W106", "W107"}
REQUIRED = {"S001", "S002", "S003", "S004", "S005", "S006",
            "W101", "W102", "W103", "W104", "W106", "W107"}


class GenerationTaskTests(unittest.TestCase):
    def setUp(self):
        self.tasks = load_generation_tasks()

    def test_twenty_tasks_with_unique_ids_and_difficulty_mix(self):
        self.assertEqual([t.id for t in self.tasks], ["g%02d" % i for i in range(1, 21)])
        self.assertEqual(Counter(t.difficulty for t in self.tasks),
                         {"easy": 6, "medium": 8, "hard": 6})

    def test_references_compile_and_match_vocabulary(self):
        for t in self.tasks:
            with self.subTest(t.id):
                res = compile_file(t.reference_path)
                self.assertTrue(res.ok, [d.format() for d in res.errors])
                self.assertEqual(res.ir.subjects, t.vocabulary.subjects)
                self.assertEqual(res.ir.resources, t.vocabulary.resources)
                used = set()
                for rule in res.ir.rules:
                    used |= logic.attrs(rule.condition)
                self.assertLessEqual(used, set(t.vocabulary.attributes))
                self.assertTrue(t.english.strip())


class OptimizationPolicyTests(unittest.TestCase):
    def test_eight_policies_in_the_planned_categories(self):
        policies = load_optimization_policies()
        self.assertEqual(len(policies), 8)
        removed = {}
        for name, path in policies:
            res = compile_file(path)
            self.assertTrue(res.ok, name)
            removed[name[:2]] = len(res.ir.rules) - len(optimize(res.ir).optimized.rules)
        for key in ("o1", "o2", "o3", "o7"):
            self.assertGreater(removed[key], 0, key)
        for key in ("o4", "o5", "o6", "o8"):
            self.assertEqual(removed[key], 0, key)


class SemanticCaseTests(unittest.TestCase):
    def test_ten_files_with_well_formed_markers(self):
        cases = load_semantic_cases()
        self.assertEqual(len(cases), 10)
        codes = set()
        markers = 0
        policy_lines = 0
        for path, expectations in cases:
            for e in expectations:
                self.assertIsInstance(e, Expectation)
                self.assertIn(e.code, ALL_CODES)
                codes.add(e.code)
            markers += len(expectations)
            with open(path, encoding="utf-8") as f:
                policy_lines += sum(1 for line in f if line.split("#")[0].strip())
        self.assertLessEqual(REQUIRED, codes)
        self.assertGreater(policy_lines, markers)
        misses = [e for _, es in cases for e in es if e.known_miss]
        self.assertEqual(sorted(e.code for e in misses), ["W102", "W104"])


class BaselineTests(unittest.TestCase):
    def test_baseline_metrics_keeps_its_shape(self):
        m = baseline_metrics(example("optimizable.pol"))
        self.assertEqual(set(m), {"file", "compiles", "rules_before", "rules_after",
                                  "verified", "cases_checked", "ms"})
        self.assertEqual((m["rules_before"], m["rules_after"], m["verified"]), (9, 6, True))


if __name__ == "__main__":
    unittest.main()
