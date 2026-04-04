"""
Slab Rebar Length Calculator — Tier 2

Computes bar-by-bar lengths for slab reinforcement including:
- Panel role detection (SINGLE/START/INTERMEDIATE/END) via coordinate adjacency
- Thickness mismatch detection with adjacent panels
- Anchorage strategy (hook vs lap) based on role and mismatch
- Beam width lookup at slab edges for clear span
- Z-coordinate positioning within slab thickness
- Mesh coordinates for BIM visualization
- Stock length split for bars exceeding 12m

Logic adapted from RebarLengthsSlabCalculator.py (V3)
Uses coordinate-based adjacency instead of grid-based.

Input:  MembersSlab.csv, ReinforcementSlab.csv, MembersBeam.csv,
        Nodes.csv, development_lengths.csv, lap_splice.csv
Output: RebarLengthsSlab.csv
"""

import pandas as pd
import numpy as np
import re
import math
from pathlib import Path
from tier2.stock_split import split_bar

# ── Constants ────────────────────────────────────────────────────────────────

COVER_MM = 30.0  # slab cover (thinner than beam/column 50mm)
ADJACENCY_TOLERANCE = 50.0  # mm tolerance for edge matching
THICKNESS_MISMATCH_TOLERANCE = 5.0  # mm


# ── Helpers ──────────────────────────────────────────────────────────────────

def _steel_grade(dia_mm, dia_fy_map=None, fy_override=None):
    if fy_override is not None:
        return int(fy_override)
    if dia_fy_map and int(dia_mm) in dia_fy_map:
        return dia_fy_map[int(dia_mm)]
    return 400 if int(dia_mm) in (10, 13) else 600


def _dia_label(d_mm):
    return f'D{int(d_mm)}'


def _parse_fc(material_id):
    m = re.search(r'(\d+)', str(material_id).upper())
    return int(m.group(1)) if m else 35


def _bar_z(z_panel, thickness, location, dia_mm, cover=COVER_MM):
    """Z coordinate for bar within slab thickness."""
    if location == 'Top':
        return z_panel + thickness / 2 - cover - dia_mm / 2
    else:  # Bot
        return z_panel - thickness / 2 + cover + dia_mm / 2


# ── Lookup ───────────────────────────────────────────────────────────────────

class SlabDevLapLookup:
    """Development/lap splice lookup for slabs (uses SLAB_WALL member_type)."""

    def __init__(self, dev_path, lap_path):
        self.dev_df = pd.read_csv(dev_path)
        self.lap_df = pd.read_csv(lap_path)
        self.dev_df.columns = self.dev_df.columns.str.strip()
        self.lap_df.columns = self.lap_df.columns.str.strip()

    def get(self, fy, dia_mm, fc, member_type='SLAB_WALL'):
        """Returns (Ldh, Lpt, Lpb)."""
        d_label = _dia_label(dia_mm)

        dev_mt = self.dev_df[self.dev_df['member_type'] == member_type] \
            if 'member_type' in self.dev_df.columns else self.dev_df
        row_dev = dev_mt[(dev_mt['fy'] == fy) & (dev_mt['diameter'] == d_label) & (dev_mt['fc'] == fc)]
        if row_dev.empty:
            row_dev = dev_mt[(dev_mt['fy'] == fy) & (dev_mt['diameter'] == d_label)]
            if row_dev.empty:
                print(f'  [WARN] No slab dev length for fy={fy}, {d_label}, fc={fc}')
                return 200, 400, 300
            row_dev = row_dev.iloc[(row_dev['fc'] - fc).abs().argsort()[:1]]
        Ldh = float(row_dev['Ldh'].iloc[0])

        lap_mt = self.lap_df[self.lap_df['member_type'] == member_type] \
            if 'member_type' in self.lap_df.columns else self.lap_df
        row_lap = lap_mt[(lap_mt['fy'] == fy) & (lap_mt['diameter'] == d_label) & (lap_mt['fc'] == fc)]
        if row_lap.empty:
            row_lap = lap_mt[(lap_mt['fy'] == fy) & (lap_mt['diameter'] == d_label)]
            if row_lap.empty:
                return Ldh, 400, 300
            row_lap = row_lap.iloc[(row_lap['fc'] - fc).abs().argsort()[:1]]

        lpt_col = 'Lpt' if 'Lpt' in row_lap.columns else 'Lpt_B'
        lpb_col = 'Lpb' if 'Lpb' in row_lap.columns else 'Lpb_B'
        Lpt = float(row_lap[lpt_col].iloc[0])
        Lpb = float(row_lap[lpb_col].iloc[0])

        return Ldh, Lpt, Lpb


# ── Polygon scan-line clipping ───────────────────────────────────────────────

def _scanline_intersect(polygon, scan_val, scan_axis='Y'):
    """Find intersection spans of a horizontal/vertical line with a polygon.

    scan_axis='Y': horizontal line at y=scan_val → returns X spans
    scan_axis='X': vertical line at x=scan_val → returns Y spans

    Returns sorted list of (min, max) tuples representing inside spans.
    """
    n = len(polygon)
    if n < 3:
        return []

    intersections = []
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]

        if scan_axis == 'Y':
            a1, b1 = y1, x1
            a2, b2 = y2, x2
        else:
            a1, b1 = x1, y1
            a2, b2 = x2, y2

        # Check if edge crosses the scan line
        if (a1 - scan_val) * (a2 - scan_val) < 0:
            # Linear interpolation for intersection
            t = (scan_val - a1) / (a2 - a1)
            b_int = b1 + t * (b2 - b1)
            intersections.append(b_int)
        elif abs(a1 - scan_val) < 0.01 and abs(a2 - scan_val) < 0.01:
            # Edge lies on the scan line — add both endpoints
            intersections.append(b1)
            intersections.append(b2)

    intersections.sort()

    # Pair up intersections into spans (inside polygon)
    spans = []
    i = 0
    while i + 1 < len(intersections):
        span_min = intersections[i]
        span_max = intersections[i + 1]
        if span_max - span_min > 1.0:  # ignore degenerate spans < 1mm
            spans.append((span_min, span_max))
        i += 2

    return spans


def _group_bar_spans(bar_positions, polygon, bar_direction, cover, beam_w1, beam_w2):
    """Compute bar spans at each position, then group adjacent same-length bars.

    bar_positions: list of coordinate values along the distribution axis
    polygon: list of (x, y) vertices
    bar_direction: 'X' or 'Y' — which axis the bars run along
    cover, beam_w1, beam_w2: adjustments for clear span

    Returns list of groups: [{
        'span_min': float, 'span_max': float,  # bar extent (bar direction)
        'dist_start': float, 'dist_end': float,  # distribution range
        'n_bars': int,
        'l_cl': float,  # clear span length
    }]
    """
    if not bar_positions:
        return []

    # scan_axis is the distribution direction (perpendicular to bar)
    # bar_direction='X' → bars run along X, distributed along Y → scan Y
    scan_axis = 'Y' if bar_direction == 'X' else 'X'

    groups = []
    current_group = None

    for pos in bar_positions:
        spans = _scanline_intersect(polygon, pos, scan_axis=scan_axis)
        if not spans:
            # No intersection at this position — close current group
            if current_group:
                groups.append(current_group)
                current_group = None
            continue

        # Use the widest span (for slabs with holes, take the main span)
        span = max(spans, key=lambda s: s[1] - s[0])
        span_min = span[0] + beam_w1 / 2
        span_max = span[1] - beam_w2 / 2
        l_cl = span_max - span_min

        if l_cl < 10:  # skip degenerate spans
            if current_group:
                groups.append(current_group)
                current_group = None
            continue

        # Check if this bar has the same span as current group (within tolerance)
        if current_group and abs(l_cl - current_group['l_cl']) < 50:  # 50mm tolerance
            # Same group — extend
            current_group['dist_end'] = pos
            current_group['n_bars'] += 1
        else:
            # New group
            if current_group:
                groups.append(current_group)
            current_group = {
                'span_min': span_min,
                'span_max': span_max,
                'dist_start': pos,
                'dist_end': pos,
                'n_bars': 1,
                'l_cl': l_cl,
            }

    if current_group:
        groups.append(current_group)

    return groups


# ── Data Adapter ─────────────────────────────────────────────────────────────

class SlabDataAdapter:
    """Adapts Tier 1 slab data for the calculator."""

    def __init__(self, slabs_df, reinf_df, beams_df, nodes_df):
        self.slabs_df = slabs_df.copy()
        self.reinf_df = reinf_df.copy()
        self.beams_df = beams_df.copy()
        self.nodes_df = nodes_df.copy()

        # Node coordinate lookup
        self.node_coords = {}
        for _, r in nodes_df.iterrows():
            self.node_coords[str(r['node_id'])] = {
                'x_mm': float(r['x_mm']),
                'y_mm': float(r['y_mm']),
                'z_mm': float(r['z_mm']),
            }

        # Build panel bounds from boundary nodes
        self._build_panel_bounds()

        # Build beam width lookup
        self._build_beam_lookup()

        # Build reinforcement config
        self._build_reinf_config()

    def _build_panel_bounds(self):
        """Extract bounding box and polygon for each slab panel from boundary nodes."""
        self.panel_bounds = {}
        for _, s in self.slabs_df.iterrows():
            mid = s['member_id']
            bnodes = [n.strip() for n in str(s['boundary_nodes']).split(';')]
            coords = [self.node_coords[n] for n in bnodes if n in self.node_coords]
            if not coords:
                continue

            xs = [c['x_mm'] for c in coords]
            ys = [c['y_mm'] for c in coords]
            zs = [c['z_mm'] for c in coords]

            # Polygon vertices for scan-line clipping (plan XY coordinates)
            polygon = [(c['x_mm'], c['y_mm']) for c in coords]

            self.panel_bounds[mid] = {
                'x_min': min(xs), 'x_max': max(xs),
                'y_min': min(ys), 'y_max': max(ys),
                'z_mm': sum(zs) / len(zs),
                'level': s['level'],
                'thickness': float(s['thickness_mm']) if pd.notna(s['thickness_mm']) else 200,
                'Lx_mm': float(s['Lx_mm']),
                'Ly_mm': float(s['Ly_mm']),
                'polygon': polygon,
                'n_nodes': len(coords),
            }

    def _build_beam_lookup(self):
        """Build beam width lookup by coordinate range at each level."""
        self.beam_segments = []
        for _, b in self.beams_df.iterrows():
            x_from = b.get('x_from_mm', 0) or 0
            y_from = b.get('y_from_mm', 0) or 0
            x_to = b.get('x_to_mm', 0) or 0
            y_to = b.get('y_to_mm', 0) or 0
            b_mm = b.get('b_mm', 400) or 400

            self.beam_segments.append({
                'level': b.get('level', ''),
                'x_from': float(x_from), 'y_from': float(y_from),
                'x_to': float(x_to), 'y_to': float(y_to),
                'b_mm': float(b_mm),
            })

    def _build_reinf_config(self):
        """Parse reinforcement into per-slab config."""
        self.reinf_cfg = {}
        for _, r in self.reinf_df.iterrows():
            mid = str(r['member_id']).strip()
            direction = str(r['direction']).strip()  # X or Y
            layer = str(r['layer']).strip()  # Top or Bot
            dia = r.get('bar_dia_mm', 10)
            spacing = r.get('bar_spacing_mm', 200)

            if mid not in self.reinf_cfg:
                self.reinf_cfg[mid] = {}
            key = f'{direction}_{layer}'
            self.reinf_cfg[mid][key] = {
                'dia': float(dia) if pd.notna(dia) else 10,
                'spacing': float(spacing) if pd.notna(spacing) else 200,
            }

    def get_beam_width_at_edge(self, level, edge_coord, edge_axis, tolerance=200):
        """Find beam width at a slab edge.

        edge_coord: the fixed coordinate of the edge (e.g., y=7800 for a horizontal edge)
        edge_axis: 'X' if edge runs along X (fixed Y), 'Y' if edge runs along Y (fixed X)
        """
        for seg in self.beam_segments:
            if seg['level'] != level:
                continue

            if edge_axis == 'X':
                # Edge runs along X, so beam should be at this Y
                if (abs(seg['y_from'] - edge_coord) < tolerance and
                        abs(seg['y_to'] - edge_coord) < tolerance):
                    return seg['b_mm']
            elif edge_axis == 'Y':
                # Edge runs along Y, so beam should be at this X
                if (abs(seg['x_from'] - edge_coord) < tolerance and
                        abs(seg['x_to'] - edge_coord) < tolerance):
                    return seg['b_mm']

        return 400.0  # default beam width


# ── Adjacency Detection ─────────────────────────────────────────────────────

def _find_adjacent_panels(member_id, direction, adapter):
    """Find adjacent panels using coordinate proximity.

    For X-direction bars (span along X):
        Adjacent panels share the same Y range and touch at X edges.
    For Y-direction bars (span along Y):
        Adjacent panels share the same X range and touch at Y edges.

    Returns {'before': member_id or None, 'after': member_id or None}
    """
    bounds = adapter.panel_bounds.get(member_id)
    if not bounds:
        return {'before': None, 'after': None}

    level = bounds['level']
    tol = ADJACENCY_TOLERANCE
    result = {'before': None, 'after': None}

    for other_id, other_bounds in adapter.panel_bounds.items():
        if other_id == member_id or other_bounds['level'] != level:
            continue

        if direction == 'X':
            # Check if Y ranges overlap (perpendicular to bar direction)
            y_overlap = (min(bounds['y_max'], other_bounds['y_max']) -
                        max(bounds['y_min'], other_bounds['y_min']))
            if y_overlap < tol:
                continue
            # Check if X edges touch
            if abs(other_bounds['x_max'] - bounds['x_min']) < tol:
                result['before'] = other_id
            elif abs(bounds['x_max'] - other_bounds['x_min']) < tol:
                result['after'] = other_id

        elif direction == 'Y':
            # Check if X ranges overlap
            x_overlap = (min(bounds['x_max'], other_bounds['x_max']) -
                        max(bounds['x_min'], other_bounds['x_min']))
            if x_overlap < tol:
                continue
            # Check if Y edges touch
            if abs(other_bounds['y_max'] - bounds['y_min']) < tol:
                result['before'] = other_id
            elif abs(bounds['y_max'] - other_bounds['y_min']) < tol:
                result['after'] = other_id

    return result


def _get_panel_role(member_id, direction, adapter):
    """Determine panel role: SINGLE, START, INTERMEDIATE, END."""
    adj = _find_adjacent_panels(member_id, direction, adapter)
    has_before = adj['before'] is not None
    has_after = adj['after'] is not None

    if has_before and has_after:
        return 'INTERMEDIATE', adj
    elif not has_before and has_after:
        return 'START', adj
    elif has_before and not has_after:
        return 'END', adj
    else:
        return 'SINGLE', adj


def _check_mismatch(member_id, adj_id, adapter):
    """Check thickness mismatch with adjacent panel."""
    if adj_id is None:
        return False, None
    b1 = adapter.panel_bounds.get(member_id)
    b2 = adapter.panel_bounds.get(adj_id)
    if not b1 or not b2:
        return False, None
    diff = abs(b1['thickness'] - b2['thickness'])
    if diff > THICKNESS_MISMATCH_TOLERANCE:
        return True, b2['thickness']
    return False, None


# ── Anchorage Strategy ───────────────────────────────────────────────────────

def _determine_anchorage(role, mismatch_before, mismatch_after, layer):
    """Determine start/end anchorage type based on role and thickness mismatch.

    Returns (bar_role, start_type, end_type)
    """
    if layer == 'Top':
        # Top bars: hook at free edges, lap at continuous edges
        if role == 'SINGLE':
            return 'MAIN_SINGLE', 'hook', 'hook'
        elif role == 'START':
            return 'MAIN_START', 'hook', 'lap'
        elif role == 'END':
            return 'MAIN_END', 'lap', 'hook'
        else:
            return 'MAIN_INTERMEDIATE', 'lap', 'lap'
    else:
        # Bottom bars: hooks at thickness mismatches
        if role == 'SINGLE':
            return 'MAIN_SINGLE', 'hook', 'hook'
        elif role == 'START':
            if mismatch_after:
                return 'MAIN_START_ANCHOR', 'hook', 'hook'
            return 'MAIN_START', 'hook', 'lap'
        elif role == 'END':
            if mismatch_before:
                return 'MAIN_END_ANCHOR', 'hook', 'hook'
            return 'MAIN_END', 'lap', 'hook'
        else:
            if mismatch_before and mismatch_after:
                return 'MAIN_INTER_ANCHOR_BOTH', 'hook', 'hook'
            elif mismatch_before:
                return 'MAIN_INTER_ANCHOR_START', 'hook', 'lap'
            elif mismatch_after:
                return 'MAIN_INTER_ANCHOR_END', 'lap', 'hook'
            return 'MAIN_INTERMEDIATE', 'lap', 'lap'


def _compute_bar_length(l_cl, Wg1, Wg2, Ldh, Llap, start_type, end_type):
    """Compute bar length with anchorage."""
    L = l_cl
    if start_type == 'hook':
        L += Ldh
    else:
        L += 0.5 * Wg1  # extend to column/beam face for lap
    if end_type == 'hook':
        L += Ldh
    else:
        L += Llap
    return L


# ── Public API ───────────────────────────────────────────────────────────────

def calculate_slab_rebar_lengths(
    slabs_df: pd.DataFrame,
    reinf_df: pd.DataFrame,
    beams_df: pd.DataFrame,
    nodes_df: pd.DataFrame,
    dev_lengths_path: str,
    lap_splice_path: str,
    fc: int = 35,
    dia_fy_map: dict = None,
) -> pd.DataFrame:
    """
    Calculate slab rebar lengths from Tier 1 converter output.

    Returns DataFrame for RebarLengthsSlab.csv
    """
    print('[RebarSlab] Loading lookup tables...')
    lookup = SlabDevLapLookup(dev_lengths_path, lap_splice_path)

    print('[RebarSlab] Building data adapter...')
    adapter = SlabDataAdapter(slabs_df, reinf_df, beams_df, nodes_df)
    print(f'[RebarSlab] {len(adapter.panel_bounds)} panels, '
          f'{len(adapter.reinf_cfg)} reinf configs, '
          f'{len(adapter.beam_segments)} beam segments')

    results = []

    for _, slab in slabs_df.iterrows():
        mid = slab['member_id']
        level = slab['level']
        bounds = adapter.panel_bounds.get(mid)
        if not bounds:
            continue

        thickness = bounds['thickness']
        Lx = bounds['Lx_mm']
        Ly = bounds['Ly_mm']
        z_panel = bounds['z_mm']

        # Short direction
        short_dir = 'X' if Lx <= Ly else 'Y'

        # Get reinforcement config
        cfg = adapter.reinf_cfg.get(mid)
        if not cfg:
            continue

        # Process each direction
        for direction in ['X', 'Y']:
            # Panel role in this direction
            role, adj = _get_panel_role(mid, direction, adapter)
            mismatch_before, adj_thk_before = _check_mismatch(mid, adj['before'], adapter)
            mismatch_after, adj_thk_after = _check_mismatch(mid, adj['after'], adapter)

            # Span length and beam widths
            if direction == 'X':
                span = Lx
                # Beam widths at left/right edges (Y-running beams)
                Wg1 = adapter.get_beam_width_at_edge(level, bounds['x_min'], 'Y')
                Wg2 = adapter.get_beam_width_at_edge(level, bounds['x_max'], 'Y')
                # Distribution along Y
                dist_Wg1 = adapter.get_beam_width_at_edge(level, bounds['y_min'], 'X')
                dist_Wg2 = adapter.get_beam_width_at_edge(level, bounds['y_max'], 'X')
                dist_span = Ly
            else:
                span = Ly
                Wg1 = adapter.get_beam_width_at_edge(level, bounds['y_min'], 'X')
                Wg2 = adapter.get_beam_width_at_edge(level, bounds['y_max'], 'X')
                dist_Wg1 = adapter.get_beam_width_at_edge(level, bounds['x_min'], 'Y')
                dist_Wg2 = adapter.get_beam_width_at_edge(level, bounds['x_max'], 'Y')
                dist_span = Lx

            l_cl = span - 0.5 * (Wg1 + Wg2)

            # Process Top and Bot layers
            for layer in ['Top', 'Bot']:
                cfg_key = f'{direction}_{layer}'
                bar_cfg = cfg.get(cfg_key)
                if not bar_cfg:
                    continue

                dia = bar_cfg['dia']
                spacing = bar_cfg['spacing']
                fy = _steel_grade(dia)
                Ldh, Lpt, Lpb = lookup.get(fy, dia, fc)
                Llap = Lpt if layer == 'Top' else Lpb

                # Anchorage strategy
                bar_role, start_type, end_type = _determine_anchorage(
                    role, mismatch_before, mismatch_after, layer)

                # Z coordinate
                z_bar = _bar_z(z_panel, thickness, layer, dia)

                polygon = bounds.get('polygon', [])
                n_nodes = bounds.get('n_nodes', 4)

                # ── Polygon slabs (non-rectangular): scan-line grouped bars ──
                if n_nodes >= 3 and len(polygon) >= 3 and spacing > 0:
                    # Generate bar positions along distribution direction
                    if direction == 'X':
                        dist_start = bounds['y_min'] + dist_Wg1 / 2
                        dist_end = bounds['y_max'] - dist_Wg2 / 2
                        beam_w1, beam_w2 = Wg1, Wg2
                    else:
                        dist_start = bounds['x_min'] + dist_Wg1 / 2
                        dist_end = bounds['x_max'] - dist_Wg2 / 2
                        beam_w1, beam_w2 = Wg1, Wg2

                    n_pos = int((dist_end - dist_start) / spacing) + 1
                    bar_positions = [dist_start + i * spacing for i in range(n_pos)]

                    groups = _group_bar_spans(
                        bar_positions, polygon, direction,
                        COVER_MM, beam_w1, beam_w2)

                    for grp in groups:
                        grp_l_cl = grp['l_cl']
                        L_bar = _compute_bar_length(
                            grp_l_cl, Wg1, Wg2, Ldh, Llap, start_type, end_type)

                        if direction == 'X':
                            mesh_origin_x = grp['span_min']
                            mesh_terminus_x = grp['span_max']
                            mesh_origin_y = grp['dist_start']
                            mesh_terminus_y = mesh_origin_y
                            mesh_dist_axis = 'Y'
                        else:
                            mesh_origin_y = grp['span_min']
                            mesh_terminus_y = grp['span_max']
                            mesh_origin_x = grp['dist_start']
                            mesh_terminus_x = mesh_origin_x
                            mesh_dist_axis = 'X'

                        bar_record = {
                            'member_id': mid, 'level': level,
                            'slab_type': slab.get('slab_type', ''),
                            'thickness_mm': thickness,
                            'direction': direction, 'layer': layer,
                            'bar_role': bar_role,
                            'start_type': start_type, 'end_type': end_type,
                            'dia_mm': int(dia), 'spacing_mm': int(spacing),
                            'n_bars': grp['n_bars'],
                            'length_mm': int(round(L_bar)),
                            'l_cl_mm': round(grp_l_cl, 1),
                            'Wg1_mm': round(Wg1, 1), 'Wg2_mm': round(Wg2, 1),
                            'Ldh_mm': round(Ldh, 1), 'Llap_mm': round(Llap, 1),
                            'Lx_mm': round(Lx, 1), 'Ly_mm': round(Ly, 1),
                            'short_direction': short_dir,
                            'panel_role': role,
                            'mismatch_before': mismatch_before,
                            'mismatch_after': mismatch_after,
                            'adj_thickness_before_mm': adj_thk_before,
                            'adj_thickness_after_mm': adj_thk_after,
                            'centroid_x_mm': round(slab['centroid_x_mm'], 1) if pd.notna(slab.get('centroid_x_mm')) else None,
                            'centroid_y_mm': round(slab['centroid_y_mm'], 1) if pd.notna(slab.get('centroid_y_mm')) else None,
                            'z_mm': round(z_panel, 1),
                            'mesh_origin_x_mm': round(mesh_origin_x, 1),
                            'mesh_origin_y_mm': round(mesh_origin_y, 1),
                            'mesh_origin_z_mm': round(z_bar, 1),
                            'mesh_terminus_x_mm': round(mesh_terminus_x, 1),
                            'mesh_terminus_y_mm': round(mesh_terminus_y, 1),
                            'mesh_terminus_z_mm': round(z_bar, 1),
                            'mesh_distribution_axis': mesh_dist_axis,
                        }
                        for piece in split_bar(bar_record, Llap):
                            results.append(piece)

                    continue  # skip rectangular fallback

                # ── Rectangular slabs (4 nodes): original logic ──
                L_bar = _compute_bar_length(l_cl, Wg1, Wg2, Ldh, Llap, start_type, end_type)

                dist_width = dist_span - 0.5 * (dist_Wg1 + dist_Wg2)
                n_bars = int(dist_width / spacing) + 1 if spacing > 0 else 0

                if direction == 'X':
                    mesh_origin_x = bounds['x_min'] + Wg1 / 2
                    mesh_terminus_x = bounds['x_max'] - Wg2 / 2
                    mesh_origin_y = bounds['y_min'] + dist_Wg1 / 2
                    mesh_terminus_y = mesh_origin_y
                    mesh_dist_axis = 'Y'
                else:
                    mesh_origin_y = bounds['y_min'] + Wg1 / 2
                    mesh_terminus_y = bounds['y_max'] - Wg2 / 2
                    mesh_origin_x = bounds['x_min'] + dist_Wg1 / 2
                    mesh_terminus_x = mesh_origin_x
                    mesh_dist_axis = 'X'

                bar_record = {
                    'member_id': mid,
                    'level': level,
                    'slab_type': slab.get('slab_type', ''),
                    'thickness_mm': thickness,
                    'direction': direction,
                    'layer': layer,
                    'bar_role': bar_role,
                    'start_type': start_type,
                    'end_type': end_type,
                    'dia_mm': int(dia),
                    'spacing_mm': int(spacing),
                    'n_bars': n_bars,
                    'length_mm': int(round(L_bar)),
                    'l_cl_mm': round(l_cl, 1),
                    'Wg1_mm': round(Wg1, 1),
                    'Wg2_mm': round(Wg2, 1),
                    'Ldh_mm': round(Ldh, 1),
                    'Llap_mm': round(Llap, 1),
                    'Lx_mm': round(Lx, 1),
                    'Ly_mm': round(Ly, 1),
                    'short_direction': short_dir,
                    'panel_role': role,
                    'mismatch_before': mismatch_before,
                    'mismatch_after': mismatch_after,
                    'adj_thickness_before_mm': adj_thk_before,
                    'adj_thickness_after_mm': adj_thk_after,
                    'centroid_x_mm': round(slab['centroid_x_mm'], 1) if pd.notna(slab.get('centroid_x_mm')) else None,
                    'centroid_y_mm': round(slab['centroid_y_mm'], 1) if pd.notna(slab.get('centroid_y_mm')) else None,
                    'z_mm': round(z_panel, 1),
                    'mesh_origin_x_mm': round(mesh_origin_x, 1),
                    'mesh_origin_y_mm': round(mesh_origin_y, 1),
                    'mesh_origin_z_mm': round(z_bar, 1),
                    'mesh_terminus_x_mm': round(mesh_terminus_x, 1),
                    'mesh_terminus_y_mm': round(mesh_terminus_y, 1),
                    'mesh_terminus_z_mm': round(z_bar, 1),
                    'mesh_distribution_axis': mesh_dist_axis,
                }

                # Stock length split (>12m → multiple pieces with lap)
                for piece in split_bar(bar_record, Llap):
                    results.append(piece)

    df = pd.DataFrame(results)

    # Summary
    if not df.empty:
        main = df[~df['bar_role'].str.contains('ADD', na=False)]
        roles = df['panel_role'].value_counts().to_dict()
        mismatches = df['mismatch_before'].sum() + df['mismatch_after'].sum()
        print(f'[RebarSlab] {len(df)} records from {df["member_id"].nunique()} panels')
        print(f'[RebarSlab] Panel roles: {roles}')
        if mismatches:
            print(f'[RebarSlab] {int(mismatches)} thickness mismatches detected')

    # Add total_length_mm
    if not df.empty and 'total_length_mm' not in df.columns:
        df['total_length_mm'] = (df['length_mm'] * df['n_bars']).astype(int)

    return df
