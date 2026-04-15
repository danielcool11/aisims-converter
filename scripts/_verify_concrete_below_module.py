"""
Cross-check: the new `concrete_below` module must reproduce the same
hit counts as the standalone `_verify_has_concrete_below.py` script.

Expected on 15Apr26a output:
    P1  456 endpoints → 83 col + 162 wall → 243 either
    P2 4310 endpoints → 985 col + 1345 wall → 2280 either
"""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from converters.concrete_below import build_has_concrete_below


def _int(v, default=0):
    try:
        return int(round(float(v)))
    except Exception:
        return default


def main(folder: Path) -> None:
    beams = pd.read_csv(folder / 'MembersBeam.csv', encoding='utf-8')
    cols = pd.read_csv(folder / 'MembersColumn.csv', encoding='utf-8')
    walls = pd.read_csv(folder / 'MembersWall.csv', encoding='utf-8')
    try:
        bwalls = pd.read_csv(folder / 'MembersBasementWall.csv', encoding='utf-8')
    except Exception:
        bwalls = pd.DataFrame()
    nodes = pd.read_csv(folder / 'Nodes.csv', encoding='utf-8')

    has = build_has_concrete_below(cols, walls, bwalls, nodes)

    endpoints = 0
    hits = 0
    for _, b in beams.iterrows():
        lv = str(b.get('level', '')).strip()
        nf = str(b.get('node_from', '')).strip()
        nt = str(b.get('node_to', '')).strip()
        bz = _int(b.get('z_mm'))
        ax = _int(b.get('x_to_mm')) - _int(b.get('x_from_mm'))
        ay = _int(b.get('y_to_mm')) - _int(b.get('y_from_mm'))
        axis = (ax, ay, 0)
        for terminal in (nf, nt):
            if not terminal or terminal == 'nan':
                continue
            endpoints += 1
            if has(terminal, lv, bz, axis):
                hits += 1

    print(f"{folder.name}: endpoints={endpoints}, hits={hits}")


if __name__ == '__main__':
    main(Path(sys.argv[1]))
