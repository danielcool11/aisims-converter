"""
Column Rebar Length Calculator — Tier 2

Computes bar-by-bar lengths for column reinforcement including:
- Dowel bars (basement to footing)
- Main vertical bars (MAIN_BOTTOM, MAIN_INTERMEDIATE, MAIN_TOP)
  with splice zones at story transitions
- Hoops/ties in 3 zones (END_BOTTOM 25%, MID 50%, END_TOP 25%)

Logic adapted from RebarLengthsColumnCalculator.py
Reads Tier 1 output from AISIMS converter.

Input:  MembersColumn.csv, ReinforcementColumn.csv, Sections.csv,
        Nodes.csv, development_lengths.csv, lap_splice.csv
Output: RebarLengthsColumn.csv
"""

import pandas as pd
import numpy as np
import re
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────

FOOTING_DEPTH_MM = 450
COVER_MM = 50
HOOK_EXTENSION_FACTOR = 10


# ── Helpers ──────────────────────────────────────────────────────────────────

def _steel_grade(dia_mm):
    return 500 if int(dia_mm) in (10, 13) else 600


def _dia_label(d_mm):
    return f'D{int(d_mm)}'


def _parse_fc(material_id):
    m = re.search(r'(\d+)', str(material_id).upper())
    return int(m.group(1)) if m else 35


def _level_sort_key(lv):
    """Sort key: B4=-104, B1=-101, 1F=1, 7F=7, Roof=999."""
    if lv is None:
        return 999
    s = str(lv).upper()
    if s.startswith('B') and s[1:].isdigit():
        return -100 - int(s[1:])
    if s.endswith('F') and s[:-1].isdigit():
        return int(s[:-1])
    if s in ('ROOF', 'RF'):
        return 999
    return 500


def _is_basement(lv):
    return str(lv).upper().startswith('B') if lv else False


def _split_continuous_groups(story_info):
    """Split story_info into continuous groups based on Z continuity.

    Two segments are continuous if the previous segment's top Z matches
    the current segment's bottom Z (within tolerance).
    """
    if len(story_info) <= 1:
        return [story_info]

    Z_TOLERANCE = 200  # mm — allow small gap for slab thickness

    groups = [[story_info[0]]]
    for i in range(1, len(story_info)):
        prev = story_info[i - 1]
        curr = story_info[i]

        # Check Z continuity: prev top ≈ curr bottom
        prev_top = prev['z_start'] + prev['height_mm']
        curr_bottom = curr['z_start']
        z_gap = abs(curr_bottom - prev_top)

        if z_gap <= Z_TOLERANCE:
            groups[-1].append(curr)
        else:
            groups.append([curr])

    return groups


# ── Lookup tables ────────────────────────────────────────────────────────────

class ColDevLapLookup:
    """Column development length and lap splice lookup.

    Reads unified tables with member_type column.
    Filters by BEAM_COLUMN member_type (beam and column share same values).
    """

    def __init__(self, dev_path, lap_path):
        self.dev_df = pd.read_csv(dev_path)
        self.lap_df = pd.read_csv(lap_path)
        self.dev_df.columns = self.dev_df.columns.str.strip()
        self.lap_df.columns = self.lap_df.columns.str.strip()

    def get(self, fy, dia_mm, fc, member_type='BEAM_COLUMN'):
        """Returns (Ldh, Lpc): hook dev length, column lap splice."""
        d_label = _dia_label(dia_mm)

        # Filter by member_type
        dev_mt = self.dev_df[self.dev_df['member_type'] == member_type] \
            if 'member_type' in self.dev_df.columns else self.dev_df

        row_dev = dev_mt[
            (dev_mt['fy'] == fy) &
            (dev_mt['diameter'] == d_label) &
            (dev_mt['fc'] == fc)
        ]
        if row_dev.empty:
            row_dev = dev_mt[
                (dev_mt['fy'] == fy) &
                (dev_mt['diameter'] == d_label)
            ]
            if row_dev.empty:
                print(f'  [WARN] No col dev length for fy={fy}, {d_label}, fc={fc}, {member_type}')
                return 300, 600
            row_dev = row_dev.iloc[(row_dev['fc'] - fc).abs().argsort()[:1]]

        Ldh = float(row_dev['Ldh'].iloc[0])

        lap_mt = self.lap_df[self.lap_df['member_type'] == member_type] \
            if 'member_type' in self.lap_df.columns else self.lap_df

        row_lap = lap_mt[
            (lap_mt['fy'] == fy) &
            (lap_mt['diameter'] == d_label) &
            (lap_mt['fc'] == fc)
        ]
        if row_lap.empty:
            row_lap = lap_mt[
                (lap_mt['fy'] == fy) &
                (lap_mt['diameter'] == d_label)
            ]
            if row_lap.empty:
                return Ldh, 600
            row_lap = row_lap.iloc[(row_lap['fc'] - fc).abs().argsort()[:1]]

        Lpc = float(row_lap['Lpc'].iloc[0])
        return Ldh, Lpc


# ── Data adapter ─────────────────────────────────────────────────────────────

class ColumnDataAdapter:
    """Adapts our converter Tier 1 output for the column calculator."""

    def __init__(self, columns_df, reinf_df, sections_df, nodes_df):
        self.columns_df = columns_df.copy()
        self.reinf_df = reinf_df.copy()
        self.sections_df = sections_df.copy()
        self.nodes_df = nodes_df.copy()

        self._build_lookups()

    def _build_lookups(self):
        from converters.validation import _extract_base_member_id

        # Section lookup: (member_id, level_from, level_to) → {b_mm, h_mm, shape}
        self.section_level = {}
        self.section_generic = {}
        for _, row in self.sections_df.iterrows():
            if str(row.get('member_type', '')).upper() != 'COLUMN':
                continue
            mid = str(row.get('member_id', '')).strip()
            b = float(row['b_mm']) if pd.notna(row.get('b_mm')) else None
            h = float(row['h_mm']) if pd.notna(row.get('h_mm')) else None
            shape = str(row.get('shape', 'RECT')).upper()
            if b is None or h is None:
                continue

            lf = row.get('level_from')
            lt = row.get('level_to')
            if pd.notna(lf) and pd.notna(lt) and str(lf).strip() and str(lt).strip():
                self.section_level[(mid, str(lf).strip(), str(lt).strip())] = (b, h, shape)
            else:
                self.section_generic[mid] = (b, h, shape)

        # Node coordinates — index by node_id for lookup
        # Build from ALL available node_ids (handles both raw and grid-reassigned)
        self.node_coords = {}
        for _, row in self.nodes_df.iterrows():
            coords = {
                'x_mm': float(row['x_mm']),
                'y_mm': float(row['y_mm']),
                'z_mm': float(row['z_mm']),
            }
            self.node_coords[str(row['node_id'])] = coords

        # Also build level → Z lookup from nodes (for fallback)
        self.level_z = {}
        for level in self.nodes_df['level'].unique():
            z_vals = self.nodes_df[self.nodes_df['level'] == level]['z_mm']
            if not z_vals.empty:
                self.level_z[str(level)] = float(z_vals.mean())

        # Parse reinforcement — keyed by raw_mid to support per-level configs
        # (e.g. P2: 'TC1 (B5-B1)' vs 'TC1 (1-P)' have different bar counts)
        self._main_cfg_raw = {}   # raw_mid → {dia, n_bars}
        self._hoop_cfg_raw = {}   # raw_mid → {end: {dia, spacing}, mid: {…}}
        self.base_to_raw = {}     # base_mid → set of raw_mids

        for _, row in self.reinf_df.iterrows():
            raw_mid = str(row.get('member_id', '')).strip()
            mid = _extract_base_member_id(raw_mid)

            if mid not in self.base_to_raw:
                self.base_to_raw[mid] = set()
            self.base_to_raw[mid].add(raw_mid)

            # Main bars
            main_dia = row.get('main_dia_mm')
            main_total = row.get('main_total')
            if pd.notna(main_dia) and pd.notna(main_total) and int(main_total) > 0:
                self._main_cfg_raw[raw_mid] = {
                    'dia': float(main_dia),
                    'n_bars': int(main_total),
                }

            # Tie end
            tie_end_dia = row.get('tie_end_dia_mm')
            tie_end_spacing = row.get('tie_end_spacing_mm')
            if pd.notna(tie_end_dia) and pd.notna(tie_end_spacing):
                if raw_mid not in self._hoop_cfg_raw:
                    self._hoop_cfg_raw[raw_mid] = {}
                self._hoop_cfg_raw[raw_mid]['end'] = {
                    'dia_mm': float(tie_end_dia),
                    'spacing_mm': float(tie_end_spacing),
                }

            # Tie mid
            tie_mid_dia = row.get('tie_mid_dia_mm')
            tie_mid_spacing = row.get('tie_mid_spacing_mm')
            if pd.notna(tie_mid_dia) and pd.notna(tie_mid_spacing):
                if raw_mid not in self._hoop_cfg_raw:
                    self._hoop_cfg_raw[raw_mid] = {}
                self._hoop_cfg_raw[raw_mid]['mid'] = {
                    'dia_mm': float(tie_mid_dia),
                    'spacing_mm': float(tie_mid_spacing),
                }

        # Build level_range_map: raw_mid → set of levels it covers
        # Parses parenthetical ranges like '(B5-B1)', '(1-P)' and also
        # generic prefixes like '-1', 'R', '-1~-4' via beam helpers.
        self._level_range_map = {}  # raw_mid → set of level names
        self._build_level_range_map()

        # Legacy flat lookups (for single-config members / fallback)
        self.main_cfg = {}
        self.hoop_cfg = {}
        for mid, raw_ids in self.base_to_raw.items():
            for raw_mid in raw_ids:
                if raw_mid in self._main_cfg_raw and mid not in self.main_cfg:
                    self.main_cfg[mid] = self._main_cfg_raw[raw_mid]
                if raw_mid in self._hoop_cfg_raw and mid not in self.hoop_cfg:
                    self.hoop_cfg[mid] = self._hoop_cfg_raw[raw_mid]

    def _build_level_range_map(self):
        """Build raw_mid → set of level names from column level_from/level_to."""
        # Collect all unique levels from columns_df, ordered by z
        level_z = {}
        for _, row in self.columns_df.iterrows():
            lf = str(row.get('level_from', '')).strip()
            lt = str(row.get('level_to', '')).strip()
            z = row.get('z_mm', None)
            if lf and z is not None and pd.notna(z):
                if lf not in level_z or float(z) < level_z[lf]:
                    level_z[lf] = float(z)
            if lt:
                zt = self.level_z.get(lt)
                if zt is not None and (lt not in level_z or zt < level_z[lt]):
                    level_z[lt] = zt

        level_order = sorted(level_z.keys(), key=lambda lv: level_z.get(lv, 0))

        for base_mid, raw_ids in self.base_to_raw.items():
            for raw_mid in raw_ids:
                levels = self._parse_raw_mid_levels(raw_mid, base_mid, level_order)
                if levels:
                    self._level_range_map[raw_mid] = levels

        # Assign uncovered column levels to unassigned raw_mids
        for base_mid, raw_ids in self.base_to_raw.items():
            if len(raw_ids) <= 1:
                continue
            col_levels = set()
            for _, row in self.columns_df[
                self.columns_df['member_id'] == base_mid
            ].iterrows():
                lf = str(row.get('level_from', '')).strip()
                if lf:
                    col_levels.add(lf)
            covered = set()
            for raw_mid in raw_ids:
                covered |= self._level_range_map.get(raw_mid, set())
            uncovered = col_levels - covered
            if not uncovered:
                continue
            assigned_raws = {r for r in raw_ids if r in self._level_range_map}
            unassigned = [r for r in raw_ids if r not in assigned_raws]
            if len(unassigned) == 1:
                existing = self._level_range_map.get(unassigned[0], set())
                self._level_range_map[unassigned[0]] = existing | uncovered

    @staticmethod
    def _parse_raw_mid_levels(raw_mid, base_mid, level_order):
        """Parse a raw reinforcement member_id to determine which levels it covers.

        Handles:
        - P2 parenthetical: 'TC1 (B5-B1)' → {B5,B4,B3,B2,B1}
        - P2 space+range:   '-1~-4 C1' → {B1,B2,B3,B4}
        - P1 concatenated:  '-1C1' → {B1}, 'RC1' → {Roof}
        """
        from tier2.rebar_lengths_beam import (
            _extract_raw_prefix, _prefix_token_to_level, _expand_level_prefix)

        # Strategy 1: Parenthetical range like '(B5-B1)' or '(1-P)'
        paren_match = re.search(r'\(([^)]+)\)', raw_mid)
        if paren_match:
            inner = paren_match.group(1)  # e.g. 'B5-B1', '1-P'
            # Split on '-' but handle negative numbers: 'B5-B1' → ['B5','B1']
            # For '1-P' → ['1','P']
            tokens = re.split(r'(?<=[A-Za-z0-9])-(?=[A-Za-z0-9])', inner)
            if len(tokens) == 2:
                start = _prefix_token_to_level(
                    tokens[0].replace('B', '-') if tokens[0].startswith('B') and tokens[0][1:].isdigit()
                    else tokens[0])
                end = _prefix_token_to_level(
                    tokens[1].replace('B', '-') if tokens[1].startswith('B') and tokens[1][1:].isdigit()
                    else tokens[1])
                if (start and end
                        and start in level_order and end in level_order):
                    i1 = level_order.index(start)
                    i2 = level_order.index(end)
                    lo, hi = min(i1, i2), max(i1, i2)
                    return set(level_order[lo:hi + 1])
            return None

        # Strategy 2: Use beam-style prefix parsing
        prefix = _extract_raw_prefix(raw_mid, base_mid)
        return _expand_level_prefix(prefix, level_order)

    def get_main_cfg(self, member_id, level_from=None):
        """Get main bar config for a column, level-aware.

        For multi-range members (P2: TC1 has different configs at B5-B1 vs 1-P),
        returns the config matching the segment's level_from.
        Falls back to the legacy flat lookup for single-config members.
        """
        raw_ids = self.base_to_raw.get(member_id, set())
        if len(raw_ids) <= 1:
            return self.main_cfg.get(member_id)

        if level_from:
            for raw_mid in raw_ids:
                levels = self._level_range_map.get(raw_mid, set())
                if level_from in levels and raw_mid in self._main_cfg_raw:
                    return self._main_cfg_raw[raw_mid]

        return self.main_cfg.get(member_id)

    def get_hoop_cfg(self, member_id, level_from=None):
        """Get hoop/tie config for a column, level-aware."""
        raw_ids = self.base_to_raw.get(member_id, set())
        if len(raw_ids) <= 1:
            return self.hoop_cfg.get(member_id)

        if level_from:
            for raw_mid in raw_ids:
                levels = self._level_range_map.get(raw_mid, set())
                if level_from in levels and raw_mid in self._hoop_cfg_raw:
                    return self._hoop_cfg_raw[raw_mid]

        return self.hoop_cfg.get(member_id)

    def get_section_dims(self, member_id, level_from, level_to):
        """Get (b_mm, h_mm, shape) for a column segment."""
        key = (member_id, level_from, level_to)
        if key in self.section_level:
            return self.section_level[key]
        # Generic fallback
        if member_id in self.section_generic:
            return self.section_generic[member_id]
        return (None, None, None)


# ── Public API ───────────────────────────────────────────────────────────────

def calculate_column_rebar_lengths(
    columns_df: pd.DataFrame,
    reinf_df: pd.DataFrame,
    sections_df: pd.DataFrame,
    nodes_df: pd.DataFrame,
    dev_lengths_path: str,
    lap_splice_path: str,
) -> pd.DataFrame:
    """
    Calculate column rebar lengths from Tier 1 converter output.

    Returns DataFrame for RebarLengthsColumn.csv
    """
    print('[RebarColumn] Loading lookup tables...')
    lookup = ColDevLapLookup(dev_lengths_path, lap_splice_path)

    print('[RebarColumn] Building data adapter...')
    adapter = ColumnDataAdapter(columns_df, reinf_df, sections_df, nodes_df)
    print(f'[RebarColumn] {len(adapter._main_cfg_raw)} main configs '
          f'({len(adapter.base_to_raw)} members), '
          f'{len(adapter._hoop_cfg_raw)} hoop configs')

    results = []

    # Group columns by grid point and member_id
    col_df = adapter.columns_df.copy()

    # Build level index for sorting
    all_levels = set()
    for lv in col_df['level_from'].dropna():
        all_levels.add(str(lv))
    for lv in col_df['level_to'].dropna():
        all_levels.add(str(lv))
    sorted_levels = sorted(all_levels, key=_level_sort_key)
    level_index = {lv: i for i, lv in enumerate(sorted_levels)}

    # Get fc from material (default C35)
    fc = 35

    # Process each column stack
    # Step 1: Group by grid + member_id (separates P2's multiple columns)
    # Step 2: Merge Z-continuous stacks of same member_id across grids (P1 slanted)
    col_df = col_df.copy()

    raw_stacks = {}
    for (grid, member_id), sub in col_df.groupby(['grid', 'member_id']):
        sub = sub.copy()
        sub['_lv_idx'] = sub['level_from'].apply(_level_sort_key)
        sub = sub.sort_values('_lv_idx').reset_index(drop=True)
        raw_stacks[(grid, member_id)] = sub

    # Merge slanted column stacks: same member_id, different grids, Z-continuous
    merged = {}
    used = set()
    stack_keys = sorted(raw_stacks.keys(), key=lambda k: (k[1], _level_sort_key(raw_stacks[k].iloc[0]['level_from'])))

    for key in stack_keys:
        if key in used:
            continue
        grid, mid = key
        combined = raw_stacks[key].copy()
        used.add(key)

        # Try to find Z-continuous stacks of same member_id at other grids
        changed = True
        while changed:
            changed = False
            z_top = combined.iloc[-1]
            top_z = adapter.level_z.get(str(z_top['level_to']), None)
            if top_z is None:
                nd = adapter.node_coords.get(str(z_top.get('node_to', '')), {})
                top_z = nd.get('z_mm')

            for other_key in stack_keys:
                if other_key in used or other_key[1] != mid:
                    continue
                other = raw_stacks[other_key]
                bot = other.iloc[0]
                bot_z = adapter.level_z.get(str(bot['level_from']), None)
                if bot_z is None:
                    nd = adapter.node_coords.get(str(bot.get('node_from', '')), {})
                    bot_z = nd.get('z_mm')

                if top_z is not None and bot_z is not None and abs(bot_z - top_z) < 200:
                    combined = pd.concat([combined, other], ignore_index=True)
                    combined = combined.sort_values('_lv_idx').reset_index(drop=True)
                    used.add(other_key)
                    changed = True
                    break

        merged[(grid, mid)] = combined

    for (grid, member_id), grp in merged.items():
        # Sort bottom to top
        grp = grp.copy()
        grp['_lv_idx'] = grp['level_from'].apply(_level_sort_key)
        grp = grp.sort_values('_lv_idx').reset_index(drop=True)

        # Check if any rebar config exists for this member
        first_level = str(grp.iloc[0]['level_from'])
        main_check = adapter.get_main_cfg(member_id, first_level)
        if main_check is None:
            continue

        # Build story info
        story_info = []
        for _, seg in grp.iterrows():
            lv_from = str(seg['level_from'])
            lv_to = str(seg['level_to'])
            h = float(seg['height_mm'])

            # Use actual 3D length for rebar calculation (handles slanted columns)
            length = float(seg['length_mm']) if 'length_mm' in seg.index and pd.notna(seg.get('length_mm')) else h

            # Get coordinates from node lookups or direct columns
            col_x = seg.get('x_mm', 0) or 0
            col_y = seg.get('y_mm', 0) or 0
            col_x_top = seg.get('x_top_mm', col_x) or col_x
            col_y_top = seg.get('y_top_mm', col_y) or col_y

            # Z from nodes (try node lookup, fallback to level_z)
            nd_from = adapter.node_coords.get(str(seg.get('node_from', '')), {})
            nd_to = adapter.node_coords.get(str(seg.get('node_to', '')), {})
            z_start = nd_from.get('z_mm')
            z_end = nd_to.get('z_mm')

            # Fallback: use level→Z lookup from StoryDefinition
            if z_start is None:
                z_start = adapter.level_z.get(lv_from, 0)
            if z_end is None:
                z_end = adapter.level_z.get(lv_to, z_start + h)

            # Section dimensions — priority: exact section match > MembersColumn > generic section > hard fallback
            sec_key = (member_id, lv_from, lv_to)
            if sec_key in adapter.section_level:
                b_mm, h_mm, shape = adapter.section_level[sec_key]
            elif pd.notna(seg.get('b_mm')) and pd.notna(seg.get('h_mm')):
                b_mm = float(seg['b_mm'])
                h_mm = float(seg['h_mm'])
                shape = str(seg.get('shape', 'RECT')).upper() if pd.notna(seg.get('shape')) else 'RECT'
            elif member_id in adapter.section_generic:
                b_mm, h_mm, shape = adapter.section_generic[member_id]
            else:
                b_mm, h_mm, shape = 400, 400, 'RECT'

            seg_no = len(story_info) + 1
            segment_id = f"{member_id}-SEG{seg_no:03d}"

            story_info.append({
                'segment_id': segment_id,
                'level_from': lv_from,
                'level_to': lv_to,
                'height_mm': h,
                'length_mm': length,
                'col_x': col_x,
                'col_y': col_y,
                'col_x_top': col_x_top,
                'col_y_top': col_y_top,
                'z_start': z_start,
                'z_end': z_end,
                'b_mm': b_mm,
                'h_mm': h_mm,
                'shape': shape,
            })

        if not story_info:
            continue

        # ── Split into continuous groups (detect level gaps) ──
        groups = _split_continuous_groups(story_info)

        for group in groups:
            # ── DOWEL BAR at the bottom of each continuous group ──
            # Any column that's the first in its stack needs dowels into
            # the slab/footing below (Ldh embedment + Lpc lap above).
            first = group[0]
            main_d = adapter.get_main_cfg(member_id, first['level_from'])
            if main_d:
                dia_d = main_d['dia']
                n_d = main_d['n_bars']
                fy_d = _steel_grade(dia_d)
                Ldh_d, Lpc_d = lookup.get(fy_d, dia_d, fc)
                dowel_len = Lpc_d + Ldh_d
                col_z_bottom = first['z_start']
                # Dowel Z: Ldh below column base (into slab/footing) → Lpc above (lap)
                rebar_z_start = col_z_bottom - Ldh_d + COVER_MM
                rebar_z_end = col_z_bottom + Lpc_d

                results.append({
                    'member_id': member_id, 'start_grid': grid,
                    'level_from': first['level_from'], 'level_to': first['level_from'],
                    'bar_position': 'MAIN', 'bar_role': 'DOWEL', 'bar_type': 'MAIN',
                    'dia_mm': dia_d, 'n_bars': n_d,
                    'length_mm': int(round(dowel_len)),
                    'splice_start_mm': None, 'splice_start_end_mm': None,
                    'splice_end_mm': round(col_z_bottom, 1),
                    'splice_end_end_mm': round(col_z_bottom + Lpc_d, 1),
                    'x_start_mm': first['col_x'], 'y_start_mm': first['col_y'],
                    'z_start_mm': round(rebar_z_start, 1),
                    'x_end_mm': first['col_x'], 'y_end_mm': first['col_y'],
                    'z_end_mm': round(rebar_z_end, 1),
                    'segment_id': first['segment_id'],
                    'b_mm': first['b_mm'], 'h_mm': first['h_mm'],
                    'shape': first['shape'],
                })

            # ── MAIN BARS (story by story within group) ──
            for j, s in enumerate(group):
                is_first = (j == 0)
                is_top = (j == len(group) - 1)
                h = s['height_mm']
                L = s['length_mm']
                z_start = s['z_start']

                # Per-segment config (handles P2 range-specific rebar)
                main_s = adapter.get_main_cfg(member_id, s['level_from'])
                if main_s is None:
                    continue
                dia_main = main_s['dia']
                n_bars = main_s['n_bars']
                fy = _steel_grade(dia_main)
                Ldh, Lpc = lookup.get(fy, dia_main, fc)

                if is_top:
                    L_bar = L + Ldh
                    role = 'MAIN_TOP'
                    sp_start = round(z_start, 1)
                    sp_start_end = round(z_start + Lpc, 1)
                    sp_end = None
                    sp_end_end = None
                elif is_first:
                    L_bar = L + Lpc
                    role = 'MAIN_BOTTOM'
                    sp_start = round(z_start, 1)
                    sp_start_end = round(z_start + Lpc, 1)
                    sp_end = round(z_start + h, 1)
                    sp_end_end = round(z_start + h + Lpc, 1)
                else:
                    L_bar = L + Lpc
                    role = 'MAIN_INTERMEDIATE'
                    sp_start = round(z_start, 1)
                    sp_start_end = round(z_start + Lpc, 1)
                    sp_end = round(z_start + h, 1)
                    sp_end_end = round(z_start + h + Lpc, 1)

                rebar_z_start = z_start
                rebar_z_end = z_start + L_bar

                results.append({
                    'member_id': member_id, 'start_grid': grid,
                    'level_from': s['level_from'], 'level_to': s['level_to'],
                    'bar_position': 'MAIN', 'bar_role': role, 'bar_type': 'MAIN',
                    'dia_mm': dia_main, 'n_bars': n_bars,
                    'length_mm': int(round(L_bar)),
                    'splice_start_mm': sp_start, 'splice_start_end_mm': sp_start_end,
                    'splice_end_mm': sp_end, 'splice_end_end_mm': sp_end_end,
                    'x_start_mm': s['col_x'], 'y_start_mm': s['col_y'],
                    'z_start_mm': round(rebar_z_start, 1),
                    'x_end_mm': s['col_x_top'], 'y_end_mm': s['col_y_top'],
                    'z_end_mm': round(rebar_z_end, 1),
                    'segment_id': s['segment_id'],
                    'b_mm': s['b_mm'], 'h_mm': s['h_mm'],
                    'shape': s['shape'],
                })

            # ── HOOPS (3 zones per story within group) ──
            for s in group:
                hoop = adapter.get_hoop_cfg(member_id, s['level_from'])
                if not hoop:
                    continue
                end_cfg = hoop.get('end')
                mid_cfg = hoop.get('mid')
                if not end_cfg:
                    end_cfg = mid_cfg
                if not mid_cfg:
                    mid_cfg = end_cfg

                if end_cfg and mid_cfg:
                    H_clear = s['length_mm']
                    b_mm = s['b_mm']
                    h_mm = s['h_mm']
                    b_clear = b_mm - 2 * COVER_MM
                    h_clear = h_mm - 2 * COVER_MM

                    zones = [
                        ('HOOP_END_BOTTOM', 0.25 * H_clear, end_cfg),
                        ('HOOP_MID', 0.50 * H_clear, mid_cfg),
                        ('HOOP_END_TOP', 0.25 * H_clear, end_cfg),
                    ]

                    z_cursor = s['z_start']

                    for zone_role, zone_length, cfg in zones:
                        dia = cfg['dia_mm']
                        spacing = cfg['spacing_mm']

                        L_hoop = 2 * (b_clear + h_clear) + 2 * HOOK_EXTENSION_FACTOR * dia
                        n_hoops = int(zone_length / spacing) + 1
                        total_len = L_hoop * n_hoops

                        results.append({
                            'member_id': member_id, 'start_grid': grid,
                            'level_from': s['level_from'], 'level_to': s['level_to'],
                            'bar_position': 'HOOP', 'bar_role': zone_role,
                            'bar_type': 'HOOP',
                            'dia_mm': int(dia), 'n_bars': 0,
                            'length_mm': int(round(L_hoop)),
                            'spacing_mm': int(spacing),
                            'zone_length_mm': int(round(zone_length)),
                            'quantity_pieces': n_hoops,
                            'total_length_mm': int(round(total_len)),
                            'splice_start_mm': None, 'splice_start_end_mm': None,
                            'splice_end_mm': None, 'splice_end_end_mm': None,
                            'x_start_mm': s['col_x'], 'y_start_mm': s['col_y'],
                            'z_start_mm': round(z_cursor, 1),
                            'x_end_mm': s['col_x'], 'y_end_mm': s['col_y'],
                            'z_end_mm': round(z_cursor + zone_length, 1),
                            'segment_id': s['segment_id'],
                            'b_mm': int(b_mm), 'h_mm': int(h_mm),
                            'shape': s['shape'],
                        })

                        z_cursor += zone_length

    # Build output
    df = pd.DataFrame(results)

    if not df.empty:
        # Sort
        df['_lv_idx'] = df['level_from'].apply(_level_sort_key)
        role_order = {
            'DOWEL': 0, 'MAIN_BOTTOM': 1, 'MAIN_INTERMEDIATE': 2, 'MAIN_TOP': 3,
            'HOOP_END_BOTTOM': 10, 'HOOP_MID': 11, 'HOOP_END_TOP': 12,
        }
        df['_role_idx'] = df['bar_role'].apply(lambda r: role_order.get(r, 99))
        df = df.sort_values(['start_grid', 'member_id', '_lv_idx', '_role_idx'])
        df = df.drop(columns=['_lv_idx', '_role_idx']).reset_index(drop=True)

    column_order = [
        'member_id', 'start_grid', 'level_from', 'level_to',
        'bar_position', 'bar_role', 'bar_type',
        'dia_mm', 'n_bars', 'length_mm',
        'spacing_mm', 'zone_length_mm', 'quantity_pieces', 'total_length_mm',
        'splice_start_mm', 'splice_start_end_mm',
        'splice_end_mm', 'splice_end_end_mm',
        'x_start_mm', 'y_start_mm', 'z_start_mm',
        'x_end_mm', 'y_end_mm', 'z_end_mm',
        'segment_id', 'b_mm', 'h_mm', 'shape',
    ]
    avail = [c for c in column_order if c in df.columns]
    df = df[avail]

    main_count = len(df[df['bar_type'] == 'MAIN'])
    hoop_count = len(df[df['bar_type'] == 'HOOP'])
    print(f'[RebarColumn] {main_count} main bar records + '
          f'{hoop_count} hoop records = {len(df)} total')

    # Add split columns for schema consistency (columns don't exceed 12m)
    if not df.empty:
        for col in ('split_piece', 'split_total', 'original_length_mm'):
            if col not in df.columns:
                df[col] = None

    return df
