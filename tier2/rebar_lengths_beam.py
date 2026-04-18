"""
Beam Rebar Length Calculator — Tier 2

Computes bar-by-bar lengths for beam reinforcement including:
- Main bars (TOP/BOT) with anchorage (hook/lap) based on gridline role
- Additional bars for VARIABLE reinforcement zones
- Stirrups with zone lengths (EXT/INT/CTR)
- Stock length split (>12m → 2 pieces with mid-bar lap)
- 3D coordinates for BIM rendering

Logic adapted from RebarLengthsBeamCalculator.py (v8)
Reads Tier 1 output from AISIMS converter.

Input:  MembersBeam.csv, MembersColumn.csv, Sections.csv,
        ReinforcementBeam.csv, Nodes.csv,
        development_lengths.csv, lap_splice.csv
Output: RebarLengthsBeam.csv
"""

import pandas as pd
import numpy as np
import re
import math
from pathlib import Path
from typing import Dict, List, Optional, Set

from converters.beam_junction_graph import (
    BeamRebarCount,
    BeamRun,
    build_beam_refs,
    build_support_node_set,
    classify_junctions,
    compute_runs,
)

# ── Constants ────────────────────────────────────────────────────────────────

COVER_MM = 50.0
HOOK_EXTENSION_FACTOR = 10
MAX_STOCK_LENGTH_MM = 12000
DEFAULT_STIRRUP_DIA = 13.0  # D13 — used when stirrup cfg unavailable


def _bars_per_layer(b_mm, cover_mm, stirrup_dia_mm, bar_dia_mm):
    """Max bars that fit in one layer of a beam cross-section.

    Layout: cover + stirrup + [bar + gap]*n + bar + stirrup + cover = b.
    Effective width = b - 2*(cover + stirrup).
    Clear gap = max(25, 1.5*bar_dia) per KCI.
    """
    eff = float(b_mm) - 2 * (float(cover_mm) + float(stirrup_dia_mm))
    if eff <= 0:
        return 1
    gap = max(25.0, 1.5 * float(bar_dia_mm))
    pitch = float(bar_dia_mm) + gap
    return max(1, int((eff - float(bar_dia_mm)) / pitch) + 1)

# Feature flag — count-matched anchorage per Prof. Sunkuk rule (issue #78 B).
# When True, beam-to-beam coaxial junctions are classified using the junction
# graph + runs (TOP and BOT independently). Bar counts determine whether bars
# continue through as LAP or terminate as HOOK, and remainder bars are emitted
# for the "extra" bars on the higher-count side.
# When False, fall back to the legacy zone-width feasibility rule.
USE_COUNT_MATCHED_ANCHORAGE = True


# ── Helpers ──────────────────────────────────────────────────────────────────

def _steel_grade(dia_mm, dia_fy_map=None, fy_override=None):
    """Get fy for a rebar diameter.
    Priority: fy_override (per-element) > dia_fy_map (project-level) > hardcoded fallback.
    """
    if fy_override is not None:
        return int(fy_override)
    if dia_fy_map and int(dia_mm) in dia_fy_map:
        return dia_fy_map[int(dia_mm)]
    return 400 if int(dia_mm) in (10, 13) else 600


def _dia_label(d_mm):
    return f'D{int(d_mm)}'


def _parse_fc(material_id):
    """Extract concrete strength from material_id: 'C35' → 35."""
    m = re.search(r'(\d+)', str(material_id).upper())
    return int(m.group(1)) if m else 35  # default to 35


def _beam_direction(x_from, y_from, x_to, y_to):
    """Determine beam direction from coordinates."""
    dx = abs(x_to - x_from)
    dy = abs(y_to - y_from)
    if dy < 1 and dx > 1:
        return 'X'
    if dx < 1 and dy > 1:
        return 'Y'
    # Diagonal — use dominant direction
    return 'X' if dx >= dy else 'Y'


def _level_to_raw_mid(level, base_member_id):
    """Reconstruct the prefixed member_id used in ReinforcementBeam (P1 style).

    Level encoding: B1→'-1', B2→'-2', 1F→'1', 2F→'2', RF→'R', Roof→'R',
    PHR→'PHR'. Prefix is prepended to the base member_id.
    """
    if not level:
        return base_member_id
    lv = level.strip().upper()
    if lv.startswith('B') and len(lv) > 1 and lv[1:].isdigit():
        prefix = '-' + lv[1:]
    elif lv == 'ROOF':
        prefix = 'R'
    elif lv.endswith('F') and lv[:-1].replace('-', '').isdigit():
        prefix = lv[:-1]
    elif lv == 'PHR':
        prefix = 'PHR'
    else:
        prefix = lv
    return prefix + base_member_id


def _extract_raw_prefix(raw_mid, base_mid):
    """Extract level prefix from a raw reinforcement member_id.

    P2 space-separated: '-1~-4 B1' → '-1~-4', 'P G7' → 'P'
    P1 concatenated:    '-1B11' → '-1', 'RB13' → 'R'
    No prefix:          'TG0' (base='TG0') → ''
    """
    if ' ' in raw_mid:
        return raw_mid.rsplit(' ', 1)[0]
    if raw_mid.endswith(base_mid) and len(raw_mid) > len(base_mid):
        return raw_mid[:-len(base_mid)]
    return ''


def _prefix_token_to_level(token):
    """Convert a single prefix token to a MembersBeam level name.

    '-1' → 'B1', '1' → '1F', 'R' → 'Roof', 'P' → 'PIT', 'PHR' → 'PHR'.
    Returns None for unrecognised tokens.
    """
    t = token.strip()
    if not t:
        return None
    if t.startswith('-') and t[1:].isdigit():
        return f'B{t[1:]}'
    if t.isdigit():
        return f'{t}F'
    up = t.upper()
    if up in ('R', 'ROOF'):
        return 'Roof'
    if up == 'P':
        return 'PIT'
    if up == 'PHR':
        return 'PHR'
    return None


def _expand_level_prefix(prefix, level_order):
    """Expand a prefix (possibly a range) into a set of level names.

    '1' → {'1F'}, '-1~-4' → {'B1','B2','B3','B4'}, '3~R' → {'3F',...,'Roof'}
    Returns None if prefix is empty or contains unrecognised tokens.
    """
    if not prefix:
        return None
    if '~' in prefix:
        parts = prefix.split('~', 1)
        start = _prefix_token_to_level(parts[0])
        end = _prefix_token_to_level(parts[1])
        if (start and end
                and start in level_order and end in level_order):
            i1 = level_order.index(start)
            i2 = level_order.index(end)
            lo, hi = min(i1, i2), max(i1, i2)
            return set(level_order[lo:hi + 1])
        return None
    lv = _prefix_token_to_level(prefix)
    if lv and lv in level_order:
        return {lv}
    return None


def _bar_z(z_ref, h_mm, cover_mm, position, layer, dia_mm, splice_layer=False):
    """Calculate bar Z coordinate within beam cross-section.

    Layer stacking (inward from beam face):
      Layer 1: MAIN outer (at stirrup) — even span index in chain
      Layer 2: MAIN splice pair (odd span) — touching (offset=db) when
               splice_layer=True, standard gap otherwise
      Layer 3: ADD bars — always standard gap from layer 2

    splice_layer: True for layer 2 contact splice (bars touch, 0 gap).
    Same concept applies to columns (shift inward by 1d at splice zone).
    """
    d = float(dia_mm)
    std_gap = max(25.0, d)
    gap_1_2 = 0.0 if splice_layer else std_gap
    gap_2_3 = std_gap

    if position == 'TOP':
        z_top = z_ref + h_mm / 2
        z1 = z_top - cover_mm - d / 2
        if layer == 1:
            return z1
        z2 = z1 - d - gap_1_2
        if layer == 2:
            return z2
        z3 = z2 - d - gap_2_3
        if layer == 3:
            return z3
    elif position == 'BOT':
        z_bot = z_ref - h_mm / 2
        z1 = z_bot + cover_mm + d / 2
        if layer == 1:
            return z1
        z2 = z1 + d + gap_1_2
        if layer == 2:
            return z2
        z3 = z2 + d + gap_2_3
        if layer == 3:
            return z3
    return z_ref


# ── Lookup tables ────────────────────────────────────────────────────────────

class DevLapLookup:
    """Unified development length and lap splice lookup.

    Reads the new unified tables with member_type column:
        development_lengths.csv: fy, diameter, fc, member_type, Ldb, Ldt, Ldh, Ldc
        lap_splice.csv: fy, diameter, fc, member_type, Lpb, Lpt, Ldc, Lpc
    """

    def __init__(self, dev_path, lap_path):
        self.dev_df = pd.read_csv(dev_path)
        self.lap_df = pd.read_csv(lap_path)
        self.dev_df.columns = self.dev_df.columns.str.strip()
        self.lap_df.columns = self.lap_df.columns.str.strip()

    def get(self, fy, dia_mm, fc, member_type='BEAM_COLUMN'):
        """
        Returns (Ldh, Ldb, Ldt, Lpt, Lpb):
            Ldh: development length for hook (hooked anchorage)
            Ldb: basic development length, BOTTOM bars (straight, bond only)
            Ldt: basic development length, TOP bars (straight, bond only)
            Lpt: top bar lap splice (Class B)
            Lpb: bottom bar lap splice (Class B)

        Ldb/Ldt are for the #78 STRAIGHT anchorage fallback (BOT bars
        when has_concrete_below is False).
        """
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
                print(f'  [WARN] No dev length for fy={fy}, {d_label}, fc={fc}, {member_type}')
                return 300, 800, 1000, 600, 500  # safe defaults
            row_dev = row_dev.iloc[(row_dev['fc'] - fc).abs().argsort()[:1]]

        Ldh = float(row_dev['Ldh'].iloc[0])
        Ldb = float(row_dev['Ldb'].iloc[0]) if 'Ldb' in row_dev.columns else Ldh * 2.5
        Ldt = float(row_dev['Ldt'].iloc[0]) if 'Ldt' in row_dev.columns else Ldh * 3.2

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
                return Ldh, Ldb, Ldt, 600, 500
            row_lap = row_lap.iloc[(row_lap['fc'] - fc).abs().argsort()[:1]]

        # Support both old (Lpt_B/Lpb_B) and new (Lpt/Lpb) column names
        lpt_col = 'Lpt' if 'Lpt' in row_lap.columns else 'Lpt_B'
        lpb_col = 'Lpb' if 'Lpb' in row_lap.columns else 'Lpb_B'
        Lpt = float(row_lap[lpt_col].iloc[0])
        Lpb = float(row_lap[lpb_col].iloc[0])

        return Ldh, Ldb, Ldt, Lpt, Lpb


# ── Data adapter (reads our Tier 1 format) ───────────────────────────────────

class BeamDataAdapter:
    """Adapts our converter output to the calculator's needs."""

    def __init__(self, beams_df, columns_df, sections_df, reinf_df, nodes_df,
                 walls_df=None, bwalls_df=None):
        self.beams_df = beams_df.copy()
        self.columns_df = columns_df.copy()
        self.sections_df = sections_df.copy()
        self.reinf_df = reinf_df.copy()
        self.nodes_df = nodes_df.copy()
        self.walls_df = walls_df.copy() if walls_df is not None else pd.DataFrame()
        self.bwalls_df = bwalls_df.copy() if bwalls_df is not None else pd.DataFrame()

        # Issue #78 unified anchorage predicate — integer-exact column_below
        # OR wall_below. Used by _emit_remainder_bars and (eventually) the
        # other main-bar emit sites to decide HOOK vs STRAIGHT and the hook
        # direction flag. Built once, queried per terminal.
        from converters.concrete_below import build_has_concrete_below
        self.has_concrete_below = build_has_concrete_below(
            self.columns_df, self.walls_df, self.bwalls_df, self.nodes_df
        )

        self._build_lookups()

    def _build_lookups(self):
        # Section properties: section_id → {b_mm, h_mm, cover_mm, effective_depth, shape}
        self.section_props = {}
        for _, row in self.sections_df.iterrows():
            sid = str(row['section_id'])
            self.section_props[sid] = {
                'b_mm': float(row['b_mm']) if pd.notna(row.get('b_mm')) else None,
                'h_mm': float(row['h_mm']) if pd.notna(row.get('h_mm')) else None,
                'effective_depth': float(row['effective_depth_mm']) if pd.notna(row.get('effective_depth_mm')) else None,
                'cover_mm': float(row.get('cover_mm', COVER_MM)),
                'shape': str(row.get('shape', 'RECT')).upper(),
            }

        # Node coordinates
        self.node_coords = {}
        for _, row in self.nodes_df.iterrows():
            self.node_coords[str(row['node_id'])] = {
                'x_mm': float(row['x_mm']),
                'y_mm': float(row['y_mm']),
                'z_mm': float(row['z_mm']),
            }

        # Column dimensions at grid points: (grid, level) → {b_mm, h_mm}
        # A column spanning level_from~level_to supports beams at both levels.
        self.column_dims = {}
        for _, row in self.columns_df.iterrows():
            grid = str(row.get('grid', ''))
            if not grid or grid == 'OFF_GRID':
                continue
            b = float(row['b_mm']) if pd.notna(row.get('b_mm')) else 0
            h = float(row['h_mm']) if pd.notna(row.get('h_mm')) else 0
            levels = set()
            for lf in ('level_from', 'level_to', 'level'):
                lv = str(row.get(lf, '') or '').strip()
                if lv:
                    levels.add(lv)
            for lv in levels:
                key = (grid, lv)
                if key not in self.column_dims:
                    self.column_dims[key] = {'b_mm': 0, 'h_mm': 0}
                self.column_dims[key]['b_mm'] = max(self.column_dims[key]['b_mm'], b)
                self.column_dims[key]['h_mm'] = max(self.column_dims[key]['h_mm'], h)

        # Wall thickness at grid points for beam-to-wall anchorage.
        # Match walls to beam grids by proximity of wall centroid to node grid coords.
        # wall_thickness: (grid, level) → thickness_mm
        self.wall_thickness = {}
        if not self.walls_df.empty:
            # Build grid_pos → grid_label lookup from nodes
            grid_positions = {}  # grid_label → (x_mm, y_mm)
            for _, row in self.nodes_df.iterrows():
                g = str(row.get('grid', ''))
                if g and g != 'OFF_GRID':
                    grid_positions[g] = (float(row['x_mm']), float(row['y_mm']))

            for _, w in self.walls_df.iterrows():
                t = float(w.get('thickness_mm', 0) or 0)
                if t <= 0:
                    continue
                lv = str(w.get('level', '') or '').strip()
                if not lv:
                    continue
                cx = float(w.get('centroid_x_mm', 0) or 0)
                cy = float(w.get('centroid_y_mm', 0) or 0)
                # Find closest grid to wall centroid
                best_grid = None
                best_dist = 500  # max snap distance mm
                for g, (gx, gy) in grid_positions.items():
                    d = min(abs(cx - gx), abs(cy - gy))
                    if d < best_dist:
                        best_dist = d
                        best_grid = g
                if best_grid:
                    key = (best_grid, lv)
                    # Keep the max thickness at this grid
                    self.wall_thickness[key] = max(
                        self.wall_thickness.get(key, 0), t)

        # Beam direction and line_key
        self.beams_df['direction'] = self.beams_df.apply(
            lambda r: _beam_direction(
                r.get('x_from_mm', 0), r.get('y_from_mm', 0),
                r.get('x_to_mm', 0), r.get('y_to_mm', 0)
            ), axis=1
        )

        # Line key for grouping beams on same gridline
        # Use coordinate clustering: beams at similar perpendicular coordinate
        # are on the same gridline (tolerance-based, not exact match)
        self._assign_line_keys()

        # Parse reinforcement into per-member config
        self._parse_reinforcement()

    def _assign_line_keys(self, tolerance=50.0):
        """
        Group beams into gridlines by clustering perpendicular coordinates.
        X-direction beams: cluster by Y coordinate
        Y-direction beams: cluster by X coordinate
        """
        df = self.beams_df

        # Initialize line_key as object column to hold tuples
        line_keys = [None] * len(df)

        for direction in ['X', 'Y']:
            mask = df['direction'] == direction
            subset = df[mask]
            if subset.empty:
                continue

            perp_col = 'y_from_mm' if direction == 'X' else 'x_from_mm'

            for level in subset['level'].unique():
                level_mask = mask & (df['level'] == level)
                level_subset = df[level_mask]
                if level_subset.empty:
                    continue

                perp_values = level_subset[perp_col].values
                unique_perps = sorted(set(float(v) for v in perp_values))

                if not unique_perps:
                    continue

                # Cluster
                clusters = []
                current_cluster = [unique_perps[0]]
                for val in unique_perps[1:]:
                    if val - current_cluster[-1] <= tolerance:
                        current_cluster.append(val)
                    else:
                        clusters.append(current_cluster)
                        current_cluster = [val]
                clusters.append(current_cluster)

                val_to_cluster = {}
                for cluster in clusters:
                    rep = round(sum(cluster) / len(cluster), 1)
                    for v in cluster:
                        val_to_cluster[v] = rep

                for idx in level_subset.index:
                    perp = float(df.at[idx, perp_col])
                    cluster_rep = val_to_cluster.get(perp, perp)
                    pos = df.index.get_loc(idx)
                    line_keys[pos] = (level, direction, cluster_rep)

        df['line_key'] = line_keys

    def _parse_reinforcement(self):
        """Parse our flat reinforcement format into per-member rebar config.

        ReinforcementBeam member_ids have level prefixes (e.g., '-1B11', '6G1')
        while MembersBeam uses base IDs ('B11', 'G1').

        Configs are stored PER LEVEL (keyed by raw prefixed member_id) to avoid
        cross-level contamination. A merged fallback keyed by base member_id
        is also kept for stirrups (which are level-invariant in practice).
        """
        from converters.validation import _extract_base_member_id

        # Per-level configs: (raw_mid, position) → {dia, main, zones, is_uniform}
        self.long_cfg = {}
        # Reverse map: base_mid → set of raw_mids seen in reinforcement data
        self.base_to_raw = {}
        self.stirrup_cfg = {} # base_member_id → {zone: {dia_mm, n_legs, spacing_mm}}

        for _, row in self.reinf_df.iterrows():
            raw_mid = str(row.get('member_id', '')).strip()
            pos = str(row.get('position', '')).strip()  # I, M, J

            if not raw_mid or not pos:
                continue

            # Track base → raw mapping for fallback lookup
            base_mid = _extract_base_member_id(raw_mid)
            if base_mid not in self.base_to_raw:
                self.base_to_raw[base_mid] = set()
            self.base_to_raw[base_mid].add(raw_mid)

            # Map I/M/J to zones.
            # Legacy zone keys (EXT/INT/CTR) are kept for backward compat
            # with stirrup and ADD_INTERMEDIATE formulas. New I/M/J keys
            # store the per-position totals separately so the emission code
            # can distinguish asymmetric I vs J (e.g. G2A TOP I=12 J=4).
            #
            # 'main' = min(I, M, J) = true through-bar count, NOT min(EXT, INT).
            # TOP bars — keyed by RAW member_id (level-specific)
            top_total = row.get('top_total')
            top_dia = row.get('top_dia_mm')
            if pd.notna(top_total) and pd.notna(top_dia) and int(top_total) > 0:
                legacy_zone = 'EXT' if pos in ('I', 'J') else 'INT'
                key = (raw_mid, 'TOP')
                if key not in self.long_cfg:
                    self.long_cfg[key] = {
                        'dia': float(top_dia),
                        'zones': {},
                        'main': 0,
                    }
                cfg = self.long_cfg[key]
                cfg['zones'][legacy_zone] = max(
                    cfg['zones'].get(legacy_zone, 0), int(top_total)
                )
                # Per-position totals (I/M/J)
                imj_key = pos  # 'I', 'M', or 'J'
                cfg['zones'][imj_key] = max(
                    cfg['zones'].get(imj_key, 0), int(top_total)
                )

            # BOT bars — keyed by RAW member_id (level-specific)
            bot_total = row.get('bot_total')
            bot_dia = row.get('bot_dia_mm')
            if pd.notna(bot_total) and pd.notna(bot_dia) and int(bot_total) > 0:
                legacy_zone = 'EXT' if pos in ('I', 'J') else 'CTR'
                key = (raw_mid, 'BOT')
                if key not in self.long_cfg:
                    self.long_cfg[key] = {
                        'dia': float(bot_dia),
                        'zones': {},
                        'main': 0,
                    }
                cfg = self.long_cfg[key]
                cfg['zones'][legacy_zone] = max(
                    cfg['zones'].get(legacy_zone, 0), int(bot_total)
                )
                imj_key = pos
                cfg['zones'][imj_key] = max(
                    cfg['zones'].get(imj_key, 0), int(bot_total)
                )

            # Stirrups — keyed by BASE member_id (level-invariant in practice)
            st_dia = row.get('stirrup_dia_mm')
            st_legs = row.get('stirrup_legs')
            st_spacing = row.get('stirrup_spacing_mm')
            if pd.notna(st_dia) and pd.notna(st_spacing):
                zone = 'EXT' if pos in ('I', 'J') else 'INT'
                if base_mid not in self.stirrup_cfg:
                    self.stirrup_cfg[base_mid] = {}
                self.stirrup_cfg[base_mid][zone] = {
                    'dia_mm': float(st_dia),
                    'n_legs': int(st_legs) if pd.notna(st_legs) else 2,
                    'spacing_mm': float(st_spacing),
                }

        # ── Build level_prefix_map: (base_mid, level) → raw_mid ──────────
        #
        # Must handle two reinforcement naming conventions:
        #   P1 concatenated:       '-1B11', '6G1', 'RB13'
        #   P2 space + ranges:     '-1~-4 B1', '1 G7', 'P G7', '3~R LB1'
        #
        # Strategy order (first match wins per (base_mid, level)):
        #   Phase 1 — Parse raw_mid prefix → expand level range
        #   Phase 2 — Section_id suffix hint (handles P1 7F→Roof etc.)
        #   Phase 3 — P1 _level_to_raw_mid heuristic (concatenated prefix)
        #   Phase 4 — Assign remaining uncovered levels to unassigned raw_mids

        # Level ordering by z_mm (needed for range expansion)
        _level_z = self.beams_df.groupby('level')['z_mm'].first().sort_values()
        _level_order = list(_level_z.index)

        self.level_prefix_map = {}  # (base_mid, level) → raw_mid

        # Phase 1: Expand parsed prefixes into level sets
        for base_mid, raw_ids in self.base_to_raw.items():
            for raw_mid in raw_ids:
                prefix = _extract_raw_prefix(raw_mid, base_mid)
                levels = _expand_level_prefix(prefix, _level_order)
                if levels:
                    for lv in levels:
                        key = (base_mid, lv)
                        if key not in self.level_prefix_map:
                            self.level_prefix_map[key] = raw_mid

        # Phase 2: Section_id suffix (e.g. RC_B13_Roof → R prefix → RB13)
        _SUFFIX_TO_PREFIX = {'Roof': 'R', 'PHR': 'PHR'}
        for _, brow in self.beams_df.iterrows():
            mid = brow['member_id']
            lv = str(brow.get('level', '') or '')
            if not lv or mid not in self.base_to_raw:
                continue
            key = (mid, lv)
            if key in self.level_prefix_map:
                continue
            sec_id = str(brow.get('section_id', '') or '')
            sec_match = re.match(rf'^RC_{re.escape(mid)}_(.+)$', sec_id)
            if sec_match:
                suffix = sec_match.group(1)
                prefix = _SUFFIX_TO_PREFIX.get(suffix, suffix)
                candidate = prefix + mid
                if candidate in self.base_to_raw[mid]:
                    self.level_prefix_map[key] = candidate
                    continue

            # Phase 3: P1 concatenated prefix heuristic
            candidate = _level_to_raw_mid(lv, mid)
            if candidate in self.base_to_raw[mid]:
                self.level_prefix_map[key] = candidate

        # Phase 4: Assign uncovered levels to unassigned raw_mids
        # Handles ambiguous prefixes like 'P' that Phase 1 couldn't parse,
        # and also P1 edge cases with only one unmatched raw_mid.
        for base_mid, raw_ids in self.base_to_raw.items():
            covered = {lv for (m, lv) in self.level_prefix_map
                       if m == base_mid}
            beam_levels = set(
                self.beams_df.loc[
                    self.beams_df['member_id'] == base_mid, 'level'
                ]
            )
            uncovered = beam_levels - covered
            if not uncovered:
                continue
            assigned = {self.level_prefix_map[k]
                        for k in self.level_prefix_map if k[0] == base_mid}
            unassigned = [r for r in raw_ids if r not in assigned]
            if len(unassigned) == 1:
                for lv in uncovered:
                    self.level_prefix_map[(base_mid, lv)] = unassigned[0]

        # Finalize: compute 'main' count (minimum across I/M/J zones)
        # and is_uniform. Use per-position (I/M/J) values when available,
        # fall back to legacy (EXT/INT/CTR) for backward compat.
        for key, cfg in self.long_cfg.items():
            zones = cfg['zones']
            if zones:
                # Prefer I/M/J for 'main' computation (true through-count)
                imj_vals = [zones[k] for k in ('I', 'M', 'J') if k in zones]
                if imj_vals:
                    cfg['main'] = min(imj_vals)
                    cfg['is_uniform'] = (len(set(imj_vals)) == 1)
                else:
                    # Legacy fallback (EXT/INT or EXT/CTR)
                    legacy_vals = [v for k, v in zones.items()
                                   if k in ('EXT', 'INT', 'CTR')]
                    cfg['main'] = min(legacy_vals) if legacy_vals else 0
                    cfg['is_uniform'] = (len(set(legacy_vals)) == 1) if legacy_vals else True
                cfg['num_zones'] = len(zones)
            else:
                cfg['main'] = 0
                cfg['is_uniform'] = True

    def get_column_width(self, grid, direction, level=None):
        """Get column width at a grid point in beam direction.
        Uses (grid, level) key for level-aware lookup.
        Falls back to any-level match if level not found (backward compat).
        """
        if level:
            dims = self.column_dims.get((grid, level), None)
            if dims:
                return dims.get('b_mm', 0) if direction == 'X' else dims.get('h_mm', 0)
        # Fallback: match any level at this grid (backward compat)
        for (g, _lv), dims in self.column_dims.items():
            if g == grid:
                return dims.get('b_mm', 0) if direction == 'X' else dims.get('h_mm', 0)
        return 0

    def get_wall_thickness(self, grid, direction, level=None):
        """Get wall thickness at a grid point, 0 if no wall.
        Used as fallback when get_column_width returns 0 (beam anchors into wall).
        """
        if level:
            t = self.wall_thickness.get((grid, level), 0)
            if t > 0:
                return t
        # Fallback: match any level at this grid
        for (g, _lv), t in self.wall_thickness.items():
            if g == grid and t > 0:
                return t
        return 0

    def get_section(self, section_id):
        return self.section_props.get(str(section_id))

    def get_long_cfg(self, member_id, position, level=None):
        """Look up longitudinal rebar config for a beam.

        Uses level to find the correct level-specific config (raw prefixed
        member_id). Falls back to closest uniform config if the exact level
        isn't found (prevents cross-level contamination while still covering
        data gaps like missing 7F entries).
        """
        pos = position.upper()
        raw_ids = self.base_to_raw.get(member_id, set())

        # Level-specific lookup: use empirical map first, then prefix heuristic
        if level:
            mapped = self.level_prefix_map.get((member_id, level))
            if mapped:
                cfg = self.long_cfg.get((mapped, pos))
                if cfg:
                    return cfg
            # Heuristic fallback
            raw_mid = _level_to_raw_mid(level, member_id)
            if raw_mid != mapped:  # avoid duplicate lookup
                cfg = self.long_cfg.get((raw_mid, pos))
                if cfg:
                    return cfg
            # Level provided but no config found — don't contaminate from other levels
            if len(raw_ids) != 1:
                # Multi-level: use uniform fallback below
                pass
            else:
                return self.long_cfg.get((next(iter(raw_ids)), pos))

        # Fallback: if only one raw_mid for this base, use it directly
        if len(raw_ids) == 1:
            return self.long_cfg.get((next(iter(raw_ids)), pos))

        # Multi-level with missing level data: use a UNIFORM config from
        # another level as fallback (safe — uniform means no ADD bars).
        # Skip variable configs to avoid cross-level contamination.
        for raw_id in raw_ids:
            cfg = self.long_cfg.get((raw_id, pos))
            if cfg and cfg.get('is_uniform', False):
                return cfg
        return None

    def get_stirrup_zones(self, member_id):
        return list(self.stirrup_cfg.get(member_id, {}).keys())

    def get_stirrup_cfg(self, member_id, zone):
        return self.stirrup_cfg.get(member_id, {}).get(zone)


# ── Anchorage info ───────────────────────────────────────────────────────────

def _add_anchorage(bar, Ldh, Lpt_B, Lpb_B):
    """Add anchorage type and lengths to bar dict."""
    role = bar['bar_role']
    pos = bar['bar_position']

    if role == 'MAIN_SINGLE':
        bar.update(anchorage_start='HOOK', anchorage_end='HOOK', lap_length_mm=None)
    elif role == 'MAIN_START':
        bar.update(anchorage_start='HOOK', anchorage_end='LAP',
                   lap_length_mm=Lpt_B if pos == 'TOP' else Lpb_B)
    elif role == 'MAIN_INTERMEDIATE':
        bar.update(anchorage_start='LAP', anchorage_end='LAP',
                   lap_length_mm=Lpt_B if pos == 'TOP' else Lpb_B)
    elif role == 'MAIN_END':
        bar.update(anchorage_start='LAP', anchorage_end='HOOK',
                   lap_length_mm=Lpt_B if pos == 'TOP' else Lpb_B)
    elif role == 'MAIN_REMAINDER':
        # Case 2 remainder bar: hook on each end (terminates inside adjacent
        # lower-count beams after Ldh embedment). Structurally identical to
        # MAIN_SINGLE for the purpose of anchorage type, but physically the
        # hook ends sit at a beam-beam junction, not a column face.
        # Issue #78 B Case 2.
        bar.update(anchorage_start='HOOK', anchorage_end='HOOK', lap_length_mm=None)
    elif role == 'ADD_START':
        bar.update(anchorage_start='HOOK', anchorage_end='STRAIGHT', lap_length_mm=None)
    elif role == 'ADD_END':
        bar.update(anchorage_start='STRAIGHT', anchorage_end='HOOK', lap_length_mm=None)
    elif role in ('ADD_INTERMEDIATE', 'ADD_MIDSPAN'):
        bar.update(anchorage_start='STRAIGHT', anchorage_end='STRAIGHT', lap_length_mm=None)

    bar['development_length_mm'] = Ldh
    bar['transition_type'] = None
    return bar


def _extend_lap_coords(bar, direction):
    """Extend bar end coordinate by L_lap at the LAP-anchored end.

    Per the length formula:
      MAIN_START:        l_cl + Ldh + L_lap  → LAP at END only
      MAIN_INTERMEDIATE: l_span + L_lap      → LAP extension at END
      MAIN_END:          l_cl + Ldh          → no L_lap added (receives overlap)

    The bar physically extends L_lap past the column face into the next span.
    Only the END coordinate is extended (the "forward" direction of the chain).
    The START side receives overlap from the previous bar's extension.
    """
    role = bar.get('bar_role', '')
    lap = bar.get('lap_length_mm')
    if not lap or lap <= 0:
        return bar
    # Only extend for roles that include L_lap in their length formula
    if role not in ('MAIN_START', 'MAIN_INTERMEDIATE'):
        return bar

    xs = float(bar.get('x_start_mm', 0) or 0)
    ys = float(bar.get('y_start_mm', 0) or 0)
    xe = float(bar.get('x_end_mm', 0) or 0)
    ye = float(bar.get('y_end_mm', 0) or 0)

    if direction == 'X':
        # Extend the END along X. Bar goes from smaller to larger x.
        if xe >= xs:
            bar['x_end_mm'] = round(xe + lap, 1)
        else:
            bar['x_end_mm'] = round(xe - lap, 1)
    elif direction == 'Y':
        if ye >= ys:
            bar['y_end_mm'] = round(ye + lap, 1)
        else:
            bar['y_end_mm'] = round(ye - lap, 1)
    else:
        # Diagonal: extend along actual beam direction
        dx = xe - xs
        dy = ye - ys
        blen = math.sqrt(dx**2 + dy**2)
        if blen > 1:
            ux, uy = dx / blen, dy / blen
            bar['x_end_mm'] = round(xe + ux * lap, 1)
            bar['y_end_mm'] = round(ye + uy * lap, 1)

    return bar


# ── Stock length split ───────────────────────────────────────────────────────

def _split_stock(bar, L_lap, direction):
    """Split bar if >12m. Returns list of 1 or 2 bars."""
    length = bar['length_mm']
    if length <= MAX_STOCK_LENGTH_MM:
        bar['split_piece'] = None
        bar['original_length_mm'] = None
        return [bar]

    piece_len = int(round((length + L_lap) / 2))

    xs = bar.get('x_start_mm', 0) or 0
    ys = bar.get('y_start_mm', 0) or 0
    zs = bar.get('z_start_mm', 0) or 0
    xe = bar.get('x_end_mm', 0) or 0
    ye = bar.get('y_end_mm', 0) or 0
    ze = bar.get('z_end_mm', 0) or 0
    mx, my, mz = (xs + xe) / 2, (ys + ye) / 2, (zs + ze) / 2

    p1 = {**bar, 'length_mm': piece_len, 'split_piece': 1,
          'original_length_mm': length, 'anchorage_end': 'LAP',
          'lap_length_mm': L_lap, 'x_end_mm': mx, 'y_end_mm': my, 'z_end_mm': mz}
    p2 = {**bar, 'length_mm': piece_len, 'split_piece': 2,
          'original_length_mm': length, 'anchorage_start': 'LAP',
          'lap_length_mm': L_lap, 'x_start_mm': mx, 'y_start_mm': my, 'z_start_mm': mz}
    return [p1, p2]


# ── ADD bar coordinates ──────────────────────────────────────────────────────

def _add_bar_coords(role, length, direction, xs, ys, zs, xe, ye, ze):
    """Calculate start/end coordinates for ADD bars."""
    if direction == 'X':
        if role == 'ADD_START':
            return {'x_start_mm': xs, 'y_start_mm': ys, 'z_start_mm': zs,
                    'x_end_mm': xs + length, 'y_end_mm': ys, 'z_end_mm': zs}
        if role == 'ADD_END':
            return {'x_start_mm': xe - length, 'y_start_mm': ys, 'z_start_mm': zs,
                    'x_end_mm': xe, 'y_end_mm': ye, 'z_end_mm': ze}
        if role in ('ADD_INTERMEDIATE', 'ADD_SUPPORT_BRIDGING'):
            return {'x_start_mm': xe - length / 2, 'y_start_mm': ys, 'z_start_mm': zs,
                    'x_end_mm': xe + length / 2, 'y_end_mm': ye, 'z_end_mm': ze}
        if role == 'ADD_MIDSPAN':
            mx = (xs + xe) / 2
            return {'x_start_mm': mx - length / 2, 'y_start_mm': ys, 'z_start_mm': zs,
                    'x_end_mm': mx + length / 2, 'y_end_mm': ye, 'z_end_mm': ze}
    elif direction == 'Y':
        if role == 'ADD_START':
            return {'x_start_mm': xs, 'y_start_mm': ys, 'z_start_mm': zs,
                    'x_end_mm': xs, 'y_end_mm': ys + length, 'z_end_mm': zs}
        if role == 'ADD_END':
            return {'x_start_mm': xs, 'y_start_mm': ye - length, 'z_start_mm': zs,
                    'x_end_mm': xe, 'y_end_mm': ye, 'z_end_mm': ze}
        if role == 'ADD_INTERMEDIATE':
            return {'x_start_mm': xs, 'y_start_mm': ye - length / 2, 'z_start_mm': zs,
                    'x_end_mm': xe, 'y_end_mm': ye + length / 2, 'z_end_mm': ze}
        if role == 'ADD_MIDSPAN':
            my = (ys + ye) / 2
            return {'x_start_mm': xs, 'y_start_mm': my - length / 2, 'z_start_mm': zs,
                    'x_end_mm': xe, 'y_end_mm': my + length / 2, 'z_end_mm': ze}
    return {'x_start_mm': xs, 'y_start_mm': ys, 'z_start_mm': zs,
            'x_end_mm': xe, 'y_end_mm': ye, 'z_end_mm': ze}


# ── Run index (Phase 2 count-matched anchorage) ────────────────────────────

class RunIndex:
    """Precomputed per-beam run membership for TOP and BOT positions.

    Built once at the start of the main bar calculation. Uses the adapter's
    long-cfg (main bar count per position) to compute junction classifications
    and walk connected components per Prof. Sunkuk's rule.
    """

    def __init__(self, adapter):
        refs = build_beam_refs(adapter.beams_df)

        # Bar counts per beam row_idx, pulled from the adapter's long-config
        # (not from output rebar rows, which don't exist yet).
        counts: Dict[int, BeamRebarCount] = {}
        for idx, row in adapter.beams_df.iterrows():
            mid = str(row.get('member_id', '') or '')
            lv = str(row.get('level', '') or '')
            cfg_top = adapter.get_long_cfg(mid, 'TOP', level=lv)
            cfg_bot = adapter.get_long_cfg(mid, 'BOT', level=lv)
            if cfg_top is None or cfg_bot is None:
                counts[int(idx)] = BeamRebarCount()
                continue
            counts[int(idx)] = BeamRebarCount(
                n_top=int(cfg_top.get('main', 0) or 0),
                dia_top=int(cfg_top.get('dia', 0) or 0),
                n_bot=int(cfg_bot.get('main', 0) or 0),
                dia_bot=int(cfg_bot.get('dia', 0) or 0),
            )

        supported = build_support_node_set(
            adapter.columns_df if not adapter.columns_df.empty else None,
            adapter.walls_df if not adapter.walls_df.empty else None,
        )

        findings = classify_junctions(refs, counts, supported)
        self.runs_top: List[BeamRun] = compute_runs(
            refs, findings, counts, adapter.beams_df, 'TOP'
        )
        self.runs_bot: List[BeamRun] = compute_runs(
            refs, findings, counts, adapter.beams_df, 'BOT'
        )

        # row_idx → run lookup (per position)
        self.span_to_run_top: Dict[int, BeamRun] = {}
        self.span_to_run_bot: Dict[int, BeamRun] = {}
        for r in self.runs_top:
            for idx in r.ordered_beams:
                self.span_to_run_top[idx] = r
        for r in self.runs_bot:
            for idx in r.ordered_beams:
                self.span_to_run_bot[idx] = r

        # Stats for logging
        self._stat_case2_runs = sum(1 for r in self.runs_top if r.has_case2) \
                              + sum(1 for r in self.runs_bot if r.has_case2)
        self._stat_remainders = sum(len(r.remainders) for r in self.runs_top) \
                              + sum(len(r.remainders) for r in self.runs_bot)

    def summary(self) -> str:
        return (
            f'[RunIndex] runs TOP={len(self.runs_top)} BOT={len(self.runs_bot)}, '
            f'Case2 runs={self._stat_case2_runs}, '
            f'remainder bars to emit={self._stat_remainders}'
        )

    def get_run(self, span_row_idx: int, position: str) -> Optional[BeamRun]:
        if position == 'TOP':
            return self.span_to_run_top.get(span_row_idx)
        return self.span_to_run_bot.get(span_row_idx)


def _emit_remainder_bars(run_index: RunIndex, adapter, lookup) -> list:
    """Emit MAIN_REMAINDER rows for every Case 2 remainder span across all runs.

    One physical bar per RemainderSpan at the lowest count level (subsequent
    levels are implicit in the bar's strip, not separate rows — the strip's
    level is captured in count_level for future optimization grouping).

    Phase 2 initial formula (approximation):
        length = sum(l_span of beams in the remainder interval) + 2 * Ldh

    Both ends are Case 2 hooks (anchorage_start=HOOK, anchorage_end=HOOK).
    The n_bars field carries the strip count — 1 per emitted row at this
    level. Adjacent strips at the same (interval, position) with consecutive
    levels are grouped for emission.
    """
    out: list = []
    beams_df = adapter.beams_df

    for run in (*run_index.runs_top, *run_index.runs_bot):
        if not run.remainders:
            continue

        # Group remainders by identical beam interval — multiple levels over
        # the same beams = one grouped row with n_bars = number of strips.
        from collections import defaultdict
        grouped: Dict[tuple, list] = defaultdict(list)
        for rm in run.remainders:
            key = tuple(rm.beam_row_idxs)
            grouped[key].append(rm)

        position = run.position
        dia = run.dia
        level_str = run.level

        for beam_idxs_tuple, strips in grouped.items():
            beam_idxs = list(beam_idxs_tuple)
            if not beam_idxs:
                continue
            n_bars = len(strips)

            # Issue #78 unified anchorage rule for MAIN_REMAINDER:
            #   for each end, decide HOOK vs STRAIGHT based on
            #   has_concrete_below at the *terminal node*:
            #     - run-boundary end → original first/last beam's node at
            #       that side (same node used by MAIN_START/END).
            #     - extended-into-adjacent-beam end → the adjacent beam's
            #       node at the shared junction. Despite the bar physically
            #       ending Ldh/Ld inside the adjacent span, the "concrete
            #       below" test is about what's under the junction support,
            #       not under the tip (which is typically mid-span and has
            #       no node).
            #   then:
            #     - TOP bar → always HOOK; support_extends_below flag is
            #       the predicate result (renderer maps True→down, False→up).
            #     - BOT bar → HOOK if concrete below (support_extends_below
            #       = True, renderer draws down tail); STRAIGHT if not
            #       (no hook, straight development length Ldb instead of Ldh).
            near_into_adj = strips[0].near_hooks_into_beam_idx is not None
            far_into_adj = strips[0].far_hooks_into_beam_idx is not None
            near_adj_idx = strips[0].near_hooks_into_beam_idx
            far_adj_idx = strips[0].far_hooks_into_beam_idx

            first_row = beams_df.loc[beam_idxs[0]]
            last_row = beams_df.loc[beam_idxs[-1]]
            member_id = str(first_row.get('member_id', '') or '')
            material_id = str(first_row.get('material_id', 'C35') or 'C35')
            b_mm = int(round(first_row.get('b_mm') or 400))
            h_mm = int(round(first_row.get('h_mm') or 600))
            fy_main = first_row.get('fy_main')

            # Development lengths for the diameter + material of the first
            # beam (all beams in the run share the same diameter by Case 3
            # break). Ldh = hooked; Ldb/Ldt = straight (BOT/TOP) for the
            # STRAIGHT fallback.
            fy = _steel_grade(dia, fy_override=fy_main)
            fc = _parse_fc(material_id)
            Ldh, Ldb, Ldt, Lpt_B, Lpb_B = lookup.get(fy, dia, fc)

            # Straight dev length for this bar position.
            Ld_straight = Ldt if position == 'TOP' else Ldb

            refs_on_dir = first_row.get('direction', 'X')

            # Interval min/max on primary axis (un-extended geometry).
            if refs_on_dir == 'X':
                all_primary = []
                for idx in beam_idxs:
                    r = beams_df.loc[idx]
                    all_primary.append(float(r.get('x_from_mm', 0) or 0))
                    all_primary.append(float(r.get('x_to_mm', 0) or 0))
            else:
                all_primary = []
                for idx in beam_idxs:
                    r = beams_df.loc[idx]
                    all_primary.append(float(r.get('y_from_mm', 0) or 0))
                    all_primary.append(float(r.get('y_to_mm', 0) or 0))
            interval_min = min(all_primary)
            interval_max = max(all_primary)

            # Perp axis values (constant across interval for a run).
            if refs_on_dir == 'X':
                perp = float(first_row.get('y_from_mm', 0) or 0)
            else:
                perp = float(first_row.get('x_from_mm', 0) or 0)
            zs = float(first_row.get('z_mm', 0) or 0)
            beam_z_int = int(round(zs))

            # Terminal-node resolution.
            #   pick_node(beam_row, target_primary) returns whichever of the
            #   beam's node_from/node_to sits at target_primary on the
            #   primary axis.
            def _pick_node(row, target):
                nf = str(row.get('node_from', '') or '').strip()
                nt = str(row.get('node_to', '') or '').strip()
                if refs_on_dir == 'X':
                    df_val = float(row.get('x_from_mm', 0) or 0)
                    dt_val = float(row.get('x_to_mm', 0) or 0)
                else:
                    df_val = float(row.get('y_from_mm', 0) or 0)
                    dt_val = float(row.get('y_to_mm', 0) or 0)
                return nf if abs(df_val - target) <= abs(dt_val - target) else nt

            # Near (= lower-primary) terminal node.
            if near_into_adj and near_adj_idx is not None:
                near_term_node = _pick_node(beams_df.loc[near_adj_idx], interval_min)
            else:
                near_term_node = _pick_node(first_row, interval_min)

            # Far (= higher-primary) terminal node.
            if far_into_adj and far_adj_idx is not None:
                far_term_node = _pick_node(beams_df.loc[far_adj_idx], interval_max)
            else:
                far_term_node = _pick_node(last_row, interval_max)

            # Beam axis (primary-only, z=0 for horizontal beams).
            if refs_on_dir == 'X':
                axis_ref = (int(round(interval_max - interval_min)), 0, 0)
            else:
                axis_ref = (0, int(round(interval_max - interval_min)), 0)

            # Predicate per end.
            level_int = str(level_str)
            near_concrete = adapter.has_concrete_below(
                near_term_node, level_int, beam_z_int, axis_ref
            )
            far_concrete = adapter.has_concrete_below(
                far_term_node, level_int, beam_z_int, axis_ref
            )

            # Decide anchorage per end.
            #   TOP: always HOOK. support_extends_below = predicate result.
            #   BOT: HOOK if concrete, STRAIGHT if not.
            if position == 'TOP':
                near_anchor = 'HOOK'
                far_anchor = 'HOOK'
            else:  # BOT
                near_anchor = 'HOOK' if near_concrete else 'STRAIGHT'
                far_anchor = 'HOOK' if far_concrete else 'STRAIGHT'

            # Coordinate extension. For ends that extend into an adjacent
            # span (near/far_into_adj=True), extend by:
            #   - Ldh past the interval boundary if HOOK
            #   - Ld_straight past the interval boundary if STRAIGHT
            # For run-boundary ends, don't extend the coord (the HOOK
            # geometry sits inside the boundary column, rendered from col
            # width). STRAIGHT is not valid at a run boundary in the
            # current spec — MAIN_REMAINDER only arises from Case 2 hooks
            # into adjacent beams — but if we end up with BOT + no
            # concrete on a run-boundary end, emit STRAIGHT and trust the
            # renderer.
            def _extension(end_into_adj, anchor):
                if not end_into_adj:
                    return 0.0
                return Ld_straight if anchor == 'STRAIGHT' else Ldh

            near_ext = _extension(near_into_adj, near_anchor)
            far_ext = _extension(far_into_adj, far_anchor)

            if refs_on_dir == 'X':
                xs = interval_min - near_ext
                xe = interval_max + far_ext
                ys = perp
                ye = perp
            else:
                xs = float(first_row.get('x_from_mm', 0) or 0)
                xe = xs
                ys = interval_min - near_ext
                ye = interval_max + far_ext
            ze = zs

            # col_width at each end:
            #   - extended-into-adjacent end → 0 (bar is in the adjacent
            #     span's clear zone, renderer mustn't try to place hook
            #     geometry relative to a column face).
            #   - run-boundary end → original beam's col_width on the
            #     matching side so the renderer places the hook at the
            #     actual column face.
            def _col_width_for_end(row, at_smaller_primary):
                """Return row's col_width for whichever side sits at the
                smaller (or larger) primary coord of the beam itself."""
                if refs_on_dir == 'X':
                    from_smaller = float(row.get('x_from_mm', 0) or 0) <= float(row.get('x_to_mm', 0) or 0)
                else:
                    from_smaller = float(row.get('y_from_mm', 0) or 0) <= float(row.get('y_to_mm', 0) or 0)
                if at_smaller_primary:
                    src = 'col_width_start_mm' if from_smaller else 'col_width_end_mm'
                else:
                    src = 'col_width_end_mm' if from_smaller else 'col_width_start_mm'
                return int(row.get(src, 0) or 0)

            cw_start_mm = 0 if near_into_adj else _col_width_for_end(first_row, at_smaller_primary=True)
            cw_end_mm = 0 if far_into_adj else _col_width_for_end(last_row, at_smaller_primary=False)

            # length_mm = sum of in-run span lengths + extension on each
            # extended end. Run-boundary ends add 0 (hook allowance is
            # handled by _split_stock / renderer from col_width).
            sum_span = 0.0
            for idx in beam_idxs:
                sum_span += float(beams_df.loc[idx].get('length_mm') or 0)
            length_mm = sum_span + near_ext + far_ext

            # support_extends_below flag.
            #   BOT + HOOK → True (concrete below, hook down).
            #   BOT + STRAIGHT → False (no concrete, no hook anyway).
            #   TOP + HOOK → predicate result (True=down into wall/col,
            #                False=up into slab).
            if position == 'TOP':
                seb_near = bool(near_concrete)
                seb_far = bool(far_concrete)
            else:
                seb_near = bool(near_concrete) and near_anchor == 'HOOK'
                seb_far = bool(far_concrete) and far_anchor == 'HOOK'

            sec = adapter.get_section(str(first_row.get('section_id', '') or ''))
            if sec is not None:
                cover = sec['cover_mm']
                shape = sec['shape']
            else:
                cover = COVER_MM
                shape = 'RECT'

            z_bar = _bar_z(zs, h_mm, cover, position, 1, dia)

            # Segment id for traceability — tie to the first beam's member
            seg_hint = f'{member_id}-REMAIN-{beam_idxs[0]}'

            bar = {
                'segment_id': seg_hint,
                'level': level_str,
                'direction': refs_on_dir,
                'line_grid': str(first_row.get('line_grid', '') or ''),
                'member_id': member_id,
                'span_index': beam_idxs[0],
                'start_grid': str(first_row.get('grid_from', '') or ''),
                'end_grid': str(last_row.get('grid_to', '') or ''),
                'b_mm': b_mm,
                'h_mm': h_mm,
                'shape': shape,
                'col_width_start_mm': cw_start_mm,
                'col_width_end_mm': cw_end_mm,
                'support_extends_below_start': seb_near,
                'support_extends_below_end': seb_far,
                'x_start_mm': xs,
                'y_start_mm': ys,
                'z_start_mm': z_bar,
                'x_end_mm': xe,
                'y_end_mm': ye,
                'z_end_mm': z_bar,
                'bar_position': position,
                'bar_role': 'MAIN_REMAINDER',
                'bar_type': 'MAIN',
                'dia_mm': dia,
                'n_bars': n_bars,
                'length_mm': int(round(length_mm)),
                'layer': 1,
                'reinforcement_type': 'UNIFORM',
                # Issue #78: terminal node refs for predicate audit.
                'terminal_node_start': near_term_node,
                'terminal_node_end': far_term_node,
            }
            bar = _add_anchorage(bar, Ldh, Lpt_B, Lpb_B)
            # Override _add_anchorage (which hardcodes HOOK/HOOK for
            # MAIN_REMAINDER) with the per-end decision from the predicate.
            bar['anchorage_start'] = near_anchor
            bar['anchorage_end'] = far_anchor
            # Remainder bars are typically short enough to not need stock split,
            # but run it through _split_stock anyway for consistency.
            lap_for_split = Lpt_B if position == 'TOP' else Lpb_B
            for piece in _split_stock(bar, lap_for_split, refs_on_dir):
                out.append(piece)

    return out


def _resolve_role_from_run(
    span_row_idx: int,
    position: str,
    fallback_role: str,
    fallback_n: int,
    run_index: Optional[RunIndex],
):
    """Return (role, n_bars, chain_pos) for MAIN_{TOP,BOT} at this span.

    chain_pos: 0-based index of this span within the run's ordered beams.
    Used for layer alternation at splices (even→layer 1, odd→layer 2).
    Returns -1 if not in a run.

    Uses run graph if the span is part of a Case 1/2 run at this position.
    Otherwise returns the fallback (current zone-width-based role + count).
    """
    if run_index is None:
        return fallback_role, fallback_n, -1
    run = run_index.get_run(span_row_idx, position)
    if run is None:
        return fallback_role, fallback_n, -1
    ordered = run.ordered_beams
    if span_row_idx not in ordered:
        return fallback_role, fallback_n, -1
    pos = ordered.index(span_row_idx)
    n_run = len(ordered)
    if n_run == 1:
        return fallback_role, fallback_n, -1
    if pos == 0:
        role = 'MAIN_START'
    elif pos == n_run - 1:
        role = 'MAIN_END'
    else:
        role = 'MAIN_INTERMEDIATE'
    return role, run.min_count, pos


# ── Process one sub-group of same-diameter contiguous spans ──────────────────

def _process_subgroup(span_list, gm_top, gm_bot, adapter, lookup, direction,
                     run_index: Optional[RunIndex] = None):
    """Process contiguous same-diameter spans. Returns list of bar dicts."""
    results = []
    n_sub = len(span_list)

    # ── Pre-compute joint feasibility for the entire subgroup ──
    # Joint_i connects span_i to span_{i+1}.
    # Feasible if the RECEIVING span's (span_{i+1}) allowable zone ≥ L_lap.
    # We need L_lap and clear span for each span first.
    span_data = []
    for info in span_list:
        sp = info['sp']
        grid_from = sp.get('grid_from', '')
        grid_to = sp.get('grid_to', '')
        l_span = float(sp['length_mm'])
        beam_level = sp.get('level', '')
        # Prefer precomputed col_width from Phase 2.7 (coordinate-based,
        # includes wall fallback). Only fall back to grid-based lookup when
        # the Phase 2.7 field is genuinely missing (NaN), NOT when it's 0
        # (which means "no support at this end"). Same fix as stirrups —
        # prevents grid_from == grid_to double-count (e.g. G5A-E33907).
        cw_s = sp.get('col_width_start_mm')
        cw_e = sp.get('col_width_end_mm')
        if pd.notna(cw_s):
            Wc1 = float(cw_s)
        else:
            Wc1 = adapter.get_column_width(grid_from, direction, beam_level)
            if Wc1 == 0:
                Wc1 = adapter.get_wall_thickness(grid_from, direction, beam_level)
        if pd.notna(cw_e):
            Wc2 = float(cw_e)
        else:
            Wc2 = adapter.get_column_width(grid_to, direction, beam_level)
            if Wc2 == 0:
                Wc2 = adapter.get_wall_thickness(grid_to, direction, beam_level)
        l_cl = l_span - 0.5 * (Wc1 + Wc2)

        dia_top = info['cfg_top']['dia']
        fy = _steel_grade(dia_top, fy_override=sp.get('fy_main'))
        fc = _parse_fc(sp.get('material_id', 'C35'))
        Ldh, Ldb, Ldt, Lpt_B, Lpb_B = lookup.get(fy, dia_top, fc)

        span_data.append({
            'l_span': l_span, 'l_cl': l_cl,
            'Wc1': Wc1, 'Wc2': Wc2,
            'Ldh': Ldh, 'Ldb': Ldb, 'Ldt': Ldt,
            'Lpt_B': Lpt_B, 'Lpb_B': Lpb_B,
            'L_lap': max(Lpt_B, Lpb_B),
        })

    # Determine joint feasibility: joint_i connects span_i → span_{i+1}
    # Feasible if receiving span (i+1) has allowable zone ≥ L_lap
    joint_feasible = []  # length = n_sub - 1
    for i in range(n_sub - 1):
        receiving = span_data[i + 1]
        zone_width = 0.50 * max(0, receiving['l_cl'])
        L_lap = receiving['L_lap']
        joint_feasible.append(zone_width >= L_lap)

    # Derive bar role per span from joint types
    # left_joint: joint_{i-1} (or run_start if i==0)
    # right_joint: joint_i (or run_end if i==n_sub-1)
    span_roles = []
    for i in range(n_sub):
        left_feasible = joint_feasible[i - 1] if i > 0 else None       # None = run start
        right_feasible = joint_feasible[i] if i < n_sub - 1 else None   # None = run end

        if left_feasible is None and right_feasible is None:
            role = 'MAIN_SINGLE'
        elif left_feasible is None and right_feasible is True:
            role = 'MAIN_START'
        elif left_feasible is None and right_feasible is False:
            role = 'MAIN_SINGLE'
        elif left_feasible is True and right_feasible is True:
            role = 'MAIN_INTERMEDIATE'
        elif left_feasible is True and (right_feasible is False or right_feasible is None):
            role = 'MAIN_END'
        elif left_feasible is False and right_feasible is True:
            role = 'MAIN_START'
        elif left_feasible is False and (right_feasible is False or right_feasible is None):
            role = 'MAIN_SINGLE'
        else:
            role = 'MAIN_SINGLE'

        span_roles.append(role)

    # ── Process each span with its assigned role ──
    for sub_i, info in enumerate(span_list):
        sp = info['sp']
        member_id = info['member_id']
        cfg_top = info['cfg_top']
        cfg_bot = info['cfg_bot']
        is_uniform = info['is_uniform']

        span_idx = info['index'] + 1
        segment_id = f"{member_id}-SEG{span_idx:03d}"

        grid_from = sp.get('grid_from', '')
        grid_to = sp.get('grid_to', '')
        l_span = float(sp['length_mm'])
        fc = _parse_fc(sp.get('material_id', 'C35'))

        xs = sp.get('x_from_mm', 0) or 0
        ys = sp.get('y_from_mm', 0) or 0
        zs = sp.get('z_mm', 0) or 0
        xe = sp.get('x_to_mm', 0) or 0
        ye = sp.get('y_to_mm', 0) or 0
        ze = zs

        dia_top = cfg_top['dia']
        dia_bot = cfg_bot['dia']

        sd = span_data[sub_i]
        Ldh, Lpt_B, Lpb_B = sd['Ldh'], sd['Lpt_B'], sd['Lpb_B']
        Wc1, Wc2 = sd['Wc1'], sd['Wc2']
        l_cl = sd['l_cl']

        sec = adapter.get_section(sp.get('section_id', ''))
        if sec is None:
            b_mm = sp.get('b_mm', 300)
            h_mm = sp.get('h_mm', 500)
            d_eff = h_mm - COVER_MM - 11
            cover = COVER_MM
            shape = 'RECT'
        else:
            b_mm = sec['b_mm'] or sp.get('b_mm', 300)
            h_mm = sec['h_mm'] or sp.get('h_mm', 500)
            d_eff = sec['effective_depth'] or (h_mm - COVER_MM - 11)
            cover = sec['cover_mm']
            shape = sec['shape']

        lext = max(d_eff, 12.0 * dia_top)

        legacy_role = span_roles[sub_i]
        is_single_legacy = (legacy_role == 'MAIN_SINGLE')

        # Legacy bar counts — used as fallback when run graph doesn't apply
        if is_single_legacy:
            fb_top = int(cfg_top['zones'].get('INT', cfg_top['main']))
            fb_bot = int(cfg_bot['zones'].get('CTR', cfg_bot['main']))
        elif is_uniform:
            fb_top = int(cfg_top['main'])
            fb_bot = int(cfg_bot['main'])
        else:
            fb_top = int(gm_top)
            fb_bot = int(gm_bot)

        # Per-position role (TOP and BOT independently when the flag is on)
        span_row_idx = sp.get('_row_idx')
        top_role, main_top, top_chain_pos = _resolve_role_from_run(
            span_row_idx, 'TOP', legacy_role, fb_top, run_index,
        )
        bot_role, main_bot, bot_chain_pos = _resolve_role_from_run(
            span_row_idx, 'BOT', legacy_role, fb_bot, run_index,
        )

        # Feasibility gate: if the run gives a LAP-based role but the
        # ADJACENT beam in the run can't accommodate the lap (50% rule:
        # 0.5*adjacent_l_cl < L_lap), fall back to HOOK at that end.
        # With member_id breaks, subgroup joints are always empty (n_sub=1),
        # so we check feasibility from the RUN context directly.
        def _check_run_feasibility(pos_label):
            """Check if the junctions to the left/right of this beam
            in the run are feasible. Returns (left_ok, right_ok)."""
            if run_index is None or span_row_idx is None:
                return True, True
            run = run_index.get_run(span_row_idx, pos_label)
            if run is None:
                return True, True
            ordered = run.ordered_beams
            if span_row_idx not in ordered:
                return True, True
            pos = ordered.index(span_row_idx)

            # This beam's L_lap (conservative: use max of TOP/BOT)
            sd = span_data[sub_i]
            my_llap = sd['L_lap']

            # Check right neighbor: can it receive a lap from this beam?
            right_ok = True
            if pos < len(ordered) - 1:
                right_idx = ordered[pos + 1]
                right_row = adapter.beams_df.loc[right_idx]
                r_span = float(right_row.get('length_mm', 0) or 0)
                r_cw1 = float(right_row.get('col_width_start_mm', 0) or 0)
                r_cw2 = float(right_row.get('col_width_end_mm', 0) or 0)
                r_lcl = r_span - 0.5 * (r_cw1 + r_cw2)
                right_ok = (0.5 * max(0, r_lcl) >= my_llap)

            # Check left neighbor: can it receive a lap from this beam?
            left_ok = True
            if pos > 0:
                left_idx = ordered[pos - 1]
                left_row = adapter.beams_df.loc[left_idx]
                l_span = float(left_row.get('length_mm', 0) or 0)
                l_cw1 = float(left_row.get('col_width_start_mm', 0) or 0)
                l_cw2 = float(left_row.get('col_width_end_mm', 0) or 0)
                l_lcl = l_span - 0.5 * (l_cw1 + l_cw2)
                left_ok = (0.5 * max(0, l_lcl) >= my_llap)

            # Also check: can THIS beam receive a lap from its neighbors?
            my_lcl = sd['l_cl']
            self_ok = (0.5 * max(0, my_lcl) >= my_llap)
            if not self_ok:
                left_ok = False
                right_ok = False

            return left_ok, right_ok

        def _gate_feasibility(role, n_bars, pos_label, fb_n):
            """Downgrade LAP-based role if the receiving joint is infeasible."""
            left_ok, right_ok = _check_run_feasibility(pos_label)
            if role == 'MAIN_START' and not right_ok:
                return 'MAIN_SINGLE', fb_n
            if role == 'MAIN_END' and not left_ok:
                return 'MAIN_SINGLE', fb_n
            if role == 'MAIN_INTERMEDIATE':
                if not left_ok and not right_ok:
                    return 'MAIN_SINGLE', fb_n
                if not right_ok:
                    return 'MAIN_END', fb_n
                if not left_ok:
                    return 'MAIN_START', fb_n
            return role, n_bars

        top_role, main_top = _gate_feasibility(top_role, main_top, 'TOP', fb_top)
        bot_role, main_bot = _gate_feasibility(bot_role, main_bot, 'BOT', fb_bot)

        # Track which positions were downgraded — skip gap bars for those.
        top_downgraded = (top_role == 'MAIN_SINGLE' and run_index is not None
                          and run_index.get_run(span_row_idx, 'TOP') is not None)
        bot_downgraded = (bot_role == 'MAIN_SINGLE' and run_index is not None
                          and run_index.get_run(span_row_idx, 'BOT') is not None)

        def _main_length(role_name: str, lap_len: float) -> float:
            if role_name == 'MAIN_SINGLE':
                return l_cl + Ldh + Ldh
            if role_name == 'MAIN_START':
                return l_cl + Ldh + lap_len
            if role_name == 'MAIN_END':
                return l_cl + Ldh
            # MAIN_INTERMEDIATE
            return l_span + lap_len

        Ltop = _main_length(top_role, Lpt_B)
        Lbot = _main_length(bot_role, Lpb_B)

        # Back-compat local — some downstream ADD-bar logic keys on the old
        # subgroup-level role. Default to the TOP role when TOP and BOT agree,
        # otherwise use the legacy role (which is what ADD bars expected).
        role = top_role if top_role == bot_role else legacy_role
        is_single_role = (role == 'MAIN_SINGLE')

        base = {
            'segment_id': segment_id, 'level': sp.get('level', ''),
            'direction': direction, 'line_grid': info['line_grid'],
            'member_id': member_id, 'span_index': span_idx,
            'start_grid': grid_from, 'end_grid': grid_to,
            'b_mm': b_mm, 'h_mm': h_mm, 'shape': shape,
            'col_width_start_mm': int(round(Wc1)) if Wc1 else 0,
            'col_width_end_mm': int(round(Wc2)) if Wc2 else 0,
            'support_extends_below_start': bool(sp.get('support_extends_below_start', False)),
            'support_extends_below_end': bool(sp.get('support_extends_below_end', False)),
        }

        # ── Pre-compute z-layer and width constraint per bar group ──
        # Through bars (MAIN_*) go to layer 1. If the total through count
        # exceeds the layer capacity for this beam, FULL_CHAIN bars have
        # priority for layer 1 and the rest overflow to layer 2.
        # Width constraint: each bar's lateral distribution is limited to
        # the narrowest beam in its chain/sub-run (chain_min_b_mm).
        st_cfg = adapter.get_stirrup_cfg(member_id, 'EXT') or \
                 adapter.get_stirrup_cfg(member_id, 'INT')
        stirrup_dia = st_cfg['dia_mm'] if st_cfg else DEFAULT_STIRRUP_DIA
        # b_lookup for chain_min_b: beam_row_idx → b_mm
        b_lookup = {int(idx): float(adapter.beams_df.loc[idx].get('b_mm', 300) or 300)
                    for idx in adapter.beams_df.index}

        def _layer_and_width(pos_label, cfg_pos, dia_pos):
            """Return {continuity_type: (layer, chain_min_b_mm)} for this
            beam at this position. Handles capacity overflow."""
            run_pos = run_index.get_run(span_row_idx, pos_label) \
                if run_index and span_row_idx is not None else None

            # Default for non-chain beams
            if run_pos is None:
                return {None: (1, float(b_mm))}

            capacity = _bars_per_layer(b_mm, cover, stirrup_dia, dia_pos)
            chain_n = run_pos.min_count
            gap_roles = run_pos.gap_bar_roles().get(span_row_idx, {})
            total_gap = sum(gap_roles.values())
            total_through = chain_n + total_gap

            # Width constraints
            full_min_b = run_pos.chain_min_b(b_lookup)
            gap_min_bs = run_pos.gap_min_b(b_lookup).get(span_row_idx, {})

            result = {}

            if total_through <= capacity:
                # All through bars fit in layer 1
                result['FULL_CHAIN'] = (1, full_min_b)
                for cont, min_b_val in gap_min_bs.items():
                    result[cont] = (1, min_b_val)
            else:
                # Overflow: FULL_CHAIN stays layer 1, gaps go to layer 2
                result['FULL_CHAIN'] = (1, full_min_b)
                remaining = capacity - chain_n
                for cont, min_b_val in gap_min_bs.items():
                    gap_n_this = sum(
                        n for r, n in gap_roles.items()
                        if (r == 'MAIN_SINGLE') == (cont == 'LOCAL')
                    )
                    if remaining >= gap_n_this:
                        result[cont] = (1, min_b_val)
                        remaining -= gap_n_this
                    else:
                        result[cont] = (2, min_b_val)

            return result

        layer_width_top = _layer_and_width('TOP', cfg_top, dia_top)
        layer_width_bot = _layer_and_width('BOT', cfg_bot, dia_bot)

        def _get_lw(lw_map, cont_type):
            return lw_map.get(cont_type, lw_map.get(None, (1, float(b_mm))))

        # Splice layer alternation: even chain_pos → layer 1 (outer),
        # odd chain_pos → layer 2 (inner, touching = contact splice).
        # At the splice zone between span N (layer 1) and span N+1 (layer 2),
        # both bars are visible as stacked circles in cross-section.
        # Standalone beams (chain_pos == -1) stay at layer 1.
        def _splice_layer_for(chain_pos):
            if chain_pos < 0:
                return 1, False  # not in chain → layer 1, no splice
            if chain_pos % 2 == 0:
                return 1, False  # even → outer layer
            return 2, True       # odd → inner layer (splice, touching)

        # MAIN TOP
        top_cont = 'FULL_CHAIN' if top_role in ('MAIN_START', 'MAIN_INTERMEDIATE', 'MAIN_END') \
            and run_index and run_index.get_run(span_row_idx, 'TOP') else None
        top_splice_layer, top_is_splice = _splice_layer_for(top_chain_pos)
        top_layer_resolved, top_min_b = _get_lw(layer_width_top, top_cont)
        # Use splice layer for chain bars, capacity overflow layer otherwise
        top_layer = top_splice_layer if top_cont else top_layer_resolved
        z_s_top = _bar_z(zs, h_mm, cover, 'TOP', top_layer, dia_top, splice_layer=top_is_splice)
        z_e_top = _bar_z(ze, h_mm, cover, 'TOP', top_layer, dia_top, splice_layer=top_is_splice)
        mt = {**base, 'x_start_mm': xs, 'y_start_mm': ys, 'z_start_mm': z_s_top,
              'x_end_mm': xe, 'y_end_mm': ye, 'z_end_mm': z_e_top,
              'bar_position': 'TOP', 'bar_role': top_role, 'bar_type': 'MAIN',
              'dia_mm': dia_top, 'n_bars': main_top, 'length_mm': int(round(Ltop)),
              'layer': top_layer, 'reinforcement_type': 'UNIFORM' if is_uniform else 'VARIABLE',
              'continuity_type': top_cont, 'chain_min_b_mm': int(round(top_min_b))}
        mt = _add_anchorage(mt, Ldh, Lpt_B, Lpb_B)
        for piece in _split_stock(mt, Lpt_B, direction):
            results.append(piece)

        # MAIN BOT
        bot_cont = 'FULL_CHAIN' if bot_role in ('MAIN_START', 'MAIN_INTERMEDIATE', 'MAIN_END') \
            and run_index and run_index.get_run(span_row_idx, 'BOT') else None
        bot_splice_layer, bot_is_splice = _splice_layer_for(bot_chain_pos)
        bot_layer_resolved, bot_min_b = _get_lw(layer_width_bot, bot_cont)
        bot_layer = bot_splice_layer if bot_cont else bot_layer_resolved
        z_s_bot = _bar_z(zs, h_mm, cover, 'BOT', bot_layer, dia_bot, splice_layer=bot_is_splice)
        z_e_bot = _bar_z(ze, h_mm, cover, 'BOT', bot_layer, dia_bot, splice_layer=bot_is_splice)
        mb = {**base, 'x_start_mm': xs, 'y_start_mm': ys, 'z_start_mm': z_s_bot,
              'x_end_mm': xe, 'y_end_mm': ye, 'z_end_mm': z_e_bot,
              'bar_position': 'BOT', 'bar_role': bot_role, 'bar_type': 'MAIN',
              'dia_mm': dia_bot, 'n_bars': main_bot, 'length_mm': int(round(Lbot)),
              'layer': bot_layer, 'reinforcement_type': 'UNIFORM' if is_uniform else 'VARIABLE',
              'continuity_type': bot_cont, 'chain_min_b_mm': int(round(bot_min_b))}
        mb = _add_anchorage(mb, Ldh, Lpt_B, Lpb_B)
        for piece in _split_stock(mb, Lpb_B, direction):
            results.append(piece)

        # ── Gap bars: excess through-bars above chain continuous count ──
        # Skip gap bars for positions where the feasibility gate
        # downgraded the main role — the beam is standalone there.
        if run_index is not None and span_row_idx is not None:
            for pos_label, cfg_pos, dia_pos in [
                ('TOP', cfg_top, dia_top),
                ('BOT', cfg_bot, dia_bot),
            ]:
                if pos_label == 'TOP' and top_downgraded:
                    continue
                if pos_label == 'BOT' and bot_downgraded:
                    continue
                run_pos = run_index.get_run(span_row_idx, pos_label)
                if run_pos is None:
                    continue
                gap_roles = run_pos.gap_bar_roles().get(span_row_idx, {})
                if not gap_roles:
                    continue
                lw_map = layer_width_top if pos_label == 'TOP' else layer_width_bot
                lap_len = Lpt_B if pos_label == 'TOP' else Lpb_B
                for gap_role, gap_n in gap_roles.items():
                    if gap_n <= 0:
                        continue
                    if gap_role == 'MAIN_SINGLE':
                        cont_type = 'LOCAL'
                    else:
                        cont_type = 'PARTIAL_CHAIN'
                    gap_layer, gap_min_b = _get_lw(lw_map, cont_type)
                    # Apply splice layer alternation to PARTIAL_CHAIN gap bars
                    # so their overlap is visible (same even/odd as main bar).
                    gap_chain_pos = top_chain_pos if pos_label == 'TOP' else bot_chain_pos
                    if gap_chain_pos >= 0 and cont_type == 'PARTIAL_CHAIN':
                        gap_layer, gap_is_splice = _splice_layer_for(gap_chain_pos)
                    else:
                        gap_is_splice = False
                    z_s_gap = _bar_z(zs, h_mm, cover, pos_label, gap_layer, dia_pos, splice_layer=gap_is_splice)
                    z_e_gap = _bar_z(ze, h_mm, cover, pos_label, gap_layer, dia_pos, splice_layer=gap_is_splice)
                    L_gap = _main_length(gap_role, lap_len)
                    seg_gap = f"{member_id}-GAP{span_idx:03d}"
                    gap_bar = {
                        **base,
                        'segment_id': seg_gap,
                        'x_start_mm': xs, 'y_start_mm': ys, 'z_start_mm': z_s_gap,
                        'x_end_mm': xe, 'y_end_mm': ye, 'z_end_mm': z_e_gap,
                        'bar_position': pos_label,
                        'bar_role': gap_role,
                        'bar_type': 'MAIN',
                        'dia_mm': dia_pos,
                        'n_bars': gap_n,
                        'length_mm': int(round(L_gap)),
                        'layer': gap_layer,
                        'reinforcement_type': 'UNIFORM' if is_uniform else 'VARIABLE',
                        'continuity_type': cont_type,
                        'chain_min_b_mm': int(round(gap_min_b)),
                    }
                    gap_bar = _add_anchorage(gap_bar, Ldh, Lpt_B, Lpb_B)
                    for piece in _split_stock(gap_bar, lap_len, direction):
                        results.append(piece)

        # ADD bars (VARIABLE only, with continuity)
        is_first = (sub_i == 0)
        is_last = (sub_i == n_sub - 1)
        if not is_uniform and not is_single_role:
            zones_top = cfg_top.get('zones', {})
            zones_bot = cfg_bot.get('zones', {})

            # Baseline for "ADD bar count" = how many bars at each zone minus
            # the ones already accounted for by MAIN (+ MAIN_REMAINDER).
            #
            # Legacy: baseline = gm_top (subgroup min across variable spans).
            #   This conflates two concepts: (a) "continuous-remainder" bars
            #   that existed in this beam but not in the subgroup min, and
            #   (b) true zone-specific ADD bars (extra top reinforcement at
            #   supports to resist negative moment). Both end up in ADD_* rows.
            #
            # Count-matched (flag on) + this span is in a Case 2 run where
            # cfg['main'] > run.min_count: the continuous-remainder bars are
            # emitted separately as MAIN_REMAINDER. Use cfg['main'] as the
            # baseline so ADD bars only represent the true zone extras.
            #
            # Span NOT in a Case 2 run (or no flag): keep legacy baseline.
            add_baseline_top = gm_top
            add_baseline_bot = gm_bot
            if run_index is not None and span_row_idx is not None:
                run_t = run_index.get_run(span_row_idx, 'TOP')
                if run_t is not None and cfg_top['main'] > run_t.min_count:
                    add_baseline_top = int(cfg_top['main'])
                run_b = run_index.get_run(span_row_idx, 'BOT')
                if run_b is not None and cfg_bot['main'] > run_b.min_count:
                    add_baseline_bot = int(cfg_bot['main'])

            # Per-zone ADD bar counts. Use I/M/J keys when available (accurate
            # for asymmetric I≠J like G2A), fall back to legacy EXT/INT/CTR.
            add_top_I = int(max(0, zones_top.get('I', zones_top.get('EXT', add_baseline_top)) - add_baseline_top))
            add_top_J = int(max(0, zones_top.get('J', zones_top.get('EXT', add_baseline_top)) - add_baseline_top))
            add_top_int = int(max(0, zones_top.get('M', zones_top.get('INT', add_baseline_top)) - add_baseline_top))
            add_bot_I = int(max(0, zones_bot.get('I', zones_bot.get('EXT', add_baseline_bot)) - add_baseline_bot))
            add_bot_J = int(max(0, zones_bot.get('J', zones_bot.get('EXT', add_baseline_bot)) - add_baseline_bot))
            add_bot_ctr = int(max(0, zones_bot.get('M', zones_bot.get('CTR', add_baseline_bot)) - add_baseline_bot))

            # Adjacent clear length
            l_cl_next = l_cl
            if sub_i < n_sub - 1:
                next_sp = span_list[sub_i + 1]['sp']
                next_level = next_sp.get('level', beam_level)
                next_Wc1 = adapter.get_column_width(next_sp.get('grid_from', ''), direction, next_level)
                if next_Wc1 == 0:
                    next_Wc1 = adapter.get_wall_thickness(next_sp.get('grid_from', ''), direction, next_level)
                next_Wc2 = adapter.get_column_width(next_sp.get('grid_to', ''), direction, next_level)
                if next_Wc2 == 0:
                    next_Wc2 = adapter.get_wall_thickness(next_sp.get('grid_to', ''), direction, next_level)
                l_cl_next = float(next_sp['length_mm']) - 0.5 * (next_Wc1 + next_Wc2)

            # ADD_START (first span, I-zone has more bars than main)
            if is_first and add_top_I > 0:
                L = Ldh + 0.25 * l_cl + lext
                ac = _add_bar_coords('ADD_START', L, direction, xs, ys, zs, xe, ye, ze)
                zsa = _bar_z(ac['z_start_mm'], h_mm, cover, 'TOP', 3, dia_top)
                zea = _bar_z(ac['z_end_mm'], h_mm, cover, 'TOP', 3, dia_top)
                d = {**base, **ac, 'z_start_mm': zsa, 'z_end_mm': zea,
                     'bar_position': 'TOP', 'bar_role': 'ADD_START', 'bar_type': 'ADD',
                     'dia_mm': dia_top, 'n_bars': add_top_I,
                     'length_mm': int(round(L)), 'layer': 3,
                     'reinforcement_type': 'VARIABLE'}
                results.append(_add_anchorage(d, Ldh, Lpt_B, Lpb_B))

            # ADD_INTERMEDIATE (support bridging between spans)
            if not is_last and add_top_int > 0:
                L = 0.25 * (l_cl + l_cl_next) + Wc2 + 2 * lext
                ac = _add_bar_coords('ADD_INTERMEDIATE', L, direction, xs, ys, zs, xe, ye, ze)
                zsa = _bar_z(ac['z_start_mm'], h_mm, cover, 'TOP', 2, dia_top)
                zea = _bar_z(ac['z_end_mm'], h_mm, cover, 'TOP', 3, dia_top)
                d = {**base, **ac, 'z_start_mm': zsa, 'z_end_mm': zea,
                     'bar_position': 'TOP', 'bar_role': 'ADD_INTERMEDIATE', 'bar_type': 'ADD',
                     'dia_mm': dia_top, 'n_bars': add_top_int,
                     'length_mm': int(round(L)), 'layer': 3,
                     'reinforcement_type': 'VARIABLE'}
                results.append(_add_anchorage(d, Ldh, Lpt_B, Lpb_B))

            # ADD_END (last span, J-zone has more bars than main)
            if is_last and add_top_J > 0:
                L = Ldh + 0.25 * l_cl + lext
                ac = _add_bar_coords('ADD_END', L, direction, xs, ys, zs, xe, ye, ze)
                zsa = _bar_z(ac['z_start_mm'], h_mm, cover, 'TOP', 3, dia_top)
                zea = _bar_z(ac['z_end_mm'], h_mm, cover, 'TOP', 3, dia_top)
                d = {**base, **ac, 'z_start_mm': zsa, 'z_end_mm': zea,
                     'bar_position': 'TOP', 'bar_role': 'ADD_END', 'bar_type': 'ADD',
                     'dia_mm': dia_top, 'n_bars': add_top_J,
                     'length_mm': int(round(L)), 'layer': 3,
                     'reinforcement_type': 'VARIABLE'}
                results.append(_add_anchorage(d, Ldh, Lpt_B, Lpb_B))

            # ADD_START for BOT (bottom bars at I-support, asymmetric beams)
            if is_first and add_bot_I > 0:
                L = Ldh + 0.25 * l_cl + lext
                ac = _add_bar_coords('ADD_START', L, direction, xs, ys, zs, xe, ye, ze)
                zsa = _bar_z(ac['z_start_mm'], h_mm, cover, 'BOT', 3, dia_bot)
                zea = _bar_z(ac['z_end_mm'], h_mm, cover, 'BOT', 3, dia_bot)
                d = {**base, **ac, 'z_start_mm': zsa, 'z_end_mm': zea,
                     'bar_position': 'BOT', 'bar_role': 'ADD_START', 'bar_type': 'ADD',
                     'dia_mm': dia_bot, 'n_bars': add_bot_I,
                     'length_mm': int(round(L)), 'layer': 3,
                     'reinforcement_type': 'VARIABLE'}
                results.append(_add_anchorage(d, Ldh, Lpt_B, Lpb_B))

            # ADD_MIDSPAN (bottom bars in M/CTR zone)
            if add_bot_ctr > 0:
                L = 0.5 * l_cl + 2 * lext
                ac = _add_bar_coords('ADD_MIDSPAN', L, direction, xs, ys, zs, xe, ye, ze)
                zsa = _bar_z(ac['z_start_mm'], h_mm, cover, 'BOT', 3, dia_bot)
                zea = _bar_z(ac['z_end_mm'], h_mm, cover, 'BOT', 3, dia_bot)
                d = {**base, **ac, 'z_start_mm': zsa, 'z_end_mm': zea,
                     'bar_position': 'BOT', 'bar_role': 'ADD_MIDSPAN', 'bar_type': 'ADD',
                     'dia_mm': dia_bot, 'n_bars': add_bot_ctr,
                     'length_mm': int(round(L)), 'layer': 3,
                     'reinforcement_type': 'VARIABLE'}
                results.append(_add_anchorage(d, Ldh, Lpt_B, Lpb_B))

            # ADD_END for BOT (bottom bars at J-support, asymmetric beams)
            if is_last and add_bot_J > 0:
                L = Ldh + 0.25 * l_cl + lext
                ac = _add_bar_coords('ADD_END', L, direction, xs, ys, zs, xe, ye, ze)
                zsa = _bar_z(ac['z_start_mm'], h_mm, cover, 'BOT', 3, dia_bot)
                zea = _bar_z(ac['z_end_mm'], h_mm, cover, 'BOT', 3, dia_bot)
                d = {**base, **ac, 'z_start_mm': zsa, 'z_end_mm': zea,
                     'bar_position': 'BOT', 'bar_role': 'ADD_END', 'bar_type': 'ADD',
                     'dia_mm': dia_bot, 'n_bars': add_bot_J,
                     'length_mm': int(round(L)), 'layer': 3,
                     'reinforcement_type': 'VARIABLE'}
                results.append(_add_anchorage(d, Ldh, Lpt_B, Lpb_B))

    return results


# ── Main bar calculator ──────────────────────────────────────────────────────

def _calculate_main_bars(adapter, lookup):
    """Calculate all main + ADD bars grouped by gridline."""
    results = []
    beams_df = adapter.beams_df
    df_lines = beams_df[beams_df['direction'].isin(['X', 'Y'])].copy()

    # Build run index once for Phase 2 count-matched anchorage.
    run_index: Optional[RunIndex] = None
    if USE_COUNT_MATCHED_ANCHORAGE:
        run_index = RunIndex(adapter)
        print(run_index.summary())

    for key, grp in df_lines.groupby('line_key'):
        if key is None:
            continue
        level, direction, grid_coord = key

        sort_col = 'x_from_mm' if direction == 'X' else 'y_from_mm'
        grp = grp.sort_values(sort_col)
        # Preserve original DataFrame row index for run-graph lookups.
        spans = []
        for idx, row in grp.iterrows():
            rec = row.to_dict()
            rec['_row_idx'] = int(idx)
            spans.append(rec)

        # Build span info with rebar config
        span_info = []
        for i, sp in enumerate(spans):
            mid = sp['member_id']
            sp_level = sp.get('level', '')
            cfg_top = adapter.get_long_cfg(mid, 'TOP', level=sp_level)
            cfg_bot = adapter.get_long_cfg(mid, 'BOT', level=sp_level)
            if cfg_top is None or cfg_bot is None:
                span_info.append(None)
                continue
            span_info.append({
                'sp': sp, 'member_id': mid,
                'cfg_top': cfg_top, 'cfg_bot': cfg_bot,
                'is_uniform': cfg_top['is_uniform'] and cfg_bot['is_uniform'],
                'index': i, 'dia_top': cfg_top['dia'], 'dia_bot': cfg_bot['dia'],
                'line_grid': grid_coord,
            })

        valid = [s for s in span_info if s is not None]
        if not valid:
            continue

        # Split into contiguous same-diameter sub-groups
        # Contiguity: beam endpoints must be close (within tolerance)
        CONTIGUITY_TOL = 100.0  # mm — endpoint proximity tolerance

        def _are_contiguous(prev_span, next_span):
            """Check if two beams connect end-to-end."""
            sp1 = prev_span['sp']
            sp2 = next_span['sp']
            # prev end should be close to next start
            if direction == 'X':
                return abs((sp1.get('x_to_mm', 0) or 0) - (sp2.get('x_from_mm', 0) or 0)) < CONTIGUITY_TOL
            else:
                return abs((sp1.get('y_to_mm', 0) or 0) - (sp2.get('y_from_mm', 0) or 0)) < CONTIGUITY_TOL

        subgroups = []
        current = []
        for s in span_info:
            if s is None:
                if current:
                    subgroups.append(current)
                    current = []
                continue
            if current:
                # Break sub-group if diameter changes, member_id changes, or
                # beams don't connect.
                #
                # The member_id check is essential: the line_key groups all
                # beams on the same gridline at the same level regardless of
                # member_id. Without a member_id break, four different members
                # that happen to share a gridline (e.g. P2 2F x=-35800:
                # G2A → G8 → G2 → G8A, all contiguous end-to-end via shared
                # column nodes) get lumped into one "4-span run" and the
                # MAIN_START/INTERMEDIATE/END role-assignment flows across
                # member_id boundaries. The middle spans end up with LAP
                # anchorage that's supposed to overlap the PREVIOUS span —
                # but the previous span is a different member, so the bar
                # visually starts ~1m INTO its own span and appears to stop
                # short of the support. Breaking at member_id changes gives
                # each member its own subgroup; same-member contiguous runs
                # still merge correctly.
                if (current[-1]['member_id'] != s['member_id'] or
                        current[-1]['dia_top'] != s['dia_top'] or
                        not _are_contiguous(current[-1], s)):
                    subgroups.append(current)
                    current = []
            current.append(s)
        if current:
            subgroups.append(current)

        # Process each sub-group
        for subgroup in subgroups:
            var_spans = [s for s in subgroup if not s['is_uniform']]
            if var_spans:
                gm_top = min(s['cfg_top']['main'] for s in var_spans)
                gm_bot = min(s['cfg_bot']['main'] for s in var_spans)
            else:
                gm_top = gm_bot = 0

            sub_results = _process_subgroup(
                subgroup, gm_top, gm_bot, adapter, lookup, direction,
                run_index=run_index,
            )
            results.extend(sub_results)

    # Gap bars (cfg['main'] - run.min_count) are now emitted inline by
    # _process_subgroup as MAIN_SINGLE (local through-bars, HOOK/HOOK).
    # MAIN_REMAINDER is no longer emitted for self-sufficient spans.
    # The old _emit_remainder_bars is kept in the codebase but disabled —
    # can be re-enabled for deficit-based remainder if a project's zone
    # counts are designed as deficits rather than totals.
    # if run_index is not None:
    #     remainder_results = _emit_remainder_bars(run_index, adapter, lookup)
    #     results.extend(remainder_results)

    return results


# ── Stirrup calculator ───────────────────────────────────────────────────────

def _calculate_stirrups(adapter):
    """Calculate stirrup bars for all beams."""
    results = []

    for _, row in adapter.beams_df.iterrows():
        mid = row['member_id']
        zones = adapter.get_stirrup_zones(mid)
        if not zones:
            continue

        b_mm = row.get('b_mm', 300) or 300
        h_mm = row.get('h_mm', 500) or 500
        sec = adapter.get_section(row.get('section_id', ''))
        cover = sec['cover_mm'] if sec else COVER_MM
        shape = sec['shape'] if sec else 'RECT'

        b_cl = b_mm - 2 * cover
        h_cl = h_mm - 2 * cover
        l_span = float(row['length_mm'])
        direction = row['direction']

        grid_from = row.get('grid_from', '')
        grid_to = row.get('grid_to', '')
        st_level = row.get('level', '')
        # Prefer Phase 2.7 coordinate-based col_width (already in
        # MembersBeam). The grid-based lookup double-counts when
        # grid_from == grid_to (e.g. G5A-E33907 where both ends
        # map to X11Y11 but only one has a real support).
        # Only fall back to grid lookup when the Phase 2.7 field is
        # genuinely missing (NaN), NOT when it's 0 (which means
        # "no support at this end" — a valid value).
        cw_start_raw = row.get('col_width_start_mm')
        cw_end_raw = row.get('col_width_end_mm')
        if pd.notna(cw_start_raw):
            Wc1 = float(cw_start_raw)
        else:
            Wc1 = adapter.get_column_width(grid_from, direction, st_level)
            if Wc1 == 0:
                Wc1 = adapter.get_wall_thickness(grid_from, direction, st_level)
        if pd.notna(cw_end_raw):
            Wc2 = float(cw_end_raw)
        else:
            Wc2 = adapter.get_column_width(grid_to, direction, st_level)
            if Wc2 == 0:
                Wc2 = adapter.get_wall_thickness(grid_to, direction, st_level)
        l_cl = l_span - 0.5 * (Wc1 + Wc2)

        xs = row.get('x_from_mm', 0) or 0
        ys = row.get('y_from_mm', 0) or 0
        zs = row.get('z_mm', 0) or 0
        xe = row.get('x_to_mm', 0) or 0
        ye = row.get('y_to_mm', 0) or 0

        span_idx = 1
        segment_id = f"{mid}-SEG{span_idx:03d}"
        line_grid = ys if direction == 'X' else xs

        # Build zone configs (fill missing with available)
        has_ext = 'EXT' in zones
        has_int = 'INT' in zones
        zone_cfgs = {}
        if has_ext:
            zone_cfgs['EXT'] = adapter.get_stirrup_cfg(mid, 'EXT')
        elif has_int:
            zone_cfgs['EXT'] = adapter.get_stirrup_cfg(mid, 'INT')
        if has_int:
            zone_cfgs['INT'] = adapter.get_stirrup_cfg(mid, 'INT')
        if not zone_cfgs.get('CTR'):
            zone_cfgs['CTR'] = zone_cfgs.get('INT', zone_cfgs.get('EXT'))

        zone_lengths = {'EXT': 0.25 * l_cl, 'INT': 0.25 * l_cl, 'CTR': 0.5 * l_cl}

        # Zone coordinate partitioning along the beam axis (issue #81).
        # Uses parametric interpolation so diagonal beams (e.g. TG8 at
        # 43.9°) get correct (x,y) at each zone boundary, not just
        # primary-axis partitioning with fixed perpendicular coord.
        #
        # Zone boundaries as fraction of beam span (l_span):
        #   face_start = 0.5*Wc1 / l_span
        #   face_end   = 1 - 0.5*Wc2 / l_span
        #   EXT: [face_start, face_start + 0.25*l_cl/l_span]
        #   CTR: [+0.25*l_cl, +0.75*l_cl]
        #   INT: [+0.75*l_cl, face_end]
        def _interp(t):
            return (xs + t * (xe - xs), ys + t * (ye - ys))

        if l_span > 0:
            t_face_s = 0.5 * Wc1 / l_span
            t_quarter = 0.25 * l_cl / l_span
            t_face_e = 1.0 - 0.5 * Wc2 / l_span
        else:
            t_face_s, t_quarter, t_face_e = 0, 0.25, 1.0

        zone_t = {
            'EXT': (t_face_s, t_face_s + t_quarter),
            'CTR': (t_face_s + t_quarter, t_face_s + 3 * t_quarter),
            'INT': (t_face_s + 3 * t_quarter, t_face_e),
        }
        zone_coords = {}
        for zn, (t1, t2) in zone_t.items():
            x1, y1 = _interp(t1)
            x2, y2 = _interp(t2)
            zone_coords[zn] = (x1, x2, y1, y2)

        base = {
            'segment_id': segment_id, 'level': row.get('level', ''),
            'direction': direction, 'line_grid': line_grid,
            'member_id': mid, 'span_index': span_idx,
            'start_grid': grid_from, 'end_grid': grid_to,
            'b_mm': int(b_mm), 'h_mm': int(h_mm), 'shape': shape,
            'col_width_start_mm': int(round(Wc1)) if Wc1 else 0,
            'col_width_end_mm': int(round(Wc2)) if Wc2 else 0,
            'support_extends_below_start': bool(row.get('support_extends_below_start', False)),
            'support_extends_below_end': bool(row.get('support_extends_below_end', False)),
        }

        for zn, zl in zone_lengths.items():
            cfg = zone_cfgs.get(zn)
            if not cfg or not cfg['spacing_mm']:
                continue
            dia = cfg['dia_mm']
            nl = cfg['n_legs']
            sp_mm = cfg['spacing_mm']
            L_st = 2 * (b_cl + h_cl) + 2 * nl * HOOK_EXTENSION_FACTOR * dia
            n_st = int(zl / sp_mm) + 1

            zc = zone_coords.get(zn, (xs, xe, ys, ye))
            results.append({
                **base,
                'x_start_mm': zc[0], 'y_start_mm': zc[2],
                'z_start_mm': zs,
                'x_end_mm': zc[1], 'y_end_mm': zc[3],
                'z_end_mm': zs,
                'bar_position': 'STIRRUP', 'bar_role': zn, 'bar_type': 'STIRRUP',
                'dia_mm': int(dia), 'n_bars': nl,
                'length_mm': int(round(L_st)), 'layer': None,
                'spacing_mm': int(sp_mm), 'zone_length_mm': int(round(zl)),
                'quantity_pieces': n_st, 'total_length_mm': int(round(L_st * n_st)),
                'anchorage_start': None, 'anchorage_end': None,
                'lap_length_mm': None, 'development_length_mm': None,
                'transition_type': None, 'reinforcement_type': None,
                'split_piece': None, 'original_length_mm': None,
            })

    return results


# ── Splice coordinates ───────────────────────────────────────────────────────

def _compute_splice_coords(results, adapter):
    """Add splice zone coordinates for MAIN bars with LAP anchorage."""
    for bar in results:
        bar.setdefault('splice_start_mm', None)
        bar.setdefault('splice_start_end_mm', None)
        bar.setdefault('splice_end_mm', None)
        bar.setdefault('splice_end_end_mm', None)

        if (bar.get('bar_type') or '').upper() in ('ADD', 'STIRRUP'):
            continue

        lap = bar.get('lap_length_mm') or 0
        if lap <= 0:
            continue

        direction = bar.get('direction', 'X')

        bar_level = bar.get('level', '')

        if bar.get('anchorage_start') == 'LAP' and bar.get('start_grid'):
            col_w = adapter.get_column_width(bar['start_grid'], direction, bar_level)
            if col_w == 0:
                col_w = adapter.get_wall_thickness(bar['start_grid'], direction, bar_level)
            face = (bar.get('x_start_mm', 0) or 0) + col_w / 2 if direction == 'X' \
                else (bar.get('y_start_mm', 0) or 0) + col_w / 2
            bar['splice_start_mm'] = round(face, 1)
            bar['splice_start_end_mm'] = round(face + lap, 1)

        if bar.get('anchorage_end') == 'LAP' and bar.get('end_grid'):
            col_w = adapter.get_column_width(bar['end_grid'], direction, bar_level)
            if col_w == 0:
                col_w = adapter.get_wall_thickness(bar['end_grid'], direction, bar_level)
            face = (bar.get('x_end_mm', 0) or 0) + col_w / 2 if direction == 'X' \
                else (bar.get('y_end_mm', 0) or 0) + col_w / 2
            bar['splice_end_mm'] = round(face, 1)
            bar['splice_end_end_mm'] = round(face + lap, 1)


# ── Diagonal beam bend points ───────────────────────────────────────────────

_DIAG_XY_TOL = 300  # mm — endpoint proximity tolerance


def _is_diagonal_beam(row):
    """True if a beam has both significant dx AND dy (not axis-aligned)."""
    dx = abs(float(row.get('x_to_mm', 0) or 0) - float(row.get('x_from_mm', 0) or 0))
    dy = abs(float(row.get('y_to_mm', 0) or 0) - float(row.get('y_from_mm', 0) or 0))
    return dx > 100 and dy > 100


def _apply_diagonal_bends(results, beams_df, lookup=None, dia_fy_map=None):
    """Post-process: for main bars on diagonal beams, detect non-coaxial
    neighbor beams at each end and add LAP anchorage with bend points.

    Bend rule (per supervisor): at the junction face, the bar transitions
    from its own beam's axis to the neighbor's axis. Half the lap is straight
    (along source), half follows the neighbor direction.

    Uses L_lap (Lpt for TOP, Lpb for BOT) — not Ldh — because the
    anchorage is changed from HOOK to LAP.
    """
    from collections import defaultdict

    # 1. Build list of all beam segment rows with their endpoints
    all_segments = []  # list of dicts with level, member_id, xf, yf, xt, yt, dx, dy
    for _, row in beams_df.iterrows():
        rd = row.to_dict()
        xf = float(rd.get('x_from_mm', 0) or 0)
        yf = float(rd.get('y_from_mm', 0) or 0)
        xt = float(rd.get('x_to_mm', 0) or 0)
        yt = float(rd.get('y_to_mm', 0) or 0)
        all_segments.append({
            'level': rd.get('level', ''),
            'member_id': rd.get('member_id', ''),
            'xf': xf, 'yf': yf, 'xt': xt, 'yt': yt,
            'dx': xt - xf, 'dy': yt - yf,
            'is_diag': abs(xt - xf) > 100 and abs(yt - yf) > 100,
        })

    # 2. Find diagonal segments only
    diag_segs = [s for s in all_segments if s['is_diag']]
    if not diag_segs:
        return

    def _find_neighbor_at(level, px, py, exclude_mid):
        """Find a beam segment endpoint near (px, py) from a different member."""
        best = None
        best_dist = _DIAG_XY_TOL
        for seg in all_segments:
            if seg['level'] != level or seg['member_id'] == exclude_mid:
                continue
            for ex, ey in [(seg['xf'], seg['yf']), (seg['xt'], seg['yt'])]:
                dist = math.sqrt((ex - px)**2 + (ey - py)**2)
                if dist < best_dist:
                    best = seg
                    best_dist = dist
        return best

    # 3. Build a map: for each diagonal segment, find neighbors at start & end
    #    Key: (level, member_id, round_xf, round_yf, round_xt, round_yt)
    diag_info = {}  # (level, member_id, xf, yf, xt, yt) → {start_nb, end_nb}
    for ds in diag_segs:
        nb_start = _find_neighbor_at(ds['level'], ds['xf'], ds['yf'], ds['member_id'])
        nb_end = _find_neighbor_at(ds['level'], ds['xt'], ds['yt'], ds['member_id'])
        diag_info[(ds['level'], ds['member_id'],
                   round(ds['xf']), round(ds['yf']),
                   round(ds['xt']), round(ds['yt']))] = {
            'xf': ds['xf'], 'yf': ds['yf'], 'xt': ds['xt'], 'yt': ds['yt'],
            'dx': ds['dx'], 'dy': ds['dy'],
            'nb_start': nb_start, 'nb_end': nb_end,
        }

    def _neighbor_direction(nb, junction_x, junction_y, diag_mid_x, diag_mid_y):
        """Get unit vector along neighbor beam, pointing AWAY from diagonal body."""
        ndx, ndy = nb['dx'], nb['dy']
        nlen = math.sqrt(ndx**2 + ndy**2)
        if nlen < 1:
            return None
        nux, nuy = ndx / nlen, ndy / nlen
        # Pick direction that goes away from diagonal beam center
        d_pos = (junction_x + nux * 100 - diag_mid_x)**2 + (junction_y + nuy * 100 - diag_mid_y)**2
        d_neg = (junction_x - nux * 100 - diag_mid_x)**2 + (junction_y - nuy * 100 - diag_mid_y)**2
        if d_neg > d_pos:
            return nux, nuy
        return -nux, -nuy

    # 4. Match rebar results to diagonal segments by coordinate proximity
    count = 0
    for bar in results:
        if bar.get('bar_type') != 'MAIN':
            continue

        bxs = float(bar.get('x_start_mm', 0) or 0)
        bys = float(bar.get('y_start_mm', 0) or 0)
        bzs = float(bar.get('z_start_mm', 0) or 0)
        bxe = float(bar.get('x_end_mm', 0) or 0)
        bye = float(bar.get('y_end_mm', 0) or 0)
        bze = float(bar.get('z_end_mm', 0) or 0)
        level = bar.get('level', '')

        # Find matching diagonal segment: bar start/end must be near segment from/to
        matched = None
        for dkey, dinfo in diag_info.items():
            dlevel, dmid = dkey[0], dkey[1]
            if dlevel != level or dmid != bar.get('member_id', ''):
                continue
            ds_bar = math.sqrt((bxs - dinfo['xf'])**2 + (bys - dinfo['yf'])**2)
            de_bar = math.sqrt((bxe - dinfo['xt'])**2 + (bye - dinfo['yt'])**2)
            # Bar coords may be extended by hook/lap, so use generous tolerance
            if ds_bar < 2000 and de_bar < 2000:
                matched = dinfo
                break
            # Also try reversed (bar coords might be flipped)
            ds_bar2 = math.sqrt((bxs - dinfo['xt'])**2 + (bys - dinfo['yt'])**2)
            de_bar2 = math.sqrt((bxe - dinfo['xf'])**2 + (bye - dinfo['yf'])**2)
            if ds_bar2 < 2000 and de_bar2 < 2000:
                matched = {**dinfo,
                           'xf': dinfo['xt'], 'yf': dinfo['yt'],
                           'xt': dinfo['xf'], 'yt': dinfo['yf'],
                           'nb_start': dinfo['nb_end'], 'nb_end': dinfo['nb_start']}
                break

        if not matched:
            continue

        # Use L_lap (Lpt for TOP, Lpb for BOT) — the proper lap splice length.
        # If bar already has lap_length_mm (e.g. from chain), use it.
        # Otherwise compute from lookup.
        lap = bar.get('lap_length_mm')
        if not lap and lookup:
            dia = bar.get('dia_mm', 25)
            fy = (dia_fy_map or {}).get(int(dia), 400)
            fc = _parse_fc(bar.get('material_id', 'C35'))
            _, _, _, Lpt, Lpb = lookup.get(fy, dia, fc)
            lap = Lpt if bar.get('bar_position') == 'TOP' else Lpb
        lap = lap or 0
        half_lap = lap / 2.0
        mid_x = (matched['xf'] + matched['xt']) / 2
        mid_y = (matched['yf'] + matched['yt']) / 2
        applied = False

        # Start end
        nb = matched['nb_start']
        if nb:
            ndir = _neighbor_direction(nb, matched['xf'], matched['yf'], mid_x, mid_y)
            if ndir:
                nux, nuy = ndir
                bar['anchorage_start'] = 'LAP'
                if not bar.get('lap_length_mm'):
                    bar['lap_length_mm'] = lap
                bar['bend1_x_mm'] = round(matched['xf'], 1)
                bar['bend1_y_mm'] = round(matched['yf'], 1)
                bar['bend1_z_mm'] = round(bzs, 1)
                bar['bend1_end_x_mm'] = round(matched['xf'] + nux * half_lap, 1)
                bar['bend1_end_y_mm'] = round(matched['yf'] + nuy * half_lap, 1)
                bar['bend1_end_z_mm'] = round(bzs, 1)
                bar['x_start_mm'] = round(matched['xf'] + nux * half_lap, 1)
                bar['y_start_mm'] = round(matched['yf'] + nuy * half_lap, 1)
                applied = True

        # End end
        nb = matched['nb_end']
        if nb:
            ndir = _neighbor_direction(nb, matched['xt'], matched['yt'], mid_x, mid_y)
            if ndir:
                nux, nuy = ndir
                bar['anchorage_end'] = 'LAP'
                if not bar.get('lap_length_mm'):
                    bar['lap_length_mm'] = lap
                bar['bend2_x_mm'] = round(matched['xt'], 1)
                bar['bend2_y_mm'] = round(matched['yt'], 1)
                bar['bend2_z_mm'] = round(bze, 1)
                bar['bend2_end_x_mm'] = round(matched['xt'] + nux * half_lap, 1)
                bar['bend2_end_y_mm'] = round(matched['yt'] + nuy * half_lap, 1)
                bar['bend2_end_z_mm'] = round(bze, 1)
                bar['x_end_mm'] = round(matched['xt'] + nux * half_lap, 1)
                bar['y_end_mm'] = round(matched['yt'] + nuy * half_lap, 1)
                applied = True

        if applied:
            count += 1

    if count:
        print(f'[RebarBeam] Diagonal bend points applied to {count} bars')

    # 5. Second pass: straight beams connected to diagonal beams at junctions.
    #    These bars need LAP anchorage + bend into the diagonal axis.
    count2 = 0
    for ds in diag_segs:
        level = ds['level']
        diag_mid = ds['member_id']
        diag_mx = (ds['xf'] + ds['xt']) / 2
        diag_my = (ds['yf'] + ds['yt']) / 2

        for jx, jy in [(ds['xf'], ds['yf']), (ds['xt'], ds['yt'])]:
            # Direction from junction INTO the diagonal beam
            dx_into = diag_mx - jx
            dy_into = diag_my - jy
            d_into = math.sqrt(dx_into**2 + dy_into**2)
            if d_into < 1:
                continue
            ux_into, uy_into = dx_into / d_into, dy_into / d_into

            for bar in results:
                if bar.get('bar_type') != 'MAIN':
                    continue
                if bar.get('level', '') != level:
                    continue
                if bar.get('member_id', '') == diag_mid:
                    continue

                bxs = float(bar.get('x_start_mm', 0) or 0)
                bys = float(bar.get('y_start_mm', 0) or 0)
                bxe = float(bar.get('x_end_mm', 0) or 0)
                bye = float(bar.get('y_end_mm', 0) or 0)
                bzs = float(bar.get('z_start_mm', 0) or 0)
                bze = float(bar.get('z_end_mm', 0) or 0)

                lap = bar.get('lap_length_mm')
                if not lap and lookup:
                    dia = bar.get('dia_mm', 25)
                    fy = (dia_fy_map or {}).get(int(dia), 400)
                    fc = _parse_fc(bar.get('material_id', 'C35'))
                    _, _, _, Lpt, Lpb = lookup.get(fy, dia, fc)
                    lap = Lpt if bar.get('bar_position') == 'TOP' else Lpb
                lap = lap or 0
                half_lap = lap / 2.0

                # Check if bar START is near junction
                if (math.sqrt((bxs - jx)**2 + (bys - jy)**2) < _DIAG_XY_TOL
                        and bar.get('anchorage_start') != 'LAP'):
                    bar['anchorage_start'] = 'LAP'
                    if not bar.get('lap_length_mm'):
                        bar['lap_length_mm'] = lap
                    bar['bend1_x_mm'] = round(jx, 1)
                    bar['bend1_y_mm'] = round(jy, 1)
                    bar['bend1_z_mm'] = round(bzs, 1)
                    bar['bend1_end_x_mm'] = round(jx + ux_into * half_lap, 1)
                    bar['bend1_end_y_mm'] = round(jy + uy_into * half_lap, 1)
                    bar['bend1_end_z_mm'] = round(bzs, 1)
                    bar['x_start_mm'] = round(jx + ux_into * half_lap, 1)
                    bar['y_start_mm'] = round(jy + uy_into * half_lap, 1)
                    count2 += 1

                # Check if bar END is near junction
                if (math.sqrt((bxe - jx)**2 + (bye - jy)**2) < _DIAG_XY_TOL
                        and bar.get('anchorage_end') != 'LAP'):
                    bar['anchorage_end'] = 'LAP'
                    if not bar.get('lap_length_mm'):
                        bar['lap_length_mm'] = lap
                    bar['bend2_x_mm'] = round(jx, 1)
                    bar['bend2_y_mm'] = round(jy, 1)
                    bar['bend2_z_mm'] = round(bze, 1)
                    bar['bend2_end_x_mm'] = round(jx + ux_into * half_lap, 1)
                    bar['bend2_end_y_mm'] = round(jy + uy_into * half_lap, 1)
                    bar['bend2_end_z_mm'] = round(bze, 1)
                    bar['x_end_mm'] = round(jx + ux_into * half_lap, 1)
                    bar['y_end_mm'] = round(jy + uy_into * half_lap, 1)
                    count2 += 1

    if count2:
        print(f'[RebarBeam] Diagonal junction LAP applied to {count2} straight beam bars')


# ── Public API ───────────────────────────────────────────────────────────────

def calculate_beam_rebar_lengths(
    beams_df: pd.DataFrame,
    columns_df: pd.DataFrame,
    sections_df: pd.DataFrame,
    reinf_df: pd.DataFrame,
    nodes_df: pd.DataFrame,
    dev_lengths_path: str,
    lap_splice_path: str,
    dia_fy_map: dict = None,
    walls_df: pd.DataFrame = None,
    bwalls_df: pd.DataFrame = None,
) -> pd.DataFrame:
    """
    Calculate beam rebar lengths from Tier 1 converter output.

    Args:
        beams_df: MembersBeam.csv
        columns_df: MembersColumn.csv
        sections_df: Sections.csv
        reinf_df: ReinforcementBeam.csv
        nodes_df: Nodes.csv
        dev_lengths_path: path to development_lengths.csv
        lap_splice_path: path to lap_splice.csv
        walls_df: MembersWall.csv (optional, for wall_below predicate)
        bwalls_df: MembersBasementWall.csv (optional, for wall_below predicate)

    Returns:
        DataFrame for RebarLengthsBeam.csv
    """
    print('[RebarBeam] Loading lookup tables...')
    lookup = DevLapLookup(dev_lengths_path, lap_splice_path)

    print('[RebarBeam] Building data adapter...')
    adapter = BeamDataAdapter(
        beams_df, columns_df, sections_df, reinf_df, nodes_df,
        walls_df=walls_df, bwalls_df=bwalls_df,
    )
    print(f'[RebarBeam] {len(adapter.long_cfg)} longitudinal configs, '
          f'{len(adapter.stirrup_cfg)} stirrup configs')

    print('[RebarBeam] Calculating main bars...')
    main_bars = _calculate_main_bars(adapter, lookup)

    print('[RebarBeam] Calculating stirrups...')
    stirrups = _calculate_stirrups(adapter)

    all_results = main_bars + stirrups

    print('[RebarBeam] Computing splice coordinates...')
    _compute_splice_coords(all_results, adapter)

    print('[RebarBeam] Extending LAP bar coordinates...')
    for bar in all_results:
        if bar.get('bar_type') == 'MAIN':
            _extend_lap_coords(bar, bar.get('direction', 'X'))

    print('[RebarBeam] Applying diagonal beam bend points...')
    _apply_diagonal_bends(all_results, beams_df, lookup=lookup, dia_fy_map=dia_fy_map)

    # Output column order
    column_order = [
        'segment_id', 'level', 'direction', 'line_grid',
        'member_id', 'span_index', 'start_grid', 'end_grid',
        'bar_position', 'bar_role', 'bar_type',
        'dia_mm', 'n_bars', 'length_mm', 'layer',
        'spacing_mm', 'zone_length_mm', 'quantity_pieces', 'total_length_mm',
        'anchorage_start', 'anchorage_end',
        'lap_length_mm', 'development_length_mm',
        'splice_start_mm', 'splice_start_end_mm',
        'splice_end_mm', 'splice_end_end_mm',
        'transition_type', 'reinforcement_type',
        'split_piece', 'original_length_mm',
        'x_start_mm', 'y_start_mm', 'z_start_mm',
        'x_end_mm', 'y_end_mm', 'z_end_mm',
        # Bend points for diagonal beam junctions (bar changes direction)
        'bend1_x_mm', 'bend1_y_mm', 'bend1_z_mm',
        'bend1_end_x_mm', 'bend1_end_y_mm', 'bend1_end_z_mm',
        'bend2_x_mm', 'bend2_y_mm', 'bend2_z_mm',
        'bend2_end_x_mm', 'bend2_end_y_mm', 'bend2_end_z_mm',
        'b_mm', 'h_mm', 'shape',
        'col_width_start_mm', 'col_width_end_mm',
        'support_extends_below_start', 'support_extends_below_end',
        # Issue #78: node reference at each bar terminal for auditability
        # of the has_concrete_below predicate without re-running the
        # converter. Renderer ignores these columns.
        'terminal_node_start', 'terminal_node_end',
        # Continuity type: FULL_CHAIN (SLP-eligible), PARTIAL_CHAIN (ILP),
        # LOCAL (ILP), or empty (ADD / stirrup / standalone).
        'continuity_type',
        # Width constraint: narrowest beam in this bar's chain/sub-run.
        # Renderer uses this for lateral bar distribution instead of the
        # host beam's own b_mm.
        'chain_min_b_mm',
    ]

    df = pd.DataFrame(all_results)
    avail = [c for c in column_order if c in df.columns]
    df = df[avail]

    print(f'[RebarBeam] {len(main_bars)} main bar records + '
          f'{len(stirrups)} stirrup records = {len(df)} total')

    return df
