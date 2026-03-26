"""
Basement wall converter — transforms Part C data (BasementWall Boundary +
BasementWall Reinforcement) into MembersWall and ReinforcementWall entries.

Basement walls have a different reinforcement pattern from standard walls:
- 3 horizontal zones (Left/Middle/Right) with Interior/Exterior face
- 3 vertical zones (Top/Middle/Bottom) with Interior/Exterior face
- Composite bars (D13+D16@100 = alternating, split into 2 rows)
- Variable thickness across zones

Input:  Part C Excel or CSVs:
        BasementWall Boundary (Node, NAME, Position, Length, Height,
                               Left_mm, Middle_mm, Right_mm,
                               Top_mm, Middle_mm, Bottom_mm)
        BasementWall Reinforcement (NAME, Position, TYP, THK,
                               H_Int/Ext per zone, V_Int/Ext per zone)
Output: Appended to MembersWall.csv and ReinforcementWall.csv
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
    Convert basement wall data into MembersWall and ReinforcementWall rows.

    Args:
        boundary_df: DataFrame from BasementWall Boundary sheet
        reinforcement_df: DataFrame from BasementWall Reinforcement sheet
        nodes_df: Nodes DataFrame for coordinate lookup (optional)

    Returns:
        tuple: (members_df, reinforcement_df)
    """

    # ── Parse boundary ──
    # Normalize columns
    bcols = boundary_df.columns.tolist()
    boundary_df.columns = ['node', 'name', 'position', 'length_mm', 'height_mm',
                           'left_mm', 'middle_mm', 'right_mm',
                           'top_mm', 'middle2_mm', 'bottom_mm'][:len(bcols)]

    # Build node coordinate lookup
    node_coords = {}
    if nodes_df is not None:
        for _, row in nodes_df.iterrows():
            node_coords[int(row['node_number'])] = {
                'x_mm': float(row['x_mm']),
                'y_mm': float(row['y_mm']),
                'z_mm': float(row['z_mm']),
            }

    # Group boundary by wall name and level
    wall_boundaries = {}
    for _, row in boundary_df.iterrows():
        name = str(row['name']).strip()
        level = normalize_level(str(row['position']).strip())
        node_id = int(row['node']) if pd.notna(row['node']) else None

        key = (name, level)
        if key not in wall_boundaries:
            wall_boundaries[key] = {
                'name': name,
                'level': level,
                'length_mm': float(row['length_mm']) if pd.notna(row.get('length_mm')) else None,
                'height_mm': float(row['height_mm']) if pd.notna(row.get('height_mm')) else None,
                'nodes': [],
                'zone_widths': {
                    'left': float(row['left_mm']) if pd.notna(row.get('left_mm')) else None,
                    'middle': float(row['middle_mm']) if pd.notna(row.get('middle_mm')) else None,
                    'right': float(row['right_mm']) if pd.notna(row.get('right_mm')) else None,
                },
                'zone_heights': {
                    'top': float(row['top_mm']) if pd.notna(row.get('top_mm')) else None,
                    'middle': float(row.get('middle2_mm')) if pd.notna(row.get('middle2_mm')) else None,
                    'bottom': float(row.get('bottom_mm')) if pd.notna(row.get('bottom_mm')) else None,
                },
            }
        if node_id:
            wall_boundaries[key]['nodes'].append(node_id)

    # ── Build MembersWall entries ──
    members = []
    for (name, level), wb in wall_boundaries.items():
        # Get coordinates from nodes
        coords = []
        for nid in wb['nodes']:
            nd = node_coords.get(nid)
            if nd:
                coords.append(nd)

        if coords:
            xs = [c['x_mm'] for c in coords]
            ys = [c['y_mm'] for c in coords]
            zs = [c['z_mm'] for c in coords]
            centroid_x = sum(xs) / len(xs)
            centroid_y = sum(ys) / len(ys)
            centroid_z = sum(zs) / len(zs)
        else:
            centroid_x = centroid_y = centroid_z = None

        node_ids = [str(n) for n in wb['nodes']]

        members.append({
            'element_id': None,
            'wall_mark': name,
            'wall_id': name,  # use name as ID for basement walls
            'member_type': 'WALL',
            'wall_type': 'BASEMENT',
            'level': level,
            'centroid_x_mm': round(centroid_x, 1) if centroid_x else None,
            'centroid_y_mm': round(centroid_y, 1) if centroid_y else None,
            'centroid_z_mm': round(centroid_z, 1) if centroid_z else None,
            'thickness_mm': None,  # varies by zone, set from reinforcement
            'height_mm': wb['height_mm'],
            'width_mm': wb['length_mm'],
            'node_i': node_ids[0] if len(node_ids) > 0 else None,
            'node_j': node_ids[1] if len(node_ids) > 1 else None,
            'node_k': node_ids[2] if len(node_ids) > 2 else None,
            'node_l': node_ids[3] if len(node_ids) > 3 else None,
        })

    members_df = pd.DataFrame(members)

    # ── Parse reinforcement ──
    rcols = reinforcement_df.columns.tolist()
    # Standardize column names
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

    # ── Build ReinforcementWall entries ──
    reinf_rows = []

    # Rebar position mappings
    h_positions = [
        ('h_int_left', 'HORIZONTAL', 'INTERIOR', 'LEFT'),
        ('h_ext_left', 'HORIZONTAL', 'EXTERIOR', 'LEFT'),
        ('h_int_middle', 'HORIZONTAL', 'INTERIOR', 'MIDDLE'),
        ('h_ext_middle', 'HORIZONTAL', 'EXTERIOR', 'MIDDLE'),
        ('h_int_right', 'HORIZONTAL', 'INTERIOR', 'RIGHT'),
        ('h_ext_right', 'HORIZONTAL', 'EXTERIOR', 'RIGHT'),
    ]
    v_positions = [
        ('v_int_top', 'VERTICAL', 'INTERIOR', 'TOP'),
        ('v_ext_top', 'VERTICAL', 'EXTERIOR', 'TOP'),
        ('v_int_middle', 'VERTICAL', 'INTERIOR', 'MIDDLE'),
        ('v_ext_middle', 'VERTICAL', 'EXTERIOR', 'MIDDLE'),
        ('v_int_bottom', 'VERTICAL', 'INTERIOR', 'BOTTOM'),
        ('v_ext_bottom', 'VERTICAL', 'EXTERIOR', 'BOTTOM'),
    ]

    for _, row in reinforcement_df.iterrows():
        name = str(row.get('name', '')).strip()
        level = normalize_level(str(row.get('position', '')).strip())
        thickness = row.get('thickness_mm')
        wall_type = str(row.get('wall_type', '')).strip()

        if not name or name == 'nan':
            continue

        for col_name, direction, face, zone in h_positions + v_positions:
            spec_str = str(row.get(col_name, '')).strip()
            if not spec_str or spec_str == 'nan':
                continue

            # Check for composite bars (D13+D16@100, D19+22@100)
            if '+' in spec_str:
                # Extract spacing from the spec
                spacing_match = re.search(r'@(\d+)', spec_str)
                spacing = int(spacing_match.group(1)) if spacing_match else None

                # Parse composite: split into individual bars
                composite = parse_composite_bar(spec_str)
                for bar in composite:
                    doubled_spacing = spacing * 2 if spacing else None
                    reinf_rows.append({
                        'wall_id': name,
                        'wall_mark': name,
                        'level': level,
                        'wall_type': wall_type,
                        'thickness_mm': thickness,
                        'direction': direction,
                        'face': face,
                        'zone': zone,
                        'bar_spec': f"D{bar['dia']}@{doubled_spacing}" if doubled_spacing else spec_str,
                        'dia_mm': bar['dia'],
                        'spacing_mm': doubled_spacing,
                        'bar_layer': 'Double',
                        'source': 'BASEMENT_WALL',
                    })
            else:
                # Simple bar spec
                bar = parse_bar_at_spacing(spec_str)
                if bar:
                    reinf_rows.append({
                        'wall_id': name,
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
                        'bar_layer': 'Double',
                        'source': 'BASEMENT_WALL',
                    })

    reinf_df = pd.DataFrame(reinf_rows)

    # Summary
    n_composite = len([r for r in reinf_rows if '+' in str(r.get('bar_spec', ''))])
    print(f'[BasementWall] {len(members_df)} wall members, '
          f'{len(reinf_df)} reinforcement rows '
          f'({n_composite} from composite bars)')

    return members_df, reinf_df
