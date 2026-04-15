"""
Re-run tier2 beam rebar length calculation on an already-converted folder.

Useful for validating tier2 changes (e.g. #78 unified anchorage rule) without
running the full Streamlit pipeline. Reads Tier 1 CSVs from <folder>, calls
calculate_beam_rebar_lengths, and writes the result next to the originals as
RebarLengthsBeam.new.csv so the original file is preserved.

Usage:
    python scripts/_rerun_tier2_beam.py <converted_project_folder>
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tier2.rebar_lengths_beam import calculate_beam_rebar_lengths


def _read(folder: Path, name: str, required: bool = True) -> pd.DataFrame:
    p = folder / name
    if not p.exists():
        if required:
            raise FileNotFoundError(p)
        return pd.DataFrame()
    return pd.read_csv(p, encoding='utf-8')


def main(folder: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    dev_path = str(root / 'config' / 'development_lengths.csv')
    lap_path = str(root / 'config' / 'lap_splice.csv')

    beams = _read(folder, 'MembersBeam.csv')
    cols = _read(folder, 'MembersColumn.csv')
    secs = _read(folder, 'Sections.csv')
    reinf = _read(folder, 'ReinforcementBeam.csv')
    nodes = _read(folder, 'Nodes.csv')
    walls = _read(folder, 'MembersWall.csv', required=False)
    bwalls = _read(folder, 'MembersBasementWall.csv', required=False)

    print(f'[rerun] folder: {folder.name}')
    print(f'[rerun] beams={len(beams)} cols={len(cols)} walls={len(walls)} '
          f'bwalls={len(bwalls)} nodes={len(nodes)}')

    df = calculate_beam_rebar_lengths(
        beams, cols, secs, reinf, nodes, dev_path, lap_path,
        walls_df=walls if not walls.empty else None,
        bwalls_df=bwalls if not bwalls.empty else None,
    )

    out = folder / 'RebarLengthsBeam.new.csv'
    df.to_csv(out, index=False, encoding='utf-8')
    print(f'[rerun] wrote: {out.name} ({len(df)} rows)')

    # Quick tally by bar_role + anchorage
    print()
    print('bar_role counts:')
    print(df['bar_role'].value_counts().to_string())

    print()
    print('anchorage_start counts:')
    print(df['anchorage_start'].value_counts(dropna=False).to_string())
    print('anchorage_end counts:')
    print(df['anchorage_end'].value_counts(dropna=False).to_string())

    rem = df[df['bar_role'] == 'MAIN_REMAINDER']
    if len(rem):
        print()
        print(f'MAIN_REMAINDER = {len(rem)} rows')
        print('  by anchorage_start:')
        print('  ', rem['anchorage_start'].value_counts().to_dict())
        print('  by anchorage_end:')
        print('  ', rem['anchorage_end'].value_counts().to_dict())
        print('  by (bar_position, anchorage_start, anchorage_end):')
        combo = rem.groupby(['bar_position', 'anchorage_start', 'anchorage_end']).size()
        print(combo.to_string())


if __name__ == '__main__':
    main(Path(sys.argv[1]))
