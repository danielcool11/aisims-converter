"""
Basement Wall Rebar Length Calculator — Tier 2

Computes bar-by-bar lengths for basement/retaining wall reinforcement:
- 3×3 zone grid (Left/Middle/Right × Top/Middle/Bottom) per panel
- Two faces (Interior/Exterior) with independent reinforcement
- Vertical bars with continuity-aware stacking (per-level walls)
- Horizontal bars with U-bar at free edges
- Full-height walls (B4~B1) handled as single panels
- Stock length split for bars exceeding 12m

Adapted from rebar_lengths_wall.py with zone-based reinforcement.

Input:  MembersBasementWall.csv, ReinforcementBasementWall.csv,
        Nodes.csv, development_lengths.csv, lap_splice.csv
Output: RebarLengthsBasementWall.csv
"""

import pandas as pd
import numpy as np
import math
import re
import os
from tier2.stock_split import split_bar

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_COVER_MM = 50.0
HOOK_EXT_FACTOR = 10


def _load_cover(cover_path=None):
    if cover_path and os.path.exists(cover_path):
        df = pd.read_csv(cover_path)
        for _, row in df.iterrows():
            if str(row.get('member_type', '')).strip().upper() == 'WALL':
                return float(row['cover_mm'])
    return DEFAULT_COVER_MM


# ── Helpers ──────────────────────────────────────────────────────────────────

def _steel_grade(dia):
    return 500 if int(dia) in (10, 13) else 600


def _dia_label(d):
    return f'D{int(d)}'


def _level_sort_key(level):
    """Sort key for basement levels: B4=-104, B3=-103, B2=-102, B1=-101."""
    s = str(level).strip().upper()
    if '~' in s:
        # Full-height: B4~B1 → use lowest level
        parts = s.split('~')
        return min(_level_sort_key(p) for p in parts)
    m = re.match(r'B(\d+)', s)
    if m:
        return -100 - int(m.group(1))
    return 0


def _is_full_height(level):
    """Check if this is a full-height wall spanning multiple levels."""
    return '~' in str(level)


# ── Lookup ───────────────────────────────────────────────────────────────────

class BWallDevLapLookup:
    """Development/lap splice lookup for basement walls (uses SLAB_WALL)."""

    def __init__(self, dev_path, lap_path):
        self.dev_df = pd.read_csv(dev_path)
        self.lap_df = pd.read_csv(lap_path)
        self.dev_df.columns = self.dev_df.columns.str.strip()
        self.lap_df.columns = self.lap_df.columns.str.strip()

    def get(self, fy, dia_mm, fc, member_type='SLAB_WALL'):
        """Returns dict with Ldh, Lpc (vertical lap), Lpb, Lpt."""
        d_label = _dia_label(dia_mm)

        dev_mt = self.dev_df[self.dev_df['member_type'] == member_type] \
            if 'member_type' in self.dev_df.columns else self.dev_df
        row_dev = dev_mt[(dev_mt['fy'] == fy) & (dev_mt['diameter'] == d_label) & (dev_mt['fc'] == fc)]
        if row_dev.empty:
            row_dev = dev_mt[(dev_mt['fy'] == fy) & (dev_mt['diameter'] == d_label)]
            if row_dev.empty:
                print(f'  [WARN] No dev length for fy={fy}, {d_label}, fc={fc}')
                return {'Ldh': 200, 'Lpc': 400, 'Lpb': 300, 'Lpt': 400}
            row_dev = row_dev.iloc[(row_dev['fc'] - fc).abs().argsort()[:1]]
        Ldh = float(row_dev['Ldh'].iloc[0])

        lap_mt = self.lap_df[self.lap_df['member_type'] == member_type] \
            if 'member_type' in self.lap_df.columns else self.lap_df
        row_lap = lap_mt[(lap_mt['fy'] == fy) & (lap_mt['diameter'] == d_label) & (lap_mt['fc'] == fc)]
        if row_lap.empty:
            row_lap = lap_mt[(lap_mt['fy'] == fy) & (lap_mt['diameter'] == d_label)]
            if row_lap.empty:
                return {'Ldh': Ldh, 'Lpc': 400, 'Lpb': 300, 'Lpt': 400}
            row_lap = row_lap.iloc[(row_lap['fc'] - fc).abs().argsort()[:1]]

        Lpc = float(row_lap['Lpc'].iloc[0]) if 'Lpc' in row_lap.columns else 400
        Lpb = float(row_lap['Lpb'].iloc[0]) if 'Lpb' in row_lap.columns else 300
        Lpt = float(row_lap['Lpt'].iloc[0]) if 'Lpt' in row_lap.columns else 400

        return {'Ldh': Ldh, 'Lpc': Lpc, 'Lpb': Lpb, 'Lpt': Lpt}


# ── Continuity Detection ────────────────────────────────────────────────────

def _split_into_continuous_groups(stack):
    """Split a per-level wall stack into continuous groups using Z-based gaps.

    Same logic as standard wall calculator.
    """
    if len(stack) <= 1:
        return [stack]

    Z_TOLERANCE = 200  # mm
    stack_sorted = sorted(stack, key=lambda s: s['z_mm'] - s['height_mm'] / 2)

    groups = [[stack_sorted[0]]]
    for i in range(1, len(stack_sorted)):
        prev = stack_sorted[i - 1]
        curr = stack_sorted[i]
        prev_top = prev['z_mm'] + prev['height_mm'] / 2
        curr_bot = curr['z_mm'] - curr['height_mm'] / 2
        if abs(curr_bot - prev_top) <= Z_TOLERANCE:
            groups[-1].append(curr)
        else:
            groups.append([curr])

    return groups


# ── Zone Dimension Helpers ───────────────────────────────────────────────────

def _get_zone_width(panel, zone):
    """Get width of a horizontal zone (LEFT/MIDDLE/RIGHT/FULL)."""
    zone_upper = str(zone).upper()
    if zone_upper == 'LEFT':
        return float(panel.get('zone_width_left_mm', 0) or 0)
    elif zone_upper == 'MIDDLE':
        return float(panel.get('zone_width_middle_mm', 0) or 0)
    elif zone_upper == 'RIGHT':
        return float(panel.get('zone_width_right_mm', 0) or 0)
    elif zone_upper == 'FULL':
        return float(panel.get('length_mm', 0) or 0)
    return 0


def _get_zone_height(panel, zone):
    """Get height of a vertical zone (TOP/MIDDLE/BOTTOM)."""
    zone_upper = str(zone).upper()
    if zone_upper == 'TOP':
        return float(panel.get('zone_height_top_mm', 0) or 0)
    elif zone_upper == 'MIDDLE':
        return float(panel.get('zone_height_middle_mm', 0) or 0)
    elif zone_upper == 'BOTTOM':
        return float(panel.get('zone_height_bottom_mm', 0) or 0)
    return 0


def _get_zone_x_offset(panel, zone):
    """Get X offset from wall start to zone start (for horizontal zones)."""
    zone_upper = str(zone).upper()
    if zone_upper == 'LEFT' or zone_upper == 'FULL':
        return 0
    elif zone_upper == 'MIDDLE':
        return float(panel.get('zone_width_left_mm', 0) or 0)
    elif zone_upper == 'RIGHT':
        left = float(panel.get('zone_width_left_mm', 0) or 0)
        mid = float(panel.get('zone_width_middle_mm', 0) or 0)
        return left + mid
    return 0


def _get_zone_z_offset(panel, zone):
    """Get Z offset from wall bottom to zone bottom (for vertical zones)."""
    zone_upper = str(zone).upper()
    if zone_upper == 'BOTTOM':
        return 0
    elif zone_upper == 'MIDDLE':
        return float(panel.get('zone_height_bottom_mm', 0) or 0)
    elif zone_upper == 'TOP':
        bot = float(panel.get('zone_height_bottom_mm', 0) or 0)
        mid = float(panel.get('zone_height_middle_mm', 0) or 0)
        return bot + mid
    return 0


# ── Wall Plan Geometry ──────────────────────────────────────────────────────

def _get_wall_plan(panel, node_coords):
    """Compute wall start/end in structural XY.

    Priority:
    1. start_x_mm/end_x_mm from ELEMENT sheet (actual horizontal endpoints)
    2. node_i/node_j coordinates (works for P1 horizontal nodes)
    3. centroid ± length/2 fallback

    Returns (start_x, start_y, end_x, end_y) in structural mm.
    """
    # 1. Use ELEMENT sheet endpoints if available (fixes P2 vertical panels)
    sx = panel.get('start_x_mm')
    sy = panel.get('start_y_mm')
    ex = panel.get('end_x_mm')
    ey = panel.get('end_y_mm')
    if pd.notna(sx) and pd.notna(ex):
        sx, sy, ex, ey = float(sx), float(sy), float(ex), float(ey)
        if abs(sx - ex) > 1 or abs(sy - ey) > 1:
            return sx, sy, ex, ey

    # 2. Use quad node coordinates (P1 style — horizontal span)
    ni = str(panel.get('node_i', ''))
    nj = str(panel.get('node_j', ''))
    ci = node_coords.get(ni)
    cj = node_coords.get(nj)
    if ci and cj:
        plan_d = math.sqrt((ci['x_mm'] - cj['x_mm'])**2 + (ci['y_mm'] - cj['y_mm'])**2)
        if plan_d > 10:
            return ci['x_mm'], ci['y_mm'], cj['x_mm'], cj['y_mm']

    # 3. Fallback: centroid ± length/2
    cx = float(panel.get('centroid_x_mm', 0) or 0)
    cy = float(panel.get('centroid_y_mm', 0) or 0)
    length = float(panel.get('length_mm', 0) or 0)
    return cx - length / 2, cy, cx + length / 2, cy


def _actual_panel_length(panel, node_coords):
    """Get the actual plan length of a panel from its quad nodes.

    For L-shaped walls, each panel may have a different length even though
    the boundary data reports the same length_mm for all panels.
    """
    sx, sy, ex, ey = _get_wall_plan(panel, node_coords)
    return math.sqrt((ex - sx) ** 2 + (ey - sy) ** 2)


def _wall_plan_origin(panel, node_coords, cover, offset_along=0.0):
    """Compute a world-frame XY position along the wall plan direction.

    offset_along: distance from wall start along the plan direction (mm).
    Returns (world_x, world_y) in structural mm.
    """
    sx, sy, ex, ey = _get_wall_plan(panel, node_coords)
    dx = ex - sx
    dy = ey - sy
    length = math.sqrt(dx * dx + dy * dy)
    if length < 1:
        return sx + offset_along, sy

    # Unit direction along wall
    ux = dx / length
    uy = dy / length

    return sx + ux * offset_along, sy + uy * offset_along


# ── Main Processing ─────────────────────────────────────────────────────────

def _process_vertical_bars(panel, reinf_rows, lookup, cover, fc, role_prefix, results, node_coords=None):
    """Process vertical bars for a single panel.

    Vertical bars: distributed along wall length, within each vertical zone.
    Zone determines bar height (TOP/MIDDLE/BOTTOM).
    """
    wall_mark = panel['wall_mark']
    level = panel['level']
    thickness = float(panel['thickness_mm'])
    height = float(panel['height_mm'])
    nominal_length = float(panel['length_mm'])
    z_center = float(panel['z_mm'])
    z_bottom = z_center - height / 2
    cx = float(panel.get('centroid_x_mm', 0) or 0)
    cy = float(panel.get('centroid_y_mm', 0) or 0)

    # Use actual panel length from quad nodes (may differ from nominal for L-shapes)
    length = _actual_panel_length(panel, node_coords) if node_coords else nominal_length
    if length < 100:
        length = nominal_length  # fallback

    for _, r in reinf_rows.iterrows():
        zone = str(r['zone']).strip()
        face = str(r['face']).strip()
        dia = int(r['dia_mm'])
        spacing = int(r['spacing_mm'])

        fy = _steel_grade(dia)
        dev = lookup.get(fy, dia, fc)
        Ldh = dev['Ldh']
        Lpc = dev['Lpc']

        zone_h = _get_zone_height(panel, zone)
        if zone_h <= 0:
            continue

        zone_z_off = _get_zone_z_offset(panel, zone)

        # Bar length depends on role
        zone_upper = zone.upper()
        if role_prefix == 'SINGLE':
            L_bar = zone_h + Ldh
        elif role_prefix == 'BOTTOM':
            L_bar = zone_h + Lpc
        elif role_prefix == 'TOP':
            L_bar = zone_h + Ldh
        elif role_prefix == 'INTERMEDIATE':
            L_bar = zone_h + Lpc
        elif role_prefix == 'FULL_HEIGHT':
            L_bar = zone_h + Ldh
        else:
            L_bar = zone_h + Ldh

        # Number of vertical bars: use extended panel length (including junctions)
        ext_start = float(panel.get('extend_start_mm', 0) or 0)
        ext_end = float(panel.get('extend_end_mm', 0) or 0)
        extended_length = length + ext_start + ext_end
        n_bars = int(math.floor((extended_length - 2 * cover) / spacing)) + 1 if spacing > 0 else 0

        # Mesh coordinates — compute in WORLD structural frame
        bar_z_bot = z_bottom + zone_z_off + cover
        bar_z_top = z_bottom + zone_z_off + zone_h

        # First bar position: at wall start + cover, shifted into junction zone
        # Also compute wall plan direction for renderer distribution
        if node_coords:
            ox, oy = _wall_plan_origin(panel, node_coords, cover, offset_along=cover - ext_start)
            sx, sy, ex, ey = _get_wall_plan(panel, node_coords)
        else:
            ox = cx - length / 2 + cover - ext_start
            oy = cy
            sx, sy = cx - length / 2, cy
            ex, ey = cx + length / 2, cy

        # Unit direction along wall plan (for renderer V-bar distribution)
        wdx = ex - sx
        wdy = ey - sy
        wlen = math.sqrt(wdx * wdx + wdy * wdy)
        if wlen > 0.001:
            wall_dir_x = round(wdx / wlen * length, 1)  # scaled by panel length
            wall_dir_y = round(wdy / wlen * length, 1)
        else:
            wall_dir_x = round(length, 1)
            wall_dir_y = 0.0

        bar_record = {
            'wall_mark': wall_mark,
            'level': level,
            'direction': 'VERTICAL',
            'face': face,
            'zone': zone,
            'bar_role': f'{role_prefix}_V_{zone_upper}',
            'dia_mm': dia,
            'spacing_mm': spacing,
            'n_bars': n_bars,
            'length_mm': int(round(L_bar)),
            'total_length_mm': int(round(L_bar * n_bars)),
            'height_mm': height,
            'length_wall_mm': length,
            'thickness_mm': thickness,
            'zone_height_mm': round(zone_h, 1),
            'Ldh_mm': round(Ldh, 1),
            'Lpc_mm': round(Lpc, 1),
            'cover_mm': cover,
            'mesh_origin_x_mm': round(ox, 1),
            'mesh_origin_y_mm': round(oy, 1),
            'mesh_origin_z_mm': round(bar_z_bot, 1),
            'mesh_terminus_x_mm': round(ox, 1),
            'mesh_terminus_y_mm': round(oy, 1),
            'mesh_terminus_z_mm': round(bar_z_top, 1),
            'mesh_distribution_axis': 'ALONG_WALL_LENGTH',
            'wall_dir_x_mm': wall_dir_x,
            'wall_dir_y_mm': wall_dir_y,
        }

        for piece in split_bar(bar_record, Lpc):
            results.append(piece)


def _width_zone_for_vertical(zone):
    """Vertical zones (TOP/MID/BOT) span the full wall length.
    Return None — we use full length, not a width zone."""
    return None


def _process_horizontal_bars(panel, reinf_rows, lookup, cover, fc, results, node_coords=None):
    """Process horizontal bars for a single panel.

    Horizontal bars: distributed along wall height, within each horizontal zone.
    Zone determines bar width (LEFT/MIDDLE/RIGHT).
    """
    wall_mark = panel['wall_mark']
    level = panel['level']
    thickness = float(panel['thickness_mm'])
    height = float(panel['height_mm'])
    nominal_length = float(panel['length_mm'])
    z_center = float(panel['z_mm'])
    z_bottom = z_center - height / 2
    cx = float(panel.get('centroid_x_mm', 0) or 0)
    cy = float(panel.get('centroid_y_mm', 0) or 0)

    # Use actual panel length from quad nodes
    actual_length = _actual_panel_length(panel, node_coords) if node_coords else nominal_length
    if actual_length < 100:
        actual_length = nominal_length

    for _, r in reinf_rows.iterrows():
        zone = str(r['zone']).strip()
        face = str(r['face']).strip()
        dia = int(r['dia_mm'])
        spacing = int(r['spacing_mm'])

        fy = _steel_grade(dia)
        dev = lookup.get(fy, dia, fc)
        Ldh = dev['Ldh']

        zone_w = _get_zone_width(panel, zone)
        if zone_w <= 0:
            continue

        zone_x_off = _get_zone_x_offset(panel, zone)

        # Clamp zone offset + width to actual panel length
        if zone_x_off + zone_w > actual_length:
            zone_w = max(0, actual_length - zone_x_off)
        if zone_x_off >= actual_length:
            continue  # entire zone is beyond this panel's extent

        # Junction extensions for H-bar at wall endpoints:
        # LEFT zone gets extend_start, RIGHT zone gets extend_end
        ext_start = float(panel.get('extend_start_mm', 0) or 0)
        ext_end = float(panel.get('extend_end_mm', 0) or 0)
        zone_upper = str(zone).upper()
        if zone_upper == 'LEFT':
            rebar_ext_start = max(0, ext_start - cover) if ext_start > 0 else 0
            rebar_ext_end = 0
        elif zone_upper == 'RIGHT':
            rebar_ext_start = 0
            rebar_ext_end = max(0, ext_end - cover) if ext_end > 0 else 0
        elif zone_upper == 'FULL':
            rebar_ext_start = max(0, ext_start - cover) if ext_start > 0 else 0
            rebar_ext_end = max(0, ext_end - cover) if ext_end > 0 else 0
        else:
            rebar_ext_start = 0
            rebar_ext_end = 0

        # H-bar = straight bar along zone width + junction extensions at both ends
        rebar_ext = rebar_ext_start + rebar_ext_end
        L_h_bar = zone_w + rebar_ext

        # Number of horizontal bars along wall height
        n_bars = int(math.floor((height - 2 * cover) / spacing)) + 1 if spacing > 0 else 0

        # Mesh coordinates — compute in WORLD structural frame
        # H-bar runs along wall plan direction within the zone
        # Extend mesh into junction zone for LEFT (start) and RIGHT (end) zones
        ext_offset_start = rebar_ext_start
        ext_offset_end = rebar_ext_end
        if node_coords:
            ox_start, oy_start = _wall_plan_origin(
                panel, node_coords, cover, offset_along=zone_x_off + cover - ext_offset_start)
            ox_end, oy_end = _wall_plan_origin(
                panel, node_coords, cover, offset_along=zone_x_off + zone_w - cover + ext_offset_end)
        else:
            ox_start = cx - length / 2 + zone_x_off + cover - ext_offset_start
            oy_start = cy
            ox_end = cx - length / 2 + zone_x_off + zone_w - cover + ext_offset_end
            oy_end = cy

        bar_record = {
            'wall_mark': wall_mark,
            'level': level,
            'direction': 'HORIZONTAL',
            'face': face,
            'zone': zone,
            'bar_role': f'H_{zone.upper()}',
            'dia_mm': dia,
            'spacing_mm': spacing,
            'n_bars': n_bars,
            'length_mm': int(round(L_h_bar)),
            'total_length_mm': int(round(L_h_bar * n_bars)),
            'height_mm': height,
            'length_wall_mm': actual_length,
            'thickness_mm': thickness,
            'zone_width_mm': round(zone_w, 1),
            'Ldh_mm': round(Ldh, 1),
            'Lpc_mm': None,
            'cover_mm': cover,
            'mesh_origin_x_mm': round(ox_start, 1),
            'mesh_origin_y_mm': round(oy_start, 1),
            'mesh_origin_z_mm': round(z_bottom + cover, 1),
            'mesh_terminus_x_mm': round(ox_end, 1),
            'mesh_terminus_y_mm': round(oy_end, 1),
            'mesh_terminus_z_mm': round(z_bottom + cover, 1),
            'mesh_distribution_axis': 'ALONG_WALL_HEIGHT',
        }

        for piece in split_bar(bar_record, Ldh):
            results.append(piece)

        # ── U-BAR (separate cap piece at zone endpoints) ──
        # U-bar at LEFT zone = wall start, RIGHT zone = wall end
        # MIDDLE zone gets no U-bars (internal to wall)
        if zone_upper in ('LEFT', 'RIGHT', 'FULL'):
            U_bar_width = max(0, thickness - 2 * cover)
            leg_len = min(Ldh, actual_length / 2)  # cap leg at half wall length
            U_bar_len = 2 * leg_len + U_bar_width

            # U-bar mesh: origin at wall endpoint, terminus points toward wall center
            # (leg direction). Renderer computes connector perpendicular to this.
            sx, sy, ex, ey = _get_wall_plan(panel, node_coords) if node_coords else (
                cx - nominal_length / 2, cy, cx + nominal_length / 2, cy)
            wall_dx = ex - sx
            wall_dy = ey - sy
            wlen = math.sqrt(wall_dx * wall_dx + wall_dy * wall_dy)
            if wlen > 0.001:
                uwx, uwy = wall_dx / wlen, wall_dy / wlen
            else:
                uwx, uwy = 1.0, 0.0

            # Generate U-bars at both ends for FULL zone, one end for LEFT/RIGHT
            ubar_positions = []
            if zone_upper in ('LEFT', 'FULL'):
                # Start end
                uox = sx - uwx * rebar_ext
                uoy = sy - uwy * rebar_ext
                utx = uox + uwx * leg_len
                uty = uoy + uwy * leg_len
                ubar_positions.append((uox, uoy, utx, uty))
            if zone_upper in ('RIGHT', 'FULL'):
                # End end
                uox = ex + uwx * rebar_ext
                uoy = ey + uwy * rebar_ext
                utx = uox - uwx * leg_len
                uty = uoy - uwy * leg_len
                ubar_positions.append((uox, uoy, utx, uty))

            for ubar_ox, ubar_oy, ubar_tx, ubar_ty in ubar_positions:
                ubar_record = {
                    'wall_mark': wall_mark,
                    'level': level,
                    'direction': 'HORIZONTAL',
                    'face': face,
                    'zone': zone,
                    'bar_role': 'U_BAR',
                    'dia_mm': dia,
                    'spacing_mm': spacing,
                    'n_bars': n_bars,
                    'length_mm': int(round(U_bar_len)),
                    'total_length_mm': int(round(U_bar_len * n_bars)),
                    'height_mm': height,
                    'length_wall_mm': actual_length,
                    'thickness_mm': thickness,
                    'zone_width_mm': round(zone_w, 1),
                    'Ldh_mm': round(Ldh, 1),
                    'Lpc_mm': None,
                    'cover_mm': cover,
                    'mesh_origin_x_mm': round(ubar_ox, 1),
                    'mesh_origin_y_mm': round(ubar_oy, 1),
                    'mesh_origin_z_mm': round(z_bottom + cover, 1),
                    'mesh_terminus_x_mm': round(ubar_tx, 1),
                    'mesh_terminus_y_mm': round(ubar_ty, 1),
                    'mesh_terminus_z_mm': round(z_bottom + cover, 1),
                    'mesh_distribution_axis': 'ALONG_WALL_HEIGHT',
                }
                results.append(ubar_record)


# ── Dowel Generation ────────────────────────────────────────────────────────

def _emit_bwall_dowel(panel, reinf_rows, lookup, cover, fc, results, node_coords=None):
    """Emit dowel bars at the bottom of the wall's lowest level.

    Dowels are short vertical bars: Ldh below wall base (into slab/footing)
    and Lpc above wall base (lapping with wall's BOTTOM vertical bar).
    Generated for each face using the BOTTOM zone's vertical bar spec.
    """
    wall_mark = panel['wall_mark']
    level = panel['level']
    thickness = float(panel['thickness_mm'])
    nominal_length = float(panel['length_mm'])
    z_center = float(panel['z_mm'])
    height = float(panel['height_mm'])
    z_bottom = z_center - height / 2

    actual_length = _actual_panel_length(panel, node_coords) if node_coords else nominal_length
    if actual_length < 100:
        actual_length = nominal_length

    for _, r in reinf_rows.iterrows():
        zone = str(r['zone']).strip().upper()
        if zone != 'BOTTOM':
            continue

        face = str(r['face']).strip()
        dia = int(r['dia_mm'])
        spacing = int(r['spacing_mm'])

        fy = _steel_grade(dia)
        dev = lookup.get(fy, dia, fc)
        Ldh = dev['Ldh']
        Lpc = dev['Lpc']

        dowel_len = Ldh + Lpc
        n_bars = int(math.floor((actual_length - 2 * cover) / spacing)) + 1 if spacing > 0 else 0

        # Dowel Z range: Ldh below wall base → Lpc above wall base
        z_start = z_bottom - Ldh
        z_end = z_bottom + Lpc

        # Mesh coordinates in world frame
        if node_coords:
            ox, oy = _wall_plan_origin(panel, node_coords, cover, offset_along=cover)
        else:
            cx = float(panel.get('centroid_x_mm', 0) or 0)
            ox = cx - nominal_length / 2 + cover
            oy = float(panel.get('centroid_y_mm', 0) or 0)

        # Wall plan direction for distribution
        sx, sy, ex, ey = _get_wall_plan(panel, node_coords) if node_coords else (
            ox, oy, ox + nominal_length, oy)
        wdir_x = ex - sx
        wdir_y = ey - sy

        results.append({
            'wall_mark': wall_mark,
            'level': level,
            'direction': 'VERTICAL',
            'face': face,
            'zone': 'DOWEL',
            'bar_role': 'DOWEL',
            'dia_mm': dia,
            'spacing_mm': spacing,
            'n_bars': n_bars,
            'length_mm': int(round(dowel_len)),
            'total_length_mm': int(round(dowel_len * n_bars)),
            'height_mm': height,
            'length_wall_mm': actual_length,
            'thickness_mm': thickness,
            'zone_height_mm': None,
            'Ldh_mm': round(Ldh, 1),
            'Lpc_mm': round(Lpc, 1),
            'cover_mm': cover,
            'mesh_origin_x_mm': round(ox, 1),
            'mesh_origin_y_mm': round(oy, 1),
            'mesh_origin_z_mm': round(z_start + cover, 1),
            'mesh_terminus_x_mm': round(ox, 1),
            'mesh_terminus_y_mm': round(oy, 1),
            'mesh_terminus_z_mm': round(z_end, 1),
            'mesh_distribution_axis': 'ALONG_WALL_LENGTH',
            'wall_dir_x_mm': round(wdir_x, 1),
            'wall_dir_y_mm': round(wdir_y, 1),
        })


# ── Public API ───────────────────────────────────────────────────────────────

def calculate_basement_wall_rebar_lengths(
    members_df: pd.DataFrame,
    reinf_df: pd.DataFrame,
    nodes_df: pd.DataFrame,
    dev_lengths_path: str,
    lap_splice_path: str,
    fc: int = 35,
    cover_path: str = None,
) -> pd.DataFrame:
    """
    Calculate basement wall rebar lengths from Tier 1 output.

    Returns DataFrame for RebarLengthsBasementWall.csv
    """
    print('[RebarBWall] Loading lookup tables...')
    lookup = BWallDevLapLookup(dev_lengths_path, lap_splice_path)
    cover = _load_cover(cover_path)
    print(f'[RebarBWall] Cover: {cover}mm')

    # Build node coordinate lookup for Z reference
    node_coords = {}
    for _, r in nodes_df.iterrows():
        node_coords[str(r['node_id'])] = {
            'x_mm': float(r['x_mm']), 'y_mm': float(r['y_mm']), 'z_mm': float(r['z_mm'])
        }

    # Build reinforcement lookup: (wall_mark, level, direction) → rows
    reinf_lookup = {}
    for _, r in reinf_df.iterrows():
        key = (str(r['wall_mark']).strip(), str(r['level']).strip(), str(r['direction']).strip())
        if key not in reinf_lookup:
            reinf_lookup[key] = []
        reinf_lookup[key].append(r)

    # Group panels by wall_mark
    wall_groups = {}
    for _, m in members_df.iterrows():
        wm = str(m['wall_mark']).strip()
        if wm not in wall_groups:
            wall_groups[wm] = []
        wall_groups[wm].append(m.to_dict())

    # Filter out panels with unreliable coordinates (INFERRED/PARTIAL/MISSING)
    skipped_panels = 0
    for wm in list(wall_groups.keys()):
        original = wall_groups[wm]
        if 'node_status' in members_df.columns:
            filtered = [p for p in original if p.get('node_status', 'OK') == 'OK']
            skipped_panels += len(original) - len(filtered)
            wall_groups[wm] = filtered
        # Remove wall entirely if no panels left
        if not wall_groups[wm]:
            del wall_groups[wm]

    print(f'[RebarBWall] {len(wall_groups)} walls, {len(members_df)} panels, '
          f'{len(reinf_df)} reinforcement records')
    if skipped_panels:
        print(f'[RebarBWall] Skipped {skipped_panels} panels with unreliable node coordinates '
              f'(INFERRED/PARTIAL/MISSING)')

    results = []

    for wm, panels in wall_groups.items():
        # Separate full-height panels from per-level panels
        full_height = [p for p in panels if _is_full_height(p['level'])]
        per_level = [p for p in panels if not _is_full_height(p['level'])]

        # Process full-height panels (no stacking needed)
        for panel in full_height:
            level = panel['level']

            # Vertical bars
            v_key = (wm, level, 'VERTICAL')
            v_rows = reinf_lookup.get(v_key, [])
            if v_rows:
                v_df = pd.DataFrame(v_rows)
                _process_vertical_bars(panel, v_df, lookup, cover, fc, 'FULL_HEIGHT', results, node_coords)

                # Dowels for full-height walls too
                _emit_bwall_dowel(panel, v_df, lookup, cover, fc, results, node_coords)

            # Horizontal bars
            h_key = (wm, level, 'HORIZONTAL')
            h_rows = reinf_lookup.get(h_key, [])
            if h_rows:
                h_df = pd.DataFrame(h_rows)
                _process_horizontal_bars(panel, h_df, lookup, cover, fc, results, node_coords)

        # Emit dowels at the lowest level of per-level walls
        if per_level:
            # Find the lowest level (deepest Z) among all per-level panels
            lowest_z = min(p['z_mm'] - p['height_mm'] / 2 for p in per_level)
            lowest_panels = [p for p in per_level
                             if abs((p['z_mm'] - p['height_mm'] / 2) - lowest_z) < 100]
            for panel in lowest_panels:
                level = panel['level']
                v_key = (wm, level, 'VERTICAL')
                v_rows = reinf_lookup.get(v_key, [])
                if v_rows:
                    v_df = pd.DataFrame(v_rows)
                    _emit_bwall_dowel(panel, v_df, lookup, cover, fc, results, node_coords)

        # Process per-level panels with continuity stacking
        if per_level:
            groups = _split_into_continuous_groups(per_level)
            for group in groups:
                for idx, panel in enumerate(group):
                    level = panel['level']
                    is_first = (idx == 0)
                    is_last = (idx == len(group) - 1)

                    if is_first and is_last:
                        role = 'SINGLE'
                    elif is_first:
                        role = 'BOTTOM'
                    elif is_last:
                        role = 'TOP'
                    else:
                        role = 'INTERMEDIATE'

                    # Vertical bars
                    v_key = (wm, level, 'VERTICAL')
                    v_rows = reinf_lookup.get(v_key, [])
                    if v_rows:
                        v_df = pd.DataFrame(v_rows)
                        _process_vertical_bars(panel, v_df, lookup, cover, fc, role, results, node_coords)

                    # Horizontal bars
                    h_key = (wm, level, 'HORIZONTAL')
                    h_rows = reinf_lookup.get(h_key, [])
                    if h_rows:
                        h_df = pd.DataFrame(h_rows)
                        _process_horizontal_bars(panel, h_df, lookup, cover, fc, results, node_coords)

    df = pd.DataFrame(results)

    if not df.empty:
        v_count = len(df[df['direction'] == 'VERTICAL'])
        h_count = len(df[df['direction'] == 'HORIZONTAL'])
        split_ct = len(df[df['split_piece'].notna()]) if 'split_piece' in df.columns else 0
        print(f'[RebarBWall] {len(df)} records ({v_count} vertical, {h_count} horizontal) '
              f'from {df["wall_mark"].nunique()} walls')
        if split_ct:
            print(f'[RebarBWall] {split_ct} records from stock length splits')

    return df
