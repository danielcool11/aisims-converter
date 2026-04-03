"""
Footing Rebar Length Calculator — Tier 2

Computes bar-by-bar lengths for mat foundation reinforcement:
- BASE: main bars across full footing footprint (L-shaped support)
- ADDITIONAL: extra bars in high-stress zones near columns
- STIRRUP: vertical ties at column locations

Handles L-shaped footprints via zone_boundary sub-rectangles.
Multi-part continuity: bars span across adjacent parts where they share edges.
Stock length split for bars exceeding 12m.

Input:  MembersFooting.csv, ReinforcementFooting.csv,
        development_lengths.csv, lap_splice.csv
Output: RebarLengthsFooting.csv
"""

import pandas as pd
import numpy as np
import math
import re
import os
from tier2.stock_split import split_bar

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_COVER_MM = 75.0  # KDS footing cover (ground contact)
HOOK_EXT_FACTOR = 12     # 90-degree hook = 12 × dia


# ── Polygon Scan-Line Clipping ──────────────────────────────────────────────

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
            t = (scan_val - a1) / (a2 - a1)
            b_int = b1 + t * (b2 - b1)
            intersections.append(b_int)
        elif abs(a1 - scan_val) < 0.01 and abs(a2 - scan_val) < 0.01:
            # Edge lies on the scan line
            intersections.append(b1)
            intersections.append(b2)

    intersections.sort()

    # Pair up intersections into spans (inside polygon)
    spans = []
    i = 0
    while i + 1 < len(intersections):
        span_min = intersections[i]
        span_max = intersections[i + 1]
        if span_max - span_min > 1.0:
            spans.append((span_min, span_max))
        i += 2

    return spans


def _group_bar_spans_polygon(bar_positions, polygon, bar_direction, cover):
    """Compute bar spans at each position via scan-line, group adjacent same-length bars.

    bar_positions: coordinate values along the distribution axis
    polygon: list of (x, y) vertices
    bar_direction: 'X' or 'Y' — which axis the bars run along
    cover: footing cover (mm)

    Returns list of groups: [{
        'bar_span_mm': float,       # gross span within polygon
        'bar_start': float,         # bar start coordinate (bar direction)
        'bar_end': float,           # bar end coordinate (bar direction)
        'dist_start': float,        # first bar position (distribution axis)
        'dist_end': float,          # last bar position (distribution axis)
        'dist_axis': str,           # 'X' or 'Y'
        'n_bars': int,
    }]
    """
    if not bar_positions:
        return []

    # scan_axis is perpendicular to bar direction
    scan_axis = 'Y' if bar_direction == 'X' else 'X'
    dist_axis = 'Y' if bar_direction == 'X' else 'X'

    groups = []
    current_group = None
    GROUP_TOL = 50  # mm tolerance for grouping same-length bars

    for pos in bar_positions:
        spans = _scanline_intersect(polygon, pos, scan_axis=scan_axis)
        if not spans:
            if current_group:
                groups.append(current_group)
                current_group = None
            continue

        # Use the widest span
        span = max(spans, key=lambda s: s[1] - s[0])
        span_min = span[0]
        span_max = span[1]
        bar_span = span_max - span_min

        if bar_span < 2 * cover + 10:  # skip degenerate
            if current_group:
                groups.append(current_group)
                current_group = None
            continue

        if current_group and abs(bar_span - current_group['bar_span_mm']) < GROUP_TOL:
            # Same group — extend
            current_group['dist_end'] = pos
            current_group['n_bars'] += 1
        else:
            if current_group:
                groups.append(current_group)
            current_group = {
                'bar_span_mm': bar_span,
                'bar_start': span_min,
                'bar_end': span_max,
                'dist_start': pos,
                'dist_end': pos,
                'dist_axis': dist_axis,
                'n_bars': 1,
            }

    if current_group:
        groups.append(current_group)

    return groups


def _load_cover(cover_path=None):
    if cover_path and os.path.exists(cover_path):
        df = pd.read_csv(cover_path)
        for _, row in df.iterrows():
            if str(row.get('member_type', '')).strip().upper() == 'FOOTING':
                return float(row['cover_mm'])
    return DEFAULT_COVER_MM


# ── Helpers ──────────────────────────────────────────────────────────────────

def _steel_grade(dia):
    return 500 if int(dia) in (10, 13) else 600


def _dia_label(d):
    return f'D{int(d)}'


def _parse_fc(material_id):
    m = re.search(r'(\d+)', str(material_id).upper())
    return int(m.group(1)) if m else 35


# ── Lookup ───────────────────────────────────────────────────────────────────

class FootingDevLapLookup:
    """Development/lap splice lookup for footings."""

    def __init__(self, dev_path, lap_path):
        self.dev_df = pd.read_csv(dev_path)
        self.lap_df = pd.read_csv(lap_path)
        self.dev_df.columns = self.dev_df.columns.str.strip()
        self.lap_df.columns = self.lap_df.columns.str.strip()

    def get(self, fy, dia_mm, fc, member_type='FOOTING'):
        """Returns (Ldh, Lpb, Lpt)."""
        d_label = _dia_label(dia_mm)

        # Development length
        dev_mt = self.dev_df[self.dev_df['member_type'] == member_type] \
            if 'member_type' in self.dev_df.columns else self.dev_df
        row_dev = dev_mt[(dev_mt['fy'] == fy) & (dev_mt['diameter'] == d_label) & (dev_mt['fc'] == fc)]
        if row_dev.empty:
            row_dev = dev_mt[(dev_mt['fy'] == fy) & (dev_mt['diameter'] == d_label)]
            if row_dev.empty:
                print(f'  [WARN] No footing dev length for fy={fy}, {d_label}, fc={fc}')
                return 400, 600, 500
            row_dev = row_dev.iloc[(row_dev['fc'] - fc).abs().argsort()[:1]]
        Ldh = float(row_dev['Ldh'].iloc[0])

        # Lap splice
        lap_mt = self.lap_df[self.lap_df['member_type'] == member_type] \
            if 'member_type' in self.lap_df.columns else self.lap_df
        row_lap = lap_mt[(lap_mt['fy'] == fy) & (lap_mt['diameter'] == d_label) & (lap_mt['fc'] == fc)]
        if row_lap.empty:
            row_lap = lap_mt[(lap_mt['fy'] == fy) & (lap_mt['diameter'] == d_label)]
            if row_lap.empty:
                return Ldh, 600, 500
            row_lap = row_lap.iloc[(row_lap['fc'] - fc).abs().argsort()[:1]]

        Lpb = float(row_lap['Lpb'].iloc[0])
        Lpt = float(row_lap['Lpt'].iloc[0])

        return Ldh, Lpb, Lpt


# ── Zone Boundary Parsing ────────────────────────────────────────────────────

def _parse_zone_boundary(boundary_str):
    """Parse pipe-separated quad boundary into list of sub-rectangles.

    Input: "(0,0);(11600,0);(0,13600);(11600,13600) | (7750,13600);..."
    Returns: (sub_rects, polygon_or_None)
        sub_rects: [{'x_min', 'x_max', 'y_min', 'y_max'}, ...]
        polygon: [(x,y), ...] if single segment with >4 points, else None
    """
    if pd.isna(boundary_str) or not str(boundary_str).strip():
        return [], None

    sub_rects = []
    all_segments_points = []  # track raw points per segment

    for segment in str(boundary_str).split('|'):
        segment = segment.strip()
        if not segment:
            continue
        # Parse (x,y) pairs
        points = re.findall(r'\(([^)]+)\)', segment)
        if not points:
            # Try semicolon-separated x,y pairs without parens
            points_raw = segment.split(';')
            parsed = []
            for pt in points_raw:
                parts = pt.strip().split(',')
                if len(parts) >= 2:
                    try:
                        parsed.append((float(parts[0].strip()), float(parts[1].strip())))
                    except ValueError:
                        continue
            all_segments_points.append(parsed)
            xs = [p[0] for p in parsed]
            ys = [p[1] for p in parsed]
        else:
            parsed = []
            xs, ys = [], []
            for pt in points:
                parts = pt.split(',')
                if len(parts) >= 2:
                    x, y = float(parts[0].strip()), float(parts[1].strip())
                    xs.append(x)
                    ys.append(y)
                    parsed.append((x, y))
            all_segments_points.append(parsed)

        if xs and ys:
            rect = {
                'x_min': min(xs), 'x_max': max(xs),
                'y_min': min(ys), 'y_max': max(ys),
            }
            # Filter degenerate rects
            if rect['x_max'] - rect['x_min'] > 1 and rect['y_max'] - rect['y_min'] > 1:
                sub_rects.append(rect)

    # Detect polygon: single segment with >4 unique points
    polygon = None
    if len(all_segments_points) == 1 and len(all_segments_points[0]) > 4:
        polygon = all_segments_points[0]

    return sub_rects, polygon


# ── Bar Span Computation ────────────────────────────────────────────────────

def _compute_bar_groups(sub_rects, direction):
    """Compute bar groups for BASE reinforcement in an L-shaped footprint.

    For X-direction bars: each sub-rect is independent (bars span its X width).
    For Y-direction bars: merge vertically adjacent sub-rects sharing X overlap.
    (And vice versa.)

    Returns list of:
        {'bar_span_mm': float, 'dist_start': float, 'dist_end': float,
         'bar_start': float, 'bar_end': float, 'dist_axis': str}
    where dist_start/end are the range along the distribution axis.
    """
    if not sub_rects:
        return []

    if direction == 'X':
        # X-bars span each sub-rect's X width, distributed along Y
        # Merge sub-rects that are X-adjacent (share Y overlap) for longer bars
        return _merge_spans(sub_rects, span_axis='X')
    else:
        # Y-bars span each sub-rect's Y height, distributed along X
        # Merge sub-rects that are Y-adjacent (share X overlap) for longer bars
        return _merge_spans(sub_rects, span_axis='Y')


def _merge_spans(sub_rects, span_axis='Y'):
    """Merge sub-rectangles along span_axis for continuous bars.

    For span_axis='Y': bars run along Y, distributed along X.
    Find unique X-strips, then for each strip merge Y-adjacent sub-rects.

    For span_axis='X': bars run along X, distributed along Y.
    Find unique Y-strips, then for each strip merge X-adjacent sub-rects.
    """
    MERGE_TOL = 50  # mm tolerance for edge adjacency

    if span_axis == 'Y':
        # Collect all unique X boundaries
        x_bounds = sorted(set(
            [r['x_min'] for r in sub_rects] + [r['x_max'] for r in sub_rects]
        ))

        groups = []
        for i in range(len(x_bounds) - 1):
            strip_x_min = x_bounds[i]
            strip_x_max = x_bounds[i + 1]
            strip_mid = (strip_x_min + strip_x_max) / 2

            # Find sub-rects covering this X-strip
            covering = [r for r in sub_rects
                       if r['x_min'] <= strip_mid + MERGE_TOL
                       and r['x_max'] >= strip_mid - MERGE_TOL]

            if not covering:
                continue

            # Sort by y_min and merge adjacent
            covering.sort(key=lambda r: r['y_min'])
            merged_y_min = covering[0]['y_min']
            merged_y_max = covering[0]['y_max']

            for j in range(1, len(covering)):
                if covering[j]['y_min'] <= merged_y_max + MERGE_TOL:
                    merged_y_max = max(merged_y_max, covering[j]['y_max'])
                else:
                    # Gap: emit previous span
                    groups.append({
                        'bar_span_mm': merged_y_max - merged_y_min,
                        'bar_start': merged_y_min,
                        'bar_end': merged_y_max,
                        'dist_start': strip_x_min,
                        'dist_end': strip_x_max,
                        'dist_axis': 'X',
                    })
                    merged_y_min = covering[j]['y_min']
                    merged_y_max = covering[j]['y_max']

            groups.append({
                'bar_span_mm': merged_y_max - merged_y_min,
                'bar_start': merged_y_min,
                'bar_end': merged_y_max,
                'dist_start': strip_x_min,
                'dist_end': strip_x_max,
                'dist_axis': 'X',
            })

        # Merge adjacent groups with same bar span (same length bars)
        return _consolidate_groups(groups)

    else:  # span_axis == 'X'
        # Collect all unique Y boundaries
        y_bounds = sorted(set(
            [r['y_min'] for r in sub_rects] + [r['y_max'] for r in sub_rects]
        ))

        groups = []
        for i in range(len(y_bounds) - 1):
            strip_y_min = y_bounds[i]
            strip_y_max = y_bounds[i + 1]
            strip_mid = (strip_y_min + strip_y_max) / 2

            covering = [r for r in sub_rects
                       if r['y_min'] <= strip_mid + MERGE_TOL
                       and r['y_max'] >= strip_mid - MERGE_TOL]

            if not covering:
                continue

            covering.sort(key=lambda r: r['x_min'])
            merged_x_min = covering[0]['x_min']
            merged_x_max = covering[0]['x_max']

            for j in range(1, len(covering)):
                if covering[j]['x_min'] <= merged_x_max + MERGE_TOL:
                    merged_x_max = max(merged_x_max, covering[j]['x_max'])
                else:
                    groups.append({
                        'bar_span_mm': merged_x_max - merged_x_min,
                        'bar_start': merged_x_min,
                        'bar_end': merged_x_max,
                        'dist_start': strip_y_min,
                        'dist_end': strip_y_max,
                        'dist_axis': 'Y',
                    })
                    merged_x_min = covering[j]['x_min']
                    merged_x_max = covering[j]['x_max']

            groups.append({
                'bar_span_mm': merged_x_max - merged_x_min,
                'bar_start': merged_x_min,
                'bar_end': merged_x_max,
                'dist_start': strip_y_min,
                'dist_end': strip_y_max,
                'dist_axis': 'Y',
            })

        return _consolidate_groups(groups)


def _consolidate_groups(groups):
    """Merge adjacent groups that have the same bar_span and bar_start/end."""
    if not groups:
        return []

    consolidated = [groups[0].copy()]
    for g in groups[1:]:
        prev = consolidated[-1]
        # Same bar span AND adjacent distribution range
        if (abs(g['bar_span_mm'] - prev['bar_span_mm']) < 1 and
                abs(g['bar_start'] - prev['bar_start']) < 1 and
                abs(g['bar_end'] - prev['bar_end']) < 1 and
                abs(g['dist_start'] - prev['dist_end']) < 50):
            prev['dist_end'] = g['dist_end']
        else:
            consolidated.append(g.copy())

    return consolidated


# ── Zone Processors ──────────────────────────────────────────────────────────

def _process_base_zone(member_id, zone_row, thickness, z_mm, lookup, cover, fc, results):
    """Process BASE reinforcement zone."""
    direction = str(zone_row['direction']).strip()
    layer = str(zone_row['layer']).strip()
    dia = int(zone_row['dia_mm'])
    spacing = int(zone_row['spacing_mm'])

    fy = _steel_grade(dia)
    Ldh, Lpb, Lpt = lookup.get(fy, dia, fc)
    Llap = Lpt if layer == 'Top' else Lpb

    # Parse zone boundary into sub-rectangles + optional polygon
    sub_rects, polygon = _parse_zone_boundary(zone_row.get('zone_boundary', ''))
    if not sub_rects:
        # Fallback: use zone bounding box
        sub_rects = [{
            'x_min': float(zone_row['zone_x_min']),
            'x_max': float(zone_row['zone_x_max']),
            'y_min': float(zone_row['zone_y_min']),
            'y_max': float(zone_row['zone_y_max']),
        }]

    bar_role = f'BASE_{direction}_{layer.upper()}'

    # Z coordinate within footing thickness (z_mm = footing top surface)
    if layer == 'Top':
        z_bar = z_mm - cover - dia / 2
    else:
        z_bar = z_mm - thickness + cover + dia / 2

    # ── Polygon footing: scan-line clipping ──
    if polygon and len(polygon) > 4 and spacing > 0:
        # Bounding box of polygon for distribution range
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]

        if direction == 'X':
            # Bars run along X, distributed along Y
            dist_start = min(ys) + cover
            dist_end = max(ys) - cover
        else:
            # Bars run along Y, distributed along X
            dist_start = min(xs) + cover
            dist_end = max(xs) - cover

        n_pos = int((dist_end - dist_start) / spacing) + 1
        bar_positions = [dist_start + i * spacing for i in range(n_pos)]

        bar_groups = _group_bar_spans_polygon(bar_positions, polygon, direction, cover)

        print(f'  [POLYGON] {member_id} {bar_role}: {len(polygon)} vertices, '
              f'{len(bar_groups)} bar groups ({n_pos} positions)')

    else:
        # ── Rectangular footing: existing logic ──
        bar_groups = _compute_bar_groups(sub_rects, direction)

    for grp in bar_groups:
        bar_span = grp['bar_span_mm']
        clear_span = bar_span - 2 * cover
        L_bar = clear_span + 2 * Ldh  # hook at both free edges

        if 'n_bars' in grp:
            # Polygon path: n_bars already computed
            n_bars = grp['n_bars']
            dist_width = grp['dist_end'] - grp['dist_start']
        else:
            dist_width = grp['dist_end'] - grp['dist_start']
            n_bars = int(dist_width / spacing) + 1 if spacing > 0 else 0

        # Mesh coordinates
        if direction == 'X':
            mesh = {
                'mesh_origin_x_mm': round(grp['bar_start'] + cover, 1),
                'mesh_origin_y_mm': round(grp['dist_start'], 1),
                'mesh_origin_z_mm': round(z_bar, 1),
                'mesh_terminus_x_mm': round(grp['bar_end'] - cover, 1),
                'mesh_terminus_y_mm': round(grp['dist_start'], 1),
                'mesh_terminus_z_mm': round(z_bar, 1),
            }
        else:
            mesh = {
                'mesh_origin_x_mm': round(grp['dist_start'], 1),
                'mesh_origin_y_mm': round(grp['bar_start'] + cover, 1),
                'mesh_origin_z_mm': round(z_bar, 1),
                'mesh_terminus_x_mm': round(grp['dist_start'], 1),
                'mesh_terminus_y_mm': round(grp['bar_end'] - cover, 1),
                'mesh_terminus_z_mm': round(z_bar, 1),
            }

        bar_record = {
            'member_id': member_id,
            'zone': zone_row['zone'],
            'zone_type': 'BASE',
            'direction': direction,
            'layer': layer,
            'bar_role': bar_role,
            'dia_mm': dia,
            'spacing_mm': spacing,
            'n_bars': n_bars,
            'length_mm': int(round(L_bar)),
            'total_length_mm': int(round(L_bar * n_bars)),
            'Ldh_mm': round(Ldh, 1),
            'Llap_mm': round(Llap, 1),
            'cover_mm': cover,
            'bar_span_mm': round(bar_span, 1),
            'dist_width_mm': round(dist_width, 1),
            'mesh_distribution_axis': grp['dist_axis'],
            **mesh,
        }

        for piece in split_bar(bar_record, Llap):
            results.append(piece)


def _process_additional_zone(member_id, zone_row, thickness, z_mm, lookup, cover, fc, results):
    """Process ADDITIONAL reinforcement zone."""
    direction = str(zone_row['direction']).strip()
    layer = str(zone_row['layer']).strip()
    dia = int(zone_row['dia_mm'])
    spacing = int(zone_row['spacing_mm'])

    fy = _steel_grade(dia)
    Ldh, Lpb, Lpt = lookup.get(fy, dia, fc)
    Llap = Lpt if layer == 'Top' else Lpb

    zx_min = float(zone_row['zone_x_min'])
    zx_max = float(zone_row['zone_x_max'])
    zy_min = float(zone_row['zone_y_min'])
    zy_max = float(zone_row['zone_y_max'])

    if direction == 'X':
        bar_span = zx_max - zx_min
        dist_width = zy_max - zy_min
    else:
        bar_span = zy_max - zy_min
        dist_width = zx_max - zx_min

    clear_span = bar_span - 2 * cover
    L_bar = clear_span + 2 * Ldh

    n_bars = int(dist_width / spacing) + 1 if spacing > 0 else 0

    if layer == 'Top':
        z_bar = z_mm - cover - dia / 2
    else:
        z_bar = z_mm - thickness + cover + dia / 2

    if direction == 'X':
        mesh = {
            'mesh_origin_x_mm': round(zx_min + cover, 1),
            'mesh_origin_y_mm': round(zy_min + cover, 1),
            'mesh_origin_z_mm': round(z_bar, 1),
            'mesh_terminus_x_mm': round(zx_max - cover, 1),
            'mesh_terminus_y_mm': round(zy_min + cover, 1),
            'mesh_terminus_z_mm': round(z_bar, 1),
        }
    else:
        mesh = {
            'mesh_origin_x_mm': round(zx_min + cover, 1),
            'mesh_origin_y_mm': round(zy_min + cover, 1),
            'mesh_origin_z_mm': round(z_bar, 1),
            'mesh_terminus_x_mm': round(zx_min + cover, 1),
            'mesh_terminus_y_mm': round(zy_max - cover, 1),
            'mesh_terminus_z_mm': round(z_bar, 1),
        }

    bar_record = {
        'member_id': member_id,
        'zone': zone_row['zone'],
        'zone_type': 'ADDITIONAL',
        'direction': direction,
        'layer': layer,
        'bar_role': f'ADDITIONAL_{direction}_{layer.upper()}',
        'dia_mm': dia,
        'spacing_mm': spacing,
        'n_bars': n_bars,
        'length_mm': int(round(L_bar)),
        'total_length_mm': int(round(L_bar * n_bars)),
        'Ldh_mm': round(Ldh, 1),
        'Llap_mm': round(Llap, 1),
        'cover_mm': cover,
        'bar_span_mm': round(bar_span, 1),
        'dist_width_mm': round(dist_width, 1),
        'mesh_distribution_axis': 'Y' if direction == 'X' else 'X',
        **mesh,
    }

    for piece in split_bar(bar_record, Llap):
        results.append(piece)


def _process_stirrup_zone(member_id, zone_row, thickness, z_mm, lookup, cover, fc, results):
    """Process STIRRUP reinforcement zone."""
    dia = int(zone_row['dia_mm'])
    spacing = int(zone_row['spacing_mm'])
    n_legs = int(zone_row['n_legs']) if pd.notna(zone_row.get('n_legs')) else 2

    fy = _steel_grade(dia)
    Ldh, _, _ = lookup.get(fy, dia, fc)

    # Stirrup bar: vertical tie within footing thickness
    hook_ext = HOOK_EXT_FACTOR * dia
    L_bar = thickness - 2 * cover + 2 * hook_ext

    # Number of stirrup sets from zone dimensions
    zx_min = float(zone_row['zone_x_min'])
    zx_max = float(zone_row['zone_x_max'])
    zy_min = float(zone_row['zone_y_min'])
    zy_max = float(zone_row['zone_y_max'])

    zone_lx = zx_max - zx_min
    zone_ly = zy_max - zy_min
    n_x = int(zone_lx / spacing) + 1 if spacing > 0 else 1
    n_y = int(zone_ly / spacing) + 1 if spacing > 0 else 1
    n_total = n_x * n_y * n_legs

    z_bot = z_mm - thickness + cover
    z_top = z_mm - cover

    mesh = {
        'mesh_origin_x_mm': round(zx_min + cover, 1),
        'mesh_origin_y_mm': round(zy_min + cover, 1),
        'mesh_origin_z_mm': round(z_bot, 1),
        'mesh_terminus_x_mm': round(zx_min + cover, 1),
        'mesh_terminus_y_mm': round(zy_min + cover, 1),
        'mesh_terminus_z_mm': round(z_top, 1),
    }

    results.append({
        'member_id': member_id,
        'zone': zone_row['zone'],
        'zone_type': 'STIRRUP',
        'direction': 'VERTICAL',
        'layer': None,
        'bar_role': 'STIRRUP',
        'dia_mm': dia,
        'spacing_mm': spacing,
        'n_bars': n_total,
        'length_mm': int(round(L_bar)),
        'total_length_mm': int(round(L_bar * n_total)),
        'Ldh_mm': round(Ldh, 1),
        'Llap_mm': None,
        'cover_mm': cover,
        'bar_span_mm': round(thickness, 1),
        'dist_width_mm': None,
        'mesh_distribution_axis': 'XY_GRID',
        'split_piece': None,
        'split_total': None,
        'original_length_mm': None,
        **mesh,
    })


# ── Public API ───────────────────────────────────────────────────────────────

def calculate_footing_rebar_lengths(
    members_df: pd.DataFrame,
    reinf_df: pd.DataFrame,
    dev_lengths_path: str,
    lap_splice_path: str,
    fc: int = 35,
    cover_path: str = None,
) -> pd.DataFrame:
    """
    Calculate footing rebar lengths from Tier 1 output.

    Returns DataFrame for RebarLengthsFooting.csv
    """
    print('[RebarFooting] Loading lookup tables...')
    lookup = FootingDevLapLookup(dev_lengths_path, lap_splice_path)
    cover = _load_cover(cover_path)
    print(f'[RebarFooting] Cover: {cover}mm')

    # Build member lookup: member_id → {thickness, z_mm, material_id}
    member_info = {}
    for _, m in members_df.iterrows():
        mid = m['member_id']
        if mid not in member_info:
            member_info[mid] = {
                'thickness': float(m['thickness_mm']) if pd.notna(m['thickness_mm']) else 700,
                'z_mm': float(m['z_mm']) if pd.notna(m['z_mm']) else 0,
                'material_id': m.get('material_id', 'C35'),
            }

    print(f'[RebarFooting] {len(member_info)} footings, {len(reinf_df)} reinforcement zones')

    results = []

    for _, zone_row in reinf_df.iterrows():
        mid = str(zone_row['member_id']).strip()
        zone_type = str(zone_row['zone_type']).strip().upper()

        info = member_info.get(mid)
        if not info:
            print(f'  [WARN] No member info for {mid}')
            continue

        thickness = info['thickness']
        z_mm = info['z_mm']

        if zone_type == 'BASE':
            _process_base_zone(mid, zone_row, thickness, z_mm, lookup, cover, fc, results)
        elif zone_type == 'ADDITIONAL':
            _process_additional_zone(mid, zone_row, thickness, z_mm, lookup, cover, fc, results)
        elif zone_type == 'STIRRUP':
            _process_stirrup_zone(mid, zone_row, thickness, z_mm, lookup, cover, fc, results)
        else:
            print(f'  [WARN] Unknown zone_type: {zone_type}')

    df = pd.DataFrame(results)

    if not df.empty:
        base_ct = len(df[df['zone_type'] == 'BASE'])
        add_ct = len(df[df['zone_type'] == 'ADDITIONAL'])
        stir_ct = len(df[df['zone_type'] == 'STIRRUP'])
        split_ct = len(df[df['split_piece'].notna()]) if 'split_piece' in df.columns else 0
        print(f'[RebarFooting] {len(df)} records ({base_ct} base, {add_ct} additional, '
              f'{stir_ct} stirrup)')
        if split_ct:
            print(f'[RebarFooting] {split_ct} records from stock length splits')

    return df
