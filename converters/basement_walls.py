"""
Basement wall converter — transforms Part C data (BasementWall Boundary +
BasementWall Reinforcement) into separate output files.

Output:
    MembersBasementWall.csv — wall panel geometry (one row per quad panel)
    ReinforcementBasementWall.csv — reinforcement per zone/face/direction

Basement walls have a different pattern from standard walls:
- 3 horizontal zones (Left/Middle/Right) with Interior/Exterior face
- 3 vertical zones (Top/Middle/Bottom) with Interior/Exterior face
- Composite bars (D13+D16@100 = alternating, split into 2 rows)
- Variable thickness across levels
- Panels defined by quad nodes (4 per panel, multiple panels per wall)
"""

import pandas as pd
import re
from parsers.rebar_spec import parse_bar_at_spacing, parse_composite_bar
from parsers.level_normalizer import normalize_level


def convert_basement_walls(
    boundary_df: pd.DataFrame,
    reinforcement_df: pd.DataFrame,
    nodes_df: pd.DataFrame = None,
) -> tuple:
    """
    Convert basement wall data into MembersBasementWall and
    ReinforcementBasementWall.

    Args:
        boundary_df: DataFrame from BasementWall Boundary sheet
        reinforcement_df: DataFrame from BasementWall Reinforcement sheet
        nodes_df: Nodes DataFrame for coordinate lookup (optional)

    Returns:
        tuple: (members_df, reinforcement_df)
    """

    # ── Node coordinate lookup ──
    node_coords = {}
    if nodes_df is not None:
        for _, row in nodes_df.iterrows():
            node_coords[int(row['node_number'])] = {
                'x_mm': float(row['x_mm']),
                'y_mm': float(row['y_mm']),
                'z_mm': float(row['z_mm']),
            }

    # ── Parse boundary ──
    bcols = boundary_df.columns.tolist()
    std_cols = ['node', 'name', 'position', 'length_mm', 'height_mm',
                'left_mm', 'middle_mm', 'right_mm',
                'top_mm', 'middle2_mm', 'bottom_mm']
    boundary_df.columns = std_cols[:len(bcols)]

    # Group boundary by wall name + level, collect nodes
    wall_entries = {}
    for _, row in boundary_df.iterrows():
        name = str(row['name']).strip()
        level = normalize_level(str(row['position']).strip())
        node_id = int(row['node']) if pd.notna(row.get('node')) else None

        if not name or name == 'nan':
            continue

        key = (name, level)
        if key not in wall_entries:
            wall_entries[key] = {
                'name': name,
                'level': level,
                'length_mm': _safe_float(row.get('length_mm')),
                'height_mm': _safe_float(row.get('height_mm')),
                'zone_width_left_mm': _safe_float(row.get('left_mm')),
                'zone_width_middle_mm': _safe_float(row.get('middle_mm')),
                'zone_width_right_mm': _safe_float(row.get('right_mm')),
                'zone_height_top_mm': _safe_float(row.get('top_mm')),
                'zone_height_middle_mm': _safe_float(row.get('middle2_mm')),
                'zone_height_bottom_mm': _safe_float(row.get('bottom_mm')),
                'nodes': [],
            }
        if node_id:
            wall_entries[key]['nodes'].append(node_id)

    # ── Build MembersBasementWall: one row per quad panel ──
    members = []
    for (name, level), entry in wall_entries.items():
        nodes = entry['nodes']

        # Split into panels: 4 nodes per panel
        if len(nodes) >= 4 and len(nodes) % 4 == 0:
            panels = [nodes[i:i+4] for i in range(0, len(nodes), 4)]
        elif len(nodes) >= 3:
            panels = [nodes]  # polygon, keep as one
        else:
            continue

        for pi, panel_nodes in enumerate(panels, start=1):
            # Get coordinates from nodes
            coords = [node_coords.get(n) for n in panel_nodes if n in node_coords]

            if coords:
                xs = [c['x_mm'] for c in coords]
                ys = [c['y_mm'] for c in coords]
                zs = [c['z_mm'] for c in coords]
                centroid_x = round(sum(xs) / len(xs), 1)
                centroid_y = round(sum(ys) / len(ys), 1)
                z_mm = round(sum(zs) / len(zs), 1)
            else:
                centroid_x = centroid_y = z_mm = None

            members.append({
                'wall_mark': name,
                'level': level,
                'panel_no': pi,
                'wall_type': None,  # filled from reinforcement below
                'thickness_mm': None,  # filled from reinforcement below
                'length_mm': entry['length_mm'],
                'height_mm': entry['height_mm'],
                'zone_width_left_mm': entry['zone_width_left_mm'],
                'zone_width_middle_mm': entry['zone_width_middle_mm'],
                'zone_width_right_mm': entry['zone_width_right_mm'],
                'zone_height_top_mm': entry['zone_height_top_mm'],
                'zone_height_middle_mm': entry['zone_height_middle_mm'],
                'zone_height_bottom_mm': entry['zone_height_bottom_mm'],
                'node_i': panel_nodes[0] if len(panel_nodes) > 0 else None,
                'node_j': panel_nodes[1] if len(panel_nodes) > 1 else None,
                'node_k': panel_nodes[2] if len(panel_nodes) > 2 else None,
                'node_l': panel_nodes[3] if len(panel_nodes) > 3 else None,
                'centroid_x_mm': centroid_x,
                'centroid_y_mm': centroid_y,
                'z_mm': z_mm,
            })

    # ── Parse reinforcement ──
    rcols = reinforcement_df.columns.tolist()
    col_map = {}
    for col in rcols:
        cl = col.strip().lower()
        if cl == 'name':
            col_map[col] = 'name'
        elif cl == 'position':
            col_map[col] = 'position'
        elif cl == 'typ':
            col_map[col] = 'wall_type'
        elif 'thk' in cl:
            col_map[col] = 'thickness_mm'
        elif 'h_int' in cl and 'left' in cl:
            col_map[col] = 'h_int_left'
        elif 'h_ext' in cl and 'left' in cl:
            col_map[col] = 'h_ext_left'
        elif 'h_int' in cl and 'middle' in cl:
            col_map[col] = 'h_int_middle'
        elif 'h_ext' in cl and 'middle' in cl:
            col_map[col] = 'h_ext_middle'
        elif 'h_int' in cl and 'right' in cl:
            col_map[col] = 'h_int_right'
        elif 'h_ext' in cl and 'right' in cl:
            col_map[col] = 'h_ext_right'
        elif 'v' in cl and 'int' in cl and 'top' in cl:
            col_map[col] = 'v_int_top'
        elif 'v' in cl and 'ext' in cl and 'top' in cl:
            col_map[col] = 'v_ext_top'
        elif 'v' in cl and 'int' in cl and 'middle' in cl:
            col_map[col] = 'v_int_middle'
        elif 'v' in cl and 'ext' in cl and 'middle' in cl:
            col_map[col] = 'v_ext_middle'
        elif 'v' in cl and 'int' in cl and 'bottom' in cl:
            col_map[col] = 'v_int_bottom'
        elif 'v' in cl and 'ext' in cl and 'bottom' in cl:
            col_map[col] = 'v_ext_bottom'
    reinforcement_df = reinforcement_df.rename(columns=col_map)

    # Build reinforcement lookup for filling member thickness/type
    reinf_lookup = {}
    for _, row in reinforcement_df.iterrows():
        name = str(row.get('name', '')).strip()
        level = normalize_level(str(row.get('position', '')).strip())
        if name and name != 'nan':
            reinf_lookup[(name, level)] = {
                'thickness_mm': _safe_float(row.get('thickness_mm')),
                'wall_type': str(row.get('wall_type', '')).strip(),
            }

    # Fill thickness and wall_type in members from reinforcement
    for m in members:
        key = (m['wall_mark'], m['level'])
        info = reinf_lookup.get(key)
        if info:
            m['thickness_mm'] = info['thickness_mm']
            m['wall_type'] = info['wall_type']

    members_df = pd.DataFrame(members)

    # ── Build ReinforcementBasementWall ──
    rebar_positions = [
        ('h_int_left', 'HORIZONTAL', 'INTERIOR', 'LEFT'),
        ('h_ext_left', 'HORIZONTAL', 'EXTERIOR', 'LEFT'),
        ('h_int_middle', 'HORIZONTAL', 'INTERIOR', 'MIDDLE'),
        ('h_ext_middle', 'HORIZONTAL', 'EXTERIOR', 'MIDDLE'),
        ('h_int_right', 'HORIZONTAL', 'INTERIOR', 'RIGHT'),
        ('h_ext_right', 'HORIZONTAL', 'EXTERIOR', 'RIGHT'),
        ('v_int_top', 'VERTICAL', 'INTERIOR', 'TOP'),
        ('v_ext_top', 'VERTICAL', 'EXTERIOR', 'TOP'),
        ('v_int_middle', 'VERTICAL', 'INTERIOR', 'MIDDLE'),
        ('v_ext_middle', 'VERTICAL', 'EXTERIOR', 'MIDDLE'),
        ('v_int_bottom', 'VERTICAL', 'INTERIOR', 'BOTTOM'),
        ('v_ext_bottom', 'VERTICAL', 'EXTERIOR', 'BOTTOM'),
    ]

    reinf_rows = []
    composite_count = 0

    for _, row in reinforcement_df.iterrows():
        name = str(row.get('name', '')).strip()
        level = normalize_level(str(row.get('position', '')).strip())
        thickness = _safe_float(row.get('thickness_mm'))
        wall_type = str(row.get('wall_type', '')).strip()

        if not name or name == 'nan':
            continue

        for col_name, direction, face, zone in rebar_positions:
            spec_str = str(row.get(col_name, '')).strip()
            if not spec_str or spec_str == 'nan':
                continue

            if '+' in spec_str:
                # Composite bar: D13+D16@100 → 2 rows with doubled spacing
                spacing_match = re.search(r'@(\d+)', spec_str)
                spacing = int(spacing_match.group(1)) if spacing_match else None
                composite = parse_composite_bar(spec_str)

                for bar in composite:
                    doubled = spacing * 2 if spacing else None
                    reinf_rows.append({
                        'wall_mark': name,
                        'level': level,
                        'wall_type': wall_type,
                        'thickness_mm': thickness,
                        'direction': direction,
                        'face': face,
                        'zone': zone,
                        'bar_spec': f"D{bar['dia']}@{doubled}" if doubled else spec_str,
                        'dia_mm': bar['dia'],
                        'spacing_mm': doubled,
                    })
                composite_count += 1
            else:
                bar = parse_bar_at_spacing(spec_str)
                if bar:
                    reinf_rows.append({
                        'wall_mark': name,
                        'level': level,
                        'wall_type': wall_type,
                        'thickness_mm': thickness,
                        'direction': direction,
                        'face': face,
                        'zone': zone,
                        'bar_spec': spec_str,
                        'dia_mm': bar['dia'],
                        'spacing_mm': bar['spacing'],
                    })

    reinf_result_df = pd.DataFrame(reinf_rows)

    print(f'[BasementWall] {len(members_df)} wall panels, '
          f'{len(reinf_result_df)} reinforcement rows '
          f'({composite_count} composite bars split)')

    return members_df, reinf_result_df


def _safe_float(val):
    if val is None:
        return None
    try:
        v = float(val)
        return v if not pd.isna(v) else None
    except (ValueError, TypeError):
        return None
