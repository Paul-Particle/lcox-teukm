"""
lcot.py — single entry point for the LCOT model.

    uv run python scripts/lcot.py run             # fleet study -> results/lcot.{parquet,csv}
    uv run python scripts/lcot.py study [name...]  # sensitivity studies -> results/sobol/<name>/
    uv run python scripts/lcot.py plot            # figures -> results/ (runs studies it needs)
    uv run python scripts/lcot.py all             # run, then plot

The evaluation renders live in pipeline.py; plotting in viz/plots.py (imported lazily so
`run`/`study` don't pull in the plotting stack).
"""

from __future__ import annotations

import argparse

from common.paths import RESULTS_DIR, LCOT_PARQUET, LCOT_CSV, ASSUMPTIONS_PATH, STUDIES_PATH
from assumptions.load_assumptions import read_raw
from assumptions.studies import load_studies
from pipeline import build_results, run_study


def _cmd_run(args: argparse.Namespace) -> None:
    results = build_results()
    RESULTS_DIR.mkdir(exist_ok=True)
    results.to_parquet(LCOT_PARQUET, index=False)
    results.to_csv(LCOT_CSV, index=False)
    feasible = results["feasible"].sum()
    print(f"{len(results)} rows across {results['case'].nunique()} cases "
          f"({feasible} feasible) -> {LCOT_PARQUET}")


def _cmd_study(args: argparse.Namespace) -> None:
    raw, ranges = read_raw(ASSUMPTIONS_PATH)
    studies = load_studies(STUDIES_PATH, ranges, raw)
    names = args.names or list(studies)
    for name in names:
        if name not in studies:
            raise SystemExit(f"unknown study {name!r}; known: {list(studies)}")
        run_study(studies[name], raw)


def _cmd_plot(args: argparse.Namespace) -> None:
    from viz import plots           # lazy: keep the plotting stack out of run/study
    plots.main()


def _cmd_all(args: argparse.Namespace) -> None:
    _cmd_run(args)
    _cmd_plot(args)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lcot", description="LCOT techno-economic model — single entry point.")
    sub = parser.add_subparsers(dest="command", required=True)

    (sub.add_parser("run", help="render the fleet study -> results/lcot.{parquet,csv}")
        .set_defaults(func=_cmd_run))

    p_study = sub.add_parser(
        "study", help="run sensitivity studies -> results/sobol/<name>/ (all if none named)")
    p_study.add_argument("names", nargs="*", help="study names from studies.yaml; default: all")
    p_study.set_defaults(func=_cmd_study)

    (sub.add_parser("plot", help="render figures -> results/ (runs studies it needs)")
        .set_defaults(func=_cmd_plot))

    sub.add_parser("all", help="run, then plot").set_defaults(func=_cmd_all)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
