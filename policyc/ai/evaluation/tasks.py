"""Loads the benchmark data in evaluation/.

    evaluation/generation/tasks.json   English requirements + vocabulary + reference
    evaluation/generation/gNN.pol      hand-written reference policies (never shown to the model)
    evaluation/optimization/*.pol      policies for the optimizer
    evaluation/semantic/*.pol          seeded mistakes, marked `# expect: CODE[, CODE]`
                                       (`# known miss CODE: why` marks a case the analyzer is known to miss)
"""

from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass
from typing import List, Tuple

from ..prompts import Vocabulary

EVAL_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "evaluation")

_EXPECT = re.compile(r"#\s*expect:\s*([A-Z]\d{3}(?:\s*,\s*[A-Z]\d{3})*)")
_KNOWN_MISS = re.compile(r"#\s*known miss\s+([A-Z]\d{3}):\s*(.+)$")


@dataclass
class GenTask:
    id: str
    difficulty: str
    english: str
    vocabulary: Vocabulary
    reference_path: str


@dataclass
class Expectation:
    file: str
    line: int
    code: str
    known_miss: str = ""         # reason, if the analyzer is known to miss it


def load_generation_tasks(root: str = EVAL_ROOT) -> List[GenTask]:
    folder = os.path.join(root, "generation")
    with open(os.path.join(folder, "tasks.json"), encoding="utf-8") as f:
        raw = json.load(f)
    return [GenTask(t["id"], t["difficulty"], t["english"],
                    Vocabulary(t["vocabulary"]["subjects"], t["vocabulary"]["resources"],
                               t["vocabulary"].get("attributes", [])),
                    os.path.join(folder, t["reference"]))
            for t in raw]


def load_optimization_policies(root: str = EVAL_ROOT) -> List[Tuple[str, str]]:
    paths = sorted(glob.glob(os.path.join(root, "optimization", "*.pol")))
    return [(os.path.splitext(os.path.basename(p))[0], p) for p in paths]


def parse_expectations(path: str) -> List[Expectation]:
    """The `# expect: CODE[, CODE]` markers of one .pol file."""
    expectations = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            m = _EXPECT.search(line)
            if not m:
                continue
            miss = _KNOWN_MISS.search(line)
            for code in re.split(r"\s*,\s*", m.group(1)):
                reason = miss.group(2).strip() if miss and miss.group(1) == code else ""
                expectations.append(Expectation(path, lineno, code, reason))
    return expectations


def load_semantic_cases(root: str = EVAL_ROOT) -> List[Tuple[str, List[Expectation]]]:
    return [(path, parse_expectations(path))
            for path in sorted(glob.glob(os.path.join(root, "semantic", "*.pol")))]
