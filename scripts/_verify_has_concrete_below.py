"""
Reproduce Suhwan's has_concrete_below predicate (issue #78 consolidated spec)
on our converter output WITHOUT running the converter — pure CSV read.

column_below(terminal_node, beam_level):
    any column with node_to == terminal_node AND level_to == beam_level

wall_below(terminal_node, beam_z_mm, beam_axis):
    exists wall such that
      1. terminal_node in {node_i, node_j, node_k, node_l}
      2. max(wall_node_z_mm) == beam_z_mm
      3. dot(cross(v_ij, v_il), beam_axis) == 0     # all integer mm

Expected on P1 Cheongdam (Suhwan numbers):
    endpoints         484
    column_below hits  85
    wall_below hits   166

Run:  venv/Scripts/python scripts/_verify_has_concrete_below.py <folder>
"""
import sys
from pathlib import Path
import pandas as pd


def _int(v, default=0):
    try:
        return int(round(float(v)))
    except Exception:
        return default


def load(folder: Path):
    beams = pd.read_csv(folder / 'MembersBeam.csv', encoding='utf-8')
    cols = pd.read_csv(folder / 'MembersColumn.csv', encoding='utf-8')
    walls = pd.read_csv(folder / 'MembersWall.csv', encoding='utf-8')
    try:
        bwalls = pd.read_csv(folder / 'MembersBasementWall.csv', encoding='utf-8')
    except Exception:
        bwalls = pd.DataFrame()
    nodes = pd.read_csv(folder / 'Nodes.csv', encoding='utf-8')
    return beams, cols, walls, bwalls, nodes


def build_node_z(nodes_df):
    """node_id -> int z_mm."""
    out = {}
    for _, n in nodes_df.iterrows():
        nid = str(n.get('node_id', '')).strip()
        if nid and nid != 'nan':
            out[nid] = _int(n.get('z_mm', 0))
    return out


def build_column_below_index(cols_df):
    """(node_to, level_to) -> True — for fast lookup."""
    idx = set()
    for _, c in cols_df.iterrows():
        nt = str(c.get('node_to', '')).strip()
        lvt = str(c.get('level_to', '')).strip()
        if nt and nt != 'nan' and lvt:
            idx.add((nt, lvt))
    return idx


def wall_below_match(wall_row, terminal_node, beam_z, beam_axis, node_z):
    """Apply Suhwan's 3-condition wall_below test to one wall row."""
    ni = str(wall_row.get('node_i', '')).strip()
    nj = str(wall_row.get('node_j', '')).strip()
    nk = str(wall_row.get('node_k', '')).strip()
    nl = str(wall_row.get('node_l', '')).strip()
    # 1. terminal node in wall's 4 panel nodes
    if terminal_node not in (ni, nj, nk, nl):
        return False
    # 2. max(wall_node_z) == beam_z_mm
    zs = []
    for n in (ni, nj, nk, nl):
        if n in node_z:
            zs.append(node_z[n])
    if not zs:
        return False
    if max(zs) != beam_z:
        return False
    # 3. cross(v_ij, v_il) . beam_axis == 0
    # Need the 4 node coords.
    pts = []
    for n in (ni, nj, nk, nl):
        if n not in node_z:
            return False
        pts.append(n)
    return True  # orientation test added below


def compute_wall_cross(wall_row, node_xyz):
    """(v_ij x v_il) integer vector, returns None if degenerate or nodes missing."""
    ni = str(wall_row.get('node_i', '')).strip()
    nj = str(wall_row.get('node_j', '')).strip()
    nl = str(wall_row.get('node_l', '')).strip()
    for n in (ni, nj, nl):
        if n not in node_xyz:
            return None
    xi, yi, zi = node_xyz[ni]
    xj, yj, zj = node_xyz[nj]
    xl, yl, zl = node_xyz[nl]
    # v_ij
    ax, ay, az = xj - xi, yj - yi, zj - zi
    # v_il
    bx, by, bz = xl - xi, yl - yi, zl - zi
    # cross
    cx = ay * bz - az * by
    cy = az * bx - ax * bz
    cz = ax * by - ay * bx
    if cx == 0 and cy == 0 and cz == 0:
        return None
    return (cx, cy, cz)


def build_node_xyz(nodes_df):
    out = {}
    for _, n in nodes_df.iterrows():
        nid = str(n.get('node_id', '')).strip()
        if nid and nid != 'nan':
            out[nid] = (_int(n.get('x_mm', 0)),
                        _int(n.get('y_mm', 0)),
                        _int(n.get('z_mm', 0)))
    return out


def main(folder: Path):
    beams, cols, walls, bwalls, nodes = load(folder)
    node_xyz = build_node_xyz(nodes)
    node_z = {k: v[2] for k, v in node_xyz.items()}
    col_idx = build_column_below_index(cols)

    # Combine walls + basement walls for wall_below (both are wall panels)
    all_walls = pd.concat([walls, bwalls], ignore_index=True, sort=False)

    endpoint_count = 0
    col_hits = 0
    wall_hits = 0
    either_hits = 0

    for _, b in beams.iterrows():
        lv = str(b.get('level', '')).strip()
        nf = str(b.get('node_from', '')).strip()
        nt = str(b.get('node_to', '')).strip()
        z = _int(b.get('z_mm', 0))
        # integer beam axis from x/y delta (z is constant for MembersBeam)
        ax = _int(b.get('x_to_mm', 0)) - _int(b.get('x_from_mm', 0))
        ay = _int(b.get('y_to_mm', 0)) - _int(b.get('y_from_mm', 0))
        az = 0
        axis = (ax, ay, az)

        for terminal in (nf, nt):
            if not terminal or terminal == 'nan':
                continue
            endpoint_count += 1
            col_hit = (terminal, lv) in col_idx
            # wall_below
            wall_hit = False
            for _, w in all_walls.iterrows():
                ni = str(w.get('node_i', '')).strip()
                nj = str(w.get('node_j', '')).strip()
                nk = str(w.get('node_k', '')).strip()
                nl = str(w.get('node_l', '')).strip()
                if terminal not in (ni, nj, nk, nl):
                    continue
                zs = [node_z[n] for n in (ni, nj, nk, nl) if n in node_z]
                if not zs or max(zs) != z:
                    continue
                cross = compute_wall_cross(w, node_xyz)
                if cross is None:
                    continue
                dot = cross[0]*axis[0] + cross[1]*axis[1] + cross[2]*axis[2]
                if dot != 0:
                    continue
                wall_hit = True
                break
            if col_hit:
                col_hits += 1
            if wall_hit:
                wall_hits += 1
            if col_hit or wall_hit:
                either_hits += 1

    print(f"Folder:     {folder.name}")
    print(f"Endpoints:  {endpoint_count}")
    print(f"column_below hits: {col_hits}")
    print(f"wall_below hits:   {wall_hits}")
    print(f"either hit:        {either_hits}")


if __name__ == '__main__':
    folder = Path(sys.argv[1])
    main(folder)
