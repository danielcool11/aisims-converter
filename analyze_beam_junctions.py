"""
CLI: analyze beam-to-beam junctions in a converter output folder.

Usage:
    python analyze_beam_junctions.py <converted_project_folder> [<another>...]

Reads MembersBeam.csv, MembersColumn.csv, MembersWall.csv (optional),
MembersBasementWall.csv (optional), and RebarLengthsBeam.csv from each given
folder, then classifies every coaxial beam-to-beam pair at every shared node
per Prof. Sunkuk's Case 1/2/3 rule (issue #78 Error B).

Outputs:
    - Per-folder counts by case and position
    - List of Case 2 + Case 3 findings (the ones Prof. Sunkuk's rule rewrites)

This is the Phase 1 "analysis only, no output change" step. It verifies the
classifier against issue #78's baseline before we modify the rebar calc loop.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent))

from converters.beam_junction_graph import (
    build_beam_refs,
    build_rebar_counts,
    build_support_node_set,
    classify_junctions,
    compute_runs,
    summarize,
    summarize_runs,
)


def _read_optional(folder: Path, filename: str) -> pd.DataFrame | None:
    p = folder / filename
    if not p.exists():
        return None
    try:
        return pd.read_csv(p)
    except Exception as e:
        print(f'  [warn] failed to read {filename}: {e}')
        return None


def analyze_folder(folder: Path) -> None:
    print(f'\n=== {folder.name} ===')
    beams_df = _read_optional(folder, 'MembersBeam.csv')
    if beams_df is None or beams_df.empty:
        print('  no MembersBeam.csv')
        return
    columns_df = _read_optional(folder, 'MembersColumn.csv')
    walls_df = _read_optional(folder, 'MembersWall.csv')
    bwalls_df = _read_optional(folder, 'MembersBasementWall.csv')
    rebar_df = _read_optional(folder, 'RebarLengthsBeam.csv')

    refs = build_beam_refs(beams_df)
    counts = build_rebar_counts(rebar_df, beams_df)
    supported = build_support_node_set(columns_df, walls_df, bwalls_df)

    print(f'  beams={len(refs)}, supported nodes={len(supported)}')

    findings = classify_junctions(refs, counts, supported)
    summary = summarize(findings)

    total = summary.get('total', 0)
    print(f'  junction pairs classified: {total}')
    for case in (0, 1, 2, 3):
        label = {
            0: 'Case 0 (has support / section mismatch / not coaxial / etc.)',
            1: 'Case 1 (same count + same dia, LAP through)',
            2: 'Case 2 (same dia, different count, LAP + remainder)',
            3: 'Case 3 (different dia, HOOK both sides)',
        }[case]
        c_total = summary.get(f'case_{case}', 0)
        c_top = summary.get(f'case_{case}_TOP', 0)
        c_bot = summary.get(f'case_{case}_BOT', 0)
        print(f'    {label}: {c_total} (TOP={c_top}, BOT={c_bot})')

    # Detail for Case 2 + Case 3 (the ones that actually change behavior)
    rewrite_targets = [f for f in findings if f.case in (2, 3)]
    if rewrite_targets:
        print(f'\n  Case 2/3 targets (these are Prof. Sunkuk rewrites):')
        for f in rewrite_targets:
            tag = 'CASE2' if f.case == 2 else 'CASE3'
            print(
                f'    {tag} {f.level:>5s} {f.position} '
                f'{f.beam_a_member:>6s}({f.n_a}-D{f.dia_a}) vs '
                f'{f.beam_b_member:>6s}({f.n_b}-D{f.dia_b}) '
                f'@ {f.node_id}'
            )

    # Run-level analysis (per Prof. Sunkuk: a run is a maximal Case 1/2 component)
    print(f'\n  Run-level analysis (physical bars, not per-junction findings):')
    all_runs = []
    for position in ('TOP', 'BOT'):
        runs = compute_runs(refs, findings, counts, beams_df, position)
        all_runs.extend(runs)
    run_summary = summarize_runs(all_runs)
    print(f'    total runs: {run_summary.get("total_runs", 0)} '
          f'(TOP={run_summary.get("runs_TOP", 0)}, '
          f'BOT={run_summary.get("runs_BOT", 0)})')
    print(f'    runs with Case 2 (count variance): '
          f'{run_summary.get("runs_with_case2", 0)} '
          f'(TOP={run_summary.get("runs_with_case2_TOP", 0)}, '
          f'BOT={run_summary.get("runs_with_case2_BOT", 0)})')
    print(f'    total remainder bars to emit: '
          f'{run_summary.get("total_remainders", 0)} '
          f'(TOP={run_summary.get("remainders_TOP", 0)}, '
          f'BOT={run_summary.get("remainders_BOT", 0)})')

    # Detail: runs that have Case 2 transitions
    case2_runs = [r for r in all_runs if r.has_case2]
    if case2_runs:
        print(f'\n  Case 2 runs with count profile:')
        for r in case2_runs:
            members = [str(beams_df.loc[i].get("member_id", "?")) for i in r.ordered_beams]
            profile_str = " -> ".join(f'{m}({c})' for m, c in zip(members, r.counts))
            print(
                f'    {r.level:>5s} {r.position} D{r.dia}  '
                f'{profile_str}   min={r.min_count} max={r.max_count} '
                f'remainders={len(r.remainders)}'
            )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    for arg in argv[1:]:
        folder = Path(arg)
        if not folder.exists():
            print(f'[error] not found: {folder}')
            continue
        analyze_folder(folder)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
