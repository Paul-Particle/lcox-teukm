"""
evaluate.py — run the kernel for each case and collapse its lever axes to the optimum.

`compose.place_axes` has already put the study's sample/sweep axes on the shared library as named
`xr.DataArray` leaves, so a strategy call broadcasts them (sample x swept x lever) by dim name. For
each case we hand the chosen optimizer (`study.optimizer`) a closure that runs the case's strategy at
a given lever assignment; the optimizer OWNS the lever axes — it proposes points, collapses them to
the optimum, and returns every measure at that optimum over the retained (sample, swept) dims (see
`optimize.py`). A case with no levers just gets its block back, uncollapsed.

The closure brackets the lever assignment: it sets each lever leaf, runs the strategy, and restores
the leaf afterwards, so the shared library is left as it was for the next case (a case that doesn't
optimize a given leaf still sees its nominal value). Feasibility masking lives in the strategies
(`_finalize`); each case becomes one `xr.Dataset` over the retained dims, with the swept/sample
coordinate echoes dropped (they survive as coordinates).
"""

from __future__ import annotations

import numpy as np
import xarray as xr

from model import strategies
import compose
import config
import optimize


def evaluate(study: config.Study) -> dict[str, xr.Dataset]:
    """Evaluate every member case over its block and collapse its levers, one `xr.Dataset` per case.
    The lever collapse is delegated to the study's optimizer; the retained dims are `sample` (if the
    study samples) plus the swept conditions."""
    optimizer = optimize.OPTIMIZERS[study.optimizer]
    case_levers = compose.levers(study)
    echoes = set(compose.sweep_dims(study)) | ({"sample"} if compose.sample_paths(study) else set())
    datasets: dict[str, xr.Dataset] = {}
    for case in study.cases:
        strategy = getattr(strategies, case.strategy)
        run_kernel = _evaluator(study.library, case, strategy)
        with np.errstate(all="ignore"):
            collapsed = optimizer(run_kernel, case_levers[case.name],
                                  study.optimize_by, study.minimize)
        datasets[case.name] = collapsed.drop_vars(
            [name for name in echoes if name in collapsed.data_vars])
    return datasets


def _evaluator(library: config.Library, case: config.Case, strategy):
    """A closure the optimizer drives: place the lever assignment on the shared library, run the
    case's strategy, and restore the leaves — so the mutation is scoped to this one kernel call."""
    def run_kernel(assignment):
        originals = {path: config.get_leaf(library, path) for path in assignment}
        for path, value in assignment.items():
            config.set_leaf(library, path, value)
        try:
            return strategy(case)
        finally:
            for path, value in originals.items():
                config.set_leaf(library, path, value)
    return run_kernel
