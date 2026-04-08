"""
Grid auto-detection — detects grid lines from column node positions.

After Phase 2 identifies columns, this module:
1. Collects all column bottom-node coordinates
2. Clusters X positions → X grid lines
3. Clusters Y positions → Y grid lines
4. Assigns labels (X1, X2, ... / Y1, Y2, ...)
5. Returns grid definitions that can be fed back into nodes converter

This runs AFTER elements.py (Phase 2) so we know which nodes are columns.
"""

import numpy as np
from typing import Optional


def detect_grid_from_columns(
    column_positions: list,
    cluster_tolerance: float = 200.0,
    min_columns_per_line: int = 2,
) -> dict:
    """
    Detect grid lines from column X/Y positions.

    Args:
        column_positions: list of (x_mm, y_mm) tuples (bottom node of each column)
        cluster_tolerance: max distance to merge positions into same grid line (mm)
        min_columns_per_line: minimum columns needed to define a grid line

    Returns:
        dict with:
            'grid_x': list of (label, position_mm) sorted by position
            'grid_y': list of (label, position_mm) sorted by position
            'x_origin': float — position of first X grid line
            'y_origin': float — position of first Y grid line
    """
    if not column_positions:
        return {'grid_x': [], 'grid_y': [], 'x_origin': 0, 'y_origin': 0}

    xs = [p[0] for p in column_positions]
    ys = [p[1] for p in column_positions]

    # Cluster X positions
    x_lines = _cluster_positions(xs, cluster_tolerance, min_columns_per_line)
    y_lines = _cluster_positions(ys, cluster_tolerance, min_columns_per_line)

    # Sort and label
    x_lines.sort()
    y_lines.sort()

    grid_x = [(f'X{i+1}', pos) for i, pos in enumerate(x_lines)]
    grid_y = [(f'Y{i+1}', pos) for i, pos in enumerate(y_lines)]

    x_origin = x_lines[0] if x_lines else 0
    y_origin = y_lines[0] if y_lines else 0

    print(f'[GridDetect] Detected {len(grid_x)} X-lines, {len(grid_y)} Y-lines '
          f'from {len(column_positions)} column positions')

    return {
        'grid_x': grid_x,
        'grid_y': grid_y,
        'x_origin': x_origin,
        'y_origin': y_origin,
    }


def _cluster_positions(values: list, tolerance: float, min_count: int) -> list:
    """
    Cluster 1D values into grid line positions.

    Algorithm:
    1. Sort values
    2. Walk through, merging values within tolerance into clusters
    3. Each cluster's position = mean of its members
    4. Keep only clusters with >= min_count members

    Returns sorted list of cluster center positions.
    """
    if not values:
        return []

    sorted_vals = sorted(values)
    clusters = []
    current_cluster = [sorted_vals[0]]

    for val in sorted_vals[1:]:
        if val - current_cluster[-1] <= tolerance:
            current_cluster.append(val)
        else:
            clusters.append(current_cluster)
            current_cluster = [val]
    clusters.append(current_cluster)

    # Filter by min count and compute mean positions
    positions = []
    for cluster in clusters:
        if len(cluster) >= min_count:
            positions.append(round(sum(cluster) / len(cluster), 1))

    return positions


def detect_reference_lines(
    grid_x: list,
    grid_y: list,
    beams_df=None,
    columns_df=None,
    walls_df=None,
    cluster_tolerance: float = 200.0,
) -> list:
    """
    Detect reference lines from member coordinates.

    A reference line is a structural axis (constant X or Y) where members sit.
    Grid lines (from columns) are major; reference lines (beams/walls only) are minor.

    Args:
        grid_x: list of (label, position_mm) from grid detection
        grid_y: list of (label, position_mm) from grid detection
        beams_df: DataFrame with x_from_mm, y_from_mm, direction columns
        columns_df: DataFrame with x_mm, y_mm columns
        walls_df: DataFrame with start/end coordinates
        cluster_tolerance: merge positions within this distance (mm)

    Returns:
        list of dicts: {label, coord_mm, axis, type}
        sorted by axis then coordinate
    """
    import pandas as pd

    # Build sets of existing grid positions
    grid_x_positions = {pos for _, pos in grid_x}
    grid_y_positions = {pos for _, pos in grid_y}
    grid_x_sorted = sorted(grid_x, key=lambda g: g[1])
    grid_y_sorted = sorted(grid_y, key=lambda g: g[1])

    # Collect all Y-coordinates where X-direction members sit (→ Y-axis references)
    y_coords = []
    # Collect all X-coordinates where Y-direction members sit (→ X-axis references)
    x_coords = []

    # From beams
    if beams_df is not None and not beams_df.empty:
        for _, bm in beams_df.iterrows():
            x1 = float(bm.get('x_from_mm', 0) or 0)
            y1 = float(bm.get('y_from_mm', 0) or 0)
            x2 = float(bm.get('x_to_mm', 0) or 0)
            y2 = float(bm.get('y_to_mm', 0) or 0)

            # Derive direction from coordinates if not available
            direction = str(bm.get('direction', ''))
            if direction not in ('X', 'Y'):
                dx = abs(x2 - x1)
                dy = abs(y2 - y1)
                direction = 'X' if dx >= dy else 'Y'

            if direction == 'X':
                # X-direction beam: constant Y → add to Y references
                avg_y = (y1 + y2) / 2
                y_coords.append(avg_y)
            else:
                # Y-direction beam: constant X → add to X references
                avg_x = (x1 + x2) / 2
                x_coords.append(avg_x)

    # From columns (already captured in grid_x/grid_y, but include for completeness)
    if columns_df is not None and not columns_df.empty:
        for _, c in columns_df.iterrows():
            x = float(c.get('x_mm', 0) or 0)
            y = float(c.get('y_mm', 0) or 0)
            if x != 0:
                x_coords.append(x)
            if y != 0:
                y_coords.append(y)

    # From walls
    if walls_df is not None and not walls_df.empty:
        for _, w in walls_df.iterrows():
            # Determine wall direction from node coordinates
            x1 = float(w.get('x_from_mm', 0) or w.get('start_x_mm', 0) or 0)
            y1 = float(w.get('y_from_mm', 0) or w.get('start_y_mm', 0) or 0)
            x2 = float(w.get('x_to_mm', 0) or w.get('end_x_mm', 0) or 0)
            y2 = float(w.get('y_to_mm', 0) or w.get('end_y_mm', 0) or 0)
            dx = abs(x2 - x1)
            dy = abs(y2 - y1)
            if dx > dy:  # X-direction wall
                avg_y = (y1 + y2) / 2
                if avg_y != 0:
                    y_coords.append(avg_y)
            else:  # Y-direction wall
                avg_x = (x1 + x2) / 2
                if avg_x != 0:
                    x_coords.append(avg_x)

    # Cluster X and Y coordinates
    x_clusters = _cluster_positions(x_coords, cluster_tolerance, min_count=1)
    y_clusters = _cluster_positions(y_coords, cluster_tolerance, min_count=1)

    # Build reference lines
    ref_lines = []

    def _is_on_grid(pos, grid_positions, tol=200):
        for gp in grid_positions:
            if abs(pos - gp) < tol:
                return True
        return False

    def _find_parent_grid(pos, grid_sorted):
        """Find the grid line just before this position."""
        parent = None
        for label, gpos in grid_sorted:
            if gpos < pos - 100:
                parent = label
            else:
                break
        return parent

    # X-axis reference lines (constant X positions for Y-direction frames)
    ref_x_count = {}  # parent_grid → count
    for pos in x_clusters:
        is_grid = _is_on_grid(pos, grid_x_positions)
        if is_grid:
            # Find matching grid label
            for label, gpos in grid_x:
                if abs(pos - gpos) < 200:
                    ref_lines.append({
                        'label': label,
                        'coord_mm': round(gpos, 1),
                        'axis': 'X',
                        'type': 'GRID',
                    })
                    break
        else:
            parent = _find_parent_grid(pos, grid_x_sorted)
            if parent:
                ref_x_count[parent] = ref_x_count.get(parent, 0) + 1
                label = f'{parent}-{ref_x_count[parent]}'
            else:
                ref_x_count['X0'] = ref_x_count.get('X0', 0) + 1
                label = f'X0-{ref_x_count["X0"]}'
            ref_lines.append({
                'label': label,
                'coord_mm': round(pos, 1),
                'axis': 'X',
                'type': 'REF',
            })

    # Y-axis reference lines (constant Y positions for X-direction frames)
    ref_y_count = {}
    for pos in y_clusters:
        is_grid = _is_on_grid(pos, grid_y_positions)
        if is_grid:
            for label, gpos in grid_y:
                if abs(pos - gpos) < 200:
                    ref_lines.append({
                        'label': label,
                        'coord_mm': round(gpos, 1),
                        'axis': 'Y',
                        'type': 'GRID',
                    })
                    break
        else:
            parent = _find_parent_grid(pos, grid_y_sorted)
            if parent:
                ref_y_count[parent] = ref_y_count.get(parent, 0) + 1
                label = f'{parent}-{ref_y_count[parent]}'
            else:
                ref_y_count['Y0'] = ref_y_count.get('Y0', 0) + 1
                label = f'Y0-{ref_y_count["Y0"]}'
            ref_lines.append({
                'label': label,
                'coord_mm': round(pos, 1),
                'axis': 'Y',
                'type': 'REF',
            })

    # Sort by axis then coordinate
    ref_lines.sort(key=lambda r: (r['axis'], r['coord_mm']))

    n_grid = sum(1 for r in ref_lines if r['type'] == 'GRID')
    n_ref = sum(1 for r in ref_lines if r['type'] == 'REF')
    print(f'[RefLines] {n_grid} grid lines + {n_ref} reference lines = {len(ref_lines)} total')

    return ref_lines


def assign_member_refs(df, ref_lines: list, direction_col='direction',
                       x_col='x_from_mm', y_col='y_from_mm',
                       tolerance: float = 200.0):
    """
    Assign x_ref and y_ref labels to a member DataFrame.

    For X-direction members: assign y_ref from Y-coordinate
    For Y-direction members: assign x_ref from X-coordinate
    Columns/footings: assign both x_ref and y_ref

    Args:
        df: member DataFrame
        ref_lines: list of {label, coord_mm, axis, type}
        direction_col: column name for direction ('X'/'Y'), or None for point members
        x_col: column name for X coordinate
        y_col: column name for Y coordinate
        tolerance: matching tolerance (mm)
    """
    import pandas as pd

    # Build lookup: axis → sorted list of (coord, label)
    x_refs = [(r['coord_mm'], r['label']) for r in ref_lines if r['axis'] == 'X']
    y_refs = [(r['coord_mm'], r['label']) for r in ref_lines if r['axis'] == 'Y']
    x_refs.sort()
    y_refs.sort()

    def _find_ref(val, refs, tol):
        best_label = ''
        best_dist = tol + 1
        for coord, label in refs:
            d = abs(val - coord)
            if d < best_dist:
                best_dist = d
                best_label = label
        return best_label if best_dist <= tol else ''

    x_ref_list = []
    y_ref_list = []

    x_to_col = x_col.replace('from', 'to') if 'from' in x_col else None
    y_to_col = y_col.replace('from', 'to') if 'from' in y_col else None

    for _, row in df.iterrows():
        x = float(row.get(x_col, 0) or 0)
        y = float(row.get(y_col, 0) or 0)

        # Determine direction
        direction = ''
        if direction_col and direction_col in df.columns:
            direction = str(row.get(direction_col, ''))

        # Derive direction from coordinates if not available
        x2 = float(row.get(x_to_col, 0) or 0) if x_to_col and x_to_col in df.columns else 0
        y2 = float(row.get(y_to_col, 0) or 0) if y_to_col and y_to_col in df.columns else 0
        has_endpoints = (x2 != 0 or y2 != 0) and (x != x2 or y != y2)

        if direction not in ('X', 'Y') and has_endpoints:
            dx = abs(x2 - x)
            dy = abs(y2 - y)
            if dx > 0 or dy > 0:
                direction = 'X' if dx >= dy else 'Y'

        is_diagonal = has_endpoints and direction not in ('X', 'Y')
        if not is_diagonal and has_endpoints:
            # Check if truly diagonal (both axes differ significantly)
            dx = abs(x2 - x)
            dy = abs(y2 - y)
            if dx > 200 and dy > 200:
                is_diagonal = True

        if is_diagonal:
            # Diagonal beam — assign to nearest ref on dominant axis
            dx = abs(x2 - x)
            dy = abs(y2 - y)
            avg_x = (x + x2) / 2
            avg_y = (y + y2) / 2
            if dy >= dx:
                yr = _find_ref(avg_y, y_refs, tolerance * 10)
                x_ref_list.append('')
                y_ref_list.append(yr if yr else _find_ref(avg_y, y_refs, float('inf')))
            else:
                xr = _find_ref(avg_x, x_refs, tolerance * 10)
                x_ref_list.append(xr if xr else _find_ref(avg_x, x_refs, float('inf')))
                y_ref_list.append('')
        elif direction == 'X':
            x_ref_list.append('')
            y_ref_list.append(_find_ref(y, y_refs, tolerance))
        elif direction == 'Y':
            x_ref_list.append(_find_ref(x, x_refs, tolerance))
            y_ref_list.append('')
        elif has_endpoints:
            # Should not reach here, but fallback
            x_ref_list.append(_find_ref(x, x_refs, tolerance))
            y_ref_list.append(_find_ref(y, y_refs, tolerance))
        else:
            # Point members (columns, footings) — assign both
            x_ref_list.append(_find_ref(x, x_refs, tolerance))
            y_ref_list.append(_find_ref(y, y_refs, tolerance))

    df['x_ref'] = x_ref_list
    df['y_ref'] = y_ref_list


def grid_positions_to_spacing(grid_lines: list) -> list:
    """
    Convert absolute grid positions to (label, spacing) format
    for compatibility with nodes.compute_grid_positions().

    [(X1, 0), (X2, 6000), (X3, 12000)] → [(X1, 0), (X2, 6000), (X3, 6000)]

    The first entry has spacing = 0 (origin).
    """
    if not grid_lines:
        return []

    result = [(grid_lines[0][0], 0)]  # first line: spacing = 0
    for i in range(1, len(grid_lines)):
        label = grid_lines[i][0]
        spacing = grid_lines[i][1] - grid_lines[i-1][1]
        result.append((label, spacing))

    return result


def reassign_node_grids(
    nodes_df,
    grid_x: list,
    grid_y: list,
    tolerance: float = 50.0,
) -> None:
    """
    Reassign grid labels and offsets to nodes using detected grid lines.
    Modifies nodes_df in-place.

    Args:
        nodes_df: DataFrame with columns [x_mm, y_mm, grid, grid_offset_x_mm, grid_offset_y_mm]
        grid_x: list of (label, position_mm)
        grid_y: list of (label, position_mm)
        tolerance: snap tolerance (mm)
    """
    from converters.nodes import find_nearest

    on_grid = 0
    off_grid = 0

    for idx, row in nodes_df.iterrows():
        x = float(row['x_mm'])
        y = float(row['y_mm'])

        x_label, x_offset = find_nearest(x, grid_x, tolerance)
        y_label, y_offset = find_nearest(y, grid_y, tolerance)

        if x_label and y_label:
            nodes_df.at[idx, 'grid'] = f'{x_label}{y_label}'
            nodes_df.at[idx, 'node_id'] = f"N_{row['level']}_{x_label}{y_label}"
            on_grid += 1
        else:
            nodes_df.at[idx, 'grid'] = 'OFF_GRID'
            off_grid += 1

        nodes_df.at[idx, 'grid_offset_x_mm'] = x_offset
        nodes_df.at[idx, 'grid_offset_y_mm'] = y_offset

    print(f'[GridDetect] Reassigned: {on_grid} on-grid, {off_grid} off-grid')
