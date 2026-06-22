"""
mrv_unify.py — concatenate every EU MRV export in data/ into one lossless table on disk.

A convenience dump so the model/analysis doesn't re-parse seven workbooks each run. Faithful
to the Excels: ALL ship types, ALL rows (every sheet), ALL columns — nothing filtered, nothing
dropped. Each row gains provenance (`reporting_year`, `source_file`, `source_sheet`,
`report_type`) — per-row facts, so they're columns, not metadata.

The one cosmetic fix: the annual-intensity headers drift across export years (2018-2023 prefix
them "Annual average …" / "Annual Total …"; 2024 drops the prefix and adds the CH4/N2O/CO2eq
breakdowns). That prefix is stripped so the same field lands in one column across years — but
ONLY where stripping it doesn't collide with another column in that file; on a collision the
original header is kept, so no cell is ever overwritten or merged into the wrong field. Genuinely
distinct headers (e.g. "Verifier Number" vs "Verifier Accreditation number") keep their own
columns. Row count is asserted conserved.

Losslessness of the rename is sealed in `DataFrame.attrs` (dataset-level metadata, preserved
through Parquet): a source manifest plus the canonical->original header map, so the original
per-year headers are always recoverable from the file. CSV can't carry attrs, so it's the plain
backup; the Parquet is canonical.

Run:  uv run scripts/mrv_unify.py            # -> data/mrv_unified.parquet (+ .csv backup)
"""

from __future__ import annotations

import datetime as dt
import glob
import re
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]   # scripts/mrv/ -> scripts/ -> repo root
DATA_DIR = REPO_ROOT / "data"
OUT_STEM = DATA_DIR / "mrv_unified"

_PREFIX = re.compile(r"^(annual average|annual total|annual)\s+", re.IGNORECASE)
_HEADER_KEYS = ("imo number", "ship type")   # the real header row sits below a title row or two


def _header_row(path: Path, sheet) -> int:
    """Locate the column-header row (a couple of title rows sit above it)."""
    head = pd.read_excel(path, sheet_name=sheet, header=None, dtype=object, nrows=20)
    for i in range(len(head)):
        cells = [str(c).lower() for c in head.iloc[i]]
        if any(any(k in c for k in _HEADER_KEYS) for c in cells):
            return i
    return 0


def _normalize_headers(columns: list[str]) -> dict[str, str]:
    """Map each column to its prefix-stripped name, but only when that rename stays unique
    within the file (no clobbering a sibling column). Colliding columns keep their original
    name, so the rename is always lossless."""
    existing = set(columns)
    proposed: dict[str, str] = {}
    seen_targets: dict[str, int] = {}
    for col in columns:
        stripped = _PREFIX.sub("", col).strip()
        target = stripped if (stripped == col or stripped not in existing) else col
        proposed[col] = target
        seen_targets[target] = seen_targets.get(target, 0) + 1
    # back off any rename whose target ended up shared by >1 source column in this file
    return {col: (tgt if seen_targets[tgt] == 1 else col) for col, tgt in proposed.items()}


def _load_sheet(path: Path, sheet: str, year: str) -> tuple[pd.DataFrame, dict[str, str]]:
    """One sheet -> (frame with normalized headers + provenance columns, original->canonical map)."""
    header = _header_row(path, sheet)
    frame = pd.read_excel(path, sheet_name=sheet, header=header, dtype=object)
    rename = _normalize_headers([str(c).strip() for c in frame.columns])
    frame = frame.rename(columns=rename)
    frame.columns = [str(c).strip() for c in frame.columns]
    frame.insert(0, "reporting_year", year)
    frame.insert(1, "source_file", path.name)
    frame.insert(2, "source_sheet", sheet)
    frame.insert(3, "report_type", "partial" if "partial" in sheet.lower() else "full")
    renamed = {orig: canon for orig, canon in rename.items() if orig != canon}
    return frame, renamed


def unify(paths: list[Path]) -> tuple[pd.DataFrame, dict]:
    """Outer-concatenate every sheet of every file. Asserts no rows lost; returns the table and
    a dataset-level metadata dict (source manifest + canonical->original header map)."""
    frames, expected_rows, manifest = [], 0, []
    header_map: dict[str, set] = {}
    for path in sorted(paths):
        matched = re.match(r"(\d{4})", path.name)
        year = matched.group(1) if matched else path.stem
        for sheet in pd.ExcelFile(path).sheet_names:
            frame, renamed = _load_sheet(path, sheet, year)
            for original, canonical in renamed.items():
                header_map.setdefault(canonical, set()).add(original)
            expected_rows += len(frame)
            frames.append(frame)
            manifest.append({"file": path.name, "sheet": sheet,
                             "year": year, "rows": len(frame), "cols": frame.shape[1]})
            print(f"  {path.name} :: {sheet:<18} {len(frame):>6} rows, {frame.shape[1]} cols")
    unified = pd.concat(frames, ignore_index=True, sort=False)
    assert len(unified) == expected_rows, f"row loss: {len(unified)} != {expected_rows}"

    metadata = {
        "dataset": "mrv_unified",
        "description": "EU MRV (THETIS-MRV) public emission reports, all ship types, "
                       "unified across export years. Lossless mirror of the source workbooks.",
        "source": "https://mrv.emsa.europa.eu/#public/emission-report",
        "built": dt.date.today().isoformat(),
        "rows": len(unified),
        "columns": unified.shape[1],
        "provenance_columns": ["reporting_year", "source_file", "source_sheet", "report_type"],
        "header_normalization_note": "Drifting 'Annual average/Total' prefixes were stripped "
            "where non-colliding so a field lands in one column across years; the map below "
            "recovers the original per-year headers.",
        "header_normalization": {canon: sorted(orig) for canon, orig in sorted(header_map.items())},
        "source_manifest": manifest,
    }
    return unified, metadata


def _write_excel(frame: pd.DataFrame, metadata: dict, path: Path) -> None:
    """Write the unified table plus a source manifest and header-normalization sheet."""
    manifest_df = pd.DataFrame(metadata["source_manifest"])
    header_map_df = pd.DataFrame([
        {"canonical": canon, "original_names": " | ".join(sorted(origs))}
        for canon, origs in sorted(metadata["header_normalization"].items())
    ])
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="data", index=False)
        manifest_df.to_excel(writer, sheet_name="sources", index=False)
        header_map_df.to_excel(writer, sheet_name="header_map", index=False)


def _write_csv_with_metadata_header(frame: pd.DataFrame, metadata: dict, path: Path) -> None:
    """Plain CSV preceded by a '#'-commented metadata block (read back with comment='#')."""
    import json
    lines = [f"# {metadata['dataset']} — {metadata['description']}",
             f"# source: {metadata['source']}",
             f"# built: {metadata['built']}  rows: {metadata['rows']}  columns: {metadata['columns']}",
             f"# provenance_columns: {', '.join(metadata['provenance_columns'])}",
             f"# {metadata['header_normalization_note']}",
             f"# header_normalization: {json.dumps(metadata['header_normalization'], ensure_ascii=False)}",
             "# (re-read with: pandas.read_csv(path, comment='#'))"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(lines) + "\n")
        frame.to_csv(fh, index=False)


def main() -> None:
    paths = [Path(p) for p in glob.glob(str(DATA_DIR / "*.xlsx"))]
    if not paths:
        print(f"no .xlsx files in {DATA_DIR}")
        return

    print(f"Unifying {len(paths)} workbook(s):")
    unified, metadata = unify(paths)

    OUT_STEM.parent.mkdir(parents=True, exist_ok=True)
    # Everything is read as object (strings) for fidelity; cast to string so Arrow doesn't choke
    # on mixed int/float/str cells. attrs (incl. the header map) ride along in the Parquet.
    parquet_frame = unified.astype({c: "string" for c in unified.columns if unified[c].dtype == object})
    parquet_frame.attrs = metadata
    parquet_frame.to_parquet(f"{OUT_STEM}.parquet", index=False)
    _write_csv_with_metadata_header(unified, metadata, Path(f"{OUT_STEM}.csv"))
    _write_excel(unified, metadata, Path(f"{OUT_STEM}.xlsx"))

    print(f"\n{len(unified):,} rows x {unified.shape[1]} cols across "
          f"{unified['reporting_year'].nunique()} years, "
          f"{unified['report_type'].value_counts().to_dict()}")
    print(f"  -> {OUT_STEM.relative_to(REPO_ROOT)}.parquet (attrs metadata)"
          f" + .csv (# header) + .xlsx (data / sources / header_map)")


if __name__ == "__main__":
    main()
