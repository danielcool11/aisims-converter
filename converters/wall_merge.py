"""
Wall merge — merges adjacent FEM wall panels into structural walls.

MIDAS Gen exports walls as 2D FEM mesh panels (quad plate elements).
A single physical wall W2 at 5F might be subdivided into 49 panels
(8 columns × 6 rows in the mesh). This module merges them back into
one row per physical wall for correct viewer rendering, QTO, and
grid visibility.

Merge rules:
  1. Group by (wall_id, level) — all panels in same group = one wall
  2. Gather all node coordinates from all quad corners
  3. Find the two farthest-apart bottom-edge nodes = wall endpoints
  4. Recompute centroid, width (plan length), height from bounding box
  5. Preserve element_ids list for traceability

The FEM mesh creates disconnected vertical strips (no shared nodes
between strips). Within each strip, panels chain via shared node_i/j
pairs along the wall height direction. The merge uses spatial
(farthest-pair) endpoint detection, not chain-walking.

Output preserves same schema as MembersWall + adds element_ids column.
Runs BEFORE junction detection (needs merged endpoints for correct results).

Issue #402: https://github.com/suhwankim-lang/aisims-v2/issues/402
"""

import math
import pandas as pd
from typing import Dict, List, Optional, Tuple


def _dist_xy(a, b):
    """2D plan distance."""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _farthest_pair(ids, pts):
    """Find the two farthest-apart points in XY plane.

    Args:
        ids: list of node ID strings
        pts: list of (x, y) tuples, same length as ids

    Returns:
        (id_a, id_b, distance)
    """
    best_dist = 0
    best_a, best_b = 0, min(1, len(pts) - 1)
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = _dist_xy(pts[i], pts[j])
            if d > best_dist:
                best_dist = d
                best_a, best_b = i, j
    return ids[best_a], ids[best_b], best_dist


def _closest_node(target_xy, candidates):
    """Find the candidate node closest to target_xy in plan.

    Args:
        target_xy: (x, y) tuple
        candidates: dict {node_id: (x, y, z)}

    Returns:
        node_id of closest candidate
    """
    best_id = ''
    best_dist = float('inf')
    for nid, coord in candidates.items():
        d = _dist_xy(target_xy, coord[:2])
        if d < best_dist:
            best_dist = d
            best_id = nid
    return best_id


def merge_wall_panels(
    walls_df: pd.DataFrame,
    nodes_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Merge FEM wall panels into structural walls.

    Groups panels by (wall_id, level). For each group, finds the two
    farthest-apart bottom-edge nodes as wall endpoints and produces
    one output row.

    Args:
        walls_df: DataFrame with one row per FEM panel.
        nodes_df: Optional nodes DataFrame for coordinate lookup.
                  If None, uses centroid-based merging (less precise).

    Returns:
        Merged DataFrame with same schema + element_ids column.
    """
    if walls_df is None or walls_df.empty:
        return walls_df

    # Build node coordinate lookup from nodes_df
    node_coords = {}  # node_id -> (x_mm, y_mm, z_mm)
    if nodes_df is not None and not nodes_df.empty:
        for _, nr in nodes_df.iterrows():
            nid = str(nr.get('node_id', ''))
            if nid:
                node_coords[nid] = (
                    float(nr.get('x_mm', 0) or 0),
                    float(nr.get('y_mm', 0) or 0),
                    float(nr.get('z_mm', 0) or 0),
                )

    # Group by (wall_id, level) — each wall_id is one physical wall segment.
    # Multiple wall_ids can share the same wall_mark (same design/reinforcement)
    # but are at different locations. E.g., W2 has 8 wall_ids = 8 parallel
    # wall segments. Within each (wall_id, level), FEM panels tile the segment.
    groups = {}
    for idx, row in walls_df.iterrows():
        wid = int(row.get('wall_id') or 0)
        level = str(row.get('level', ''))
        key = (wid, level)
        if key not in groups:
            groups[key] = []
        groups[key].append((idx, row))

    merged_rows = []
    single_count = 0
    merged_count = 0

    for (wid_key, level), panel_list in groups.items():
        if len(panel_list) == 1:
            # Single panel — keep as-is, just add element_ids
            _, row = panel_list[0]
            rec = row.to_dict()
            rec['element_ids'] = str(rec.get('element_id', ''))
            merged_rows.append(rec)
            single_count += 1
            continue

        # ── Multiple panels — merge into one structural wall ──
        merged_count += 1

        # Collect metadata
        wall_mark = str(panel_list[0][1].get('wall_mark', ''))
        wall_id = wid_key
        material_id = panel_list[0][1].get('material_id')
        wall_status = panel_list[0][1].get('wall_status')

        # Collect all element IDs
        element_ids = [str(row.get('element_id', '')) for _, row in panel_list]

        # Collect thickness (max across panels — should be uniform)
        max_thickness = max(
            float(row.get('thickness_mm', 0) or 0) for _, row in panel_list
        )

        # ── Gather node coordinates for spatial endpoint detection ──
        bottom_nodes = {}  # node_id -> (x, y, z)
        top_nodes = {}
        all_z = []

        for _, row in panel_list:
            for node_col in ('node_i', 'node_j'):
                nid = str(row.get(node_col, '') or '')
                if nid and nid in node_coords and nid not in bottom_nodes:
                    bottom_nodes[nid] = node_coords[nid]
                    all_z.append(node_coords[nid][2])
            for node_col in ('node_k', 'node_l'):
                nid = str(row.get(node_col, '') or '')
                if nid and nid in node_coords and nid not in top_nodes:
                    top_nodes[nid] = node_coords[nid]
                    all_z.append(node_coords[nid][2])

        if len(bottom_nodes) >= 2:
            # ── Node-based merge (preferred) ──
            bot_ids = list(bottom_nodes.keys())
            bot_pts = [(bottom_nodes[n][0], bottom_nodes[n][1]) for n in bot_ids]

            # Wall endpoints = two farthest-apart bottom-edge nodes
            ep_i_id, ep_j_id, wall_length = _farthest_pair(bot_ids, bot_pts)
            ep_i = bottom_nodes[ep_i_id]
            ep_j = bottom_nodes[ep_j_id]

            # Corresponding top-edge endpoints (directly above in XY)
            ep_k_id = _closest_node(ep_i[:2], top_nodes) if top_nodes else ''
            ep_l_id = _closest_node(ep_j[:2], top_nodes) if top_nodes else ''

            # Centroid from all node coordinates
            all_coords = list(bottom_nodes.values()) + list(top_nodes.values())
            cx = sum(c[0] for c in all_coords) / len(all_coords)
            cy = sum(c[1] for c in all_coords) / len(all_coords)
            cz = (min(all_z) + max(all_z)) / 2 if all_z else 0

            # Height from Z range
            height = (max(all_z) - min(all_z)) if all_z else 0

        else:
            # ── Centroid-based fallback (no node lookup available) ──
            cx_sum = cy_sum = cz_sum = 0
            for _, row in panel_list:
                cx_sum += float(row.get('centroid_x_mm', 0) or 0)
                cy_sum += float(row.get('centroid_y_mm', 0) or 0)
                cz_sum += float(row.get('centroid_z_mm', 0) or 0)
            n = len(panel_list)
            cx, cy, cz = cx_sum / n, cy_sum / n, cz_sum / n

            # Width from centroid spread + average panel width
            xs = [float(r.get('centroid_x_mm', 0) or 0) for _, r in panel_list]
            ys = [float(r.get('centroid_y_mm', 0) or 0) for _, r in panel_list]
            avg_w = sum(float(r.get('width_mm', 0) or 0) for _, r in panel_list) / n
            wall_length = max(max(xs) - min(xs), max(ys) - min(ys)) + avg_w

            height = max(float(r.get('height_mm', 0) or 0) for _, r in panel_list)

            ep_i_id = str(panel_list[0][1].get('node_i', '') or '')
            ep_j_id = str(panel_list[-1][1].get('node_j', '') or '')
            ep_k_id = str(panel_list[0][1].get('node_k', '') or '')
            ep_l_id = str(panel_list[-1][1].get('node_l', '') or '')

        merged_rows.append({
            'element_id': int(panel_list[0][1].get('element_id', 0)),
            'wall_mark': wall_mark,
            'wall_id': wall_id,
            'level': level,
            'centroid_x_mm': round(cx, 1),
            'centroid_y_mm': round(cy, 1),
            'centroid_z_mm': round(cz, 1),
            'thickness_mm': max_thickness,
            'height_mm': round(height, 1) if height else None,
            'width_mm': round(wall_length, 1),
            'node_i': ep_i_id,
            'node_j': ep_j_id,
            'node_k': ep_k_id,
            'node_l': ep_l_id,
            'wall_status': wall_status,
            'material_id': material_id,
            'element_ids': ','.join(element_ids),
        })

    result = pd.DataFrame(merged_rows)

    # Ensure column order matches original + element_ids at end
    orig_cols = [c for c in walls_df.columns if c in result.columns]
    extra_cols = [c for c in result.columns if c not in walls_df.columns]
    result = result[orig_cols + extra_cols]

    print(f'[WallMerge] {len(walls_df)} panels → {len(result)} walls '
          f'({single_count} single + {merged_count} merged groups)')

    return result
