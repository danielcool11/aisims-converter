"""
Junction Extension — detects connected member endpoints and computes
geometric extensions to fill gaps at junctions.

MIDAS models members centerline-to-centerline, leaving gaps at corners
and T-junctions. This module extends each member's visual extent at
connected endpoints by half the adjacent member's perpendicular thickness.

Runs as a Tier 1 post-processing pass AFTER all member CSVs are generated.
Must complete BEFORE Tier 2 rebar generation starts.

Output: adds `extend_start_mm` and `extend_end_mm` columns to member CSVs.
These are VISUAL extensions only — structural lengths are unchanged.
"""

import math
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional


# Tolerance for matching endpoints (mm)
XY_TOLERANCE = 300.0
Z_TOLERANCE = 2000.0


class MemberEndpoint:
    """An endpoint of a structural member with its thickness info."""
    __slots__ = ('x', 'y', 'z', 'member_type', 'member_id', 'element_id',
                 'thickness_perp', 'direction', 'end_type', 'level')

    def __init__(self, x, y, z, member_type, member_id, element_id,
                 thickness_perp, direction, end_type, level=''):
        self.x = x                    # mm
        self.y = y                    # mm
        self.z = z                    # mm (elevation)
        self.member_type = member_type  # 'COLUMN', 'BEAM', 'WALL'
        self.member_id = member_id
        self.element_id = element_id
        self.thickness_perp = thickness_perp  # perpendicular thickness (mm)
        self.direction = direction    # (dx, dy) unit plan direction of the member
        self.end_type = end_type      # 'start' or 'end'
        self.level = level


def _unit_dir(dx, dy):
    """Normalize 2D direction vector."""
    l = math.sqrt(dx * dx + dy * dy)
    if l < 0.01:
        return (0, 0)
    return (dx / l, dy / l)


def _angle_between(d1, d2):
    """Angle in degrees between two 2D unit directions (0-180)."""
    dot = d1[0] * d2[0] + d1[1] * d2[1]
    dot = max(-1, min(1, dot))
    return math.degrees(math.acos(abs(dot)))


def _endpoints_match(ep1: MemberEndpoint, ep2: MemberEndpoint) -> bool:
    """Check if two endpoints are at the same location."""
    return (abs(ep1.x - ep2.x) < XY_TOLERANCE and
            abs(ep1.y - ep2.y) < XY_TOLERANCE and
            abs(ep1.z - ep2.z) < Z_TOLERANCE)


def collect_endpoints(
    columns_df: pd.DataFrame = None,
    beams_df: pd.DataFrame = None,
    walls_df: pd.DataFrame = None,
    nodes: Dict[str, Dict] = None,
) -> List[MemberEndpoint]:
    """Collect all member endpoints with their thickness info."""
    endpoints = []

    # ── Columns ──
    if columns_df is not None:
        for _, c in columns_df.iterrows():
            b = float(c.get('b_mm', 0) or 0)
            h = float(c.get('h_mm', 0) or 0)
            thickness = max(b, h)
            x = float(c.get('x_mm', 0) or 0)
            y = float(c.get('y_mm', 0) or 0)
            xt = float(c.get('x_top_mm', x) or x)
            yt = float(c.get('y_top_mm', y) or y)

            # Column direction is vertical — thickness is in plan
            # Use node Z for elevation
            z_bot = None
            z_top = None
            if nodes:
                n_from = str(c.get('node_from', ''))
                n_to = str(c.get('node_to', ''))
                if n_from in nodes:
                    z_bot = nodes[n_from].get('z_mm', 0)
                if n_to in nodes:
                    z_top = nodes[n_to].get('z_mm', 0)

            if z_bot is not None:
                endpoints.append(MemberEndpoint(
                    x, y, z_bot, 'COLUMN', str(c.get('member_id', '')),
                    str(c.get('element_id', '')), thickness,
                    (0, 0), 'start', str(c.get('level_from', ''))))
            if z_top is not None:
                endpoints.append(MemberEndpoint(
                    xt, yt, z_top, 'COLUMN', str(c.get('member_id', '')),
                    str(c.get('element_id', '')), thickness,
                    (0, 0), 'end', str(c.get('level_to', ''))))

    # ── Beams ──
    if beams_df is not None:
        for _, b in beams_df.iterrows():
            bw = float(b.get('b_mm', 0) or 0)
            bh = float(b.get('h_mm', 0) or 0)
            xf = float(b.get('x_from_mm', 0) or 0)
            yf = float(b.get('y_from_mm', 0) or 0)
            xt = float(b.get('x_to_mm', 0) or 0)
            yt = float(b.get('y_to_mm', 0) or 0)
            z = float(b.get('z_mm', 0) or 0)

            dx, dy = xt - xf, yt - yf
            direction = _unit_dir(dx, dy)

            # Beam perpendicular thickness = b_mm (width in plan)
            endpoints.append(MemberEndpoint(
                xf, yf, z, 'BEAM', str(b.get('member_id', '')),
                str(b.get('element_id', '')), bw,
                direction, 'start', str(b.get('level', ''))))
            endpoints.append(MemberEndpoint(
                xt, yt, z, 'BEAM', str(b.get('member_id', '')),
                str(b.get('element_id', '')), bh,
                direction, 'end', str(b.get('level', ''))))

    # ── Walls ──
    if walls_df is not None and nodes:
        for _, w in walls_df.iterrows():
            thickness = float(w.get('thickness_mm', 0) or 0)
            eid = str(w.get('element_id', ''))
            wm = str(w.get('wall_mark', ''))
            level = str(w.get('level', ''))

            # Get wall start/end from bottom nodes (node_i, node_j)
            ni = str(w.get('node_i', ''))
            nj = str(w.get('node_j', ''))
            ci = nodes.get(ni)
            cj = nodes.get(nj)

            if ci and cj:
                x1, y1 = ci['x_mm'], ci['y_mm']
                x2, y2 = cj['x_mm'], cj['y_mm']
                z_avg = (ci['z_mm'] + cj['z_mm']) / 2
                direction = _unit_dir(x2 - x1, y2 - y1)

                endpoints.append(MemberEndpoint(
                    x1, y1, z_avg, 'WALL', wm, eid, thickness,
                    direction, 'start', level))
                endpoints.append(MemberEndpoint(
                    x2, y2, z_avg, 'WALL', wm, eid, thickness,
                    direction, 'end', level))

    return endpoints


def compute_extensions(endpoints: List[MemberEndpoint]) -> Dict[str, Tuple[float, float]]:
    """
    For each member element, compute how much to extend at start and end.

    Returns dict: element_id → (extend_start_mm, extend_end_mm)
    """
    extensions: Dict[str, Tuple[float, float]] = {}

    # Index endpoints by element_id + end_type
    by_element: Dict[str, Dict[str, MemberEndpoint]] = {}
    for ep in endpoints:
        key = ep.element_id
        if key not in by_element:
            by_element[key] = {}
        by_element[key][ep.end_type] = ep

    # For each endpoint, find connected members and compute extension
    for ep in endpoints:
        best_extension = 0.0

        for other in endpoints:
            # Skip self (same element)
            if other.element_id == ep.element_id:
                continue

            # Check if endpoints are at the same location
            if not _endpoints_match(ep, other):
                continue

            # Skip if same member type and collinear (same wall continuing)
            if ep.member_type == other.member_type:
                if ep.direction != (0, 0) and other.direction != (0, 0):
                    angle = _angle_between(ep.direction, other.direction)
                    if angle < 15:  # nearly collinear — same wall/beam continuing
                        continue

            # Connected member found — extension = other's half-thickness
            ext = other.thickness_perp / 2
            if ext > best_extension:
                best_extension = ext

        # Store the max extension for this endpoint
        eid = ep.element_id
        if eid not in extensions:
            extensions[eid] = (0.0, 0.0)

        start_ext, end_ext = extensions[eid]
        if ep.end_type == 'start':
            extensions[eid] = (max(start_ext, best_extension), end_ext)
        else:
            extensions[eid] = (start_ext, max(end_ext, best_extension))

    return extensions


def apply_extensions_to_walls(walls_df: pd.DataFrame, extensions: Dict) -> pd.DataFrame:
    """Add extend_start_mm and extend_end_mm columns to walls DataFrame."""
    ext_start = []
    ext_end = []
    for _, w in walls_df.iterrows():
        eid = str(w.get('element_id', ''))
        s, e = extensions.get(eid, (0.0, 0.0))
        ext_start.append(round(s, 1))
        ext_end.append(round(e, 1))

    walls_df = walls_df.copy()
    walls_df['extend_start_mm'] = ext_start
    walls_df['extend_end_mm'] = ext_end
    return walls_df


def apply_extensions_to_beams(beams_df: pd.DataFrame, extensions: Dict) -> pd.DataFrame:
    """Add extend_start_mm and extend_end_mm columns to beams DataFrame."""
    ext_start = []
    ext_end = []
    for _, b in beams_df.iterrows():
        eid = str(b.get('element_id', ''))
        s, e = extensions.get(eid, (0.0, 0.0))
        ext_start.append(round(s, 1))
        ext_end.append(round(e, 1))

    beams_df = beams_df.copy()
    beams_df['extend_start_mm'] = ext_start
    beams_df['extend_end_mm'] = ext_end
    return beams_df


def apply_extensions_to_columns(columns_df: pd.DataFrame, extensions: Dict) -> pd.DataFrame:
    """Add extend_start_mm and extend_end_mm columns to columns DataFrame."""
    ext_start = []
    ext_end = []
    for _, c in columns_df.iterrows():
        eid = str(c.get('element_id', ''))
        s, e = extensions.get(eid, (0.0, 0.0))
        ext_start.append(round(s, 1))
        ext_end.append(round(e, 1))

    columns_df = columns_df.copy()
    columns_df['extend_start_mm'] = ext_start
    columns_df['extend_end_mm'] = ext_end
    return columns_df


def run_junction_detection(
    columns_df: pd.DataFrame = None,
    beams_df: pd.DataFrame = None,
    walls_df: pd.DataFrame = None,
    nodes: Dict[str, Dict] = None,
) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """
    Main entry point — detect junctions and apply extensions to all member CSVs.

    Returns (columns_df, beams_df, walls_df) with extend_start_mm/extend_end_mm added.
    """
    print('[JunctionExtend] Collecting endpoints...')
    endpoints = collect_endpoints(columns_df, beams_df, walls_df, nodes)
    print(f'[JunctionExtend] {len(endpoints)} endpoints from '
          f'{len(set(ep.element_id for ep in endpoints))} elements')

    print('[JunctionExtend] Computing extensions...')
    extensions = compute_extensions(endpoints)

    # Count non-zero extensions
    extended = sum(1 for s, e in extensions.values() if s > 0 or e > 0)
    print(f'[JunctionExtend] {extended} elements with extensions')

    # Apply to DataFrames
    result_cols = apply_extensions_to_columns(columns_df, extensions) if columns_df is not None else None
    result_beams = apply_extensions_to_beams(beams_df, extensions) if beams_df is not None else None
    result_walls = apply_extensions_to_walls(walls_df, extensions) if walls_df is not None else None

    # Summary
    if result_walls is not None:
        wall_ext = result_walls[(result_walls['extend_start_mm'] > 0) | (result_walls['extend_end_mm'] > 0)]
        print(f'[JunctionExtend] Walls: {len(wall_ext)} of {len(result_walls)} extended')
    if result_beams is not None:
        beam_ext = result_beams[(result_beams['extend_start_mm'] > 0) | (result_beams['extend_end_mm'] > 0)]
        print(f'[JunctionExtend] Beams: {len(beam_ext)} of {len(result_beams)} extended')
    if result_cols is not None:
        col_ext = result_cols[(result_cols['extend_start_mm'] > 0) | (result_cols['extend_end_mm'] > 0)]
        print(f'[JunctionExtend] Columns: {len(col_ext)} of {len(result_cols)} extended')

    return result_cols, result_beams, result_walls
