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
    cluster_tolerance: float = 100.0,
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
