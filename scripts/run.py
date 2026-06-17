"""
run.py — entry point (pending the rebuild).

Will: load_config(config.yaml, cases.csv) -> run(case) for each Case -> write the results
artifact. The old entry point (against the pre-rebuild modules) was deleted; this awaits
optimizer.py and the EnergySource cost methods. See TODO.md and README.md.
"""
