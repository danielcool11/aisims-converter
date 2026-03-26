"""
Elements converter — transforms raw MIDAS elements into
MembersBeam.csv, MembersColumn.csv, MembersWall.csv.

Input:  Elements.csv (Element, Type, Wall Type, Sub Type, Wall ID,
                      Material, Property, B-Angle, Node1..Node8)
        Nodes result (node_id, node_number, x_mm, y_mm, z_mm, level, grid)
        Section lookup (from sections converter)
        Thickness lookup (from sections converter)
        Wall marks (from MGT parser)
Output: MembersBeam.csv, MembersColumn.csv, MembersWall.csv

Polymorphic Property FK:
    Type=BEAM → Property references Sections table (section number)
    Type=WALL → Property references Thickness table (thickness ID)
"""

import pandas as pd
import numpy as np
import math


def _compute_length(x1, y1, z1, x2, y2, z2):
    """Euclidean distance between two 3D points (mm)."""
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2)


def _is_vertical(x1, y1, z1, x2, y2, z2, angle_threshold=15.0):
    """Check if element is predominantly vertical (column-like)."""
    dx = abs(x2 - x1)
    dy = abs(y2 - y1)
    dz = abs(z2 - z1)
    horizontal = math.sqrt(dx ** 2 + dy ** 2)
    if dz == 0:
        return False
    angle_from_vertical = math.degrees(math.atan2(horizontal, dz))
    return angle_from_vertical < angle_threshold


def _wall_centroid(nodes_coords):
    """Compute centroid of wall quad nodes."""
    xs = [c[0] for c in nodes_coords]
    ys = [c[1] for c in nodes_coords]
    zs = [c[2] for c in nodes_coords]
    n = len(nodes_coords)
    return sum(xs) / n, sum(ys) / n, sum(zs) / n


def convert_elements(
    elements_df: pd.DataFrame,
    nodes_result_df: pd.DataFrame,
    section_lookup: dict,
    thickness_lookup: dict,
    wall_marks: dict = None,
) -> dict:
    """
    Convert raw MIDAS elements to standardized member CSVs.

    Args:
        elements_df: DataFrame from Elements.csv
        nodes_result_df: DataFrame from convert_nodes() output
        section_lookup: {section_number: {section_id, member_id, member_type, b_mm, h_mm}}
        thickness_lookup: {thickness_id: thickness_mm}
        wall_marks: {mark_name: [wall_id_list]} from MGT

    Returns:
        dict with keys 'beams', 'columns', 'walls' → DataFrames
    """

    # Build node coordinate lookup: node_number → (x, y, z, level, grid, node_id)
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

    # Reverse wall marks: wall_id → mark_name
    wall_mark_lookup = {}
    if wall_marks:
        for mark_name, wid_list in wall_marks.items():
            for wid in wid_list:
                wall_mark_lookup[wid] = mark_name

    # Normalize element column names
    col_map = {}
    for col in elements_df.columns:
        cl = col.strip().lower().replace(' ', '_').replace('[', '').replace(']', '')
        if cl == 'element':
            col_map[col] = 'element_id'
        elif cl == 'type':
            col_map[col] = 'elem_type'
        elif cl == 'wall_type':
            col_map[col] = 'wall_type'
        elif cl == 'sub_type':
            col_map[col] = 'sub_type'
        elif cl == 'wall_id':
            col_map[col] = 'wall_id'
        elif cl == 'material':
            col_map[col] = 'material_id'
        elif cl == 'property':
            col_map[col] = 'property_id'
        elif 'angle' in cl:
            col_map[col] = 'b_angle'
        elif cl.startswith('node'):
            # node1..node8
            col_map[col] = cl
    elements_df = elements_df.rename(columns=col_map)

    beams = []
    columns = []
    walls = []

    reclassified_count = 0

    for _, row in elements_df.iterrows():
        elem_id = int(row['element_id'])
        elem_type = str(row['elem_type']).strip().upper()
        prop_id = str(row['property_id']).strip()

        if elem_type == 'BEAM':
            # Polymorphic FK: Property should reference Sections table.
            # If not found in Sections but found in Thickness, check geometry:
            #   - vertical (dZ > 0) → reclassify as wall
            #   - horizontal (dZ = 0) → keep as beam (link beam with thickness)
            if prop_id not in section_lookup and prop_id in thickness_lookup:
                n1 = int(row.get('node1', 0))
                n2 = int(row.get('node2', 0))
                nd1 = node_lookup.get(n1)
                nd2 = node_lookup.get(n2)
                if nd1 and nd2:
                    dz = abs(nd2['z_mm'] - nd1['z_mm'])
                    if dz > 0:
                        # Vertical + thickness property → wall
                        _process_wall_element(
                            elem_id, row, prop_id, node_lookup, thickness_lookup,
                            wall_mark_lookup, walls
                        )
                        reclassified_count += 1
                        continue
                    # Horizontal + thickness property → link beam
                    # Use per-element key so each gets its own element ID
                    thickness_mm = thickness_lookup[prop_id]
                    elem_key = f'_elem_{elem_id}'
                    section_lookup[elem_key] = {
                        'section_id': f'ELEM_{elem_id}',
                        'member_id': f'ELEM_{elem_id}',
                        'member_type': 'BEAM',
                        'b_mm': thickness_mm,
                        'h_mm': None,
                        'raw_name': f'Element {elem_id} (Thickness={prop_id})',
                    }
                    reclassified_count += 1
                    _process_beam_element(
                        elem_id, row, elem_key, node_lookup, section_lookup,
                        beams, columns
                    )
                    continue

            _process_beam_element(
                elem_id, row, prop_id, node_lookup, section_lookup,
                beams, columns
            )
        elif elem_type == 'WALL':
            _process_wall_element(
                elem_id, row, prop_id, node_lookup, thickness_lookup,
                wall_mark_lookup, walls
            )

    beams_df = pd.DataFrame(beams)
    columns_df = pd.DataFrame(columns)
    walls_df = pd.DataFrame(walls)

    # Log summary
    print(f'[Elements] {len(beams_df)} beams, {len(columns_df)} columns, '
          f'{len(walls_df)} walls from {len(elements_df)} elements')
    if reclassified_count:
        print(f'[Elements] {reclassified_count} elements reclassified '
              f'(BEAM with thickness property)')

    return {
        'beams': beams_df,
        'columns': columns_df,
        'walls': walls_df,
    }


def _process_beam_element(elem_id, row, prop_id, node_lookup, section_lookup,
                          beams_list, columns_list):
    """Process a BEAM-type element (could be beam or column based on section)."""
    n1 = int(row.get('node1', 0))
    n2 = int(row.get('node2', 0))

    if n1 == 0 or n2 == 0:
        return

    nd1 = node_lookup.get(n1)
    nd2 = node_lookup.get(n2)
    if not nd1 or not nd2:
        return

    # Look up section info
    sec_info = section_lookup.get(prop_id, {})
    member_type = sec_info.get('member_type', 'BEAM')
    member_id = sec_info.get('member_id', f'UNK{prop_id}')
    section_id = sec_info.get('section_id', f'SEC_{prop_id}')

    # Coordinates
    x1, y1, z1 = nd1['x_mm'], nd1['y_mm'], nd1['z_mm']
    x2, y2, z2 = nd2['x_mm'], nd2['y_mm'], nd2['z_mm']

    # Length
    length = round(_compute_length(x1, y1, z1, x2, y2, z2), 1)

    # Determine level — use the lower node's level for beams,
    # lower for columns (level_from)
    if z1 <= z2:
        level_from = nd1['level']
        level_to = nd2['level']
        grid_from = nd1['grid']
        grid_to = nd2['grid']
        node_from = nd1['node_id']
        node_to = nd2['node_id']
    else:
        level_from = nd2['level']
        level_to = nd1['level']
        grid_from = nd2['grid']
        grid_to = nd1['grid']
        node_from = nd2['node_id']
        node_to = nd1['node_id']

    # Check orientation to distinguish beam from column
    is_vert = _is_vertical(x1, y1, z1, x2, y2, z2)

    # Resolve: section says COLUMN + element is vertical → column
    #          section says BEAM + element is horizontal → beam
    #          section says WALL (BT) + vertical → wall-like but stays in its category
    if member_type == 'COLUMN' or (is_vert and member_type != 'BEAM'):
        height = abs(z2 - z1)
        length_3d = _compute_length(x1, y1, z1, x2, y2, z2)
        # Bottom node coordinates
        bx = x1 if z1 <= z2 else x2
        by = y1 if z1 <= z2 else y2
        # Top node coordinates
        tx = x2 if z1 <= z2 else x1
        ty = y2 if z1 <= z2 else y1
        record = {
            'element_id': elem_id,
            'member_id': member_id,
            'section_id': section_id,
            'design_key': sec_info.get('raw_name', ''),
            'node_from': node_from,
            'node_to': node_to,
            'level_from': level_from,
            'level_to': level_to,
            'grid': grid_from,  # column grid = bottom node grid
            'x_mm': bx,
            'y_mm': by,
            'x_top_mm': tx,
            'y_top_mm': ty,
            'height_mm': round(height, 1),
            'length_mm': round(length_3d, 1),
            'b_mm': sec_info.get('b_mm'),
            'h_mm': sec_info.get('h_mm'),
        }
        columns_list.append(record)

    elif member_type == 'WALL' and is_vert:
        # BT (buttress) — modeled as frame but classified as wall
        # Still add as wall with limited geometry
        record = {
            'element_id': elem_id,
            'wall_mark': f'BT_{member_id}',
            'level': level_from,
            'node_i': node_from,
            'node_j': node_to,
            'centroid_x_mm': round((x1 + x2) / 2, 1),
            'centroid_y_mm': round((y1 + y2) / 2, 1),
            'centroid_z_mm': round((z1 + z2) / 2, 1),
            'thickness_mm': sec_info.get('b_mm'),
            'height_mm': round(abs(z2 - z1), 1),
            'width_mm': None,
        }
        # walls_list.append(record)  # Uncomment if BT should be in walls
        # For now, BT goes to columns (it's modeled as a frame element)
        bt_height = abs(z2 - z1)
        bt_length = _compute_length(x1, y1, z1, x2, y2, z2)
        record_col = {
            'element_id': elem_id,
            'member_id': member_id,
            'section_id': section_id,
            'design_key': sec_info.get('raw_name', ''),
            'node_from': node_from,
            'node_to': node_to,
            'level_from': level_from,
            'level_to': level_to,
            'grid': grid_from,
            'x_mm': x1 if z1 <= z2 else x2,
            'y_mm': y1 if z1 <= z2 else y2,
            'x_top_mm': x2 if z1 <= z2 else x1,
            'y_top_mm': y2 if z1 <= z2 else y1,
            'height_mm': round(bt_height, 1),
            'length_mm': round(bt_length, 1),
            'b_mm': sec_info.get('b_mm'),
            'h_mm': sec_info.get('h_mm'),
        }
        columns_list.append(record_col)

    else:
        # Beam (horizontal element)
        # For beams, level = the level at which the beam sits
        beam_level = level_from if not is_vert else level_from

        record = {
            'element_id': elem_id,
            'member_id': member_id,
            'section_id': section_id,
            'design_key': sec_info.get('raw_name', ''),
            'node_from': node_from,
            'node_to': node_to,
            'level': beam_level,
            'grid_from': grid_from,
            'grid_to': grid_to,
            'x_from_mm': x1,
            'y_from_mm': y1,
            'x_to_mm': x2,
            'y_to_mm': y2,
            'z_mm': min(z1, z2),
            'length_mm': length,
            'b_mm': sec_info.get('b_mm'),
            'h_mm': sec_info.get('h_mm'),
        }
        beams_list.append(record)


def _process_wall_element(elem_id, row, prop_id, node_lookup,
                          thickness_lookup, wall_mark_lookup, walls_list):
    """Process a WALL-type element (plate/quad element)."""
    # Get wall nodes (quad: 4 nodes)
    node_nums = []
    for i in range(1, 9):
        nn = int(row.get(f'node{i}', 0))
        if nn != 0:
            node_nums.append(nn)

    if len(node_nums) < 3:
        return

    # Get node coordinates
    nodes_coords = []
    for nn in node_nums:
        nd = node_lookup.get(nn)
        if nd:
            nodes_coords.append((nd['x_mm'], nd['y_mm'], nd['z_mm']))

    if len(nodes_coords) < 3:
        return

    # Centroid
    cx, cy, cz = _wall_centroid(nodes_coords)

    # Get first node info for level/grid
    nd_first = node_lookup.get(node_nums[0], {})
    level = nd_first.get('level', '')

    # Wall ID and mark
    wall_id = int(row.get('wall_id', 0))
    wall_mark = wall_mark_lookup.get(wall_id, f'W{wall_id}')

    # Thickness from lookup
    thickness = thickness_lookup.get(prop_id)

    # Compute wall dimensions from node positions
    # For a quad wall: height = Z range, width = max horizontal span
    zs = [c[2] for c in nodes_coords]
    height = max(zs) - min(zs)

    # Width: horizontal distance between bottom two nodes
    bottom_z = min(zs)
    bottom_nodes = [(c[0], c[1]) for c in nodes_coords if abs(c[2] - bottom_z) < 10]
    if len(bottom_nodes) >= 2:
        width = math.sqrt(
            (bottom_nodes[1][0] - bottom_nodes[0][0]) ** 2 +
            (bottom_nodes[1][1] - bottom_nodes[0][1]) ** 2
        )
    else:
        width = None

    # Node IDs
    node_ids = []
    for nn in node_nums:
        nd = node_lookup.get(nn)
        if nd:
            node_ids.append(nd['node_id'])

    record = {
        'element_id': elem_id,
        'wall_mark': wall_mark,
        'wall_id': wall_id,
        'level': level,
        'centroid_x_mm': round(cx, 1),
        'centroid_y_mm': round(cy, 1),
        'centroid_z_mm': round(cz, 1),
        'thickness_mm': thickness,
        'height_mm': round(height, 1) if height else None,
        'width_mm': round(width, 1) if width else None,
        'node_i': node_ids[0] if len(node_ids) > 0 else None,
        'node_j': node_ids[1] if len(node_ids) > 1 else None,
        'node_k': node_ids[2] if len(node_ids) > 2 else None,
        'node_l': node_ids[3] if len(node_ids) > 3 else None,
    }
    walls_list.append(record)
