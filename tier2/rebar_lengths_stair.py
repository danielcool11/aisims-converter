"""
Stair Rebar Length Calculator — Tier 2

Computes bar-by-bar lengths for U-shaped stair reinforcement using
the 10-bar convention with 8-point geometry model.

10 Bar Types per U-shaped stair:
  #1  Floor landing along A — TOP (hook at wall)
  #2  Floor landing along A — BOT (+ lap into flight slope)
  #3  Floor landing dist span B — T/B (hook at wall)
  #4  Mid landing along C — TOP (+ lap from flight slope)
  #5  Mid landing along C — BOT (hook at wall)
  #6  Mid landing dist span B — T/B (hook at wall)
  #7  Flight slope — TOP (+ lap_top)
  #8  Flight slope — BOT (+ lap_bot)
  #9  Flight transverse — T/B (hooks at both ends)
  #10 Support/edge bars at landing edges

Logic adapted from RebarLengthsStairCalculator.py (v3)
Reads Tier 1: MembersStair.csv (73 cols) + ReinforcementStair.csv

Input:  MembersStair.csv, ReinforcementStair.csv,
        development_lengths.csv, lap_splice.csv,
        cover_requirements.csv (optional, default STAIR=30mm)
Output: RebarLengthsStair.csv
"""

import pandas as pd
import numpy as np
import math
import os

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_COVER_MM = 30.0  # KDS 14 20 50 for stairs
HOOK_EXT_FACTOR = 12     # 90-degree hook = 12 × dia


# ── Helpers ──────────────────────────────────────────────────────────────────

def _steel_grade(dia):
    return 500 if int(dia) in (10, 13) else 600


def _dia_label(d):
    return f'D{int(d)}'


def _vec(x, y, z):
    return np.array([x, y, z], dtype=float)


def _vnorm(v):
    return float(np.linalg.norm(v))


def _vunit(v):
    n = _vnorm(v)
    return v / n if n > 1e-9 else v


def _n_bars(clear_span, spacing):
    if spacing <= 0:
        return 0
    return int(math.floor(clear_span / spacing)) + 1


def _load_cover(cover_path=None):
    """Load stair cover from CoverRequirements.csv."""
    if cover_path and os.path.exists(cover_path):
        df = pd.read_csv(cover_path)
        for _, row in df.iterrows():
            if str(row.get('member_type', '')).strip().upper() == 'STAIR':
                return float(row['cover_mm'])
    return DEFAULT_COVER_MM


# ── Lookup ───────────────────────────────────────────────────────────────────

class StairDevLapLookup:
    """Development/lap splice for stairs (SLAB_WALL member_type)."""

    def __init__(self, dev_path, lap_path):
        self.dev_df = pd.read_csv(dev_path)
        self.lap_df = pd.read_csv(lap_path)
        self.dev_df.columns = self.dev_df.columns.str.strip()
        self.lap_df.columns = self.lap_df.columns.str.strip()

    def get(self, fy, dia_mm, fc):
        """Returns dict with Ldh, Lpb, Lpt."""
        d_label = _dia_label(dia_mm)
        mt = 'SLAB_WALL'

        dev_mt = self.dev_df[self.dev_df['member_type'] == mt] \
            if 'member_type' in self.dev_df.columns else self.dev_df
        row = dev_mt[(dev_mt['fy'] == fy) & (dev_mt['diameter'] == d_label) & (dev_mt['fc'] == fc)]
        if row.empty:
            row = dev_mt[(dev_mt['fy'] == fy) & (dev_mt['diameter'] == d_label)]
            if row.empty:
                return {'Ldh': 12 * dia_mm, 'Lpb': 30 * dia_mm, 'Lpt': 30 * dia_mm}
            row = row.iloc[(row['fc'] - fc).abs().argsort()[:1]]
        Ldh = float(row['Ldh'].iloc[0])

        lap_mt = self.lap_df[self.lap_df['member_type'] == mt] \
            if 'member_type' in self.lap_df.columns else self.lap_df
        lrow = lap_mt[(lap_mt['fy'] == fy) & (lap_mt['diameter'] == d_label) & (lap_mt['fc'] == fc)]
        if lrow.empty:
            lrow = lap_mt[(lap_mt['fy'] == fy) & (lap_mt['diameter'] == d_label)]
            if lrow.empty:
                return {'Ldh': Ldh, 'Lpb': 30 * dia_mm, 'Lpt': 30 * dia_mm}
            lrow = lrow.iloc[(lrow['fc'] - fc).abs().argsort()[:1]]

        lpt_col = 'Lpt' if 'Lpt' in lrow.columns else 'Lpt_B'
        lpb_col = 'Lpb' if 'Lpb' in lrow.columns else 'Lpb_B'

        return {
            'Ldh': Ldh,
            'Lpb': float(lrow[lpb_col].iloc[0]),
            'Lpt': float(lrow[lpt_col].iloc[0]),
        }


# ── Public API ───────────────────────────────────────────────────────────────

def calculate_stair_rebar_lengths(
    stairs_df: pd.DataFrame,
    reinf_df: pd.DataFrame,
    dev_lengths_path: str,
    lap_splice_path: str,
    fc: int = 35,
    cover_path: str = None,
) -> pd.DataFrame:
    """
    Calculate stair rebar lengths from Tier 1 output.

    Returns DataFrame for RebarLengthsStair.csv
    """
    print('[RebarStair] Loading lookup tables...')
    lookup = StairDevLapLookup(dev_lengths_path, lap_splice_path)
    cover = _load_cover(cover_path)
    print(f'[RebarStair] Cover: {cover}mm, {len(stairs_df)} stairs, {len(reinf_df)} reinf entries')

    results = []

    for _, seg in stairs_df.iterrows():
        mid = seg['member_id']
        sid = seg.get('segment_id', f'{mid}-SEG001')
        story = seg.get('story_group', f"{seg['level_from']}~{seg['level_to']}")
        c = cover

        # Check 8-point model
        if pd.isna(seg.get('p1_x')):
            print(f'  [WARN] No 8-point geometry for {mid}, skipping')
            continue

        # Geometry
        W_flight = float(seg['stair_width_mm'])
        gap = float(seg.get('gap_mm', 50) or 50)
        B = 2 * W_flight + gap

        # 8-point model
        P = {}
        for i in range(1, 9):
            P[i] = _vec(float(seg[f'p{i}_x']), float(seg[f'p{i}_y']), float(seg[f'p{i}_z']))

        A = _vnorm(P[2] - P[1])  # lower landing length
        C = _vnorm(P[6] - P[5])  # mid landing length

        # Flight endpoints
        F1s = _vec(seg['flight1_start_x'], seg['flight1_start_y'], seg['flight1_start_z'])
        F1e = _vec(seg['flight1_end_x'], seg['flight1_end_y'], seg['flight1_end_z'])
        F2s = _vec(seg['flight2_start_x'], seg['flight2_start_y'], seg['flight2_start_z'])
        F2e = _vec(seg['flight2_end_x'], seg['flight2_end_y'], seg['flight2_end_z'])

        L1 = _vnorm(F1e - F1s)
        L2 = _vnorm(F2e - F2s)

        # Direction vectors
        width_dir = _vunit(P[4] - P[1])
        slope1 = _vunit(F1e - F1s)
        slope2 = _vunit(F2e - F2s)

        # Get reinforcement
        reinf = reinf_df[reinf_df['member_id'] == mid]
        if reinf.empty:
            print(f'  [WARN] No reinforcement for {mid}')
            continue

        # Extract bar configs
        def _get_bar(zone, direction, layer=None):
            mask = (reinf['zone'] == zone) & (reinf['direction'] == direction)
            if layer:
                mask = mask & (reinf['layer'] == layer)
            rows = reinf[mask]
            if rows.empty:
                return None
            r = rows.iloc[0]
            return {'dia': int(r['bar_dia_mm']), 'spacing': int(r['bar_spacing_mm'])}

        # Landing transverse (distribution bar)
        dist = _get_bar('landing_left', 'transverse', 'Top')
        if dist is None:
            dist = _get_bar('stair', 'transverse', 'Top')
        if dist is None:
            continue

        dist_dia = dist['dia']
        dist_sp = dist['spacing']
        fy = _steel_grade(dist_dia)
        dev = lookup.get(fy, dist_dia, fc)
        Ldh = dev['Ldh']
        lap_bot = dev['Lpb']
        lap_top = dev['Lpt']

        # Stair longitudinal and transverse bars (may differ from landing)
        stair_long = _get_bar('stair', 'longitudinal', 'Top')
        stair_trans = _get_bar('stair', 'transverse', 'Top')
        land_long = _get_bar('landing_left', 'longitudinal', 'Top')

        # Dev lengths for stair longitudinal (may be different dia)
        if stair_long and stair_long['dia'] != dist_dia:
            sl_fy = _steel_grade(stair_long['dia'])
            sl_dev = lookup.get(sl_fy, stair_long['dia'], fc)
        else:
            sl_dev = dev

        # Emit helper
        def emit(zone, sub_zone, layer, direction, length_mm, n,
                 dia, spacing, start, end, w_dir, w_span):
            results.append({
                'segment_id': sid, 'member_id': mid, 'story_group': story,
                'zone': zone, 'sub_zone': sub_zone,
                'direction': direction, 'layer': layer,
                'dia_mm': dia, 'spacing_mm': spacing,
                'n_bars': n, 'length_mm': int(round(length_mm)),
                'total_length_mm': int(round(length_mm * n)),
                'cover_mm': c,
                'Ldh_mm': int(round(Ldh)),
                'lap_top_mm': int(round(lap_top)),
                'lap_bot_mm': int(round(lap_bot)),
                'start_x': round(start[0], 1), 'start_y': round(start[1], 1),
                'start_z': round(start[2], 1),
                'end_x': round(end[0], 1), 'end_y': round(end[1], 1),
                'end_z': round(end[2], 1),
                'width_dir_x': round(w_dir[0], 4),
                'width_dir_y': round(w_dir[1], 4),
                'width_dir_z': round(w_dir[2], 4),
                'width_span_mm': round(w_span, 1),
            })

        # ── #1: Floor landing TOP along A ──
        emit('LOWER_LANDING', 'TOP_ALONG_A', 'TOP', 'LONGITUDINAL',
             A, _n_bars(W_flight - 2*c, dist_sp),
             dist_dia, dist_sp,
             P[1] + width_dir * c, P[2] + width_dir * c,
             width_dir, W_flight - 2*c)

        # ── #2: Floor landing BOT along A + lap into flight ──
        bend2 = P[2] + width_dir * c
        end2 = bend2 + slope1 * lap_bot
        emit('LOWER_LANDING', 'BOT_ALONG_A', 'BOTTOM', 'LONGITUDINAL',
             A + lap_bot, _n_bars(W_flight - 2*c, dist_sp),
             dist_dia, dist_sp,
             P[1] + width_dir * c, end2,
             width_dir, W_flight - 2*c)

        # ── #3: Floor landing DIST span B ──
        lower_travel = _vunit(P[2] - P[1])
        emit('LOWER_LANDING', 'DIST_SPAN_B', 'BOTH', 'TRANSVERSE',
             B + Ldh + dist_dia, _n_bars(A - 2*c, dist_sp),
             dist_dia, dist_sp,
             P[1] + lower_travel * c, P[4] + lower_travel * c,
             lower_travel, A - 2*c)

        # ── #4: Mid landing TOP along C + lap from flight ──
        bend4 = P[5] + width_dir * c
        start4 = bend4 - slope1 * lap_top
        emit('MID_LANDING', 'TOP_ALONG_C', 'TOP', 'LONGITUDINAL',
             C + lap_top, _n_bars(W_flight - 2*c, dist_sp),
             dist_dia, dist_sp,
             start4, P[6] + width_dir * c,
             width_dir, W_flight - 2*c)

        # ── #5: Mid landing BOT along C ──
        emit('MID_LANDING', 'BOT_ALONG_C', 'BOTTOM', 'LONGITUDINAL',
             C, _n_bars(W_flight - 2*c, dist_sp),
             dist_dia, dist_sp,
             P[5] + width_dir * c, P[6] + width_dir * c,
             width_dir, W_flight - 2*c)

        # ── #6: Mid landing DIST span B ──
        mid_travel = _vunit(P[6] - P[5])
        emit('MID_LANDING', 'DIST_SPAN_B', 'BOTH', 'TRANSVERSE',
             B + Ldh + dist_dia, _n_bars(C - 2*c, dist_sp),
             dist_dia, dist_sp,
             P[5] + mid_travel * c, P[8] + mid_travel * c,
             mid_travel, C - 2*c)

        # ── #7/#8: Flight slope TOP and BOT (per flight) ──
        # Flight 1 is on the P1 (wall-left) side: distribute from wall → gap
        # Flight 2 is on the P4 (wall-right) side: distribute from wall → gap (reversed)
        sl_dia = stair_long['dia'] if stair_long else dist_dia
        sl_sp = stair_long['spacing'] if stair_long else dist_sp

        # Wall-side start for each flight's width distribution
        # Flight 1: wall at P1 side, distribute along +width_dir
        # Flight 2: wall at P4 side, distribute along -width_dir
        f1_w_start = P[1] + width_dir * c       # wall side + cover
        f2_w_start = P[4] - width_dir * c       # wall side + cover (from right)

        for zone, Fs, Fe, Lf, w_origin, w_dir in [
            ('FLIGHT1', F1s, F1e, L1, f1_w_start, width_dir),
            ('FLIGHT2', F2s, F2e, L2, f2_w_start, -width_dir),
        ]:
            # Longitudinal bars: along slope, distributed across flight width from wall side
            # Replace Fs/Fe x-coord with the wall-side origin's x (keep y,z from flight)
            for layer_name, lap in [('TOP', sl_dev['Lpt']), ('BOTTOM', sl_dev['Lpb'])]:
                ls_start = _vec(w_origin[0], Fs[1], Fs[2])
                ls_end = _vec(w_origin[0], Fe[1], Fe[2])
                emit(zone, f'{layer_name}_ALONG_SLOPE', layer_name, 'LONGITUDINAL',
                     Lf + lap, _n_bars(W_flight - 2*c, sl_sp),
                     sl_dia, sl_sp, ls_start, ls_end,
                     w_dir, W_flight - 2*c)

        # ── #9: Flight transverse (per flight) ──
        st_dia = stair_trans['dia'] if stair_trans else dist_dia
        st_sp = stair_trans['spacing'] if stair_trans else dist_sp
        bar9_len = W_flight - 2*c + 2 * HOOK_EXT_FACTOR * st_dia

        for zone, Fs, Fe, Lf, slope_u, w_origin, w_dir in [
            ('FLIGHT1', F1s, F1e, L1, slope1, f1_w_start, width_dir),
            ('FLIGHT2', F2s, F2e, L2, slope2, f2_w_start, -width_dir),
        ]:
            # Transverse bar: spans flight width from wall side
            t_start = _vec(w_origin[0], Fs[1], Fs[2])
            t_end = t_start + w_dir * (W_flight - 2*c)
            emit(zone, 'TRANSVERSE', 'BOTH', 'TRANSVERSE',
                 bar9_len, _n_bars(Lf - 2*c, st_sp),
                 st_dia, st_sp,
                 t_start, t_end,
                 slope_u, Lf - 2*c)

    df = pd.DataFrame(results)

    if not df.empty:
        print(f'[RebarStair] {len(df)} records from {df["member_id"].nunique()} stairs')
        zones = df['zone'].value_counts().to_dict()
        print(f'[RebarStair] Zones: {zones}')

    # Add split columns for schema consistency (stairs don't exceed 12m)
    if not df.empty:
        for col in ('split_piece', 'split_total', 'original_length_mm'):
            if col not in df.columns:
                df[col] = None

    return df
