"""
run.py — single entry point and one-study pipeline for the LCOT model.

    uv run python scripts/run.py run [name...]   # run studies -> results/studies/<name>/ (all if none named)
    uv run python scripts/run.py plot            # figures -> results/ (runs studies they need)
    uv run python scripts/run.py all             # run all studies, then plot

Running a study is ONE operation regardless of what it declares: a pure sweep renders a tidy
comparison table; a study with a `sample` probe additionally variance-decomposes it (Sobol).
Nothing is privileged — the studies in studies.yaml are run as written. `run_study` is the whole
per-study pipeline (compose -> evaluate -> analyze -> store); it takes a self-contained `Study`, so
viz/plots.py can compute a missing store on demand by calling it directly.
"""

from __future__ import annotations

import argparse

from common.paths import ASSUMPTIONS_PATH, STUDIES_PATH, REPO_ROOT
import config
import compose, evaluate, analyze, store


def run_study(study: config.Study) -> None:
    """Run one study end to end: compose the block, evaluate + collapse the lever, decompose any
    sample probe (a no-op for a pure sweep), and persist the store. `analyze.report` does the
    post-run readout (feasibility + drivers)."""
    compose.place_axes(study)
    datasets = evaluate.evaluate(study)
    indices, feasibility = analyze.sobol_indices(study, datasets)
    out = store.write(study, datasets, indices, feasibility)
    print(f"[{study.name}] -> {out.relative_to(REPO_ROOT)}")
    analyze.report(study, datasets, indices, feasibility)


def _cmd_run(args: argparse.Namespace) -> None:
    studies = {study.name: study for study in config.get_studies(ASSUMPTIONS_PATH, STUDIES_PATH)}
    names = args.names or list(studies)
    unknown = [name for name in names if name not in studies]
    if unknown:
        raise SystemExit(f"unknown study/studies {unknown}; known: {list(studies)}")
    for name in names:
        run_study(studies[name])


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
