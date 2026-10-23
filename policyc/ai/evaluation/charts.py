"""Optional PNG charts for the report (needs matplotlib; without it, none are made).

    tiers.png            classification of every AI output, generation vs optimization
    rule_reduction.png   IR rules per policy: original, compiler-only baseline, LLM
    correctness.png      generated policies equivalent to the reference, 1st try vs final
    semantic.png         seeded issues detected / missed / false alarms per code

Colors are the first three slots of the validated reference palette (blue,
orange, aqua) on a light surface. Aqua sits below 3:1 contrast, so every bar
carries a value label, and every chart has a table twin in results.md.
"""

from __future__ import annotations

import os
from typing import List

from ..metrics import TIERS

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a")


def _style(ax, title: str, ylabel: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=INK, fontsize=12, pad=28)
    ax.set_ylabel(ylabel, color=INK_2, fontsize=9)
    ax.tick_params(colors=INK_2, labelsize=9, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)


def _bars(ax, labels: List[str], series: List[tuple], fmt: str = "%d") -> None:
    """Grouped bars with a 2px surface gap and a value label on every bar."""
    n = len(series)
    width = 0.8 / n
    for i, (name, values) in enumerate(series):
        xs = [x + (i - (n - 1) / 2) * width for x in range(len(labels))]
        shown = [0 if v is None else v for v in values]
        bars = ax.bar(xs, shown, width=width, color=SERIES[i], edgecolor=SURFACE, linewidth=2,
                      label=name)
        for bar, v in zip(bars, values):
            ax.annotate("n/a" if v is None else fmt % v, (bar.get_x() + bar.get_width() / 2,
                                                         bar.get_height()),
                        xytext=(0, 3), textcoords="offset points", ha="center", va="bottom",
                        fontsize=8, color=INK_2)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels)
    top = max([v for _, values in series for v in values if v is not None] or [1])
    ax.set_ylim(0, top * 1.15)
    if fmt == "%d":
        from matplotlib.ticker import MaxNLocator
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    if n > 1:
        # Above the plot, so it never sits on a bar or its label.
        ax.legend(frameon=False, fontsize=9, labelcolor=INK_2, loc="lower right",
                  bbox_to_anchor=(1, 1.0), ncol=n)


def write_charts(out_dir: str, summary: dict) -> List[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    os.makedirs(out_dir, exist_ok=True)
    paths = []

    def save(fig, name):
        path = os.path.join(out_dir, name)
        fig.patch.set_facecolor(SURFACE)
        fig.tight_layout()
        fig.savefig(path, dpi=150, facecolor=SURFACE)
        plt.close(fig)
        paths.append(path)

    tiers = summary["tiers"]
    fig, ax = plt.subplots(figsize=(8, 4))
    _bars(ax, [t.replace("_", "\n") for t in TIERS],
          [("generation attempts", [tiers["generation"][t] for t in TIERS]),
           ("optimization proposals", [tiers["optimization"][t] for t in TIERS])])
    _style(ax, "Every AI output, by the highest stage it passed", "outputs")
    save(fig, "tiers.png")

    rows = summary["optimization"]["policies"]
    fig, ax = plt.subplots(figsize=(max(6, 1.1 * len(rows) + 2), 4))
    _bars(ax, [r["task"].split("_", 1)[0] for r in rows],
          [("original", [r["baseline_before"] for r in rows]),
           ("compiler-only", [r["baseline_after"] for r in rows]),
           ("LLM", [r["llm_after"] if r["llm_status"] == "ok" else None for r in rows])])
    _style(ax, "IR rules per policy after optimization", "IR rules")
    save(fig, "rule_reduction.png")

    by_d = summary["generation"]["by_difficulty"]
    levels = ["easy", "medium", "hard", "all"]
    fig, ax = plt.subplots(figsize=(7, 4))
    _bars(ax, levels, [("first try", [by_d[d]["first_try_verified"] for d in levels]),
                       ("after correction", [by_d[d]["final_verified"] for d in levels])],
          fmt="%.0f%%")
    ax.set_ylim(0, 115)
    _style(ax, "Generated policies equivalent to the reference", "% of tasks")
    save(fig, "correctness.png")

    codes = list(summary["semantic"]["by_code"])
    counts = summary["semantic"]["by_code"]
    fig, ax = plt.subplots(figsize=(max(6, 0.8 * len(codes) + 2), 4))
    _bars(ax, codes, [("detected", [counts[c]["detected"] for c in codes]),
                      ("missed", [counts[c]["missed"] for c in codes]),
                      ("false alarm", [counts[c]["false_alarms"] for c in codes])])
    _style(ax, "Seeded semantic issues per diagnostic code", "issues")
    save(fig, "semantic.png")
    return paths
