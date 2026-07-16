"""
run_mrv.py — the MRV pipeline in one step: unify the workbooks, then summarize the fleet.

A thin convenience entry point that imports both MRV utilities and runs them in order:
  1. mrv_unify  — concatenate data/*.xlsx into the lossless data/mrv_unified.{parquet,csv}
  2. mrv_fleet  — print the grounded config anchors + scaling fits and write the fleet plot

Each utility is still runnable on its own (`uv run scripts/mrv/mrv_unify.py`, etc.); this just
chains them. They sit in scripts/mrv/ and are imported here as sibling modules — running this by
path puts scripts/mrv/ on sys.path. The shared packages (common, viz) resolve via the editable
install of the project (see pyproject.toml), not a path hack.

Run:  uv run scripts/mrv/run_mrv.py [--no-plot]
"""

from __future__ import annotations

import sys

import mrv_unify
import mrv_fleet


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    print("=== 1/2  unify workbooks ===")
    mrv_unify.main()
    print("\n=== 2/2  summarize fleet ===")
    mrv_fleet.main(argv)   # forwards flags like --no-plot / --type / explicit paths


if __name__ == "__main__":
    main()
