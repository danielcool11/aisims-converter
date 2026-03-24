"""
Nodes converter — transforms raw MIDAS nodes into standardized Nodes.csv.

Input:  Nodes.csv (Node, X_mm, Y_mm, Z_mm)
        StoryDefinition.csv (Story Name, Level_mm)
        Grid Definition (from UI or CSV)
Output: Nodes.csv (node_id, x_mm, y_mm, z_mm, level, grid,
                       grid_offset_x_mm, grid_offset_y_mm)
"""

import pandas as pd
import numpy as np


def compute_grid_positions(grid_def: list, origin: float = 0.0) -> list:
    """
    Compute absolute grid positions from spacing list.

    Args:
        grid_def: list of (label, spacing_mm) tuples
        origin: coordinate of first grid line

    Returns:
        list of (label, absolute_position_mm) tuples
    """
    positions = []
    current = origin
    for label, spacing in grid_def:
        positions.append((label, current))
        current += spacing
    return positions


def find_nearest(value: float, positions: list, tolerance: float) -> tuple:
    """
    Find nearest grid/level label within tolerance.

    Args:
        value: coordinate value to match
        positions: list of (label, position) tuples
        tolerance: maximum distance for snapping

    Returns:
        (label, distance_mm) if within tolerance, (None, distance_mm) otherwise.
        distance_mm is the offset from the nearest grid/level line.
    """
    best_label = None
    best_dist = float('inf')

    for label, pos in positions:
        dist = abs(value - pos)
        if dist < best_dist:
            best_dist = dist
            best_label = label

    if best_dist <= tolerance:
        return best_label, round(best_dist, 1)
    return None, round(best_dist, 1) if best_dist != float('inf') else None


def convert_nodes(
    nodes_df: pd.DataFrame,
    story_df: pd.DataFrame,
    grid_x: list = None,
    grid_y: list = None,
    x_origin: float = 0.0,
    y_origin: float = 0.0,
    grid_tolerance: float = 50.0,
    level_tolerance: float = 100.0,
) -> pd.DataFrame:
    """
    Convert raw MIDAS nodes to standardized format.

    Args:
        nodes_df: DataFrame with columns [Node, X_mm, Y_mm, Z_mm]
        story_df: DataFrame with columns [Story Name, Level_mm]
        grid_x: list of (label, spacing_mm) for X-axis, or None
        grid_y: list of (label, spacing_mm) for Y-axis, or None
        x_origin: X coordinate of first X grid line
        y_origin: Y coordinate of first Y grid line
        grid_tolerance: snap tolerance for grid matching (mm)
        level_tolerance: snap tolerance for level matching (mm)

    Returns:
        DataFrame with columns [node_id, x_mm, y_mm, z_mm, level, grid]
    """

    # Normalize column names
    col_map = {}
    for col in nodes_df.columns:
        cl = col.strip().lower().replace('㎜', 'mm').replace(' ', '_')
        if 'node' in cl or cl == 'node':
            col_map[col] = 'node_number'
        elif cl in ('x_mm', 'x'):
            col_map[col] = 'x_mm'
        elif cl in ('y_mm', 'y'):
            col_map[col] = 'y_mm'
        elif cl in ('z_mm', 'z'):
            col_map[col] = 'z_mm'
    nodes_df = nodes_df.rename(columns=col_map)

    # Build level lookup from StoryDefinition
    level_positions = []
    for _, row in story_df.iterrows():
        name = str(row.get('Story Name', row.get('Story_Name', ''))).strip()
        level_val = row.get('Level_mm', row.get('Level_㎜', None))
        if name and level_val is not None and str(level_val).strip() != '':
            level_positions.append((name, float(level_val)))

    # Sort by Z value
    level_positions.sort(key=lambda x: x[1])

    # Build grid positions
    x_positions = compute_grid_positions(grid_x, x_origin) if grid_x else []
    y_positions = compute_grid_positions(grid_y, y_origin) if grid_y else []

    # Process each node
    results = []
    on_grid_count = 0
    off_grid_count = 0

    for _, row in nodes_df.iterrows():
        node_number = int(row['node_number'])
        x = float(row['x_mm'])
        y = float(row['y_mm'])
        z = float(row['z_mm'])

        # Match Z to level
        level, _ = find_nearest(z, level_positions, level_tolerance)
        if level is None:
            level = f'Z{int(z)}'  # fallback: raw Z value

        # Match (X, Y) to grid
        if x_positions:
            grid_x_label, offset_x = find_nearest(x, x_positions, grid_tolerance)
        else:
            grid_x_label, offset_x = None, None

        if y_positions:
            grid_y_label, offset_y = find_nearest(y, y_positions, grid_tolerance)
        else:
            grid_y_label, offset_y = None, None

        if grid_x_label and grid_y_label:
            grid = f'{grid_x_label}{grid_y_label}'
            node_id = f'N_{level}_{grid}'
            on_grid_count += 1
        else:
            grid = 'OFF_GRID'
            node_id = f'N_{level}_OFF{node_number}'
            off_grid_count += 1

        results.append({
            'node_id': node_id,
            'node_number': node_number,
            'x_mm': x,
            'y_mm': y,
            'z_mm': z,
            'level': level,
            'grid': grid,
            'grid_offset_x_mm': offset_x if grid_x_label else offset_x,
            'grid_offset_y_mm': offset_y if grid_y_label else offset_y,
            'source': 'MIDAS',
        })

    result_df = pd.DataFrame(results)

    # Log summary
    print(f'[Nodes] {len(result_df)} nodes: {on_grid_count} on-grid, {off_grid_count} off-grid')
    print(f'[Nodes] Levels: {sorted(set(r["level"] for r in results))}')

    return result_df


def merge_boundary_nodes(
    nodes_df: pd.DataFrame,
    boundary_df: pd.DataFrame,
    source_label: str = 'BOUNDARY',
    level_positions: list = None,
    level_tolerance: float = 100.0,
) -> pd.DataFrame:
    """
    Merge engineer-defined boundary nodes (from FootBoundary, etc.)
    into the main nodes table.

    Skips nodes whose node_number already exists in nodes_df.

    Args:
        nodes_df: existing nodes DataFrame (from convert_nodes)
        boundary_df: DataFrame with columns [NODE, X, Y, Z, ...]
            (positional: col0=node, col1=x, col2=y, col3=z)
        source_label: source tag for these nodes
        level_positions: list of (level_name, z_mm) for level matching
        level_tolerance: tolerance for Z→level matching (mm)

    Returns:
        Updated nodes_df with boundary nodes appended
    """
    existing_numbers = set(nodes_df['node_number'].astype(int).values)

    # Build level lookup from existing nodes if not provided
    if level_positions is None:
        level_positions = []
        for level in nodes_df['level'].unique():
            z_vals = nodes_df[nodes_df['level'] == level]['z_mm']
            if not z_vals.empty:
                level_positions.append((level, float(z_vals.mean())))

    new_rows = []
    added = 0
    skipped = 0

    for _, row in boundary_df.iterrows():
        node_num = int(row.iloc[0])
        if node_num in existing_numbers:
            skipped += 1
            continue

        x = float(row.iloc[1])
        y = float(row.iloc[2])
        z = float(row.iloc[3])

        # Match Z to level
        level, _ = find_nearest(z, level_positions, level_tolerance)
        if level is None:
            level = f'Z{int(z)}'

        node_id = f'N_{level}_BND{node_num}'

        new_rows.append({
            'node_id': node_id,
            'node_number': node_num,
            'x_mm': x,
            'y_mm': y,
            'z_mm': z,
            'level': level,
            'grid': 'OFF_GRID',
            'grid_offset_x_mm': None,
            'grid_offset_y_mm': None,
            'source': source_label,
        })
        existing_numbers.add(node_num)
        added += 1

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        nodes_df = pd.concat([nodes_df, new_df], ignore_index=True)

    print(f'[Nodes] Merged {added} boundary nodes ({skipped} already existed), '
          f'total: {len(nodes_df)}')

    return nodes_df
