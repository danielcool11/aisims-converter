"""
Beam merge — merges adjacent FEM beam elements into structural spans.

MIDAS Gen splits beams at every FEM node (column intersections, wall intersections,
mesh refinement nodes), creating many short segments. This module merges them back
into column-to-column (or wall-to-wall) structural spans for correct rebar calculation.

Merge rules:
  1. Same member_id + same level + same direction
  2. Contiguous (end-to-start within tolerance)
  3. Break at structural supports (column grids, wall grids, beam-beam junctions)
  4. Break on section_id change (different beam size)
  5. Break on material_id change (different concrete grade)
  6. Max merged length ≤ 12000mm (stock length)

Output preserves same schema as MembersBeam.csv + adds element_ids column.
"""

import pandas as pd
import math


CONTIGUITY_TOL = 100.0  # mm — max gap between consecutive element endpoints
MAX_MERGE_LENGTH = 12000.0  # mm — never merge beyond stock length


def _beam_direction(x_from, y_from, x_to, y_to):
    """Determine beam direction from coordinates."""
    dx = abs(x_to - x_from)
    dy = abs(y_to - y_from)
    if dy < 1 and dx > 1:
        return 'X'
    if dx < 1 and dy > 1:
        return 'Y'
    return 'X' if dx >= dy else 'Y'


def _primary_coord(row, direction):
    """Get the primary coordinate range for sorting (along beam axis)."""
    if direction == 'X':
        return min(row['x_from_mm'], row['x_to_mm']), max(row['x_from_mm'], row['x_to_mm'])
    else:
        return min(row['y_from_mm'], row['y_to_mm']), max(row['y_from_mm'], row['y_to_mm'])


def _are_contiguous(row_a, row_b, direction):
    """Check if two beam elements connect end-to-end."""
    if direction == 'X':
        # A's max-x should be close to B's min-x
        a_end = max(row_a['x_from_mm'], row_a['x_to_mm'])
        b_start = min(row_b['x_from_mm'], row_b['x_to_mm'])
    else:
        a_end = max(row_a['y_from_mm'], row_a['y_to_mm'])
        b_start = min(row_b['y_from_mm'], row_b['y_to_mm'])
    return abs(a_end - b_start) < CONTIGUITY_TOL


def _build_support_grids(columns_df, walls_df, beams_df):
    """Build set of grid labels that are structural support points.

    Support grids = column grids ∪ wall node grids ∪ beam-beam junction grids.
    """
    support_grids = set()

    # Column grids
    if columns_df is not None and not columns_df.empty:
        for g in columns_df['grid'].dropna():
            g = str(g).strip()
            if g and g != 'OFF_GRID':
                support_grids.add(g)

    # Wall node grids — extract from node_i..node_l IDs (format: N_B1_X5Y13 → X5Y13)
    if walls_df is not None and not walls_df.empty:
        for col in ['node_i', 'node_j', 'node_k', 'node_l']:
            if col not in walls_df.columns:
                continue
            for nid in walls_df[col].dropna():
                nid = str(nid).strip()
                if not nid or 'OFF' in nid or 'MISSING' in nid:
                    continue
                parts = nid.split('_')
                if len(parts) >= 3:
                    grid = parts[-1]
                    if grid != 'OFF_GRID':
                        support_grids.add(grid)

    # Beam-beam junction grids — grid points where beams of different member_id meet
    if beams_df is not None and not beams_df.empty:
        grid_members = {}  # grid → set of member_ids
        for _, b in beams_df.iterrows():
            mid = b.get('member_id', '')
            for gc in ['grid_from', 'grid_to']:
                g = str(b.get(gc, '')).strip()
                if g and g != 'OFF_GRID':
                    grid_members.setdefault(g, set()).add(mid)
        for g, mids in grid_members.items():
            if len(mids) > 1:  # multiple member_ids at same grid = junction
                support_grids.add(g)

    return support_grids


def _is_break_point(grid_label, support_grids):
    """Check if a grid label is a structural break point."""
    g = str(grid_label).strip()
    if not g or g == 'OFF_GRID':
        return False
    return g in support_grids


def _merge_chain(chain, span_counter):
    """Merge a list of contiguous beam elements into one span record."""
    if not chain:
        return None, span_counter

    first = chain[0]
    last = chain[-1]
    span_counter += 1

    # Collect all coordinates from all elements in the chain
    all_x = []
    all_y = []
    for r in chain:
        all_x.extend([r['x_from_mm'], r['x_to_mm']])
        all_y.extend([r['y_from_mm'], r['y_to_mm']])

    # Determine direction from the total coordinate span
    dx_span = max(all_x) - min(all_x)
    dy_span = max(all_y) - min(all_y)
    is_diagonal = dx_span > 100 and dy_span > 100 and min(dx_span, dy_span) > max(dx_span, dy_span) * 0.15
    direction = 'X' if dx_span >= dy_span else 'Y'

    if is_diagonal:
        # Diagonal beams: use first element's start and last element's end
        # (chain is sorted by primary axis, so first→last = full span)
        x_from = first['x_from_mm']
        y_from = first['y_from_mm']
        x_to = last['x_to_mm']
        y_to = last['y_to_mm']
    elif direction == 'X':
        # X-direction: use outermost X, average Y
        x_from = min(all_x)
        x_to = max(all_x)
        y_avg = sum(all_y) / len(all_y)
        y_from = y_avg
        y_to = y_avg
    else:
        # Y-direction: use outermost Y, average X
        y_from = min(all_y)
        y_to = max(all_y)
        x_avg = sum(all_x) / len(all_x)
        x_from = x_avg
        x_to = x_avg

    # Sum lengths
    total_length = sum(r['length_mm'] for r in chain)

    # Grid: use outermost non-OFF_GRID labels
    grid_from = 'OFF_GRID'
    grid_to = 'OFF_GRID'
    for r in chain:
        gf = str(r.get('grid_from', '')).strip()
        if gf and gf != 'OFF_GRID':
            grid_from = gf
            break
    for r in reversed(chain):
        gt = str(r.get('grid_to', '')).strip()
        if gt and gt != 'OFF_GRID':
            grid_to = gt
            break
    # Also check reversed direction
    if grid_from == 'OFF_GRID':
        for r in chain:
            gt = str(r.get('grid_to', '')).strip()
            if gt and gt != 'OFF_GRID':
                grid_from = gt
                break
    if grid_to == 'OFF_GRID':
        for r in reversed(chain):
            gf = str(r.get('grid_from', '')).strip()
            if gf and gf != 'OFF_GRID':
                grid_to = gf
                break

    # Nodes: first and last in chain (sorted by primary coordinate)
    node_from = first.get('node_from', '')
    node_to = last.get('node_to', '')

    # Use the first element's properties (all should be same within merged span)
    member_id = first['member_id']
    section_id = first['section_id']
    design_key = first.get('design_key', '')
    level = first['level']
    material_id = first.get('material_id')
    fy_main = first.get('fy_main')
    fy_sub = first.get('fy_sub')
    b_mm = first.get('b_mm')
    h_mm = first.get('h_mm')

    # Element IDs for traceability
    element_ids = ','.join(str(int(r['element_id'])) for r in chain)

    # Extensions: take max from any element at each end of the chain.
    # The first element (by sort) contributes start extension,
    # the last element contributes end extension.
    # Check both extend_start and extend_end on each since element
    # direction may not match the merged span direction.
    extend_start = max(
        first.get('extend_start_mm', 0) or 0,
        first.get('extend_end_mm', 0) or 0,
    )
    extend_end = max(
        last.get('extend_start_mm', 0) or 0,
        last.get('extend_end_mm', 0) or 0,
    )

    merged = {
        'element_id': int(first['element_id']),  # keep first for backward compat
        'member_id': member_id,
        'section_id': section_id,
        'design_key': design_key,
        'node_from': node_from,
        'node_to': node_to,
        'level': level,
        'grid_from': grid_from,
        'grid_to': grid_to,
        'x_from_mm': x_from,
        'y_from_mm': y_from,
        'x_to_mm': x_to,
        'y_to_mm': y_to,
        'z_mm': first.get('z_mm', 0),
        'length_mm': round(total_length, 1),
        'b_mm': b_mm,
        'h_mm': h_mm,
        'extend_start_mm': extend_start,
        'extend_end_mm': extend_end,
        'material_id': material_id,
        'fy_main': fy_main,
        'fy_sub': fy_sub,
        'element_ids': element_ids,
    }
    return merged, span_counter


def merge_beam_spans(
    beams_df: pd.DataFrame,
    columns_df: pd.DataFrame,
    walls_df: pd.DataFrame = None,
    tolerance: float = CONTIGUITY_TOL,
    max_length: float = MAX_MERGE_LENGTH,
) -> pd.DataFrame:
    """Merge adjacent FEM beam elements into structural spans.

    Args:
        beams_df: MembersBeam.csv DataFrame (per-element)
        columns_df: MembersColumn.csv DataFrame (for support grid detection)
        walls_df: MembersWall.csv DataFrame (for wall support detection)
        tolerance: max gap between consecutive endpoints (mm)
        max_length: max merged span length (mm)

    Returns:
        Merged DataFrame with same schema + element_ids column.
    """
    if beams_df is None or beams_df.empty:
        return beams_df

    # Build support grids
    support_grids = _build_support_grids(columns_df, walls_df, beams_df)
    print(f'[BeamMerge] {len(support_grids)} support grids detected')

    # Add direction column
    beams = beams_df.copy()
    beams['_direction'] = beams.apply(
        lambda r: _beam_direction(r['x_from_mm'], r['y_from_mm'], r['x_to_mm'], r['y_to_mm']),
        axis=1,
    )

    # Add primary sort key and perpendicular coordinate for grouping
    beams['_sort_key'] = beams.apply(
        lambda r: _primary_coord(r, r['_direction'])[0],
        axis=1,
    )
    # Perpendicular coordinate — round to 50mm to group beams on same gridline
    beams['_perp_key'] = beams.apply(
        lambda r: round((r['y_from_mm'] if r['_direction'] == 'X' else r['x_from_mm']) / 50) * 50,
        axis=1,
    )

    # Group by (level, member_id, direction, perpendicular coordinate)
    grouped = beams.groupby(['level', 'member_id', '_direction', '_perp_key'])

    merged_spans = []
    span_counter = 0
    total_elements = 0

    for (level, member_id, direction, perp_key), group in grouped:
        # Sort by primary coordinate
        sorted_group = group.sort_values('_sort_key')
        rows = [row.to_dict() for _, row in sorted_group.iterrows()]
        total_elements += len(rows)

        # Form contiguous chains, splitting at break points
        chains = []
        current_chain = []

        for i, row in enumerate(rows):
            if not current_chain:
                current_chain.append(row)
                continue

            prev = current_chain[-1]

            # Check contiguity
            if not _are_contiguous(prev, row, direction):
                chains.append(current_chain)
                current_chain = [row]
                continue

            # Check section change
            if row.get('section_id') != prev.get('section_id'):
                chains.append(current_chain)
                current_chain = [row]
                continue

            # Check material change
            if row.get('material_id') != prev.get('material_id'):
                chains.append(current_chain)
                current_chain = [row]
                continue

            # Check if intermediate junction is a support grid
            # The junction node is prev's end grid or row's start grid
            prev_end_grid = str(prev.get('grid_to', '')).strip()
            row_start_grid = str(row.get('grid_from', '')).strip()
            if _is_break_point(prev_end_grid, support_grids) or \
               _is_break_point(row_start_grid, support_grids):
                chains.append(current_chain)
                current_chain = [row]
                continue

            # Check max length
            chain_length = sum(r['length_mm'] for r in current_chain) + row['length_mm']
            if chain_length > max_length:
                chains.append(current_chain)
                current_chain = [row]
                continue

            current_chain.append(row)

        if current_chain:
            chains.append(current_chain)

        # Merge each chain
        for chain in chains:
            merged, span_counter = _merge_chain(chain, span_counter)
            if merged:
                merged_spans.append(merged)

    result = pd.DataFrame(merged_spans)

    # Sanity check: total length should be preserved
    orig_total = beams_df['length_mm'].sum()
    merged_total = result['length_mm'].sum() if not result.empty else 0
    length_diff = abs(orig_total - merged_total)

    print(f'[BeamMerge] {total_elements} elements → {len(result)} spans '
          f'({total_elements - len(result)} elements merged)')
    if length_diff > 1.0:
        print(f'[BeamMerge] WARNING: length mismatch! '
              f'original={orig_total:.0f} merged={merged_total:.0f} diff={length_diff:.0f}')
    else:
        print(f'[BeamMerge] Length check OK (total={orig_total:.0f}mm)')

    # Drop internal columns
    for col in ['_direction', '_sort_key', '_perp_key']:
        if col in result.columns:
            result.drop(columns=[col], inplace=True)

    return result
