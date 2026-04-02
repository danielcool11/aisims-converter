"""
Wall Junction Algorithm — Polygon-based vertex manipulation.

Implements proper wall junction handling using 4-vertex floor plan polygons:
- L-junction (2-segment node): vertex-move approach (mitered corners)
- T-junction (3-segment node): branch wall extension to through wall
- L+T compound (3-segment, no coinciding pair): L first on thick pair, then T

Drawing generation (removal lines) handled by colleague's module — not here.

Runs as Tier 1 post-processing after MembersWall.csv is generated.
Must complete before Tier 2 rebar generation.

Output:
- Modified polygon vertices stored as poly_0x..poly_3y on wall elements
- Updated extend_start_mm/extend_end_mm derived from polygon modifications

Polygon vertex convention (plan view, looking down):
  polygon[0] = i+ (at node_i, LEFT of direction i→j)
  polygon[1] = j+ (at node_j, LEFT of direction i→j)
  polygon[2] = j- (at node_j, RIGHT of direction i→j)
  polygon[3] = i- (at node_i, RIGHT of direction i→j)

  "Left" = CCW perpendicular: dir=(dx,dy) → normal=(-dy,dx)
"""

import math
import pandas as pd
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field


TOL = 1.0  # vertex matching tolerance (mm)


# ─────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────

@dataclass
class WallSegment:
    """A wall panel in plan view (one wall_id at one level)."""
    wall_id: int
    wall_mark: str
    level: str
    element_ids: List[str]
    node_i_id: str
    node_j_id: str
    node_i_xy: Tuple[float, float]  # (x_mm, y_mm)
    node_j_xy: Tuple[float, float]
    thickness_mm: float
    height_mm: float
    z_bottom_mm: float
    polygon: List[List[float]] = field(default_factory=list)


# ─────────────────────────────────────────────────────────
# Geometry helpers
# ─────────────────────────────────────────────────────────

def _dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _line_intersect(p1, d1, p2, d2):
    """Infinite line intersection. Returns (x,y) or None if parallel."""
    cross = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(cross) < 1e-9:
        return None
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / cross
    return (p1[0] + t * d1[0], p1[1] + t * d1[1])


def _seg_intersect(p1, p2, p3, p4):
    """Segment intersection with slight tolerance for numerical stability."""
    d1 = (p2[0] - p1[0], p2[1] - p1[1])
    d2 = (p4[0] - p3[0], p4[1] - p3[1])
    cross = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(cross) < 1e-9:
        return None
    dx = p3[0] - p1[0]
    dy = p3[1] - p1[1]
    t = (dx * d2[1] - dy * d2[0]) / cross
    u = (dx * d1[1] - dy * d1[0]) / cross
    if -0.01 <= t <= 1.01 and -0.01 <= u <= 1.01:
        return (p1[0] + t * d1[0], p1[1] + t * d1[1])
    return None


def _get_poly_verts(seg: WallSegment, end: str):
    """Get near/far vertex pairs and 4 edges at given end."""
    p = seg.polygon
    edges = [
        (tuple(p[0]), tuple(p[1])),  # left side
        (tuple(p[1]), tuple(p[2])),  # j-end cap
        (tuple(p[2]), tuple(p[3])),  # right side
        (tuple(p[3]), tuple(p[0])),  # i-end cap
    ]
    if end == "i":
        near = [tuple(p[0]), tuple(p[3])]
        far = [tuple(p[1]), tuple(p[2])]
    else:
        near = [tuple(p[1]), tuple(p[2])]
        far = [tuple(p[0]), tuple(p[3])]
    return near, far, edges


def _verts_coincide(na, nb):
    """Two near-vertex pairs match within tolerance (either order)."""
    d00 = _dist(na[0], nb[0])
    d01 = _dist(na[0], nb[1])
    d10 = _dist(na[1], nb[0])
    d11 = _dist(na[1], nb[1])
    return (d00 < TOL and d11 < TOL) or (d01 < TOL and d10 < TOL)


def _is_parallel(na, fa, nb, fb):
    """Two wall directions are parallel (cross product < 0.1)."""
    da = (fa[0][0] - na[0][0], fa[0][1] - na[0][1])
    db = (fb[0][0] - nb[0][0], fb[0][1] - nb[0][1])
    cross = abs(da[0] * db[1] - da[1] * db[0])
    la = math.sqrt(da[0] ** 2 + da[1] ** 2)
    lb = math.sqrt(db[0] ** 2 + db[1] ** 2)
    if la < 0.001 or lb < 0.001:
        return True
    return cross / (la * lb) < 0.1


def _find_x(edges_a, edges_b, node_xy):
    """Physical edge-edge intersection, excluding the node point itself."""
    best = None
    best_dist = float('inf')
    for ea in edges_a:
        for eb in edges_b:
            pt = _seg_intersect(ea[0], ea[1], eb[0], eb[1])
            if pt is None:
                continue
            if abs(pt[0] - node_xy[0]) < TOL and abs(pt[1] - node_xy[1]) < TOL:
                continue
            d = _dist(pt, node_xy)
            if d < best_dist:
                best_dist = d
                best = pt
    return best


def _pick_far(near_pt, far_pair):
    """Far vertex closer to near_pt (same edge line)."""
    d0 = _dist(near_pt, far_pair[0])
    d1 = _dist(near_pt, far_pair[1])
    return far_pair[0] if d0 < d1 else far_pair[1]


def _is_diag(edge, X, Y):
    """Check if edge is the diagonal X↔Y."""
    if X is None or Y is None:
        return False
    a, b = edge
    return ((_dist(a, X) < TOL and _dist(b, Y) < TOL) or
            (_dist(a, Y) < TOL and _dist(b, X) < TOL))


# ─────────────────────────────────────────────────────────
# Junction algorithms
# ─────────────────────────────────────────────────────────

def _apply_l_junction(seg_a, end_a, seg_b, end_b,
                       near_a, far_a, edges_a,
                       near_b, far_b, edges_b, n_xy):
    """L-junction vertex-move. Moves 4 near vertices to intersection points X, Y.
    Returns (X, Y) or None."""
    X = _find_x(edges_a, edges_b, n_xy)
    if X is None:
        return None

    near_idx_a = [0, 3] if end_a == "i" else [1, 2]
    near_idx_b = [0, 3] if end_b == "i" else [1, 2]

    p1i = 0 if _dist(near_a[0], X) > _dist(near_a[1], X) else 1
    p2i = 1 - p1i
    q1i = 0 if _dist(near_b[0], X) > _dist(near_b[1], X) else 1
    q2i = 1 - q1i

    P4 = _pick_far(near_a[p1i], far_a)
    Q4 = _pick_far(near_b[q1i], far_b)

    dp = (P4[0] - near_a[p1i][0], P4[1] - near_a[p1i][1])
    dq = (Q4[0] - near_b[q1i][0], Q4[1] - near_b[q1i][1])

    Y = _line_intersect(near_a[p1i], dp, near_b[q1i], dq)
    if Y is None:
        return None

    pa = seg_a.polygon
    pb = seg_b.polygon
    pa[near_idx_a[p1i]] = [Y[0], Y[1]]
    pa[near_idx_a[p2i]] = [X[0], X[1]]
    pb[near_idx_b[q1i]] = [Y[0], Y[1]]
    pb[near_idx_b[q2i]] = [X[0], X[1]]

    return (X, Y)


def _apply_t_extension(branch_seg, branch_end, through_segs, n_xy):
    """T-junction: extend branch wall to reach through wall's far edge.

    Instead of removal lines, we compute how far the branch wall needs
    to extend so its box overlaps the through wall properly in 3D.
    """
    # Branch wall direction
    bx1, by1 = branch_seg.node_i_xy
    bx2, by2 = branch_seg.node_j_xy
    bdx = bx2 - bx1
    bdy = by2 - by1
    blen = math.sqrt(bdx * bdx + bdy * bdy)
    if blen < 0.001:
        return

    bux, buy = bdx / blen, bdy / blen

    # Find maximum through-wall thickness at this node
    max_through_thick = max(s.thickness_mm for s in through_segs)

    # Extension = half of through-wall thickness / cos(angle)
    # For perpendicular: cos(90°) = ... wait, we project differently.
    #
    # More robust: find where branch centerline intersects through-wall far edges.
    # The branch centerline at the junction end extends along ±(bux, buy).
    # The through wall's far edge is at ±thickness/2 from its centerline.
    #
    # Simple approach: extension = through_thickness / 2 works for perpendicular.
    # For angled: extension = through_thickness / (2 * sin(angle)) where angle
    # is between the two wall directions.

    for ts in through_segs:
        tx1, ty1 = ts.node_i_xy
        tx2, ty2 = ts.node_j_xy
        tdx = tx2 - tx1
        tdy = ty2 - ty1
        tlen = math.sqrt(tdx * tdx + tdy * tdy)
        if tlen < 0.001:
            continue
        tux, tuy = tdx / tlen, tdy / tlen

        # sin(angle) between walls
        sin_angle = abs(bux * tuy - buy * tux)
        if sin_angle < 0.01:
            continue  # parallel walls, skip

        # Extension needed = half through thickness / sin(angle)
        ext = (ts.thickness_mm / 2) / sin_angle

        # Apply to the correct end of branch wall
        idx = branch_seg.polygon
        if branch_end == "i":
            # Extend i-end backward (against direction)
            idx[0][0] -= bux * ext
            idx[0][1] -= buy * ext
            idx[3][0] -= bux * ext
            idx[3][1] -= buy * ext
        else:
            # Extend j-end forward (along direction)
            idx[1][0] += bux * ext
            idx[1][1] += buy * ext
            idx[2][0] += bux * ext
            idx[2][1] += buy * ext
        break  # Only extend once (use thickest through wall)


# ─────────────────────────────────────────────────────────
# Polygon generation
# ─────────────────────────────────────────────────────────

def _generate_polygon(node_i_xy, node_j_xy, thickness_mm):
    """4-vertex floor plan polygon from centerline + thickness."""
    x1, y1 = node_i_xy
    x2, y2 = node_j_xy
    dx = x2 - x1
    dy = y2 - y1
    length = math.sqrt(dx * dx + dy * dy)

    if length < 0.001:
        half = thickness_mm / 2
        return [
            [x1 - half, y1 + half],
            [x1 + half, y1 + half],
            [x1 + half, y1 - half],
            [x1 - half, y1 - half],
        ]

    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux  # left normal (CCW)
    half = thickness_mm / 2

    return [
        [x1 + nx * half, y1 + ny * half],  # i+
        [x2 + nx * half, y2 + ny * half],  # j+
        [x2 - nx * half, y2 - ny * half],  # j-
        [x1 - nx * half, y1 - ny * half],  # i-
    ]


# ─────────────────────────────────────────────────────────
# Segment building from DataFrame
# ─────────────────────────────────────────────────────────

def _build_segments(walls_df: pd.DataFrame,
                    nodes: Dict[str, Dict]) -> List[WallSegment]:
    """Group wall elements by (wall_id, level), find endpoints, generate polygons."""
    groups = {}
    for _, row in walls_df.iterrows():
        wid = int(row.get('wall_id', 0))
        level = str(row.get('level', ''))
        key = (wid, level)
        if key not in groups:
            groups[key] = []
        groups[key].append(row)

    segments = []
    for (wid, level), rows in groups.items():
        node_pairs = []
        element_ids = []
        thickness = 0
        height = 0
        z_bottom = float('inf')
        wall_mark = ''

        for r in rows:
            ni = str(r.get('node_i', ''))
            nj = str(r.get('node_j', ''))
            element_ids.append(str(r.get('element_id', '')))
            node_pairs.append((ni, nj))
            thickness = max(thickness, float(r.get('thickness_mm', 0) or 0))
            h = float(r.get('height_mm', 0) or 0)
            if h > height:
                height = h
            wall_mark = str(r.get('wall_mark', ''))
            ci = nodes.get(ni)
            if ci:
                z = ci.get('z_mm', float('inf'))
                if z < z_bottom:
                    z_bottom = z

        if thickness < 1:
            continue

        # Find endpoint nodes (appear once in the element chain)
        node_count = {}
        for ni, nj in node_pairs:
            node_count[ni] = node_count.get(ni, 0) + 1
            node_count[nj] = node_count.get(nj, 0) + 1

        endpoints = [n for n, c in node_count.items() if c == 1]

        if len(endpoints) >= 2:
            ep_i, ep_j = endpoints[0], endpoints[1]
        elif len(node_pairs) == 1:
            ep_i, ep_j = node_pairs[0]
        else:
            ep_i, ep_j = node_pairs[0]

        ci = nodes.get(ep_i)
        cj = nodes.get(ep_j)
        if not ci or not cj:
            continue

        node_i_xy = (ci['x_mm'], ci['y_mm'])
        node_j_xy = (cj['x_mm'], cj['y_mm'])
        polygon = _generate_polygon(node_i_xy, node_j_xy, thickness)

        segments.append(WallSegment(
            wall_id=wid, wall_mark=wall_mark, level=level,
            element_ids=element_ids,
            node_i_id=ep_i, node_j_id=ep_j,
            node_i_xy=node_i_xy, node_j_xy=node_j_xy,
            thickness_mm=thickness, height_mm=height,
            z_bottom_mm=z_bottom if z_bottom != float('inf') else 0,
            polygon=polygon,
        ))

    return segments


def _build_node_seg_map(segments):
    """node_id → [(segment, end)] map."""
    nsm = {}
    for seg in segments:
        for nid, end in [(seg.node_i_id, "i"), (seg.node_j_id, "j")]:
            if nid not in nsm:
                nsm[nid] = []
            nsm[nid].append((seg, end))
    return nsm


# ─────────────────────────────────────────────────────────
# Extension derivation
# ─────────────────────────────────────────────────────────

def _derive_extensions(seg: WallSegment) -> Tuple[float, float]:
    """Compute extend_start/end_mm from modified polygon vertices."""
    x1, y1 = seg.node_i_xy
    x2, y2 = seg.node_j_xy
    dx = x2 - x1
    dy = y2 - y1
    length = math.sqrt(dx * dx + dy * dy)
    if length < 0.001:
        return (0.0, 0.0)

    ux, uy = dx / length, dy / length
    p = seg.polygon

    # i-end: how far do polygon[0],[3] extend behind node_i?
    proj_i0 = (p[0][0] - x1) * ux + (p[0][1] - y1) * uy
    proj_i3 = (p[3][0] - x1) * ux + (p[3][1] - y1) * uy
    extend_start = max(0.0, -min(proj_i0, proj_i3))

    # j-end: how far do polygon[1],[2] extend beyond node_j?
    proj_j1 = (p[1][0] - x2) * ux + (p[1][1] - y2) * uy
    proj_j2 = (p[2][0] - x2) * ux + (p[2][1] - y2) * uy
    extend_end = max(0.0, max(proj_j1, proj_j2))

    return (round(extend_start, 1), round(extend_end, 1))


# ─────────────────────────────────────────────────────────
# Main processing
# ─────────────────────────────────────────────────────────

def process_wall_junctions(
    walls_df: pd.DataFrame,
    nodes: Dict[str, Dict],
) -> pd.DataFrame:
    """Process wall junctions using polygon algorithm.

    Returns walls_df with polygon columns and extensions added.
    """
    print('[JunctionPolygon] Building wall segments...')
    segments = _build_segments(walls_df, nodes)
    print(f'[JunctionPolygon] {len(segments)} wall segments from '
          f'{len(walls_df)} elements')

    if not segments:
        walls_df = walls_df.copy()
        for col in ['poly_0x_mm', 'poly_0y_mm', 'poly_1x_mm', 'poly_1y_mm',
                     'poly_2x_mm', 'poly_2y_mm', 'poly_3x_mm', 'poly_3y_mm',
                     'extend_start_mm', 'extend_end_mm']:
            walls_df[col] = 0.0
        return walls_df

    nsm = _build_node_seg_map(segments)

    # ── Pass 1: L-junctions (2-segment nodes) ──
    l_count = 0
    for nid, seg_list in nsm.items():
        if len(seg_list) != 2:
            continue

        seg_a, end_a = seg_list[0]
        seg_b, end_b = seg_list[1]

        near_a, far_a, edges_a = _get_poly_verts(seg_a, end_a)
        near_b, far_b, edges_b = _get_poly_verts(seg_b, end_b)

        if _verts_coincide(near_a, near_b):
            continue
        if _is_parallel(near_a, far_a, near_b, far_b):
            continue

        n_xy = (nodes[nid]['x_mm'], nodes[nid]['y_mm'])
        result = _apply_l_junction(
            seg_a, end_a, seg_b, end_b,
            near_a, far_a, edges_a,
            near_b, far_b, edges_b, n_xy
        )
        if result:
            l_count += 1

    print(f'[JunctionPolygon] Pass 1: {l_count} L-junctions')

    # ── Pass 2: T-junctions and L+T compound (3-segment nodes) ──
    t_count = 0
    lt_count = 0

    for nid, seg_list in nsm.items():
        if len(seg_list) != 3:
            continue

        n_xy = (nodes[nid]['x_mm'], nodes[nid]['y_mm'])

        infos = []
        for seg, end in seg_list:
            near, far, edges = _get_poly_verts(seg, end)
            infos.append({'seg': seg, 'end': end,
                          'near': near, 'far': far, 'edges': edges})

        # Find coinciding pair (through-wall)
        through_pair = None
        branch_idx = None
        for i in range(3):
            for j in range(i + 1, 3):
                if _verts_coincide(infos[i]['near'], infos[j]['near']):
                    through_pair = (i, j)
                    branch_idx = 3 - i - j
                    break
            if through_pair:
                break

        if through_pair is not None:
            # ── Pure T-junction: extend branch into through wall ──
            ti, tj = through_pair
            bi = branch_idx
            through_segs = [seg_list[ti][0], seg_list[tj][0]]
            _apply_t_extension(seg_list[bi][0], seg_list[bi][1],
                               through_segs, n_xy)
            t_count += 1
        else:
            # ── L+T compound: L on thick pair, then T on thin ──
            ranked = sorted(range(3),
                            key=lambda i: -(seg_list[i][0].thickness_mm))
            ta, tb, thin = ranked[0], ranked[1], ranked[2]

            seg_ta, end_ta = seg_list[ta]
            seg_tb, end_tb = seg_list[tb]

            near_ta, far_ta, edges_ta = _get_poly_verts(seg_ta, end_ta)
            near_tb, far_tb, edges_tb = _get_poly_verts(seg_tb, end_tb)

            if (not _verts_coincide(near_ta, near_tb) and
                    not _is_parallel(near_ta, far_ta, near_tb, far_tb)):
                _apply_l_junction(
                    seg_ta, end_ta, seg_tb, end_tb,
                    near_ta, far_ta, edges_ta,
                    near_tb, far_tb, edges_tb, n_xy
                )

            # Extend thin wall into thick pair
            _apply_t_extension(seg_list[thin][0], seg_list[thin][1],
                               [seg_ta, seg_tb], n_xy)
            lt_count += 1

    print(f'[JunctionPolygon] Pass 2: {t_count} T-junctions, {lt_count} L+T compound')

    # ── Handle 4+ segment nodes (cross / star junctions) ──
    # For n-wall nodes: each wall extends by half the max crossing wall's thickness.
    # L-junction vertex-move doesn't generalize well to 4+ segments (polygon
    # interference), so we use the simpler extension approach. In 3D, the walls
    # overlap at the center which is visually correct for opaque rendering.
    n_plus_count = 0
    for nid, seg_list in nsm.items():
        if len(seg_list) < 4:
            continue

        n_xy = (nodes[nid]['x_mm'], nodes[nid]['y_mm'])

        # For each segment, extend toward the junction by half the max
        # crossing wall's thickness (accounting for angle)
        all_segs = [s for s, _ in seg_list]
        for seg, end in seg_list:
            # "Crossing" = all other segments at this node
            crossing = [s for s in all_segs if s is not seg]
            _apply_t_extension(seg, end, crossing, n_xy)

        n_plus_count += 1

    if n_plus_count:
        print(f'[JunctionPolygon] Pass 3: {n_plus_count} nodes with 4+ segments (extension)')

    # ── Build output ──
    seg_map = {}
    for seg in segments:
        seg_map[(seg.wall_id, seg.level)] = seg

    walls_df = walls_df.copy()
    poly_cols = {f'poly_{i}{c}_mm': [] for i in range(4) for c in ['x', 'y']}
    ext_start_list = []
    ext_end_list = []

    for _, row in walls_df.iterrows():
        wid = int(row.get('wall_id', 0))
        level = str(row.get('level', ''))
        seg = seg_map.get((wid, level))

        if seg and seg.polygon:
            for i in range(4):
                poly_cols[f'poly_{i}x_mm'].append(round(seg.polygon[i][0], 1))
                poly_cols[f'poly_{i}y_mm'].append(round(seg.polygon[i][1], 1))
            ext_s, ext_e = _derive_extensions(seg)
            ext_start_list.append(ext_s)
            ext_end_list.append(ext_e)
        else:
            for i in range(4):
                poly_cols[f'poly_{i}x_mm'].append(0.0)
                poly_cols[f'poly_{i}y_mm'].append(0.0)
            ext_start_list.append(0.0)
            ext_end_list.append(0.0)

    for col_name, values in poly_cols.items():
        walls_df[col_name] = values
    walls_df['extend_start_mm'] = ext_start_list
    walls_df['extend_end_mm'] = ext_end_list

    ext_count = sum(1 for s, e in zip(ext_start_list, ext_end_list) if s > 0 or e > 0)
    print(f'[JunctionPolygon] {ext_count} elements with non-zero extensions')

    return walls_df


def run_junction_detection(
    columns_df: pd.DataFrame = None,
    beams_df: pd.DataFrame = None,
    walls_df: pd.DataFrame = None,
    nodes: Dict[str, Dict] = None,
) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Main entry point — process junctions for all member types.

    Walls: polygon-based algorithm (L-junction vertex-move, T-junction extension)
    Beams/Columns: simple half-thickness extension (from junction_extend)

    Returns (columns_df, beams_df, walls_df) with polygon + extension columns.
    """
    # Walls
    result_walls = walls_df
    if walls_df is not None and nodes:
        result_walls = process_wall_junctions(walls_df, nodes)

    # Beams: simple half-thickness extension (columns don't need extensions —
    # they connect vertically through floor levels via node Z coordinates)
    result_cols = columns_df
    result_beams = beams_df
    if beams_df is not None and nodes:
        try:
            from converters.junction_extend import (
                collect_endpoints, compute_extensions,
                apply_extensions_to_beams,
            )
            # Include columns + walls in endpoint collection so beams detect
            # junctions with all member types (but only apply extensions to beams)
            endpoints = collect_endpoints(columns_df, beams_df, walls_df, nodes)
            extensions = compute_extensions(endpoints)
            if beams_df is not None:
                result_beams = apply_extensions_to_beams(beams_df, extensions)
        except ImportError:
            print('[JunctionPolygon] junction_extend not available for beams/columns')

    return result_cols, result_beams, result_walls
