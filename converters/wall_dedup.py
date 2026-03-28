"""
Wall deduplication — removes MembersWall elements that are covered by
Part C basement walls, and flags remaining no-design elements.

Adds 'wall_status' column to MembersWall:
  DESIGNED           — has DesignWall reinforcement (normal wall)
  COVERED_BY_PART_C  — overlaps Part C basement wall, removed from output
  NO_DESIGN          — no rebar from anywhere (gap elements, buttress)
"""

import pandas as pd
import numpy as np


def _point_to_segment_dist(px, py, x1, y1, x2, y2):
    """Distance from point (px,py) to line segment (x1,y1)-(x2,y2)."""
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return np.sqrt((px - x1) ** 2 + (py - y1) ** 2)
    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return np.sqrt((px - proj_x) ** 2 + (py - proj_y) ** 2)


def _segment_dist(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
    """Minimum distance between two line segments."""
    return min(
        _point_to_segment_dist(ax1, ay1, bx1, by1, bx2, by2),
        _point_to_segment_dist(ax2, ay2, bx1, by1, bx2, by2),
        _point_to_segment_dist(bx1, by1, ax1, ay1, ax2, ay2),
        _point_to_segment_dist(bx2, by2, ax1, ay1, ax2, ay2),
    )


def _midpoint_to_segment_dist(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
    """Distance from midpoint of segment A to segment B.

    More reliable than segment-to-segment distance for overlap detection,
    because it avoids false positives when segments merely share an endpoint
    but extend in different directions.
    """
    mx = (ax1 + ax2) / 2
    my = (ay1 + ay2) / 2
    return _point_to_segment_dist(mx, my, bx1, by1, bx2, by2)


def deduplicate_walls(walls_df, reinf_wall_df, bwall_members_df, nodes_df,
                      overlap_tolerance=300):
    """
    Remove MembersWall elements that overlap with Part C basement walls.

    Args:
        walls_df: MembersWall DataFrame
        reinf_wall_df: ReinforcementWall DataFrame (to find designed wall_ids)
        bwall_members_df: MembersBasementWall DataFrame
        nodes_df: Nodes DataFrame (for coordinate lookup)
        overlap_tolerance: max distance (mm) to consider overlap

    Returns:
        Updated walls_df with 'wall_status' column and duplicates removed
    """
    if walls_df is None or walls_df.empty:
        return walls_df

    # Build node coordinate lookup (XY only)
    node_xy = {}
    for _, r in nodes_df.iterrows():
        node_xy[str(r['node_id'])] = (float(r['x_mm']), float(r['y_mm']))

    # Find wall_ids with DesignWall reinforcement
    designed_ids = set()
    if reinf_wall_df is not None and not reinf_wall_df.empty:
        designed_ids = set(reinf_wall_df['wall_id'].unique())

    # Build Part C wall edges from OK-status panels only
    bwall_edges = []
    if bwall_members_df is not None and not bwall_members_df.empty:
        ok_panels = bwall_members_df
        if 'node_status' in bwall_members_df.columns:
            ok_panels = bwall_members_df[bwall_members_df['node_status'] == 'OK']

        for _, bw in ok_panels.iterrows():
            for na, nb in [('node_i', 'node_j'), ('node_k', 'node_l')]:
                ni = str(bw.get(na, ''))
                nj = str(bw.get(nb, ''))
                ci = node_xy.get(ni)
                cj = node_xy.get(nj)
                if ci and cj and 'MISSING' not in ni and 'MISSING' not in nj:
                    bwall_edges.append((ci[0], ci[1], cj[0], cj[1]))

    # Classify each wall element
    statuses = []
    for _, w in walls_df.iterrows():
        wid = w['wall_id']

        if wid in designed_ids:
            statuses.append('DESIGNED')
            continue

        # No design — check overlap with Part C
        if bwall_edges:
            ni = str(w.get('node_i', ''))
            nj = str(w.get('node_j', ''))
            ci = node_xy.get(ni)
            cj = node_xy.get(nj)

            if ci and cj:
                best_dist = float('inf')
                for ex1, ey1, ex2, ey2 in bwall_edges:
                    d = _midpoint_to_segment_dist(ci[0], ci[1], cj[0], cj[1],
                                                  ex1, ey1, ex2, ey2)
                    if d < best_dist:
                        best_dist = d

                if best_dist < overlap_tolerance:
                    statuses.append('COVERED_BY_PART_C')
                    continue

        statuses.append('NO_DESIGN')

    walls_df = walls_df.copy()
    walls_df['wall_status'] = statuses

    # Count before removal
    total = len(walls_df)
    covered = len(walls_df[walls_df['wall_status'] == 'COVERED_BY_PART_C'])
    no_design = len(walls_df[walls_df['wall_status'] == 'NO_DESIGN'])
    designed = len(walls_df[walls_df['wall_status'] == 'DESIGNED'])

    # Remove COVERED_BY_PART_C elements
    walls_df = walls_df[walls_df['wall_status'] != 'COVERED_BY_PART_C'].copy()

    print(f'[WallDedup] {total} total → {designed} DESIGNED + '
          f'{no_design} NO_DESIGN + {covered} removed (covered by Part C)')
    print(f'[WallDedup] Output: {len(walls_df)} wall elements')

    return walls_df
