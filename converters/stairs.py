"""
Stairs converter — transforms StairReinforcement.csv into MembersStair.csv
with full 8-point U-shaped geometry model.

Input:  StairReinforcement.csv (member_id, level_start, level_end,
        Stair_Height, Stair_Width, Stair_Length,
        landing(Left/Right), rebar specs)
        stair_boundaries (from slabs converter — boundary nodes from SlabBoundary.csv)
        nodes_result_df (for Z elevation lookup)
        walls_df (for determining wall attachment side)
Output: MembersStair.csv (71 columns, 8-point model)
"""

import pandas as pd
import math
from parsers.level_normalizer import normalize_level


def convert_stairs(
    stair_df: pd.DataFrame,
    stair_boundaries: dict = None,
    nodes_result_df: pd.DataFrame = None,
    walls_df: pd.DataFrame = None,
) -> pd.DataFrame:
    """
    Convert stair reinforcement data into standardized MembersStair
    with 8-point U-shaped geometry.

    8-Point Layout (plan view):
        WALL SIDE
        P1 --- P2 -------------------- P5 --- P6
        |      |  Flight1    Flight2    |      |
        |lower |  (wall)     (free)     | mid- |
        |landng|  strip      strip      |landng|
        P4 --- P3 -------------------- P8 --- P7
        FREE SIDE (B = 2 x stair_width + gap)

    P1-P4: Lower landing (z = z_start)
    P5-P8: Mid-landing (z = z_mid = z_start + height/2)
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
        elif 'stair_thickness' in cl:
            col_map[col] = 'waist_thickness_mm'
        elif 'stair_height' in cl:
            col_map[col] = 'total_height_mm'
        elif 'stair_width' in cl:
            col_map[col] = 'stair_width_mm'
        elif 'stair_length' in cl:
            col_map[col] = 'flight_run_mm'
        elif 'landing' in cl and 'left' in cl and 'transverse' not in cl and 'logitudinal' not in cl:
            if 'mm' in cl:
                col_map[col] = 'landing_mid_mm'
        elif 'landing' in cl and 'right' in cl and 'transverse' not in cl and 'logitudinal' not in cl:
            if 'mm' in cl:
                col_map[col] = 'landing_lower_mm'
        elif cl == 'riser_height':
            col_map[col] = 'riser_height_mm'
        elif cl == 'tread_depth' or cl == 'tread_depth_mm':
            col_map[col] = 'tread_depth_mm'
    stair_df = stair_df.rename(columns=col_map)

    # Determine wall attachment side from walls_df
    wall_side = _detect_wall_side(stair_boundaries, walls_df)

    # Build Z lookup from nodes
    level_z = {}
    if nodes_result_df is not None:
        for level in nodes_result_df['level'].unique():
            z_vals = nodes_result_df[nodes_result_df['level'] == level]['z_mm']
            if not z_vals.empty:
                level_z[level] = float(z_vals.mean())

    results = []
    segment_counter = {}

    for _, row in stair_df.iterrows():
        member_id = str(row.get('member_id', '')).strip()
        if not member_id:
            continue

        level_from = normalize_level(str(row.get('level_from', '')).strip())
        level_to = normalize_level(str(row.get('level_to', '')).strip())
        total_height = _safe_float(row.get('total_height_mm'))
        stair_width = _safe_float(row.get('stair_width_mm'))
        flight_run = _safe_float(row.get('flight_run_mm'))
        landing_lower = _safe_float(row.get('landing_lower_mm'))
        landing_mid = _safe_float(row.get('landing_mid_mm'))
        waist_thickness = _safe_float(row.get('waist_thickness_mm'))
        riser_height = _safe_float(row.get('riser_height_mm'))
        tread_depth = _safe_float(row.get('tread_depth_mm'))

        # Segment numbering
        if member_id not in segment_counter:
            segment_counter[member_id] = 1
        else:
            segment_counter[member_id] += 1
        seg_no = segment_counter[member_id]

        # Boundary data
        boundary = stair_boundaries.get(member_id, {})
        centroid_x = boundary.get('centroid_x_mm')
        centroid_y = boundary.get('centroid_y_mm')
        z_mm = boundary.get('z_mm')
        Lx = boundary.get('Lx_mm')
        Ly = boundary.get('Ly_mm')
        boundary_nodes_str = boundary.get('boundary_nodes')
        node_nums = boundary.get('node_nums', [])

        # Gap between flights
        gap = None
        if Lx is not None and stair_width is not None:
            # B (total perpendicular depth) is the shorter boundary dimension
            # Determine which dimension is perpendicular to wall
            B = min(Lx, Ly) if Lx and Ly else None
            if B and stair_width:
                gap = round(B - 2 * stair_width, 1)
                if gap < 0:
                    # Try the other dimension
                    B = max(Lx, Ly) if Lx and Ly else None
                    gap = round(B - 2 * stair_width, 1) if B else None

        # Z elevations
        z_start = level_z.get(level_from) if level_z else z_mm
        z_end = level_z.get(level_to) if level_z else None
        if z_start is None and z_mm is not None:
            z_start = z_mm

        # Validate level_to: if z_start + total_height doesn't match level_to's Z,
        # find the correct level (e.g., 'R' might map to 'Roof' but actual top is '7F')
        if z_start is not None and total_height and level_z:
            expected_z_end = z_start + total_height
            if z_end is None or abs(expected_z_end - z_end) > 500:
                # Find the level whose Z matches the stair top
                best_level = level_to
                best_diff = abs(expected_z_end - z_end) if z_end is not None else float('inf')
                for lv, lz in level_z.items():
                    diff = abs(lz - expected_z_end)
                    if diff < best_diff:
                        best_diff = diff
                        best_level = lv
                if best_level != level_to:
                    level_to = best_level
                    z_end = level_z.get(level_to)

        z_mid = z_start + total_height / 2.0 if z_start is not None and total_height else None

        # Flight sloped length
        flight_slope = None
        if flight_run and total_height:
            half_rise = total_height / 2.0
            flight_slope = round(math.sqrt(flight_run ** 2 + half_rise ** 2), 1)

        # Total length (both flights)
        total_length = round(flight_slope * 2, 1) if flight_slope else None

        # Story group
        story_group = f"{level_from}~{level_to}"

        # Build base record
        record = {
            # Base member columns
            'member_id': member_id,
            'member_type': 'STAIR',
            'level_from': level_from,
            'level_to': level_to,
            'centroid_x_mm': centroid_x,
            'centroid_y_mm': centroid_y,
            'z_mm': z_mm,
            'Lx_mm': Lx,
            'Ly_mm': Ly,
            'boundary_nodes': boundary_nodes_str,
            'length_mm': total_length,
            'story_group': story_group,
            'material_id': 'C35',
            'segment_no': seg_no,
            'segment_id': f"{member_id}-SEG{seg_no:03d}",

            # Stair configuration
            'stair_type': 'U_SHAPED',
            'flight_count': 2,
            'landing_count': 1,
            'total_height_mm': total_height,
            'stair_width_mm': stair_width,
            'flight_run_mm': flight_run,
            'flight_slope_mm': flight_slope,
            'gap_mm': gap,
            'landing_lower_mm': landing_lower,
            'landing_mid_mm': landing_mid,

            # Stair detailing (from design office)
            'waist_thickness_mm': waist_thickness,
            'riser_height_mm': riser_height,
            'tread_depth_mm': tread_depth,
            'num_risers': int(round(total_height / riser_height)) if total_height and riser_height else None,
            'risers_per_flight': int(round(total_height / riser_height / 2)) if total_height and riser_height else None,
        }

        # 8-point model
        points = _compute_8_point_model(
            boundary, wall_side, stair_width, gap, flight_run,
            landing_lower, landing_mid, z_start, z_mid, total_height,
            node_nums, nodes_result_df,
        )

        # Flight centerline inset: _compute_8_point_model places P-points at
        # the boundary edges (wall_x, free_x). The frontend renders each
        # flight centered on its start→end line, extending ±stair_width/2
        # perpendicular. We must inset the flight line by stair_width/2 so
        # the outer edge of each flight aligns with the boundary, not the center.
        flight_inset = stair_width / 2 if stair_width else 0

        if points:
            for i in range(1, 9):
                p = points[f'p{i}']
                record[f'p{i}_x'] = p[0]
                record[f'p{i}_y'] = p[1]
                record[f'p{i}_z'] = p[2]

            # Determine which axis to apply flight inset on
            ws_axis = wall_side['wall_axis'] if wall_side else None
            ws_perp = wall_side['perp_direction'] if wall_side else 1

            # Flight 1: wall-side strip, P2 → P5 (inset from wall edge)
            f1_sx, f1_sy, f1_sz = points['p2']
            f1_ex, f1_ey, f1_ez = points['p5']
            if ws_axis == 'X' and flight_inset:
                # Inset from wall_x toward center
                f1_sx += ws_perp * flight_inset
                f1_ex += ws_perp * flight_inset
            elif ws_axis == 'Y' and flight_inset:
                f1_sy += ws_perp * flight_inset
                f1_ey += ws_perp * flight_inset
            record['flight1_start_x'] = round(f1_sx, 1)
            record['flight1_start_y'] = round(f1_sy, 1)
            record['flight1_start_z'] = round(f1_sz, 1)
            record['flight1_end_x'] = round(f1_ex, 1)
            record['flight1_end_y'] = round(f1_ey, 1)
            record['flight1_end_z'] = round(f1_ez, 1)
            record['flight1_num_risers'] = record['risers_per_flight']

            # Flight 2: free-side strip, P8 → P3 (return direction, going UP)
            # P8 is at z_mid; flight2 rises to z_end (= z_start + total_height)
            z_end = z_start + total_height if z_start is not None and total_height else points['p3'][2]
            f2_sx, f2_sy, f2_sz = points['p8']
            f2_ex, f2_ey = points['p3'][0], points['p3'][1]
            if ws_axis == 'X' and flight_inset:
                # Inset from free_x toward center
                f2_sx -= ws_perp * flight_inset
                f2_ex -= ws_perp * flight_inset
            elif ws_axis == 'Y' and flight_inset:
                f2_sy -= ws_perp * flight_inset
                f2_ey -= ws_perp * flight_inset
            record['flight2_start_x'] = round(f2_sx, 1)
            record['flight2_start_y'] = round(f2_sy, 1)
            record['flight2_start_z'] = round(f2_sz, 1)
            record['flight2_end_x'] = round(f2_ex, 1)
            record['flight2_end_y'] = round(f2_ey, 1)
            record['flight2_end_z'] = z_end
            record['flight2_num_risers'] = record['risers_per_flight']

            # Mid-landing
            record['landing1_start_x'] = points['p5'][0]
            record['landing1_start_y'] = points['p5'][1]
            record['landing1_start_z'] = points['p5'][2]
            record['landing1_length_mm'] = landing_mid
            B_total = 2 * stair_width + gap if stair_width and gap else None
            record['landing1_width_mm'] = B_total
        else:
            # Fill nulls for 8-point model
            for i in range(1, 9):
                record[f'p{i}_x'] = None
                record[f'p{i}_y'] = None
                record[f'p{i}_z'] = None
            for prefix in ['flight1_start', 'flight1_end', 'flight2_start', 'flight2_end', 'landing1_start']:
                record[f'{prefix}_x'] = None
                record[f'{prefix}_y'] = None
                record[f'{prefix}_z'] = None
            record['flight1_num_risers'] = None
            record['flight2_num_risers'] = None
            record['landing1_length_mm'] = landing_mid
            record['landing1_width_mm'] = None

        results.append(record)

    result_df = pd.DataFrame(results)
    matched = sum(1 for r in results if r.get('p1_x') is not None)

    print(f'[Stairs] {len(result_df)} stair members, '
          f'{matched}/{len(result_df)} with 8-point geometry')

    return result_df


def _detect_wall_side(stair_boundaries: dict, walls_df: pd.DataFrame) -> dict:
    """
    Detect which side of the stair boundary attaches to the core wall.

    Returns dict with wall attachment info:
        {wall_axis: 'X' or 'Y', wall_coord: float, perp_direction: +1 or -1}
    """
    if not stair_boundaries:
        return None

    if walls_df is None or walls_df.empty:
        return None

    # Get a representative stair boundary
    sample_id = list(stair_boundaries.keys())[0]
    sample = stair_boundaries[sample_id]
    cx, cy = sample['centroid_x_mm'], sample['centroid_y_mm']

    # Find walls near this stair
    if 'centroid_x_mm' not in walls_df.columns:
        return None

    near = walls_df[
        (walls_df['centroid_x_mm'].between(cx - 5000, cx + 5000)) &
        (walls_df['centroid_y_mm'].between(cy - 5000, cy + 5000))
    ]

    if near.empty:
        return None

    # Find the wall edge closest to the stair boundary
    # Check X-aligned walls (wall runs along Y axis → constant X)
    # and Y-aligned walls (wall runs along X axis → constant Y)
    boundary_nodes = sample.get('node_nums', [])
    if not boundary_nodes:
        return None

    # Use boundary bounding box
    bx_min = cx - sample.get('Lx_mm', 0) / 2
    bx_max = cx + sample.get('Lx_mm', 0) / 2
    by_min = cy - sample.get('Ly_mm', 0) / 2
    by_max = cy + sample.get('Ly_mm', 0) / 2

    # Check which boundary edge has the most wall elements nearby
    edges = {
        'x_min': ('X', bx_min, +1),   # wall at left, stair extends right
        'x_max': ('X', bx_max, -1),   # wall at right, stair extends left
        'y_min': ('Y', by_min, +1),   # wall at bottom, stair extends up
        'y_max': ('Y', by_max, -1),   # wall at top, stair extends down
    }

    best_edge = None
    best_count = 0

    for edge_name, (axis, coord, direction) in edges.items():
        tol = 500  # tolerance for wall proximity
        if axis == 'X':
            count = len(near[near['centroid_x_mm'].between(coord - tol, coord + tol)])
        else:
            count = len(near[near['centroid_y_mm'].between(coord - tol, coord + tol)])
        if count > best_count:
            best_count = count
            best_edge = {'wall_axis': axis, 'wall_coord': coord, 'perp_direction': direction}

    return best_edge


def _compute_8_point_model(
    boundary, wall_side, stair_width, gap, flight_run,
    landing_lower, landing_mid, z_start, z_mid, total_height,
    node_nums, nodes_df,
):
    """
    Compute 8-point stair geometry from boundary and wall attachment.

    Returns dict with p1..p8 as (x, y, z) tuples, or None if insufficient data.
    """
    if not boundary or wall_side is None:
        return None
    if any(v is None for v in [stair_width, gap, flight_run, landing_lower, landing_mid, z_start, z_mid]):
        return None

    cx = boundary.get('centroid_x_mm')
    cy = boundary.get('centroid_y_mm')
    Lx = boundary.get('Lx_mm')
    Ly = boundary.get('Ly_mm')

    if any(v is None for v in [cx, cy, Lx, Ly]):
        return None

    B = 2 * stair_width + gap  # total perpendicular depth

    wall_axis = wall_side['wall_axis']
    wall_coord = wall_side['wall_coord']
    perp_dir = wall_side['perp_direction']

    if wall_axis == 'X':
        # Wall runs along Y (constant X). Stair extends in X direction.
        wall_x = wall_coord
        free_x = wall_x + perp_dir * B

        # Along-wall direction is Y
        # Get actual Y range from boundary nodes
        y_min = cy - Ly / 2
        y_max = cy + Ly / 2

        # Resolve actual boundary X/Y from nodes if available
        if node_nums and nodes_df is not None:
            node_lookup = {}
            for _, r in nodes_df.iterrows():
                node_lookup[int(r['node_number'])] = (float(r['x_mm']), float(r['y_mm']))
            xs = [node_lookup[n][0] for n in node_nums if n in node_lookup]
            ys = [node_lookup[n][1] for n in node_nums if n in node_lookup]
            if ys:
                y_min = min(ys)
                y_max = max(ys)
            # Clamp free_x to actual boundary extent (L-shaped boundaries
            # can have Lx larger than the flight area)
            if xs:
                if perp_dir > 0:
                    free_x = min(free_x, max(xs))
                else:
                    free_x = max(free_x, min(xs))

        # Lower landing: y_min to y_min + A
        A = landing_lower
        C = landing_mid

        # P1-P4 at z_start (lower landing)
        p1 = (round(wall_x, 1), round(y_min, 1), round(z_start, 1))
        p2 = (round(wall_x, 1), round(y_min + A, 1), round(z_start, 1))
        p3 = (round(free_x, 1), round(y_min + A, 1), round(z_start, 1))
        p4 = (round(free_x, 1), round(y_min, 1), round(z_start, 1))

        # P5-P8 at z_mid (mid-landing)
        p5 = (round(wall_x, 1), round(y_min + A + flight_run, 1), round(z_mid, 1))
        p6 = (round(wall_x, 1), round(y_max, 1), round(z_mid, 1))
        p7 = (round(free_x, 1), round(y_max, 1), round(z_mid, 1))
        p8 = (round(free_x, 1), round(y_min + A + flight_run, 1), round(z_mid, 1))

    elif wall_axis == 'Y':
        # Wall runs along X (constant Y). Stair extends in Y direction.
        wall_y = wall_coord
        free_y = wall_y + perp_dir * B

        x_min = cx - Lx / 2
        x_max = cx + Lx / 2

        if node_nums and nodes_df is not None:
            node_lookup = {}
            for _, r in nodes_df.iterrows():
                node_lookup[int(r['node_number'])] = (float(r['x_mm']), float(r['y_mm']))
            xs = [node_lookup[n][0] for n in node_nums if n in node_lookup]
            ys = [node_lookup[n][1] for n in node_nums if n in node_lookup]
            if xs:
                x_min = min(xs)
                x_max = max(xs)
            # Clamp free_y to actual boundary extent
            if ys:
                if perp_dir > 0:
                    free_y = min(free_y, max(ys))
                else:
                    free_y = max(free_y, min(ys))

        A = landing_lower
        C = landing_mid

        p1 = (round(x_min, 1), round(wall_y, 1), round(z_start, 1))
        p2 = (round(x_min + A, 1), round(wall_y, 1), round(z_start, 1))
        p3 = (round(x_min + A, 1), round(free_y, 1), round(z_start, 1))
        p4 = (round(x_min, 1), round(free_y, 1), round(z_start, 1))

        p5 = (round(x_min + A + flight_run, 1), round(wall_y, 1), round(z_mid, 1))
        p6 = (round(x_max, 1), round(wall_y, 1), round(z_mid, 1))
        p7 = (round(x_max, 1), round(free_y, 1), round(z_mid, 1))
        p8 = (round(x_min + A + flight_run, 1), round(free_y, 1), round(z_mid, 1))

    else:
        return None

    return {
        'p1': p1, 'p2': p2, 'p3': p3, 'p4': p4,
        'p5': p5, 'p6': p6, 'p7': p7, 'p8': p8,
    }


def _safe_float(val):
    """Convert to float safely."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
