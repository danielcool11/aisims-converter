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

    # ── Node coordinate lookup + raw-to-converted ID mapping ──
    node_coords = {}
    raw_to_converted = {}
    if nodes_df is not None:
        for _, row in nodes_df.iterrows():
            raw_num = int(row['node_number'])
            node_coords[raw_num] = {
                'x_mm': float(row['x_mm']),
                'y_mm': float(row['y_mm']),
                'z_mm': float(row['z_mm']),
            }
            raw_to_converted[raw_num] = str(row['node_id'])

    # ── Parse boundary — auto-detect Type A (11 cols) vs Type B (7 cols) ──
    bcols = boundary_df.columns.tolist()
    n_cols = len(bcols)

    if n_cols >= 11:
        # Type A (P1): Node, NAME, Position, Length, Height, Left, Middle, Right, Top, Middle, Bottom
        std_cols = ['node', 'name', 'position', 'length_mm', 'height_mm',
                    'left_mm', 'middle_mm', 'right_mm',
                    'top_mm', 'middle2_mm', 'bottom_mm']
        has_horizontal_zones = True
    else:
        # Type B (P2): Node, NAME, Position, Height, Top, Middle, Bottom
        std_cols = ['node', 'name', 'position', 'height_mm',
                    'top_mm', 'middle2_mm', 'bottom_mm']
        has_horizontal_zones = False

    boundary_df.columns = std_cols[:n_cols]
    print(f'[BasementWall] Format: {"Type A (with horizontal zones)" if has_horizontal_zones else "Type B (vertical zones only)"}')

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
                'length_mm': _safe_float(row.get('length_mm')) if has_horizontal_zones else None,
                'height_mm': _safe_float(row.get('height_mm')),
                'zone_width_left_mm': _safe_float(row.get('left_mm')) if has_horizontal_zones else None,
                'zone_width_middle_mm': _safe_float(row.get('middle_mm')) if has_horizontal_zones else None,
                'zone_width_right_mm': _safe_float(row.get('right_mm')) if has_horizontal_zones else None,
                'zone_height_top_mm': _safe_float(row.get('top_mm')),
                'zone_height_middle_mm': _safe_float(row.get('middle2_mm')),
                'zone_height_bottom_mm': _safe_float(row.get('bottom_mm')),
                'has_horizontal_zones': has_horizontal_zones,
                'nodes': [],
            }
        else:
            # Fill in any missing zone dimensions from subsequent rows
            entry = wall_entries[key]
            fill_pairs = [('top_mm', 'zone_height_top_mm'),
                          ('middle2_mm', 'zone_height_middle_mm'),
                          ('bottom_mm', 'zone_height_bottom_mm')]
            if has_horizontal_zones:
                fill_pairs += [('left_mm', 'zone_width_left_mm'),
                               ('middle_mm', 'zone_width_middle_mm'),
                               ('right_mm', 'zone_width_right_mm')]
            for src, dst in fill_pairs:
                if entry[dst] is None:
                    val = _safe_float(row.get(src))
                    if val is not None:
                        entry[dst] = val
        if node_id:
            wall_entries[key]['nodes'].append(node_id)

    # ── Validate nodes: check existence and Z consistency ──
    # Basement levels should have negative Z (below ground)
    def _is_basement_z(z):
        return z is not None and z <= 0

    def _node_is_valid(nid):
        """Node exists and has a basement-range Z coordinate."""
        c = node_coords.get(nid)
        if c is None:
            return False
        return _is_basement_z(c['z_mm'])

    # ── Collect valid XY reference per wall_mark ──
    # A wall keeps the same XY across levels; find it from any valid level.
    wall_valid_xy = {}  # wall_mark → list of (x, y) from valid nodes
    for (name, level), entry in wall_entries.items():
        for nid in entry['nodes']:
            if _node_is_valid(nid):
                c = node_coords[nid]
                wall_valid_xy.setdefault(name, []).append((c['x_mm'], c['y_mm']))

    # Compute reference XY per wall (average of all valid nodes)
    wall_ref_xy = {}
    for wm, xys in wall_valid_xy.items():
        avg_x = round(sum(p[0] for p in xys) / len(xys), 1)
        avg_y = round(sum(p[1] for p in xys) / len(xys), 1)
        wall_ref_xy[wm] = (avg_x, avg_y)

    # ── Collect valid Z per wall_mark+level from valid nodes ──
    wall_level_z = {}  # (wall_mark, level) → z from valid nodes
    for (name, level), entry in wall_entries.items():
        valid_zs = [node_coords[n]['z_mm'] for n in entry['nodes'] if _node_is_valid(n)]
        if valid_zs:
            wall_level_z[(name, level)] = round(sum(valid_zs) / len(valid_zs), 1)

    # ── Infer Z for levels without valid nodes ──
    # Strategy: stack wall panels from the bottom up using known heights.
    # Find the lowest valid Z for any wall in the building as the base reference,
    # then accumulate heights upward.
    def _infer_z_for_wall(wall_mark):
        """Compute Z centroids for all levels of a wall using height stacking.

        Returns dict: level → z_mm (centroid)
        """
        # Collect all levels for this wall with their heights
        wall_levels = {}
        for (wm, lv), entry in wall_entries.items():
            if wm == wall_mark and '~' not in lv:
                wall_levels[lv] = entry.get('height_mm') or 0

        if not wall_levels:
            return {}

        # Sort levels: B4 (deepest) → B1 (shallowest)
        sorted_levels = sorted(wall_levels.keys(), key=_level_sort_key)

        # Find bottom Z: use the lowest valid level's Z, or reference from other walls
        bottom_z = None
        for (wm, lv) in wall_level_z:
            if wm == wall_mark:
                # Use this wall's own valid Z to anchor
                entry_h = 0
                for (w2, l2), e2 in wall_entries.items():
                    if w2 == wall_mark and l2 == lv:
                        entry_h = e2.get('height_mm') or 0
                valid_z = wall_level_z[(wm, lv)]
                valid_bottom = valid_z - entry_h / 2
                # Compute the base bottom by subtracting all heights below this level
                idx = sorted_levels.index(lv) if lv in sorted_levels else -1
                if idx >= 0:
                    below_h = sum(wall_levels.get(sorted_levels[i], 0) for i in range(idx))
                    bottom_z = valid_bottom - below_h
                    break

        if bottom_z is None:
            # No valid Z for this wall — try using another wall's bottom
            all_valid_bottoms = []
            for (wm, lv) in wall_level_z:
                for (w2, l2), e2 in wall_entries.items():
                    if w2 == wm and l2 == lv:
                        h = e2.get('height_mm') or 0
                        all_valid_bottoms.append(wall_level_z[(wm, lv)] - h / 2)
            if all_valid_bottoms:
                bottom_z = min(all_valid_bottoms)
            else:
                return {}

        # Stack upward from bottom
        result = {}
        current_z = bottom_z
        for lv in sorted_levels:
            h = wall_levels.get(lv, 0)
            result[lv] = round(current_z + h / 2, 1)
            current_z += h

        return result

    # ── Compute Z centroids via height stacking ──
    # Node Z is unreliable (may be top/bottom of panel, not centroid, and some
    # nodes are missing or reference wrong levels). Use height stacking instead.
    # All per-level walls in the same building share the same basement depth.

    # Find the building's base Z from any wall with all-valid nodes
    # by checking where the lowest level's bottom sits.
    base_z = None  # bottom of B4 (deepest level)
    all_walls = set(name for (name, _) in wall_entries)

    for wm in all_walls:
        # Get sorted levels for this wall
        wm_levels = {}
        for (w, lv), e in wall_entries.items():
            if w == wm and '~' not in lv:
                wm_levels[lv] = e.get('height_mm') or 0

        if not wm_levels:
            continue

        sorted_lvs = sorted(wm_levels.keys(), key=_level_sort_key)

        # Check if this wall has nodes with a reliable centroid (4+ distinct Z values)
        for lv in sorted_lvs:
            entry = wall_entries[(wm, lv)]
            node_zs = [node_coords[n]['z_mm'] for n in entry['nodes'] if n in node_coords]
            if len(node_zs) >= 4:
                # Has 4 nodes — likely top and bottom pairs
                z_centroid = sum(node_zs) / len(node_zs)
                h = wm_levels[lv]
                panel_bottom = z_centroid - h / 2
                # Walk down to the base
                idx = sorted_lvs.index(lv)
                below_h = sum(wm_levels.get(sorted_lvs[i], 0) for i in range(idx))
                candidate_base = panel_bottom - below_h
                if base_z is None or candidate_base < base_z:
                    base_z = candidate_base
                break

    # Compute Z centroid for each wall×level via stacking from its OWN base_z.
    # Each wall may start at a different level (e.g. BW4 starts at B3, not B4).
    # Use per-wall node Z to find each wall's own base, falling back to
    # the global base_z only if no node data is available.
    wall_z_map = {}  # (wall_mark, level) → z_centroid
    for wm in all_walls:
        wm_levels = {}
        for (w, lv), e in wall_entries.items():
            if w == wm and '~' not in lv:
                wm_levels[lv] = e.get('height_mm') or 0

        sorted_lvs = sorted(wm_levels.keys(), key=_level_sort_key)
        total_h = sum(wm_levels.get(lv, 0) for lv in sorted_lvs)

        # Compute this wall's own base Z from its deepest level's nodes
        own_base_z = None
        for lv in sorted_lvs:
            entry = wall_entries.get((wm, lv))
            if not entry:
                continue
            node_zs = [node_coords[n]['z_mm'] for n in entry['nodes'] if n in node_coords]
            if len(node_zs) >= 4:
                z_centroid = sum(node_zs) / len(node_zs)
                h = wm_levels[lv]
                panel_bottom = z_centroid - h / 2
                idx = sorted_lvs.index(lv)
                below_h = sum(wm_levels.get(sorted_lvs[i], 0) for i in range(idx))
                own_base_z = panel_bottom - below_h
                break

        if own_base_z is not None:
            current_z = own_base_z
        elif base_z is not None:
            current_z = base_z
        else:
            current_z = -total_h

        for lv in sorted_lvs:
            h = wm_levels.get(lv, 0)
            wall_z_map[(wm, lv)] = round(current_z + h / 2, 1)
            current_z += h

        # Full-height walls: centroid at mid-height of total
        for (w, lv), e in wall_entries.items():
            if w == wm and '~' in lv:
                h = e.get('height_mm') or total_h
                wall_base = own_base_z if own_base_z is not None else (base_z if base_z is not None else -total_h)
                wall_z_map[(wm, lv)] = round(wall_base + h / 2, 1)

    # ── Build MembersBasementWall: one row per quad panel ──
    members = []
    inferred_count = 0
    missing_count = 0

    for (name, level), entry in wall_entries.items():
        nodes = entry['nodes']

        # Split into panels: 4 nodes per panel
        if len(nodes) >= 4 and len(nodes) % 4 == 0:
            panels = [nodes[i:i+4] for i in range(0, len(nodes), 4)]
        elif len(nodes) >= 3:
            panels = [nodes]  # polygon, keep as one
        else:
            continue

        # Z from height stacking (reliable for all panels)
        z_mm = wall_z_map.get((name, level))

        for pi, panel_nodes in enumerate(panels, start=1):
            # Classify each node for XY positioning
            valid_coords = []
            invalid_nodes = []
            for nid in panel_nodes:
                if _node_is_valid(nid):
                    valid_coords.append(node_coords[nid])
                else:
                    invalid_nodes.append(nid)

            all_valid = len(invalid_nodes) == 0
            has_some_valid = len(valid_coords) > 0

            if all_valid:
                xs = [c['x_mm'] for c in valid_coords]
                ys = [c['y_mm'] for c in valid_coords]
                centroid_x = round(sum(xs) / len(xs), 1)
                centroid_y = round(sum(ys) / len(ys), 1)
                node_status = 'OK'
            elif has_some_valid:
                xs = [c['x_mm'] for c in valid_coords]
                ys = [c['y_mm'] for c in valid_coords]
                centroid_x = round(sum(xs) / len(xs), 1)
                centroid_y = round(sum(ys) / len(ys), 1)
                node_status = 'PARTIAL'
                inferred_count += 1
            else:
                # No valid nodes — infer XY from other levels of same wall
                ref_xy = wall_ref_xy.get(name)
                if ref_xy:
                    centroid_x = ref_xy[0]
                    centroid_y = ref_xy[1]
                    node_status = 'INFERRED'
                    inferred_count += 1
                else:
                    centroid_x = centroid_y = None
                    node_status = 'MISSING'
                    missing_count += 1

            # Convert node IDs: valid → converted ID, invalid → MISSING_xxx
            def _convert_nid(nid):
                if nid in raw_to_converted and _node_is_valid(nid):
                    return raw_to_converted[nid]
                return f'MISSING_{nid}'

            # For Type B: compute per-panel length from quad nodes
            panel_length = entry['length_mm']
            if panel_length is None and len(valid_coords) >= 2:
                import math
                # Group panel nodes by Z to find bottom edge span
                z_groups = {}
                for c in valid_coords:
                    z_key = round(c['z_mm'])
                    z_groups.setdefault(z_key, []).append((c['x_mm'], c['y_mm']))
                for z_key in sorted(z_groups, key=lambda k: len(z_groups[k]), reverse=True):
                    pts = z_groups[z_key]
                    if len(pts) >= 2:
                        max_d = 0
                        for i in range(len(pts)):
                            for j in range(i+1, len(pts)):
                                d = math.sqrt((pts[j][0]-pts[i][0])**2 + (pts[j][1]-pts[i][1])**2)
                                if d > max_d:
                                    max_d = d
                        panel_length = round(max_d, 1)
                        break

            members.append({
                'wall_mark': name,
                'level': level,
                'panel_no': pi,
                'wall_type': None,  # filled from reinforcement below
                'thickness_mm': None,  # filled from reinforcement below
                'length_mm': panel_length,
                'height_mm': entry['height_mm'],
                'zone_width_left_mm': entry['zone_width_left_mm'],
                'zone_width_middle_mm': entry['zone_width_middle_mm'],
                'zone_width_right_mm': entry['zone_width_right_mm'],
                'zone_height_top_mm': entry['zone_height_top_mm'],
                'zone_height_middle_mm': entry['zone_height_middle_mm'],
                'zone_height_bottom_mm': entry['zone_height_bottom_mm'],
                'node_i': _convert_nid(panel_nodes[0]) if len(panel_nodes) > 0 else None,
                'node_j': _convert_nid(panel_nodes[1]) if len(panel_nodes) > 1 else None,
                'node_k': _convert_nid(panel_nodes[2]) if len(panel_nodes) > 2 else None,
                'node_l': _convert_nid(panel_nodes[3]) if len(panel_nodes) > 3 else None,
                'centroid_x_mm': centroid_x,
                'centroid_y_mm': centroid_y,
                'z_mm': z_mm,
                'node_status': node_status,
            })

    if inferred_count or missing_count:
        print(f'[BasementWall] Node warnings: {inferred_count} inferred, '
              f'{missing_count} missing')

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
        elif 'h_int' in cl and 'left' not in cl and 'middle' not in cl and 'right' not in cl:
            # Type B: single H_Int. column (no zone split)
            col_map[col] = 'h_int_full'
        elif 'h_ext' in cl and 'left' not in cl and 'middle' not in cl and 'right' not in cl:
            # Type B: single H_Ext. column (no zone split)
            col_map[col] = 'h_ext_full'
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
    # Detect if reinforcement has horizontal zone split (Type A) or single H (Type B)
    reinf_has_h_zones = any(c in reinforcement_df.columns for c in ('h_int_left', 'h_ext_left'))

    rebar_positions = []
    if reinf_has_h_zones:
        # Type A: 3 horizontal zones
        rebar_positions += [
            ('h_int_left', 'HORIZONTAL', 'INTERIOR', 'LEFT'),
            ('h_ext_left', 'HORIZONTAL', 'EXTERIOR', 'LEFT'),
            ('h_int_middle', 'HORIZONTAL', 'INTERIOR', 'MIDDLE'),
            ('h_ext_middle', 'HORIZONTAL', 'EXTERIOR', 'MIDDLE'),
            ('h_int_right', 'HORIZONTAL', 'INTERIOR', 'RIGHT'),
            ('h_ext_right', 'HORIZONTAL', 'EXTERIOR', 'RIGHT'),
        ]
    else:
        # Type B: single horizontal zone (FULL wall length)
        rebar_positions += [
            ('h_int_full', 'HORIZONTAL', 'INTERIOR', 'FULL'),
            ('h_ext_full', 'HORIZONTAL', 'EXTERIOR', 'FULL'),
        ]
    rebar_positions += [
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


def _level_sort_key(level):
    """Sort key for basement levels: B4=-104, B3=-103, B2=-102, B1=-101."""
    s = str(level).strip().upper()
    m = re.match(r'B(\d+)', s)
    if m:
        return -100 - int(m.group(1))
    return 0


def _safe_float(val):
    if val is None:
        return None
    try:
        v = float(val)
        return v if not pd.isna(v) else None
    except (ValueError, TypeError):
        return None
