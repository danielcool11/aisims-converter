"""
Slabs converter — transforms SlabBoundary + SlabReinforcement into
MembersSlab.csv.

Input:  SlabBoundary.csv (NO, Load_Type, Nodes for Loading Area, Slab NO.)
        SlabReinforcement.csv (member_id, position, slab_type, thickness_mm,
                               X_Top, X_Bot, Y_Top, Y_Bot)
        Nodes result (from convert_nodes)
Output: MembersSlab.csv
"""

import pandas as pd
import numpy as np
import re


def convert_slabs(
    boundary_df: pd.DataFrame,
    reinforcement_df: pd.DataFrame,
    nodes_result_df: pd.DataFrame,
) -> tuple:
    """
    Convert slab boundary + reinforcement into standardized MembersSlab.

    Args:
        boundary_df: DataFrame from SlabBoundary.csv
        reinforcement_df: DataFrame from SlabReinforcement.csv
        nodes_result_df: DataFrame from convert_nodes()

    Returns:
        tuple: (slabs_df, stair_boundaries)
            slabs_df: DataFrame for MembersSlab.csv
            stair_boundaries: dict {stair_id: {node_nums, coords, centroid, ...}}
                for stairs.py to look up location info
    """

    # Build node coordinate lookup
    node_lookup = {}
    for _, row in nodes_result_df.iterrows():
        node_lookup[int(row['node_number'])] = {
            'x_mm': float(row['x_mm']),
            'y_mm': float(row['y_mm']),
            'z_mm': float(row['z_mm']),
            'level': str(row['level']),
            'grid': str(row['grid']),
            'node_id': str(row['node_id']),
        }

    # Normalize boundary columns
    boundary_col_map = {}
    for col in boundary_df.columns:
        cl = col.strip().lower().replace(' ', '_')
        if cl == 'no':
            boundary_col_map[col] = 'row_no'
        elif 'nodes' in cl and 'loading' in cl:
            boundary_col_map[col] = 'nodes_str'
        elif 'slab_no' in cl or cl == 'slab_no.':
            boundary_col_map[col] = 'slab_id'
        elif 'description' in cl or cl == 'load_type':
            boundary_col_map[col] = 'description'
    boundary_df = boundary_df.rename(columns=boundary_col_map)

    # Normalize reinforcement columns
    reinf_col_map = {}
    for col in reinforcement_df.columns:
        cl = col.strip().lower().replace('㎜', 'mm').replace('.', '')
        if cl == 'member_id':
            reinf_col_map[col] = 'member_id'
        elif cl == 'position':
            reinf_col_map[col] = 'position'
        elif cl == 'slab_type':
            reinf_col_map[col] = 'slab_type'
        elif 'thickness' in cl:
            reinf_col_map[col] = 'thickness_mm'
    reinforcement_df = reinforcement_df.rename(columns=reinf_col_map)

    # Build slab boundaries: group by slab_id, collect all node polygons
    slab_boundaries = {}
    for _, row in boundary_df.iterrows():
        slab_id = str(row.get('slab_id', '')).strip()
        if not slab_id:
            continue

        nodes_str = str(row.get('nodes_str', ''))
        node_nums = _parse_node_list(nodes_str)

        if slab_id not in slab_boundaries:
            slab_boundaries[slab_id] = {
                'node_nums': node_nums,
                'description': str(row.get('description', '')),
            }
        else:
            # Multiple boundary entries for same slab — use the one with most nodes
            if len(node_nums) > len(slab_boundaries[slab_id]['node_nums']):
                slab_boundaries[slab_id]['node_nums'] = node_nums

    # Build reinforcement lookup
    reinf_lookup = {}
    for _, row in reinforcement_df.iterrows():
        mid = str(row.get('member_id', '')).strip()
        reinf_lookup[mid] = row

    # Generate slab members (exclude stairs — identified by 'SS' in member_id)
    results = []
    stair_ids_skipped = []

    for slab_id, boundary in slab_boundaries.items():
        # Stair entries have 'SS' in their ID (B3SS1, 1SS1, RSS1, etc.)
        if 'SS' in slab_id.upper():
            stair_ids_skipped.append(slab_id)
            continue

        node_nums = boundary['node_nums']

        # Get node coordinates
        coords = []
        for nn in node_nums:
            nd = node_lookup.get(nn)
            if nd:
                coords.append(nd)

        if not coords:
            continue

        # Compute centroid and bounding box
        xs = [c['x_mm'] for c in coords]
        ys = [c['y_mm'] for c in coords]
        zs = [c['z_mm'] for c in coords]
        centroid_x = sum(xs) / len(xs)
        centroid_y = sum(ys) / len(ys)
        centroid_z = sum(zs) / len(zs)

        # Spans (bounding box)
        Lx = max(xs) - min(xs)
        Ly = max(ys) - min(ys)

        # Level from first node
        level = coords[0]['level']

        # Get reinforcement info
        reinf = reinf_lookup.get(slab_id)
        thickness = None
        slab_type = None
        if reinf is not None:
            thickness = float(reinf['thickness_mm']) if pd.notna(reinf.get('thickness_mm')) else None
            slab_type = str(reinf.get('slab_type', '')).strip()

        # Node IDs for boundary polygon
        node_ids = [node_lookup[nn]['node_id'] for nn in node_nums if nn in node_lookup]

        record = {
            'member_id': slab_id,
            'level': level,
            'slab_type': slab_type if slab_type else 'C',
            'thickness_mm': thickness,
            'centroid_x_mm': round(centroid_x, 1),
            'centroid_y_mm': round(centroid_y, 1),
            'z_mm': round(centroid_z, 1),
            'Lx_mm': round(Lx, 1),
            'Ly_mm': round(Ly, 1),
            'boundary_nodes': ','.join(node_ids),
            'node_count': len(node_ids),
        }
        results.append(record)

    result_df = pd.DataFrame(results)

    # Build stair boundary data for stairs.py
    stair_boundary_data = {}
    for stair_id in stair_ids_skipped:
        boundary = slab_boundaries[stair_id]
        node_nums = boundary['node_nums']
        coords = []
        for nn in node_nums:
            nd = node_lookup.get(nn)
            if nd:
                coords.append(nd)
        if coords:
            xs = [c['x_mm'] for c in coords]
            ys = [c['y_mm'] for c in coords]
            zs = [c['z_mm'] for c in coords]
            node_ids = [node_lookup[nn]['node_id'] for nn in node_nums if nn in node_lookup]
            stair_boundary_data[stair_id] = {
                'node_nums': node_nums,
                'centroid_x_mm': round(sum(xs) / len(xs), 1),
                'centroid_y_mm': round(sum(ys) / len(ys), 1),
                'z_mm': round(sum(zs) / len(zs), 1),
                'Lx_mm': round(max(xs) - min(xs), 1),
                'Ly_mm': round(max(ys) - min(ys), 1),
                'level': coords[0]['level'],
                'boundary_nodes': ','.join(node_ids),
            }

    # Log summary
    print(f'[Slabs] {len(result_df)} slab members from '
          f'{len(slab_boundaries)} boundaries, '
          f'{len(reinf_lookup)} reinforcement entries')
    if stair_ids_skipped:
        print(f'[Slabs] {len(stair_ids_skipped)} stair entries excluded: {stair_ids_skipped}')

    return result_df, stair_boundary_data


def _parse_node_list(nodes_str: str) -> list:
    """Parse comma-separated node list: '60, 66, 90, 83' → [60, 66, 90, 83]."""
    nodes = []
    if not nodes_str or nodes_str == 'nan':
        return nodes

    # Remove quotes
    nodes_str = nodes_str.strip().strip('"').strip("'")

    for part in nodes_str.split(','):
        part = part.strip()
        if part:
            try:
                nodes.append(int(part))
            except ValueError:
                pass
    return nodes
