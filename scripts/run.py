"""
run.py — single entry point for the LCOT model.

    uv run python scripts/run.py run [name...]   # run studies -> results/studies/<name>/ (all if none named)
    uv run python scripts/run.py plot            # figures -> results/ (runs studies they need)
    uv run python scripts/run.py all             # run all studies, then plot

Running a study is ONE operation regardless of what it declares: a pure sweep renders a tidy
comparison table; a study with a `sample` axis additionally variance-decomposes it (Sobol).
Nothing is privileged — the studies in studies.yaml are run as written. The evaluation lives in
pipeline.py, plotting in viz/plots.py (imported lazily so `run` doesn't pull in the plotting stack).
"""

from __future__ import annotations

import argparse

from common.paths import ASSUMPTIONS_PATH, STUDIES_PATH
from config import load_assumptions, load_studies, apply_schema
from pipeline import run_study


def _cmd_run(args: argparse.Namespace) -> None:
    raw, ranges = load_assumptions(ASSUMPTIONS_PATH)
    case_specs, studies_raw = load_studies(STUDIES_PATH)
    names = args.names or list(studies_raw)
    for name in names:
        if name not in studies_raw:
            raise SystemExit(f"unknown study {name!r}; known: {list(studies_raw)}")
        run_study(apply_schema((raw, ranges), name, studies_raw[name]), raw, case_specs)


def _cmd_plot(args: argparse.Namespace) -> None:
    from viz import plots           # lazy: keep the plotting stack out of `run`
    plots.main()


def _cmd_all(args: argparse.Namespace) -> None:
    args.names = []                  # every study
    _cmd_run(args)
    _cmd_plot(args)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lcot", description="LCOT techno-economic model — single entry point.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser(
        "run", help="run studies -> results/studies/<name>/ (all if none named)")
    p_run.add_argument("names", nargs="*", help="study names from studies.yaml; default: all")
    p_run.set_defaults(func=_cmd_run)

    (sub.add_parser("plot", help="render figures -> results/ (runs studies they need)")
        .set_defaults(func=_cmd_plot))

    sub.add_parser("all", help="run all studies, then plot").set_defaults(func=_cmd_all)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
