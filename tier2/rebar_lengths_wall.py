"""
Wall Rebar Length Calculator — Tier 2

Computes bar-by-bar lengths for standard wall reinforcement:
- Vertical bars with continuity-aware stacking (DOWEL/BOTTOM/INTERMEDIATE/TOP)
- Horizontal bars with U-bar at free edges, hook at connections
- Double layer support (both faces)
- Stock length split for bars exceeding 12m

Continuity logic:
  Group by wall_id → sort by level → detect gaps
  Continuous above → lap (Lpc)
  No wall above → hook (Ldh) — wall terminates
  No wall below at foundation → dowel
  No wall below above foundation → starter from slab

Input:  MembersWall.csv, ReinforcementWall.csv, Nodes.csv,
        development_lengths.csv, lap_splice.csv
Output: RebarLengthsWall.csv
"""

import pandas as pd
import numpy as np
import math
import re
import os
from tier2.stock_split import split_bar

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_COVER_MM = 50.0
FOOTING_DEPTH_MM = 450
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


def _level_sort_key(lv):
    if lv is None:
        return 999
    s = str(lv).upper()
    if s.startswith('B') and s[1:].isdigit():
        return -100 - int(s[1:])
    if s.endswith('F') and s[:-1].isdigit():
        return int(s[:-1])
    if s in ('ROOF', 'RF'):
        return 999
    if s == 'PIT':
        return -50
    return 500


def _is_basement(lv):
    return str(lv).upper().startswith('B') if lv else False


# ── Lookup ───────────────────────────────────────────────────────────────────

class WallDevLapLookup:
    def __init__(self, dev_path, lap_path):
        self.dev_df = pd.read_csv(dev_path)
        self.lap_df = pd.read_csv(lap_path)
        self.dev_df.columns = self.dev_df.columns.str.strip()
        self.lap_df.columns = self.lap_df.columns.str.strip()

    def get_vertical(self, fy, dia_mm, fc):
        """Vertical bars use BEAM_COLUMN dev/lap (same as columns)."""
        return self._lookup(fy, dia_mm, fc, 'BEAM_COLUMN')

    def get_horizontal(self, fy, dia_mm, fc):
        """Horizontal bars use SLAB_WALL dev/lap."""
        return self._lookup(fy, dia_mm, fc, 'SLAB_WALL')

    def _lookup(self, fy, dia_mm, fc, member_type):
        d_label = _dia_label(dia_mm)

        dev_mt = self.dev_df[self.dev_df['member_type'] == member_type] \
            if 'member_type' in self.dev_df.columns else self.dev_df
        row = dev_mt[(dev_mt['fy'] == fy) & (dev_mt['diameter'] == d_label) & (dev_mt['fc'] == fc)]
        if row.empty:
            row = dev_mt[(dev_mt['fy'] == fy) & (dev_mt['diameter'] == d_label)]
            if row.empty:
                return {'Ldh': 300, 'Lpc': 600, 'Lpt': 600, 'Lpb': 500}
            row = row.iloc[(row['fc'] - fc).abs().argsort()[:1]]
        Ldh = float(row['Ldh'].iloc[0])

        lap_mt = self.lap_df[self.lap_df['member_type'] == member_type] \
            if 'member_type' in self.lap_df.columns else self.lap_df
        lrow = lap_mt[(lap_mt['fy'] == fy) & (lap_mt['diameter'] == d_label) & (lap_mt['fc'] == fc)]
        if lrow.empty:
            lrow = lap_mt[(lap_mt['fy'] == fy) & (lap_mt['diameter'] == d_label)]
            if lrow.empty:
                return {'Ldh': Ldh, 'Lpc': 600, 'Lpt': 600, 'Lpb': 500}
            lrow = lrow.iloc[(lrow['fc'] - fc).abs().argsort()[:1]]

        Lpc = float(lrow['Lpc'].iloc[0])
        lpt_col = 'Lpt' if 'Lpt' in lrow.columns else 'Lpt_B'
        lpb_col = 'Lpb' if 'Lpb' in lrow.columns else 'Lpb_B'

        return {
            'Ldh': Ldh,
            'Lpc': Lpc,
            'Lpt': float(lrow[lpt_col].iloc[0]),
            'Lpb': float(lrow[lpb_col].iloc[0]),
        }


# ── Public API ───────────────────────────────────────────────────────────────

def calculate_wall_rebar_lengths(
    walls_df: pd.DataFrame,
    reinf_df: pd.DataFrame,
    nodes_df: pd.DataFrame,
    dev_lengths_path: str,
    lap_splice_path: str,
    fc: int = 35,
    cover_path: str = None,
) -> pd.DataFrame:
    """
    Calculate wall rebar lengths from Tier 1 output.

    Returns DataFrame for RebarLengthsWall.csv
    """
    print('[RebarWall] Loading lookup tables...')
    lookup = WallDevLapLookup(dev_lengths_path, lap_splice_path)
    cover = _load_cover(cover_path)
    print(f'[RebarWall] Cover: {cover}mm')

    # Build node coordinate lookup
    node_coords = {}
    for _, r in nodes_df.iterrows():
        node_coords[str(r['node_id'])] = {
            'x_mm': float(r['x_mm']), 'y_mm': float(r['y_mm']), 'z_mm': float(r['z_mm'])
        }

    # Build story level order from nodes
    level_order = {}
    for level in walls_df['level'].dropna().unique():
        level_order[level] = _level_sort_key(level)

    # Build reinforcement lookup: (wall_id, level) → reinf data
    reinf_lookup = {}
    for _, r in reinf_df.iterrows():
        wid = r.get('wall_id')
        level = r.get('level', '')
        if pd.notna(wid):
            reinf_lookup[(int(wid), str(level))] = r

    results = []

    # ── Group walls by wall_id, aggregate per level ──
    wall_groups = {}
    for _, w in walls_df.iterrows():
        wid = int(w['wall_id'])
        level = str(w['level'])
        key = (wid, level)

        if key not in wall_groups:
            wall_groups[key] = {
                'wall_id': wid,
                'wall_mark': w['wall_mark'],
                'level': level,
                'elements': [],
                'thickness_mm': float(w['thickness_mm']) if pd.notna(w.get('thickness_mm')) else 200,
            }
        wall_groups[key]['elements'].append(w)

    # Aggregate geometry per wall_id+level using node coordinates
    wall_segments = {}
    for key, wg in wall_groups.items():
        elems = wg['elements']
        heights = [float(e['height_mm']) for e in elems if pd.notna(e.get('height_mm'))]
        widths = [float(e['width_mm']) for e in elems if pd.notna(e.get('width_mm'))]
        zs = [float(e['centroid_z_mm']) for e in elems if pd.notna(e.get('centroid_z_mm'))]

        # Collect all node coordinates for this wall segment
        all_coords = []
        for e in elems:
            for ni in ['node_i', 'node_j', 'node_k', 'node_l']:
                nid = str(e.get(ni, ''))
                if nid in node_coords:
                    all_coords.append(node_coords[nid])

        if all_coords:
            all_x = [c['x_mm'] for c in all_coords]
            all_y = [c['y_mm'] for c in all_coords]
            all_z = [c['z_mm'] for c in all_coords]
            x_min, x_max = min(all_x), max(all_x)
            y_min, y_max = min(all_y), max(all_y)
            z_min, z_max = min(all_z), max(all_z)
        else:
            x_min = x_max = y_min = y_max = 0
            z_min = min(zs) - max(heights) / 2 if zs and heights else 0
            z_max = z_min + max(heights) if heights else 0

        # Junction extensions (max across all elements in this segment)
        ext_starts = [float(e.get('extend_start_mm', 0) or 0) for e in elems]
        ext_ends = [float(e.get('extend_end_mm', 0) or 0) for e in elems]

        wall_segments[key] = {
            'wall_id': wg['wall_id'],
            'wall_mark': wg['wall_mark'],
            'level': wg['level'],
            'height_mm': max(heights) if heights else 0,
            'total_width_mm': sum(widths) if widths else 0,
            'thickness_mm': wg['thickness_mm'],
            'z_center': sum(zs) / len(zs) if zs else 0,
            'z_bottom': z_min,
            'z_top': z_max,
            'x_min': x_min, 'x_max': x_max,
            'y_min': y_min, 'y_max': y_max,
            'n_elements': len(elems),
            'extend_start_mm': max(ext_starts) if ext_starts else 0,
            'extend_end_mm': max(ext_ends) if ext_ends else 0,
        }

    # ── Stack walls by wall_id, detect continuity ──
    wid_stacks = {}
    for key, seg in wall_segments.items():
        wid = seg['wall_id']
        if wid not in wid_stacks:
            wid_stacks[wid] = []
        wid_stacks[wid].append(seg)

    # Sort each stack by level
    for wid in wid_stacks:
        wid_stacks[wid].sort(key=lambda s: _level_sort_key(s['level']))

    print(f'[RebarWall] {len(wall_segments)} wall segments in {len(wid_stacks)} stacks')

    # ── Process each wall stack ──
    for wid, stack in wid_stacks.items():
        if not stack:
            continue

        wall_mark = stack[0]['wall_mark']

        # Split into continuous groups (check level continuity)
        groups = _split_into_continuous_groups(stack, level_order)

        for group in groups:
            _process_wall_group(group, wid, wall_mark, reinf_lookup, lookup,
                               cover, fc, node_coords, results)

    df = pd.DataFrame(results)

    if not df.empty:
        v_count = len(df[df['direction'] == 'VERTICAL'])
        h_count = len(df[df['direction'] == 'HORIZONTAL'])
        print(f'[RebarWall] {len(df)} records ({v_count} vertical, {h_count} horizontal) '
              f'from {df["wall_mark"].nunique()} walls')

    return df


def _split_into_continuous_groups(stack, level_order):
    """Split a wall stack into continuous groups based on Z continuity.

    Two segments are continuous if the previous segment's top Z matches
    the current segment's bottom Z (within tolerance).
    """
    if len(stack) <= 1:
        return [stack]

    Z_TOLERANCE = 200  # mm

    # Sort by Z bottom
    sorted_stack = sorted(stack, key=lambda s: s.get('z_bottom', 0))

    groups = [[sorted_stack[0]]]
    for i in range(1, len(sorted_stack)):
        prev = sorted_stack[i - 1]
        curr = sorted_stack[i]

        # Check Z continuity: prev top ≈ curr bottom
        prev_top = prev.get('z_top', prev.get('z_bottom', 0) + prev.get('height_mm', 0))
        curr_bottom = curr.get('z_bottom', 0)
        z_gap = abs(curr_bottom - prev_top)

        if z_gap <= Z_TOLERANCE:
            groups[-1].append(curr)
        else:
            groups.append([curr])

    return groups


def _process_wall_group(group, wid, wall_mark, reinf_lookup, lookup,
                        cover, fc, node_coords, results):
    """Process one continuous group of wall segments."""

    for j, seg in enumerate(group):
        is_first = (j == 0)
        is_last = (j == len(group) - 1)
        level = seg['level']
        height = seg['height_mm']
        width = seg['total_width_mm']
        thickness = seg['thickness_mm']
        z_bottom = seg['z_bottom']

        # Get reinforcement
        reinf = reinf_lookup.get((wid, level))
        if reinf is None:
            continue

        v_dia = reinf.get('v_dia_mm')
        v_spacing = reinf.get('v_spacing_mm')
        h_dia = reinf.get('h_dia_mm')
        h_spacing = reinf.get('h_spacing_mm')
        bar_layer = reinf.get('bar_layer', 'Double')
        face_multiplier = 2 if str(bar_layer).lower() == 'double' else 1

        if pd.isna(v_dia) or pd.isna(v_spacing):
            continue

        v_dia = int(v_dia)
        v_spacing = int(v_spacing)

        # Dev/lap for vertical bars
        v_fy = _steel_grade(v_dia)
        v_dev = lookup.get_vertical(v_fy, v_dia, fc)
        Ldh_v = v_dev['Ldh']
        Lpc_v = v_dev['Lpc']

        # ── VERTICAL BARS ──

        # Determine role based on position in group
        if is_first and is_last:
            # Single level wall
            if _is_basement(level):
                # At foundation → dowel + main with hook at top
                _emit_dowel(results, wid, wall_mark, level, v_dia,
                            width, v_spacing, Lpc_v, Ldh_v, cover,
                            z_bottom, face_multiplier, seg=seg)
                role = 'MAIN_SINGLE_WITH_DOWEL'
                L_bar = height + Ldh_v
            else:
                role = 'MAIN_SINGLE'
                L_bar = height + Ldh_v
        elif is_first:
            if _is_basement(level):
                _emit_dowel(results, wid, wall_mark, level, v_dia,
                            width, v_spacing, Lpc_v, Ldh_v, cover,
                            z_bottom, face_multiplier, seg=seg)
                role = 'MAIN_BOTTOM'
            else:
                role = 'MAIN_BOTTOM'
            L_bar = height + Lpc_v
        elif is_last:
            role = 'MAIN_TOP'
            L_bar = height + Ldh_v
        else:
            role = 'MAIN_INTERMEDIATE'
            L_bar = height + Lpc_v

        # Number of vertical bars along wall width (including junction extensions)
        ext_start = seg.get('extend_start_mm', 0)
        ext_end = seg.get('extend_end_mm', 0)
        extended_width = width + ext_start + ext_end
        n_v = int(math.floor((extended_width - 2 * cover) / v_spacing)) + 1 if v_spacing > 0 else 0
        n_v *= face_multiplier

        # Splice zones
        sp_start = round(z_bottom, 1)
        sp_start_end = round(z_bottom + Lpc_v, 1) if role != 'MAIN_TOP' else None
        sp_end = round(z_bottom + height, 1) if role not in ('MAIN_TOP', 'MAIN_SINGLE', 'MAIN_SINGLE_WITH_DOWEL') else None
        sp_end_end = round(z_bottom + height + Lpc_v, 1) if sp_end else None

        # Mesh coordinates for vertical bars:
        # origin at bottom of wall + cover, terminus at top - cover
        # distributed along wall length
        mesh_v = {
            'mesh_origin_x_mm': round(seg['x_min'] + cover, 1),
            'mesh_origin_y_mm': round(seg['y_min'] + cover, 1) if seg['y_max'] - seg['y_min'] > seg['x_max'] - seg['x_min'] else round(seg['y_min'], 1),
            'mesh_origin_z_mm': round(seg['z_bottom'] + cover, 1),
            'mesh_terminus_x_mm': round(seg['x_min'] + cover, 1),
            'mesh_terminus_y_mm': round(seg['y_min'] + cover, 1) if seg['y_max'] - seg['y_min'] > seg['x_max'] - seg['x_min'] else round(seg['y_min'], 1),
            'mesh_terminus_z_mm': round(seg['z_top'] - cover, 1),
            'mesh_distribution_axis': 'ALONG_WALL_LENGTH',
        }

        # Determine wall orientation: if Y range > X range, wall runs along Y
        if seg['y_max'] - seg['y_min'] > seg['x_max'] - seg['x_min']:
            # Wall runs along Y axis (constant X)
            mesh_v['mesh_origin_x_mm'] = round(seg['x_min'] + cover, 1)
            mesh_v['mesh_terminus_x_mm'] = round(seg['x_min'] + cover, 1)
            mesh_v['mesh_origin_y_mm'] = round(seg['y_min'] + cover, 1)
            mesh_v['mesh_terminus_y_mm'] = round(seg['y_min'] + cover, 1)
        else:
            # Wall runs along X axis (constant Y)
            mesh_v['mesh_origin_x_mm'] = round(seg['x_min'] + cover, 1)
            mesh_v['mesh_terminus_x_mm'] = round(seg['x_min'] + cover, 1)
            mesh_v['mesh_origin_y_mm'] = round(seg['y_min'] + cover, 1)
            mesh_v['mesh_terminus_y_mm'] = round(seg['y_min'] + cover, 1)

        v_record = {
            'wall_id': wid, 'wall_mark': wall_mark, 'level': level,
            'direction': 'VERTICAL', 'bar_role': role,
            'dia_mm': v_dia, 'spacing_mm': v_spacing,
            'n_bars': n_v, 'length_mm': int(round(L_bar)),
            'total_length_mm': int(round(L_bar * n_v)),
            'height_mm': height, 'width_mm': width, 'thickness_mm': thickness,
            'bar_layer': bar_layer,
            'splice_start_mm': sp_start, 'splice_start_end_mm': sp_start_end,
            'splice_end_mm': sp_end, 'splice_end_end_mm': sp_end_end,
            'cover_mm': cover,
            **mesh_v,
        }
        for piece in split_bar(v_record, Lpc_v):
            results.append(piece)

        # ── HORIZONTAL BARS ──

        if pd.notna(h_dia) and pd.notna(h_spacing):
            h_dia = int(h_dia)
            h_spacing = int(h_spacing)
            h_fy = _steel_grade(h_dia)
            h_dev = lookup.get_horizontal(h_fy, h_dia, fc)
            Ldh_h = h_dev['Ldh']

            # Junction extensions for H-bar length:
            # At junction ends: H-bar extends into adjacent wall by (adj_t - cover)
            #   adj_t ≈ extend_mm * 2 (extension = half adjacent thickness)
            # At free ends: H-bar stops at wall face (no extension)
            ext_start = seg.get('extend_start_mm', 0)
            ext_end = seg.get('extend_end_mm', 0)
            rebar_ext_start = max(0, ext_start * 2 - cover) if ext_start > 0 else 0
            rebar_ext_end = max(0, ext_end * 2 - cover) if ext_end > 0 else 0

            # H-bar = straight bar along wall width + junction extensions
            L_h_bar = width + rebar_ext_start + rebar_ext_end

            # Number of horizontal bars along wall height
            n_h = int(math.floor((height - 2 * cover) / h_spacing)) + 1 if h_spacing > 0 else 0
            n_h *= face_multiplier

            # Mesh coordinates for horizontal bars:
            # origin at start of wall + cover, terminus at end - cover
            # distributed along wall height
            if seg['y_max'] - seg['y_min'] > seg['x_max'] - seg['x_min']:
                # Wall runs along Y
                mesh_h = {
                    'mesh_origin_x_mm': round(seg['x_min'] + cover, 1),
                    'mesh_origin_y_mm': round(seg['y_min'] + cover, 1),
                    'mesh_origin_z_mm': round(seg['z_bottom'] + cover, 1),
                    'mesh_terminus_x_mm': round(seg['x_min'] + cover, 1),
                    'mesh_terminus_y_mm': round(seg['y_max'] - cover, 1),
                    'mesh_terminus_z_mm': round(seg['z_bottom'] + cover, 1),
                    'mesh_distribution_axis': 'ALONG_WALL_HEIGHT',
                }
            else:
                # Wall runs along X
                mesh_h = {
                    'mesh_origin_x_mm': round(seg['x_min'] + cover, 1),
                    'mesh_origin_y_mm': round(seg['y_min'] + cover, 1),
                    'mesh_origin_z_mm': round(seg['z_bottom'] + cover, 1),
                    'mesh_terminus_x_mm': round(seg['x_max'] - cover, 1),
                    'mesh_terminus_y_mm': round(seg['y_min'] + cover, 1),
                    'mesh_terminus_z_mm': round(seg['z_bottom'] + cover, 1),
                    'mesh_distribution_axis': 'ALONG_WALL_HEIGHT',
                }

            h_record = {
                'wall_id': wid, 'wall_mark': wall_mark, 'level': level,
                'direction': 'HORIZONTAL', 'bar_role': 'HORIZONTAL',
                'dia_mm': h_dia, 'spacing_mm': h_spacing,
                'n_bars': n_h, 'length_mm': int(round(L_h_bar)),
                'total_length_mm': int(round(L_h_bar * n_h)),
                'height_mm': height, 'width_mm': width, 'thickness_mm': thickness,
                'bar_layer': bar_layer,
                'splice_start_mm': None, 'splice_start_end_mm': None,
                'splice_end_mm': None, 'splice_end_end_mm': None,
                'cover_mm': cover,
                **mesh_h,
            }
            for piece in split_bar(h_record, Ldh_h):
                results.append(piece)

            # ── U-BARS (separate cap pieces at wall endpoints) ──
            # U-bar: connector across thickness + two legs (Ldh each) along wall
            # Mesh origin→terminus defines the connector line (across thickness)
            # Legs extend perpendicular to connector, along wall direction
            U_bar_width = thickness - 2 * cover  # connector across wall thickness
            U_bar_len = 2 * Ldh_h + U_bar_width  # two legs + connector

            n_ubar_per_end = n_h

            runs_along_y = seg['y_max'] - seg['y_min'] > seg['x_max'] - seg['x_min']

            # U-bar mesh: connector line across thickness at wall endpoint
            # Start end
            if runs_along_y:
                mesh_ubar_s = {
                    'mesh_origin_x_mm': round(seg['x_min'] + cover, 1),
                    'mesh_origin_y_mm': round(seg['y_min'], 1),
                    'mesh_origin_z_mm': round(seg['z_bottom'] + cover, 1),
                    'mesh_terminus_x_mm': round(seg['x_max'] - cover, 1),
                    'mesh_terminus_y_mm': round(seg['y_min'], 1),
                    'mesh_terminus_z_mm': round(seg['z_bottom'] + cover, 1),
                    'mesh_distribution_axis': 'ALONG_WALL_HEIGHT',
                }
                mesh_ubar_e = {
                    'mesh_origin_x_mm': round(seg['x_min'] + cover, 1),
                    'mesh_origin_y_mm': round(seg['y_max'], 1),
                    'mesh_origin_z_mm': round(seg['z_bottom'] + cover, 1),
                    'mesh_terminus_x_mm': round(seg['x_max'] - cover, 1),
                    'mesh_terminus_y_mm': round(seg['y_max'], 1),
                    'mesh_terminus_z_mm': round(seg['z_bottom'] + cover, 1),
                    'mesh_distribution_axis': 'ALONG_WALL_HEIGHT',
                }
            else:
                mesh_ubar_s = {
                    'mesh_origin_x_mm': round(seg['x_min'], 1),
                    'mesh_origin_y_mm': round(seg['y_min'] + cover, 1),
                    'mesh_origin_z_mm': round(seg['z_bottom'] + cover, 1),
                    'mesh_terminus_x_mm': round(seg['x_min'], 1),
                    'mesh_terminus_y_mm': round(seg['y_max'] - cover, 1),
                    'mesh_terminus_z_mm': round(seg['z_bottom'] + cover, 1),
                    'mesh_distribution_axis': 'ALONG_WALL_HEIGHT',
                }
                mesh_ubar_e = {
                    'mesh_origin_x_mm': round(seg['x_max'], 1),
                    'mesh_origin_y_mm': round(seg['y_min'] + cover, 1),
                    'mesh_origin_z_mm': round(seg['z_bottom'] + cover, 1),
                    'mesh_terminus_x_mm': round(seg['x_max'], 1),
                    'mesh_terminus_y_mm': round(seg['y_max'] - cover, 1),
                    'mesh_terminus_z_mm': round(seg['z_bottom'] + cover, 1),
                    'mesh_distribution_axis': 'ALONG_WALL_HEIGHT',
                }

            ubar_base = {
                'wall_id': wid, 'wall_mark': wall_mark, 'level': level,
                'direction': 'HORIZONTAL', 'bar_role': 'U_BAR',
                'dia_mm': h_dia, 'spacing_mm': h_spacing,
                'n_bars': n_ubar_per_end, 'length_mm': int(round(U_bar_len)),
                'total_length_mm': int(round(U_bar_len * n_ubar_per_end)),
                'height_mm': height, 'width_mm': width, 'thickness_mm': thickness,
                'bar_layer': bar_layer,
                'splice_start_mm': None, 'splice_start_end_mm': None,
                'splice_end_mm': None, 'splice_end_end_mm': None,
                'cover_mm': cover,
            }
            results.append({**ubar_base, **mesh_ubar_s})
            results.append({**ubar_base, **mesh_ubar_e})


def _emit_dowel(results, wid, wall_mark, level, dia, width, spacing,
                Lpc, Ldh, cover, z_bottom, face_multiplier, seg=None):
    """Emit dowel bar record at the wall's own level (not hardcoded FOOTING)."""
    dowel_len = Lpc + Ldh
    n_dowels = int(math.floor((width - 2 * cover) / spacing)) + 1 if spacing > 0 else 0
    n_dowels *= face_multiplier

    # Dowel Z range: Ldh below wall base → Lpc above wall base
    z_start = z_bottom - Ldh

    # Use first story's XY coordinates for dowel mesh
    mesh_dowel = {}
    if seg:
        mesh_dowel = {
            'mesh_origin_x_mm': round(seg['x_min'] + cover, 1),
            'mesh_origin_y_mm': round(seg['y_min'] + cover, 1),
            'mesh_origin_z_mm': round(z_start + cover, 1),
            'mesh_terminus_x_mm': round(seg['x_min'] + cover, 1),
            'mesh_terminus_y_mm': round(seg['y_min'] + cover, 1),
            'mesh_terminus_z_mm': round(z_start + dowel_len, 1),
            'mesh_distribution_axis': 'ALONG_WALL_LENGTH',
        }

    results.append({
        'wall_id': wid, 'wall_mark': wall_mark, 'level': level,
        'direction': 'VERTICAL', 'bar_role': 'DOWEL',
        'dia_mm': dia, 'spacing_mm': spacing,
        'n_bars': n_dowels, 'length_mm': int(round(dowel_len)),
        'total_length_mm': int(round(dowel_len * n_dowels)),
        'height_mm': None, 'width_mm': width, 'thickness_mm': None,
        'bar_layer': 'Double',
        'splice_start_mm': None, 'splice_start_end_mm': None,
        'splice_end_mm': round(z_bottom, 1),
        'splice_end_end_mm': round(z_bottom + Lpc, 1),
        'cover_mm': cover,
        **mesh_dowel,
    })
