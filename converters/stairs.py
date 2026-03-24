"""
Stairs converter — transforms StairReinforcement.csv into MembersStair.csv.

Input:  StairReinforcement.csv (member_id, level_start, level_end,
        Stair_Height, Stair_Width, Stair_Length,
        landing(Left/Right), rebar specs)
        stair_boundaries (from slabs converter — boundary nodes from SlabBoundary.csv)
Output: MembersStair.csv
"""

import pandas as pd


def convert_stairs(
    stair_df: pd.DataFrame,
    stair_boundaries: dict = None,
) -> pd.DataFrame:
    """
    Convert stair reinforcement data into standardized MembersStair.

    Args:
        stair_df: DataFrame from StairReinforcement.csv
        stair_boundaries: dict from convert_slabs() with location data
            {stair_id: {centroid_x_mm, centroid_y_mm, z_mm, Lx_mm, Ly_mm,
                        level, boundary_nodes}}

    Returns:
        DataFrame for MembersStair.csv
    """
    if stair_boundaries is None:
        stair_boundaries = {}

    # Normalize column names
    col_map = {}
    for col in stair_df.columns:
        cl = col.strip().lower().replace('㎜', 'mm').replace(' ', '_')
        if cl == 'member_id':
            col_map[col] = 'member_id'
        elif 'level_start' in cl:
            col_map[col] = 'level_from'
        elif 'level_end' in cl:
            col_map[col] = 'level_to'
        elif 'stair_height' in cl:
            col_map[col] = 'stair_height_mm'
        elif 'stair_width' in cl:
            col_map[col] = 'stair_width_mm'
        elif 'stair_length' in cl:
            col_map[col] = 'stair_length_mm'
        elif 'landing' in cl and 'left' in cl and 'transverse' not in cl and 'logitudinal' not in cl:
            if 'mm' in cl:
                col_map[col] = 'landing_left_mm'
        elif 'landing' in cl and 'right' in cl and 'transverse' not in cl and 'logitudinal' not in cl:
            if 'mm' in cl:
                col_map[col] = 'landing_right_mm'
    stair_df = stair_df.rename(columns=col_map)

    results = []
    matched = 0

    for _, row in stair_df.iterrows():
        member_id = str(row.get('member_id', '')).strip()
        if not member_id:
            continue

        record = {
            'member_id': member_id,
            'level_from': str(row.get('level_from', '')).strip(),
            'level_to': str(row.get('level_to', '')).strip(),
            'stair_height_mm': _safe_float(row.get('stair_height_mm')),
            'stair_width_mm': _safe_float(row.get('stair_width_mm')),
            'stair_length_mm': _safe_float(row.get('stair_length_mm')),
            'landing_left_mm': _safe_float(row.get('landing_left_mm')),
            'landing_right_mm': _safe_float(row.get('landing_right_mm')),
            'centroid_x_mm': None,
            'centroid_y_mm': None,
            'z_mm': None,
            'Lx_mm': None,
            'Ly_mm': None,
            'boundary_nodes': None,
        }

        # Merge location from slab boundary
        boundary = stair_boundaries.get(member_id)
        if boundary:
            record['centroid_x_mm'] = boundary['centroid_x_mm']
            record['centroid_y_mm'] = boundary['centroid_y_mm']
            record['z_mm'] = boundary['z_mm']
            record['Lx_mm'] = boundary['Lx_mm']
            record['Ly_mm'] = boundary['Ly_mm']
            record['boundary_nodes'] = boundary['boundary_nodes']
            matched += 1

        results.append(record)

    result_df = pd.DataFrame(results)

    # Log summary
    print(f'[Stairs] {len(result_df)} stair members, '
          f'{matched}/{len(result_df)} matched with boundary location')

    return result_df


def _safe_float(val):
    """Convert to float safely."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
