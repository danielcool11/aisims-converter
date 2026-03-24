"""
Footing converter — transforms FootBoundary + FootReinforcement into
MembersFooting.csv and ReinforcementFooting.csv.

Foundation types in data:
    MF* (slab_type=C): Mat foundation — the actual member (has thickness)
    R*  (slab_type=R): Reinforcement zone — additional bars within a mat
    V*  (slab_type=V): Stirrup zone — shear reinforcement within a mat

Input:  FootBoundary.csv (NODE, X, Y, Z, Foot No., Position)
        FootReinforcement.csv (member_id, position, slab_type, thickness,
                               X_Top, X_Bot, Y_Top, Y_Bot, STR)
Output: MembersFooting.csv (only MF* entries — actual foundation members)
        ReinforcementFooting.csv (all zones: base + additional + stirrup)
"""

import pandas as pd
import math
from parsers.rebar_spec import parse_bar_at_spacing, parse_stirrup
from parsers.level_normalizer import normalize_level


def convert_footings(
    boundary_df: pd.DataFrame,
    reinforcement_df: pd.DataFrame,
) -> tuple:
    """
    Convert footing boundary + reinforcement into standardized outputs.

    Args:
        boundary_df: DataFrame from FootBoundary.csv
        reinforcement_df: DataFrame from FootReinforcement.csv

    Returns:
        tuple: (members_df, reinforcement_df)
            members_df: MembersFooting.csv (only mat foundations MF*)
            reinforcement_df: ReinforcementFooting.csv (all zones expanded)
    """

    # Normalize boundary columns
    # Columns: NODE, X_mm, X_mm.1(Y), X_mm.2(Z), Foot No., Position
    boundary_data = {}
    for _, row in boundary_df.iterrows():
        node_id = int(row.iloc[0])
        x = float(row.iloc[1])
        y = float(row.iloc[2])
        z = float(row.iloc[3])
        foot_no = str(row.iloc[4]).strip()
        level = normalize_level(str(row.iloc[5]).strip())

        if foot_no not in boundary_data:
            boundary_data[foot_no] = {
                'level': level,
                'nodes': [],
                'quads': [],
            }
        boundary_data[foot_no]['nodes'].append((node_id, x, y, z))

    # Group nodes into quads (every 4 nodes)
    for foot_no, data in boundary_data.items():
        nodes = data['nodes']
        quads = []
        for i in range(0, len(nodes), 4):
            quad = nodes[i:i+4]
            if len(quad) == 4:
                quads.append(quad)
        data['quads'] = quads

    # Normalize reinforcement columns
    reinf_col_map = {}
    for col in reinforcement_df.columns:
        cl = col.strip().lower().replace('㎜', 'mm').replace('.', '')
        if cl == 'member_id':
            reinf_col_map[col] = 'member_id'
        elif cl == 'position':
            reinf_col_map[col] = 'position'
        elif cl == 'slab_type':
            reinf_col_map[col] = 'zone_type'
        elif 'thickness' in cl:
            reinf_col_map[col] = 'thickness_mm'
        elif cl == 'x_top':
            reinf_col_map[col] = 'x_top'
        elif cl == 'x_bot':
            reinf_col_map[col] = 'x_bot'
        elif cl == 'y_top':
            reinf_col_map[col] = 'y_top'
        elif cl == 'y_bot':
            reinf_col_map[col] = 'y_bot'
        elif cl == 'str':
            reinf_col_map[col] = 'stirrup_spec'
    reinforcement_df = reinforcement_df.rename(columns=reinf_col_map)

    # Build reinforcement lookup
    reinf_lookup = {}
    for _, row in reinforcement_df.iterrows():
        mid = str(row.get('member_id', '')).strip()
        reinf_lookup[mid] = row

    # ── MembersFooting: one row per quad (MF* entries split by quad) ──
    members = []
    for foot_no, data in boundary_data.items():
        if not foot_no.startswith('MF'):
            continue

        reinf = reinf_lookup.get(foot_no)
        thickness = None
        if reinf is not None:
            thickness = _safe_float(reinf.get('thickness_mm'))

        for qi, quad in enumerate(data['quads'], start=1):
            qx = [q[1] for q in quad]
            qy = [q[2] for q in quad]
            qz = [q[3] for q in quad]

            centroid_x = sum(qx) / len(qx)
            centroid_y = sum(qy) / len(qy)
            z = sum(qz) / len(qz)
            Lx = max(qx) - min(qx)
            Ly = max(qy) - min(qy)
            area = Lx * Ly

            # Use semicolon separator to prevent Excel number interpretation
            node_ids = [str(q[0]) for q in quad]

            part_id = f"{foot_no}-{qi}"

            # Sort quad corners: bottom-left, bottom-right, top-left, top-right
            x_min, x_max = min(qx), max(qx)
            y_min, y_max = min(qy), max(qy)

            members.append({
                'member_id': foot_no,
                'part_id': part_id,
                'member_type': 'FOOTING',
                'footing_type': 'MAT',
                'level': data['level'],
                'thickness_mm': thickness,
                'centroid_x_mm': round(centroid_x, 1),
                'centroid_y_mm': round(centroid_y, 1),
                'z_mm': round(z, 1),
                'Lx_mm': round(Lx, 1),
                'Ly_mm': round(Ly, 1),
                'area_mm2': round(area, 1),
                'x_min_mm': round(x_min, 1),
                'y_min_mm': round(y_min, 1),
                'x_max_mm': round(x_max, 1),
                'y_max_mm': round(y_max, 1),
                'boundary_nodes': ';'.join(node_ids),
                'material_id': 'C35',
                'segment_no': qi,
                'segment_id': f"{foot_no}-SEG{qi:03d}",
            })

    members_df = pd.DataFrame(members)

    # ── Determine which mat each zone belongs to ──
    # Match by Z level: same Z = same mat
    mat_z_map = {}  # z_value → mat member_id
    for m in members:
        mat_z_map[m['z_mm']] = m['member_id']

    # ── ReinforcementFooting: expand all zones ──
    reinf_rows = []

    for foot_no, data in boundary_data.items():
        reinf = reinf_lookup.get(foot_no)
        if reinf is None:
            continue

        level = data['level']
        zone_type_raw = str(reinf.get('zone_type', '')).strip()

        # Determine zone_type and parent mat
        if zone_type_raw == 'C':
            zone_type = 'BASE'
            parent_mat = foot_no
        elif zone_type_raw == 'R':
            zone_type = 'ADDITIONAL'
            # Find parent mat by matching Z level
            sample_z = round(data['nodes'][0][3], 1)
            parent_mat = mat_z_map.get(sample_z, 'MF1')
        elif zone_type_raw == 'V':
            zone_type = 'STIRRUP'
            sample_z = round(data['nodes'][0][3], 1)
            parent_mat = mat_z_map.get(sample_z, 'MF1')
        else:
            zone_type = 'UNKNOWN'
            parent_mat = foot_no

        # Build zone boundary string (all quad nodes)
        zone_nodes = []
        for quad in data['quads']:
            quad_str = ';'.join([f"({q[1]},{q[2]})" for q in quad])
            zone_nodes.append(quad_str)
        zone_boundary = ' | '.join(zone_nodes)

        # Zone bounding box
        all_x = [n[1] for n in data['nodes']]
        all_y = [n[2] for n in data['nodes']]
        zone_x_min = min(all_x)
        zone_x_max = max(all_x)
        zone_y_min = min(all_y)
        zone_y_max = max(all_y)

        if zone_type == 'STIRRUP':
            # Stirrup zone — parse STR column
            str_spec = str(reinf.get('stirrup_spec', '')).strip()
            if str_spec and str_spec != 'nan':
                stirrup = parse_stirrup(str_spec)
                reinf_rows.append({
                    'member_id': parent_mat,
                    'zone': foot_no,
                    'zone_type': zone_type,
                    'direction': None,
                    'layer': None,
                    'bar_spec': str_spec,
                    'dia_mm': stirrup['dia'] if stirrup else None,
                    'spacing_mm': stirrup['spacing'] if stirrup else None,
                    'n_legs': stirrup['legs'] if stirrup else None,
                    'zone_x_min': zone_x_min,
                    'zone_x_max': zone_x_max,
                    'zone_y_min': zone_y_min,
                    'zone_y_max': zone_y_max,
                    'zone_boundary': zone_boundary,
                })
        else:
            # Bar zones — expand X_Top, X_Bot, Y_Top, Y_Bot
            for direction, layer, col_name in [
                ('X', 'Top', 'x_top'),
                ('X', 'Bot', 'x_bot'),
                ('Y', 'Top', 'y_top'),
                ('Y', 'Bot', 'y_bot'),
            ]:
                spec_str = str(reinf.get(col_name, '')).strip()
                if not spec_str or spec_str == 'nan':
                    continue

                bar = parse_bar_at_spacing(spec_str)
                reinf_rows.append({
                    'member_id': parent_mat,
                    'zone': foot_no,
                    'zone_type': zone_type,
                    'direction': direction,
                    'layer': layer,
                    'bar_spec': spec_str,
                    'dia_mm': bar['dia'] if bar else None,
                    'spacing_mm': bar['spacing'] if bar else None,
                    'n_legs': None,
                    'zone_x_min': zone_x_min,
                    'zone_x_max': zone_x_max,
                    'zone_y_min': zone_y_min,
                    'zone_y_max': zone_y_max,
                    'zone_boundary': zone_boundary,
                })

    reinf_result_df = pd.DataFrame(reinf_rows)

    # Log summary
    base_count = len([r for r in reinf_rows if r['zone_type'] == 'BASE'])
    add_count = len([r for r in reinf_rows if r['zone_type'] == 'ADDITIONAL'])
    stir_count = len([r for r in reinf_rows if r['zone_type'] == 'STIRRUP'])
    print(f'[Footings] {len(members_df)} mat foundations, '
          f'{len(reinf_result_df)} reinforcement rows '
          f'({base_count} base, {add_count} additional, {stir_count} stirrup)')

    return members_df, reinf_result_df


def _safe_float(val):
    if val is None:
        return None
    try:
        v = float(val)
        return v if not pd.isna(v) else None
    except (ValueError, TypeError):
        return None
