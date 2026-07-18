"""paths.py — canonical filesystem locations for the model's inputs and outputs.

Derived once from this file's own position, so no module has to count `parents[...]` levels
(which silently break when a module moves between package dirs) and the input/output names
live in a single place. The standalone `mrv/` utility keeps its own paths, staying decoupled.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]     # common/ -> scripts/ -> repo root

ASSUMPTIONS_PATH = REPO_ROOT / "assumptions.yaml"
STUDIES_PATH = REPO_ROOT / "studies.yaml"

RESULTS_DIR = REPO_ROOT / "results"
STUDIES_DIR = RESULTS_DIR / "studies"           # one store dir per study in studies.yaml
                                                # (results/studies/<name>/): tidy table always,
                                                # Sobol indices when the study samples
