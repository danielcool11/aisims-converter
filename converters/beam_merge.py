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
from typing import Dict


CONTIGUITY_TOL = 100.0  # mm — max gap between consecutive element endpoints
SUPPORT_XY_TOL = 500.0  # mm — proximity tolerance for column/beam support matching


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
    """Check if two beam elements connect end-to-end AND are coaxial.

    Three-part test:
      1. Primary-axis endpoint proximity — A's far-primary end ≈ B's
         near-primary end within CONTIGUITY_TOL.
      2. Endpoint coincidence (both axes) at the shared join — avoids
         chaining two parallel beams that happen to have matching
         primary coords but different perpendicular positions
         (e.g., two parallel x-beams at y=0 and y=50000).
      3. Coaxiality — direction vectors must be parallel. Prevents a
         straight x-beam and a diagonal beam that share a node from
         merging into one span (which would silently drop the
         diagonal's y-change).

    Part (2) replaces the old _perp_key grouping that grouped beams by
    rounded y_from. That bucketing failed for diagonal chains where
    successive elements have different y values.

    Issue #78 / P2 TG8 3F: previously hit by removing the support-grid
    chain-break — the straight TG8 chain and a diagonal TG8 element
    at a shared corner node got merged.
    """
    # Part 1: primary-axis proximity
    if direction == 'X':
        a_end = max(row_a['x_from_mm'], row_a['x_to_mm'])
        b_start = min(row_b['x_from_mm'], row_b['x_to_mm'])
    else:
        a_end = max(row_a['y_from_mm'], row_a['y_to_mm'])
        b_start = min(row_b['y_from_mm'], row_b['y_to_mm'])
    if abs(a_end - b_start) >= CONTIGUITY_TOL:
        return False

    # Part 2: endpoint coincidence — the two elements must share a
    # physical point, not merely have compatible primary coords.
    # Find A's far endpoint (the one at a_end on the primary axis) and
    # B's near endpoint, then compare both x and y.
    if direction == 'X':
        if row_a['x_to_mm'] >= row_a['x_from_mm']:
            a_far = (row_a['x_to_mm'], row_a['y_to_mm'])
        else:
            a_far = (row_a['x_from_mm'], row_a['y_from_mm'])
        if row_b['x_from_mm'] <= row_b['x_to_mm']:
            b_near = (row_b['x_from_mm'], row_b['y_from_mm'])
        else:
            b_near = (row_b['x_to_mm'], row_b['y_to_mm'])
    else:
        if row_a['y_to_mm'] >= row_a['y_from_mm']:
            a_far = (row_a['x_to_mm'], row_a['y_to_mm'])
        else:
            a_far = (row_a['x_from_mm'], row_a['y_from_mm'])
        if row_b['y_from_mm'] <= row_b['y_to_mm']:
            b_near = (row_b['x_from_mm'], row_b['y_from_mm'])
        else:
            b_near = (row_b['x_to_mm'], row_b['y_to_mm'])
    if (abs(a_far[0] - b_near[0]) >= CONTIGUITY_TOL or
            abs(a_far[1] - b_near[1]) >= CONTIGUITY_TOL):
        return False

    # Part 3: coaxiality via integer cross product
    dax = row_a['x_to_mm'] - row_a['x_from_mm']
    day = row_a['y_to_mm'] - row_a['y_from_mm']
    dbx = row_b['x_to_mm'] - row_b['x_from_mm']
    dby = row_b['y_to_mm'] - row_b['y_from_mm']
    cross = dax * dby - day * dbx
    mag = max(abs(dax) + abs(day), abs(dbx) + abs(dby), 1.0)
    if abs(cross) > 0.005 * mag * mag:
        return False
    return True


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

    # Build endpoint-pool: every (x, y) point that appears as a chain element
    # endpoint, with the node_id recorded from whichever side it came from.
    # We then pick the two points with the most extreme primary-axis coord
    # as the merged span's endpoints. This preserves exact structural
    # coordinates (no averaging) so beam xy always matches its referenced
    # node xy — avoids the cosmetic-drift false positives of Error F-style
    # scans when MIDAS gives slightly-imperfect colinear FEM elements.
    endpoints = []  # list of (x, y, node_id)
    for r in chain:
        endpoints.append((r['x_from_mm'], r['y_from_mm'], r.get('node_from', '')))
        endpoints.append((r['x_to_mm'], r['y_to_mm'], r.get('node_to', '')))

    # Pick the two endpoints with extreme primary-axis coordinate.
    # Works for straight X/Y chains AND diagonal chains uniformly — the
    # primary axis is whichever of X or Y has the larger span. Using
    # endpoint extremes (instead of first/last element's own x_from/x_to)
    # is essential when some chain elements have reversed orientation
    # (node1 on the right, node2 on the left), which otherwise causes the
    # merged span to lose one end — e.g. P2 TG8 3F chain [23661, 31709,
    # 31708, 23456, 22620] where 23661's own x_from=-26300, x_to=-28400
    # was dropping the -28400 endpoint when the old is_diagonal branch
    # took first['x_from_mm'].
    if direction == 'X':
        min_pt = min(endpoints, key=lambda p: p[0])
        max_pt = max(endpoints, key=lambda p: p[0])
    else:
        min_pt = min(endpoints, key=lambda p: p[1])
        max_pt = max(endpoints, key=lambda p: p[1])
    x_from, y_from = min_pt[0], min_pt[1]
    x_to, y_to = max_pt[0], max_pt[1]

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

    # Nodes: match merged endpoints to the original element endpoint that sits
    # there. Previous behaviour took first.node_from / last.node_to, which
    # silently swaps labels whenever an original element's own xy direction
    # is reverse of the merged-span direction. This caused ~48% of P2 beams
    # to have node_from/to pointing to the opposite xy field than the merged
    # x/y_from/to they're emitted with (issue #78 Error F).
    def _find_node_at(xt, yt):
        best = ''
        best_d = float('inf')
        for r in chain:
            for tag, rx, ry in (
                ('node_from', r['x_from_mm'], r['y_from_mm']),
                ('node_to',   r['x_to_mm'],   r['y_to_mm']),
            ):
                d = abs(rx - xt) + abs(ry - yt)
                if d < best_d:
                    best_d = d
                    best = r.get(tag, '')
        return best

    node_from = _find_node_at(x_from, y_from)
    node_to = _find_node_at(x_to, y_to)

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
        'direction': direction,
    }
    return merged, span_counter


def _build_support_index(columns_df, beams_df):
    """Build spatial indices for intermediate support detection.

    Returns:
        col_nodes: {level: set(node_ids)} — column nodes per level
        beam_supports: list of (x, y, level, h_mm, direction) — beam centerlines
    """
    col_nodes = {}
    if columns_df is not None and not columns_df.empty:
        for _, c in columns_df.iterrows():
            for nf, lf in [('node_from', 'level_from'), ('node_to', 'level_to')]:
                n = str(c.get(nf, '')).strip()
                lv = str(c.get(lf, '')).strip()
                if n and n != 'nan' and lv:
                    col_nodes.setdefault(lv, set()).add(n)

    beam_supports = []
    if beams_df is not None and not beams_df.empty:
        for _, b in beams_df.iterrows():
            d = _beam_direction(b['x_from_mm'], b['y_from_mm'],
                                b['x_to_mm'], b['y_to_mm'])
            beam_supports.append({
                'x_from': float(b['x_from_mm'] or 0),
                'y_from': float(b['y_from_mm'] or 0),
                'x_to': float(b['x_to_mm'] or 0),
                'y_to': float(b['y_to_mm'] or 0),
                'h': float(b.get('h_mm', 0) or 0),
                'level': str(b.get('level', '')).strip(),
                'dir': d,
                'eid': b.get('element_id'),
            })

    return col_nodes, beam_supports


def _is_intermediate_support(prev_elem, next_elem, direction, level,
                              h_mm, col_nodes, beam_supports):
    """Check if the junction between two consecutive FEM elements has
    a structural support (column or deeper/equal perpendicular beam).

    The junction point is prev_elem's far-end ≈ next_elem's near-end.
    """
    # Junction point coordinates
    if direction == 'X':
        jx = max(prev_elem['x_from_mm'], prev_elem['x_to_mm'])
        jy = (prev_elem['y_from_mm'] + prev_elem['y_to_mm']) / 2
    else:
        jx = (prev_elem['x_from_mm'] + prev_elem['x_to_mm']) / 2
        jy = max(prev_elem['y_from_mm'], prev_elem['y_to_mm'])

    # Junction node (prev's far-end node)
    if direction == 'X':
        if prev_elem['x_to_mm'] >= prev_elem['x_from_mm']:
            jnode = str(prev_elem.get('node_to', '')).strip()
        else:
            jnode = str(prev_elem.get('node_from', '')).strip()
    else:
        if prev_elem['y_to_mm'] >= prev_elem['y_from_mm']:
            jnode = str(prev_elem.get('node_to', '')).strip()
        else:
            jnode = str(prev_elem.get('node_from', '')).strip()

    # Test 1: column at this node
    level_cols = col_nodes.get(level, set())
    if jnode and jnode in level_cols:
        return True

    # Test 2: perpendicular beam support.
    # DISABLED for now — checking raw FEM elements is too aggressive
    # (each 500-1500mm FEM element triggers a break, shattering transfer
    # beams into 100+ tiny pieces). Needs a two-pass approach: merge
    # first without beam support, then check against MERGED perpendicular
    # beams. Column-node check alone correctly handles most cases.
    # TODO: implement two-pass beam support detection.

    return False


def _split_chain_at_supports(chain, direction, level, h_mm,
                              col_nodes, beam_supports):
    """Split a merged chain at intermediate support points.

    Returns a list of sub-chains. Each sub-chain is a list of element dicts.
    """
    if len(chain) <= 1:
        return [chain]

    sub_chains = []
    current = [chain[0]]

    for i in range(1, len(chain)):
        if _is_intermediate_support(chain[i - 1], chain[i], direction, level,
                                     h_mm, col_nodes, beam_supports):
            sub_chains.append(current)
            current = [chain[i]]
        else:
            current.append(chain[i])
    sub_chains.append(current)

    return sub_chains


def merge_beam_spans(
    beams_df: pd.DataFrame,
    columns_df: pd.DataFrame,
    walls_df: pd.DataFrame = None,
    tolerance: float = CONTIGUITY_TOL,
) -> pd.DataFrame:
    """Merge adjacent FEM beam elements into structural spans.

    Args:
        beams_df: MembersBeam.csv DataFrame (per-element)
        columns_df: MembersColumn.csv DataFrame (for support grid detection)
        walls_df: MembersWall.csv DataFrame (for wall support detection)
        tolerance: max gap between consecutive endpoints (mm)

    Returns:
        Merged DataFrame with same schema + element_ids column.

    Note: no max structural span length is enforced — MIDAS "structural spans"
    can exceed stock bar length (12m). Bar-level splitting happens later in
    _split_stock (tier2/rebar_lengths_beam.py), which inserts a LAP zone when
    a single bar exceeds MAX_STOCK_LENGTH_MM.
    """
    if beams_df is None or beams_df.empty:
        return beams_df


    # Build support index for intermediate support detection.
    # Chains break at column nodes and perpendicular beams of equal/greater
    # depth. Uses the RAW (pre-merge) beams_df for beam support detection.
    col_nodes, beam_supports = _build_support_index(columns_df, beams_df)

    # Add direction column
    beams = beams_df.copy()
    beams['_direction'] = beams.apply(
        lambda r: _beam_direction(r['x_from_mm'], r['y_from_mm'], r['x_to_mm'], r['y_to_mm']),
        axis=1,
    )

    # Primary-axis sort key for chain walking.
    beams['_sort_key'] = beams.apply(
        lambda r: _primary_coord(r, r['_direction'])[0],
        axis=1,
    )

    # Group by (level, member_id, direction) — no perpendicular rounding.
    # The old code grouped by _perp_key = round(y_from_mm / 50) to keep
    # beams on the same gridline together, but this fails for diagonal
    # beams (each element has a different y_from), leaving them in
    # separate perp buckets even when they share nodes and form a
    # continuous diagonal chain. Chain validity is now fully enforced by
    # _are_contiguous (primary-axis proximity + perpendicular proximity
    # + coaxiality), so the perp bucketing is no longer needed.
    grouped = beams.groupby(['level', 'member_id', '_direction'])

    merged_spans = []
    span_counter = 0
    total_elements = 0

    for (level, member_id, direction), group in grouped:
        sorted_group = group.sort_values('_sort_key')
        rows = [row.to_dict() for _, row in sorted_group.iterrows()]
        total_elements += len(rows)
        n = len(rows)

        # Graph-based chain detection (union-find). Linear chain walking
        # would split chains when the sort interleaves unrelated beams
        # at different perpendicular positions — for example, all G2 2F
        # Y-direction beams are now in one group, sorted by min_y, but
        # they live at multiple x positions across the building, so a
        # G2 at x=-50000 can land between two G2's at x=-35800 in the
        # sort and break the chain even though the -35800 pair shares a
        # node and is mergeable.
        #
        # Build a union-find over rows where two rows union only if
        # _are_contiguous + same section + same material. Each connected
        # component becomes one chain. Then sort each chain by sort_key
        # to preserve the physical order along the primary axis.
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # Pairwise check. O(n^2) per group, but groups are typically
        # tens of beams so this is cheap. Could be optimized later
        # with spatial indexing if needed.
        for i in range(n):
            for j in range(i + 1, n):
                a = rows[i]
                b = rows[j]
                if a.get('section_id') != b.get('section_id'):
                    continue
                if a.get('material_id') != b.get('material_id'):
                    continue
                # Try both orderings since _are_contiguous expects
                # prev's far-end ≈ next's near-end on the primary axis.
                if _are_contiguous(a, b, direction) or _are_contiguous(b, a, direction):
                    union(i, j)

        # Group rows by their union-find root
        groups_by_root: Dict[int, list] = {}
        for i in range(n):
            r = find(i)
            groups_by_root.setdefault(r, []).append(i)

        chains = []
        for indices in groups_by_root.values():
            indices.sort(key=lambda i: rows[i]['_sort_key'])
            chains.append([rows[i] for i in indices])

        # Split chains at intermediate supports (columns + deeper beams),
        # then merge each sub-chain into one structural span.
        for chain in chains:
            if chain:
                h_mm = chain[0].get('h_mm', 700) or 700
            sub_chains = _split_chain_at_supports(
                chain, direction, level, h_mm, col_nodes, beam_supports
            )
            for sub_chain in sub_chains:
                merged, span_counter = _merge_chain(sub_chain, span_counter)
                if merged:
                    merged_spans.append(merged)

    result = pd.DataFrame(merged_spans)
    pass1_count = len(result)

    # ── Pass 2: split at perpendicular MERGED beam + wall supports ──
    # Now that beams are merged, perpendicular beams are structural spans
    # (7-10m), not raw FEM elements (500-1500mm). Safe to check crossings.
    # For narrow beams (b ≤ 250mm, wall beams / lintels), also check
    # perpendicular wall edges as support points — these beams sit on
    # walls at upper floors where columns don't exist.
    # Only re-split beams that are still long (> 15m) — short beams are
    # already at correct span boundaries from Pass 1.
    if not result.empty:
        # Build beam support index from MERGED result
        merged_beam_supports = []
        for _, mb in result.iterrows():
            d = _beam_direction(mb['x_from_mm'], mb['y_from_mm'],
                                mb['x_to_mm'], mb['y_to_mm'])
            merged_beam_supports.append({
                'x_from': float(mb['x_from_mm'] or 0),
                'y_from': float(mb['y_from_mm'] or 0),
                'x_to': float(mb['x_to_mm'] or 0),
                'y_to': float(mb['y_to_mm'] or 0),
                'h': float(mb.get('h_mm', 0) or 0),
                'level': str(mb.get('level', '')).strip(),
                'dir': d,
            })

        # Build wall support index: perpendicular wall edges per level.
        # A wall running perpendicular to a beam provides support where
        # its edge meets the beam path. Uses centroid + width (available
        # before junction_polygon adds poly_ columns).
        wall_perp_supports = []
        if walls_df is not None and not walls_df.empty:
            for _, w in walls_df.iterrows():
                lv = str(w.get('level', '')).strip()
                cx = w.get('centroid_x_mm')
                cy = w.get('centroid_y_mm')
                width = w.get('width_mm')
                thickness = w.get('thickness_mm')
                if pd.isna(cx) or pd.isna(cy) or pd.isna(width) or width == 0:
                    continue
                cx, cy = float(cx), float(cy)
                width = float(width)
                thick = float(thickness) if pd.notna(thickness) else 200.0
                # Determine wall direction from width vs thickness extent
                # Wall "width" is along its long axis, "thickness" is the short axis
                # Use node positions if available, otherwise estimate from centroid
                # A wall with width >> thickness is elongated along one axis
                # Check node_i and node_j to determine actual direction
                ni = str(w.get('node_i', '')).strip()
                nj = str(w.get('node_j', '')).strip()
                # Fallback: if width > 2*thickness, assume wall is elongated
                # Use the node pairs to determine direction from elements converter
                # The width_mm IS the wall's length (long dimension)
                half_w = width / 2.0
                # Try to determine direction from polygon if available
                has_poly = pd.notna(w.get('poly_0x_mm'))
                if has_poly:
                    corners_x = [float(w[f'poly_{i}x_mm']) for i in range(4) if pd.notna(w.get(f'poly_{i}x_mm'))]
                    corners_y = [float(w[f'poly_{i}y_mm']) for i in range(4) if pd.notna(w.get(f'poly_{i}y_mm'))]
                    if len(corners_x) >= 4:
                        wdx = max(corners_x) - min(corners_x)
                        wdy = max(corners_y) - min(corners_y)
                        if wdy > wdx * 2:
                            wall_perp_supports.append({
                                'dir': 'Y', 'x': cx,
                                'y_min': min(corners_y), 'y_max': max(corners_y), 'level': lv,
                            })
                        elif wdx > wdy * 2:
                            wall_perp_supports.append({
                                'dir': 'X', 'y': cy,
                                'x_min': min(corners_x), 'x_max': max(corners_x), 'level': lv,
                            })
                        continue
                # No polygon — estimate from centroid + width
                # Cannot determine direction from centroid alone, so add both
                # orientations and let the proximity check filter
                wall_perp_supports.append({
                    'dir': 'Y', 'x': cx,
                    'y_min': cy - half_w, 'y_max': cy + half_w, 'level': lv,
                })
                wall_perp_supports.append({
                    'dir': 'X', 'y': cy,
                    'x_min': cx - half_w, 'x_max': cx + half_w, 'level': lv,
                })

        # Build level adjacency: beam level → set of wall levels to check.
        # Beams sit on walls from the same level or the level below.
        # E.g., Roof beams sit on 15F walls.
        # Use actual z values from beams to determine level ordering.
        level_z = {}
        for _, mb in result.iterrows():
            lv = str(mb.get('level', '')).strip()
            z = float(mb.get('z_mm', 0) or 0)
            if lv and z > 0:
                level_z[lv] = max(level_z.get(lv, 0), z)
        # Sort levels by z ascending
        sorted_levels = sorted(level_z.keys(), key=lambda l: level_z.get(l, 0))

        wall_levels = sorted(set(ws['level'] for ws in wall_perp_supports)) if wall_perp_supports else []
        level_to_wall_levels = {}
        for bl in sorted_levels:
            matching = {bl}  # same level always matches
            # Find the level just below in the sorted order
            idx = sorted_levels.index(bl)
            if idx > 0:
                below = sorted_levels[idx - 1]
                # Include wall level that matches the below beam level
                matching.add(below)
            level_to_wall_levels[bl] = matching

        NARROW_BEAM_WIDTH = 250  # mm — wall beams / lintels threshold

        pass2_splits = 0
        pass2_results = []
        for _, row in result.iterrows():
            if row['length_mm'] <= 15000:
                pass2_results.append(row.to_dict())
                continue

            direction = _beam_direction(row['x_from_mm'], row['y_from_mm'],
                                        row['x_to_mm'], row['y_to_mm'])
            level = str(row.get('level', '')).strip()
            h_mm = float(row.get('h_mm', 700) or 700)
            b_mm = float(row.get('b_mm', 300) or 300)

            # Find perpendicular MERGED beams that cross this beam
            perp_dir = 'Y' if direction == 'X' else 'X'
            cross_positions = []

            if direction == 'X':
                x_min = min(row['x_from_mm'], row['x_to_mm'])
                x_max = max(row['x_from_mm'], row['x_to_mm'])
                y_mid = (row['y_from_mm'] + row['y_to_mm']) / 2
                for bs in merged_beam_supports:
                    if bs['level'] != level or bs['dir'] != perp_dir:
                        continue
                    if bs['h'] < h_mm:
                        continue
                    bx = bs['x_from']
                    if x_min + SUPPORT_XY_TOL < bx < x_max - SUPPORT_XY_TOL:
                        by_min = min(bs['y_from'], bs['y_to'])
                        by_max = max(bs['y_from'], bs['y_to'])
                        if by_min - SUPPORT_XY_TOL <= y_mid <= by_max + SUPPORT_XY_TOL:
                            cross_positions.append(bx)

                # Wall supports for narrow beams (wall beams / lintels)
                # Check walls at same level AND level below (walls span up
                # to the beam level, e.g. 15F walls support Roof beams)
                if b_mm <= NARROW_BEAM_WIDTH:
                    match_levels = level_to_wall_levels.get(level, {level})
                    for ws in wall_perp_supports:
                        if ws['level'] not in match_levels or ws['dir'] != perp_dir:
                            continue
                        wx = ws['x']
                        if x_min + SUPPORT_XY_TOL < wx < x_max - SUPPORT_XY_TOL:
                            if ws['y_min'] - SUPPORT_XY_TOL <= y_mid <= ws['y_max'] + SUPPORT_XY_TOL:
                                cross_positions.append(wx)
            else:
                y_min = min(row['y_from_mm'], row['y_to_mm'])
                y_max = max(row['y_from_mm'], row['y_to_mm'])
                x_mid = (row['x_from_mm'] + row['x_to_mm']) / 2
                for bs in merged_beam_supports:
                    if bs['level'] != level or bs['dir'] != perp_dir:
                        continue
                    if bs['h'] < h_mm:
                        continue
                    by = bs['y_from']
                    if y_min + SUPPORT_XY_TOL < by < y_max - SUPPORT_XY_TOL:
                        bx_min = min(bs['x_from'], bs['x_to'])
                        bx_max = max(bs['x_from'], bs['x_to'])
                        if bx_min - SUPPORT_XY_TOL <= x_mid <= bx_max + SUPPORT_XY_TOL:
                            cross_positions.append(by)

                # Wall supports for narrow beams
                if b_mm <= NARROW_BEAM_WIDTH:
                    match_levels = level_to_wall_levels.get(level, {level})
                    for ws in wall_perp_supports:
                        if ws['level'] not in match_levels or ws['dir'] != perp_dir:
                            continue
                        wy = ws['y']
                        if y_min + SUPPORT_XY_TOL < wy < y_max - SUPPORT_XY_TOL:
                            if ws['x_min'] - SUPPORT_XY_TOL <= x_mid <= ws['x_max'] + SUPPORT_XY_TOL:
                                cross_positions.append(wy)

            if not cross_positions:
                pass2_results.append(row.to_dict())
                continue

            # Split at each crossing position
            cross_positions = sorted(set(round(p) for p in cross_positions))
            pieces = []
            r = row.to_dict()

            # Generate intermediate node names for split points so the
            # junction graph can detect consecutive span connections.
            orig_nf = r.get('node_from', '')
            orig_nt = r.get('node_to', '')
            mid = r.get('member_id', '')

            if direction == 'X':
                prev_x = min(r['x_from_mm'], r['x_to_mm'])
                end_x = max(r['x_from_mm'], r['x_to_mm'])
                # Collect valid crossing positions
                valid_cx = [cx for cx in cross_positions
                            if prev_x + SUPPORT_XY_TOL < cx < end_x - SUPPORT_XY_TOL]
                # Build node chain: orig_from → split_1 → split_2 → ... → orig_to
                nodes = [orig_nf]
                for cx in valid_cx:
                    nodes.append(f'N_SPLIT_{mid}_{level}_X{int(cx)}')
                nodes.append(orig_nt)

                for si, cx in enumerate(valid_cx):
                    piece = dict(r)
                    piece['x_from_mm'] = prev_x
                    piece['x_to_mm'] = cx
                    piece['length_mm'] = abs(cx - prev_x)
                    piece['node_from'] = nodes[si]
                    piece['node_to'] = nodes[si + 1]
                    pieces.append(piece)
                    prev_x = cx
                # Last piece
                piece = dict(r)
                piece['x_from_mm'] = prev_x
                piece['x_to_mm'] = end_x
                piece['length_mm'] = abs(end_x - prev_x)
                piece['node_from'] = nodes[-2]
                piece['node_to'] = nodes[-1]
                pieces.append(piece)
            else:
                prev_y = min(r['y_from_mm'], r['y_to_mm'])
                end_y = max(r['y_from_mm'], r['y_to_mm'])
                valid_cy = [cy for cy in cross_positions
                            if prev_y + SUPPORT_XY_TOL < cy < end_y - SUPPORT_XY_TOL]
                nodes = [orig_nf]
                for cy in valid_cy:
                    nodes.append(f'N_SPLIT_{mid}_{level}_Y{int(cy)}')
                nodes.append(orig_nt)

                for si, cy in enumerate(valid_cy):
                    piece = dict(r)
                    piece['y_from_mm'] = prev_y
                    piece['y_to_mm'] = cy
                    piece['length_mm'] = abs(cy - prev_y)
                    piece['node_from'] = nodes[si]
                    piece['node_to'] = nodes[si + 1]
                    pieces.append(piece)
                    prev_y = cy
                piece = dict(r)
                piece['y_from_mm'] = prev_y
                piece['y_to_mm'] = end_y
                piece['length_mm'] = abs(end_y - prev_y)
                piece['node_from'] = nodes[-2]
                piece['node_to'] = nodes[-1]
                pieces.append(piece)

            if len(pieces) > 1:
                pass2_splits += 1
                pass2_results.extend(pieces)
            else:
                pass2_results.append(row.to_dict())

        result = pd.DataFrame(pass2_results)
        if pass2_splits > 0:
            print(f'[BeamMerge] Pass 2: {pass2_splits} long beams split at '
                  f'perpendicular supports → {len(result)} spans')

    # Sanity check: total length should be preserved
    orig_total = beams_df['length_mm'].sum()
    merged_total = result['length_mm'].sum() if not result.empty else 0
    length_diff = abs(orig_total - merged_total)

    print(f'[BeamMerge] {total_elements} elements → {pass1_count} spans (pass 1) '
          f'→ {len(result)} spans (pass 2)')
    if length_diff > 1.0:
        print(f'[BeamMerge] WARNING: length mismatch! '
              f'original={orig_total:.0f} merged={merged_total:.0f} diff={length_diff:.0f}')
    else:
        print(f'[BeamMerge] Length check OK (total={orig_total:.0f}mm)')

    # Generate unique segment_id per row: member_id + '-E' + element_id + '-S' + index
    # This prevents duplicate segment_ids when Pass 2 splits a beam into pieces
    # that share the same element_id.
    if not result.empty:
        seg_ids = []
        seg_counter = {}
        for _, row in result.iterrows():
            base = f"{row['member_id']}-E{int(row['element_id'])}"
            seg_counter[base] = seg_counter.get(base, 0) + 1
            if seg_counter[base] == 1:
                seg_ids.append(base)
            else:
                seg_ids.append(f"{base}-S{seg_counter[base]}")
        result['segment_id'] = seg_ids

    # Drop internal columns
    for col in ['_direction', '_sort_key', '_perp_key']:
        if col in result.columns:
            result.drop(columns=[col], inplace=True)

    return result
