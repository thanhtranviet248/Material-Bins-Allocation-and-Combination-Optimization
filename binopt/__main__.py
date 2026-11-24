"""CLI: python -m binopt input_template.xlsx -o output_template.xlsx"""
from __future__ import annotations

import argparse
import sys

from .config import Settings
from .pipeline import run_file


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="binopt", description=__doc__)
    p.add_argument("input", help="Excel file with sheets 'production_order' and 'inventory'")
    p.add_argument("-o", "--output", default="output_template.xlsx")
    p.add_argument("--capacity", type=int, default=3000, help="default bin capacity")
    p.add_argument("--threshold", type=int, default=1000, help="fragment threshold")
    p.add_argument("--solver", default="highs", choices=["highs", "cbc"])
    p.add_argument("--time-limit", type=float, default=60.0, help="time limit (s) of the primary stage of each model")
    p.add_argument("--no-fragment-avoidance", action="store_true",
                   help="allocation minimises pick lines only (original behaviour)")
    p.add_argument("--fragments-only-receivers", action="store_true",
                   help="consolidate fragments only into other fragments (original behaviour)")
    p.add_argument("--max-split", type=int, default=2,
                   help="max receiving bins per fragment (1 = whole-bin moves only)")
    p.add_argument("-v", "--verbose", action="store_true", help="show solver log")
    a = p.parse_args(argv)

    settings = Settings(
        default_capacity=a.capacity, fragment_threshold=a.threshold, solver=a.solver,
        time_limit=a.time_limit, msg=a.verbose,
        avoid_fragments_in_allocation=not a.no_fragment_avoidance,
        allow_full_bin_receivers=not a.fragments_only_receivers, max_split=a.max_split,
    )
    res = run_file(a.input, a.output, settings)
    for k, v in res.kpis().items():
        print(f"{k:30s} {v}")
    if res.errors:
        print("VALIDATION ERRORS:", *res.errors, sep="\n  ")
        return 1
    print(f"Written: {a.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
