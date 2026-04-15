"""
has_concrete_below predicate — issue #78 unified anchorage rule.

One predicate, two tests combined with OR. Both tests are integer-exact
with no tolerance fudging; the ground truth is node-sharing and exact mm
arithmetic as specified by Suhwan in suhwankim-lang/aisims-v2#78.

Usage:
    from converters.concrete_below import build_has_concrete_below

    has_concrete_below = build_has_concrete_below(
        columns_df, walls_df, bwalls_df, nodes_df
    )

    # Then per beam terminal:
    if has_concrete_below(terminal_node, beam_level, beam_z_mm, beam_axis):
        anchorage = 'HOOK'; direction = 'DOWN'
    else:
        anchorage = 'STRAIGHT'   # BOT fallback

    beam_axis is an integer (dx, dy, dz) tuple in mm — dz is usually 0 for
    beams but we support non-horizontal.

Test 1 — column_below(terminal_node, beam_level):
    exists column with node_to == terminal_node AND level_to == beam_level
    (column top node sits at this beam's floor level → beam rests on column).

Test 2 — wall_below(terminal_node, beam_z, beam_axis):
    exists wall panel (MemberWall or MemberBasementWall) such that ALL:
      1. terminal_node in {node_i, node_j, node_k, node_l}
      2. max(wall's 4 nodes' z_mm) == beam_z_mm   (wall is below, not above)
      3. dot(cross(v_ij, v_il), beam_axis) == 0   (panel ⊥ beam, hook fits)
    All checks integer mm. No Z_TOL. No sin(5°).

Rationale for the two-part structure: column_below handles the common
"beam sits on column" case via pure ID lookup (robust to slanted columns).
wall_below handles "beam terminal has a wall panel below it" via exact
geometry (robust to shared nodes where a wall lives above vs. below).

Verified on 15Apr26a output:
    P1  456 endpoints → 83 col + 162 wall → 243 either
    P2 4310 endpoints → 985 col + 1345 wall → 2280 either
"""
from __future__ import annotations

from typing import Callable, Optional
import pandas as pd


def _int(v, default=0):
    try:
        return int(round(float(v)))
    except Exception:
        return default


def _nid(v) -> Optional[str]:
    s = str(v).strip()
    if not s or s == 'nan':
        return None
    return s


def build_has_concrete_below(
    columns_df: pd.DataFrame,
    walls_df: Optional[pd.DataFrame],
    bwalls_df: Optional[pd.DataFrame],
    nodes_df: pd.DataFrame,
) -> Callable[[str, str, int, tuple], bool]:
    """Pre-index columns/walls/nodes and return a fast predicate closure.

    The returned closure signature is:
        has_concrete_below(terminal_node, beam_level, beam_z_mm, beam_axis)

    beam_axis: integer 3-tuple (dx, dy, dz) in mm, direction only (magnitude
    doesn't matter for the perpendicularity check).
    """

    # ── Node xyz index ─────────────────────────────────────────────────
    node_xyz: dict[str, tuple[int, int, int]] = {}
    if nodes_df is not None and not nodes_df.empty:
        for _, n in nodes_df.iterrows():
            nid = _nid(n.get('node_id'))
            if nid is None:
                continue
            node_xyz[nid] = (
                _int(n.get('x_mm')),
                _int(n.get('y_mm')),
                _int(n.get('z_mm')),
            )

    # ── column_below: (node_to, level_to) set ──────────────────────────
    col_index: set[tuple[str, str]] = set()
    if columns_df is not None and not columns_df.empty:
        for _, c in columns_df.iterrows():
            nt = _nid(c.get('node_to'))
            lvt = str(c.get('level_to', '') or '').strip()
            if nt and lvt:
                col_index.add((nt, lvt))

    # ── wall_below: per-node list of (node_tuple, cross_product) ──────
    # Pre-compute cross(v_ij, v_il) for every wall panel.
    # Index: node_id -> list of (panel_node_ids_tuple, max_z, cross_xyz)
    wall_index: dict[str, list[tuple[tuple[str, str, str, str], int, tuple[int, int, int]]]] = {}

    def _ingest_walls(df: Optional[pd.DataFrame]) -> None:
        if df is None or df.empty:
            return
        for _, w in df.iterrows():
            ni = _nid(w.get('node_i'))
            nj = _nid(w.get('node_j'))
            nk = _nid(w.get('node_k'))
            nl = _nid(w.get('node_l'))
            if None in (ni, nj, nk, nl):
                continue
            # Max z among the 4 panel nodes (must equal beam z for test 2).
            zs = []
            for n in (ni, nj, nk, nl):
                if n in node_xyz:
                    zs.append(node_xyz[n][2])
            if len(zs) < 4:
                continue
            max_z = max(zs)
            # Cross product v_ij × v_il for the perpendicularity test.
            xi, yi, zi = node_xyz[ni]
            xj, yj, zj = node_xyz[nj]
            xl, yl, zl = node_xyz[nl]
            ax, ay, az = xj - xi, yj - yi, zj - zi
            bx, by, bz = xl - xi, yl - yi, zl - zi
            cx = ay * bz - az * by
            cy = az * bx - ax * bz
            cz = ax * by - ay * bx
            if cx == 0 and cy == 0 and cz == 0:
                continue  # degenerate panel (colinear 3 nodes)
            panel = (ni, nj, nk, nl)
            entry = (panel, max_z, (cx, cy, cz))
            for n in (ni, nj, nk, nl):
                wall_index.setdefault(n, []).append(entry)

    _ingest_walls(walls_df)
    _ingest_walls(bwalls_df)

    # ── Closure ────────────────────────────────────────────────────────
    def has_concrete_below(
        terminal_node: Optional[str],
        beam_level: str,
        beam_z_mm: int,
        beam_axis: tuple[int, int, int],
    ) -> bool:
        if not terminal_node:
            return False
        t = str(terminal_node).strip()
        if not t or t == 'nan':
            return False

        # Test 1 — column_below
        if (t, str(beam_level).strip()) in col_index:
            return True

        # Test 2 — wall_below
        bz = int(beam_z_mm)
        ax, ay, az = beam_axis
        for panel, max_z, cross in wall_index.get(t, ()):
            if max_z != bz:
                continue
            cx, cy, cz = cross
            dot = cx * ax + cy * ay + cz * az
            if dot != 0:
                continue
            return True

        return False

    return has_concrete_below
