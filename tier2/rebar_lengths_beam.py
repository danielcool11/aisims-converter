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


def _bar_z(z_ref, h_mm, cover_mm, position, layer, dia_mm):
    """Calculate bar Z coordinate within beam cross-section."""
    layer_spacing = max(25.0, float(dia_mm))
    if position == 'TOP':
        z_top = z_ref + h_mm / 2
        if layer == 1:
            return z_top - cover_mm - dia_mm / 2
        elif layer == 2:
            z1 = z_top - cover_mm - dia_mm / 2
            return z1 - dia_mm - layer_spacing
        elif layer == 3:
            z1 = z_top - cover_mm - dia_mm / 2
            z2 = z1 - dia_mm - layer_spacing
            return z2 - dia_mm - layer_spacing
    elif position == 'BOT':
        z_bot = z_ref - h_mm / 2
        if layer == 1:
            return z_bot + cover_mm + dia_mm / 2
        elif layer == 2:
            z1 = z_bot + cover_mm + dia_mm / 2
            return z1 + dia_mm + layer_spacing
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
        Returns (Ldh, Lpt, Lpb):
            Ldh: development length for hook
            Lpt: top bar lap splice (Class B)
            Lpb: bottom bar lap splice (Class B)
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
                return 300, 600, 500  # safe defaults
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
                return Ldh, 600, 500
            row_lap = row_lap.iloc[(row_lap['fc'] - fc).abs().argsort()[:1]]

        # Support both old (Lpt_B/Lpb_B) and new (Lpt/Lpb) column names
        lpt_col = 'Lpt' if 'Lpt' in row_lap.columns else 'Lpt_B'
        lpb_col = 'Lpb' if 'Lpb' in row_lap.columns else 'Lpb_B'
        Lpt = float(row_lap[lpt_col].iloc[0])
        Lpb = float(row_lap[lpb_col].iloc[0])

        return Ldh, Lpt, Lpb


# ── Data adapter (reads our Tier 1 format) ───────────────────────────────────

class BeamDataAdapter:
    """Adapts our converter output to the calculator's needs."""

    def __init__(self, beams_df, columns_df, sections_df, reinf_df, nodes_df, walls_df=None):
        self.beams_df = beams_df.copy()
        self.columns_df = columns_df.copy()
        self.sections_df = sections_df.copy()
        self.reinf_df = reinf_df.copy()
        self.nodes_df = nodes_df.copy()
        self.walls_df = walls_df.copy() if walls_df is not None else pd.DataFrame()

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

            # Map I/M/J to zones: I=EXT, M=INT (or CTR), J=EXT
            # TOP bars — keyed by RAW member_id (level-specific)
            top_total = row.get('top_total')
            top_dia = row.get('top_dia_mm')
            if pd.notna(top_total) and pd.notna(top_dia) and int(top_total) > 0:
                zone = 'EXT' if pos in ('I', 'J') else 'INT'
                key = (raw_mid, 'TOP')
                if key not in self.long_cfg:
                    self.long_cfg[key] = {
                        'dia': float(top_dia),
                        'zones': {},
                        'main': 0,
                    }
                self.long_cfg[key]['zones'][zone] = max(
                    self.long_cfg[key]['zones'].get(zone, 0), int(top_total)
                )

            # BOT bars — keyed by RAW member_id (level-specific)
            bot_total = row.get('bot_total')
            bot_dia = row.get('bot_dia_mm')
            if pd.notna(bot_total) and pd.notna(bot_dia) and int(bot_total) > 0:
                zone = 'EXT' if pos in ('I', 'J') else 'CTR'
                key = (raw_mid, 'BOT')
                if key not in self.long_cfg:
                    self.long_cfg[key] = {
                        'dia': float(bot_dia),
                        'zones': {},
                        'main': 0,
                    }
                self.long_cfg[key]['zones'][zone] = max(
                    self.long_cfg[key]['zones'].get(zone, 0), int(bot_total)
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

        # Finalize: compute 'main' count (minimum across zones) and is_uniform
        for key, cfg in self.long_cfg.items():
            zones = cfg['zones']
            if zones:
                cfg['main'] = min(zones.values())
                cfg['num_zones'] = len(zones)
                cfg['is_uniform'] = (len(set(zones.values())) == 1)
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

            # Collect geometry from the first and last beams in the interval
            first_row = beams_df.loc[beam_idxs[0]]
            last_row = beams_df.loc[beam_idxs[-1]]
            member_id = str(first_row.get('member_id', '') or '')
            material_id = str(first_row.get('material_id', 'C35') or 'C35')
            b_mm = int(round(first_row.get('b_mm') or 400))
            h_mm = int(round(first_row.get('h_mm') or 600))
            fy_main = first_row.get('fy_main')

            # Coordinate span: from first beam's "start" to last beam's "end"
            # Use min/max on the primary axis
            refs_on_dir = first_row.get('direction', 'X')
            if refs_on_dir == 'X':
                all_x = []
                for idx in beam_idxs:
                    r = beams_df.loc[idx]
                    all_x.append(float(r.get('x_from_mm', 0) or 0))
                    all_x.append(float(r.get('x_to_mm', 0) or 0))
                xs = min(all_x)
                xe = max(all_x)
                ys = float(first_row.get('y_from_mm', 0) or 0)
                ye = ys
            else:
                all_y = []
                for idx in beam_idxs:
                    r = beams_df.loc[idx]
                    all_y.append(float(r.get('y_from_mm', 0) or 0))
                    all_y.append(float(r.get('y_to_mm', 0) or 0))
                ys = min(all_y)
                ye = max(all_y)
                xs = float(first_row.get('x_from_mm', 0) or 0)
                xe = xs
            zs = float(first_row.get('z_mm', 0) or 0)
            ze = zs

            # Sum of physical span lengths across the interval
            sum_span = 0.0
            for idx in beam_idxs:
                sum_span += float(beams_df.loc[idx].get('length_mm') or 0)

            # Anchorage: Ldh for the diameter + material of the first beam.
            # (All beams in the run share the same diameter by Case 3 break.)
            fy = _steel_grade(dia, fy_override=fy_main)
            fc = _parse_fc(material_id)
            Ldh, Lpt_B, Lpb_B = lookup.get(fy, dia, fc)

            # Phase 2 length approximation: sum of spans + 2*Ldh
            # (Refinement pass in Phase 3 can subtract column halves when a
            #  remainder's end coincides with a run boundary at a column.)
            length_mm = sum_span + 2 * Ldh

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
                'col_width_start_mm': 0,
                'col_width_end_mm': 0,
                'support_extends_below_start': False,
                'support_extends_below_end': False,
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
            }
            bar = _add_anchorage(bar, Ldh, Lpt_B, Lpb_B)
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
    """Return (role, n_bars) for MAIN_{TOP,BOT} at this span.

    Uses run graph if the span is part of a Case 1/2 run at this position.
    Otherwise returns the fallback (current zone-width-based role + count).

    Role derivation inside a run:
        - first beam in the ordered run -> MAIN_START (HOOK far side, LAP into next)
        - last beam                      -> MAIN_END   (LAP from prev, HOOK far side)
        - interior                       -> MAIN_INTERMEDIATE (LAP both sides)
    """
    if run_index is None:
        return fallback_role, fallback_n
    run = run_index.get_run(span_row_idx, position)
    if run is None:
        return fallback_role, fallback_n
    ordered = run.ordered_beams
    if span_row_idx not in ordered:
        return fallback_role, fallback_n
    pos = ordered.index(span_row_idx)
    n_run = len(ordered)
    if n_run == 1:
        return fallback_role, fallback_n
    if pos == 0:
        role = 'MAIN_START'
    elif pos == n_run - 1:
        role = 'MAIN_END'
    else:
        role = 'MAIN_INTERMEDIATE'
    return role, run.min_count


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
        # Prefer precomputed col_width from Phase 2.7 (coordinate-based, includes wall fallback)
        Wc1 = float(sp.get('col_width_start_mm', 0) or 0)
        Wc2 = float(sp.get('col_width_end_mm', 0) or 0)
        if Wc1 == 0:
            Wc1 = adapter.get_column_width(grid_from, direction, beam_level)
        if Wc1 == 0:
            Wc1 = adapter.get_wall_thickness(grid_from, direction, beam_level)
        if Wc2 == 0:
            Wc2 = adapter.get_column_width(grid_to, direction, beam_level)
        if Wc2 == 0:
            Wc2 = adapter.get_wall_thickness(grid_to, direction, beam_level)
        l_cl = l_span - 0.5 * (Wc1 + Wc2)

        dia_top = info['cfg_top']['dia']
        fy = _steel_grade(dia_top, fy_override=sp.get('fy_main'))
        fc = _parse_fc(sp.get('material_id', 'C35'))
        Ldh, Lpt_B, Lpb_B = lookup.get(fy, dia_top, fc)

        span_data.append({
            'l_span': l_span, 'l_cl': l_cl,
            'Wc1': Wc1, 'Wc2': Wc2,
            'Ldh': Ldh, 'Lpt_B': Lpt_B, 'Lpb_B': Lpb_B,
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
        top_role, main_top = _resolve_role_from_run(
            span_row_idx, 'TOP', legacy_role, fb_top, run_index,
        )
        bot_role, main_bot = _resolve_role_from_run(
            span_row_idx, 'BOT', legacy_role, fb_bot, run_index,
        )

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

        # MAIN TOP
        z_s_top = _bar_z(zs, h_mm, cover, 'TOP', 1, dia_top)
        z_e_top = _bar_z(ze, h_mm, cover, 'TOP', 1, dia_top)
        mt = {**base, 'x_start_mm': xs, 'y_start_mm': ys, 'z_start_mm': z_s_top,
              'x_end_mm': xe, 'y_end_mm': ye, 'z_end_mm': z_e_top,
              'bar_position': 'TOP', 'bar_role': top_role, 'bar_type': 'MAIN',
              'dia_mm': dia_top, 'n_bars': main_top, 'length_mm': int(round(Ltop)),
              'layer': 1, 'reinforcement_type': 'UNIFORM' if is_uniform else 'VARIABLE'}
        mt = _add_anchorage(mt, Ldh, Lpt_B, Lpb_B)
        for piece in _split_stock(mt, Lpt_B, direction):
            results.append(piece)

        # MAIN BOT
        z_s_bot = _bar_z(zs, h_mm, cover, 'BOT', 1, dia_bot)
        z_e_bot = _bar_z(ze, h_mm, cover, 'BOT', 1, dia_bot)
        mb = {**base, 'x_start_mm': xs, 'y_start_mm': ys, 'z_start_mm': z_s_bot,
              'x_end_mm': xe, 'y_end_mm': ye, 'z_end_mm': z_e_bot,
              'bar_position': 'BOT', 'bar_role': bot_role, 'bar_type': 'MAIN',
              'dia_mm': dia_bot, 'n_bars': main_bot, 'length_mm': int(round(Lbot)),
              'layer': 1, 'reinforcement_type': 'UNIFORM' if is_uniform else 'VARIABLE'}
        mb = _add_anchorage(mb, Ldh, Lpt_B, Lpb_B)
        for piece in _split_stock(mb, Lpb_B, direction):
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

            add_top_ext = int(max(0, zones_top.get('EXT', add_baseline_top) - add_baseline_top))
            add_top_int = int(max(0, zones_top.get('INT', add_baseline_top) - add_baseline_top))
            add_bot_ctr = int(max(0, zones_bot.get('CTR', add_baseline_bot) - add_baseline_bot))

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

            # ADD_START (first span, EXT zone has more bars than main)
            if is_first and add_top_ext > 0:
                L = Ldh + 0.25 * l_cl + lext
                ac = _add_bar_coords('ADD_START', L, direction, xs, ys, zs, xe, ye, ze)
                zsa = _bar_z(ac['z_start_mm'], h_mm, cover, 'TOP', 3, dia_top)
                zea = _bar_z(ac['z_end_mm'], h_mm, cover, 'TOP', 3, dia_top)
                d = {**base, **ac, 'z_start_mm': zsa, 'z_end_mm': zea,
                     'bar_position': 'TOP', 'bar_role': 'ADD_START', 'bar_type': 'ADD',
                     'dia_mm': dia_top, 'n_bars': add_top_ext,
                     'length_mm': int(round(L)), 'layer': 3,
                     'reinforcement_type': 'VARIABLE'}
                results.append(_add_anchorage(d, Ldh, Lpt_B, Lpb_B))

            # ADD_INTERMEDIATE (support bridging between spans)
            if not is_last and add_top_int > 0:
                L = 0.25 * (l_cl + l_cl_next) + Wc2 + 2 * lext
                ac = _add_bar_coords('ADD_INTERMEDIATE', L, direction, xs, ys, zs, xe, ye, ze)
                zsa = _bar_z(ac['z_start_mm'], h_mm, cover, 'TOP', 2, dia_top)
                zea = _bar_z(ac['z_end_mm'], h_mm, cover, 'TOP', 2, dia_top)
                d = {**base, **ac, 'z_start_mm': zsa, 'z_end_mm': zea,
                     'bar_position': 'TOP', 'bar_role': 'ADD_INTERMEDIATE', 'bar_type': 'ADD',
                     'dia_mm': dia_top, 'n_bars': add_top_int,
                     'length_mm': int(round(L)), 'layer': 2,
                     'reinforcement_type': 'VARIABLE'}
                results.append(_add_anchorage(d, Ldh, Lpt_B, Lpb_B))

            # ADD_END (last span)
            if is_last and add_top_ext > 0:
                L = Ldh + 0.25 * l_cl + lext
                ac = _add_bar_coords('ADD_END', L, direction, xs, ys, zs, xe, ye, ze)
                zsa = _bar_z(ac['z_start_mm'], h_mm, cover, 'TOP', 3, dia_top)
                zea = _bar_z(ac['z_end_mm'], h_mm, cover, 'TOP', 3, dia_top)
                d = {**base, **ac, 'z_start_mm': zsa, 'z_end_mm': zea,
                     'bar_position': 'TOP', 'bar_role': 'ADD_END', 'bar_type': 'ADD',
                     'dia_mm': dia_top, 'n_bars': add_top_ext,
                     'length_mm': int(round(L)), 'layer': 3,
                     'reinforcement_type': 'VARIABLE'}
                results.append(_add_anchorage(d, Ldh, Lpt_B, Lpb_B))

            # ADD_MIDSPAN (bottom bars in CTR zone)
            if add_bot_ctr > 0:
                L = 0.5 * l_cl + 2 * lext
                ac = _add_bar_coords('ADD_MIDSPAN', L, direction, xs, ys, zs, xe, ye, ze)
                zsa = _bar_z(ac['z_start_mm'], h_mm, cover, 'BOT', 2, dia_bot)
                zea = _bar_z(ac['z_end_mm'], h_mm, cover, 'BOT', 2, dia_bot)
                d = {**base, **ac, 'z_start_mm': zsa, 'z_end_mm': zea,
                     'bar_position': 'BOT', 'bar_role': 'ADD_MIDSPAN', 'bar_type': 'ADD',
                     'dia_mm': dia_bot, 'n_bars': add_bot_ctr,
                     'length_mm': int(round(L)), 'layer': 2,
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

    # Emit MAIN_REMAINDER bars from Case 2 runs (after all subgroups processed)
    if run_index is not None:
        remainder_results = _emit_remainder_bars(run_index, adapter, lookup)
        results.extend(remainder_results)

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
        Wc1 = adapter.get_column_width(grid_from, direction, st_level)
        if Wc1 == 0:
            Wc1 = adapter.get_wall_thickness(grid_from, direction, st_level)
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

        base = {
            'segment_id': segment_id, 'level': row.get('level', ''),
            'direction': direction, 'line_grid': line_grid,
            'member_id': mid, 'span_index': span_idx,
            'start_grid': grid_from, 'end_grid': grid_to,
            'x_start_mm': xs, 'y_start_mm': ys, 'z_start_mm': zs,
            'x_end_mm': xe, 'y_end_mm': ye, 'z_end_mm': zs,
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

            results.append({
                **base,
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
        walls_df: MembersWall.csv (optional, for wall anchorage extension)

    Returns:
        DataFrame for RebarLengthsBeam.csv
    """
    print('[RebarBeam] Loading lookup tables...')
    lookup = DevLapLookup(dev_lengths_path, lap_splice_path)

    print('[RebarBeam] Building data adapter...')
    adapter = BeamDataAdapter(beams_df, columns_df, sections_df, reinf_df, nodes_df, walls_df)
    print(f'[RebarBeam] {len(adapter.long_cfg)} longitudinal configs, '
          f'{len(adapter.stirrup_cfg)} stirrup configs')

    print('[RebarBeam] Calculating main bars...')
    main_bars = _calculate_main_bars(adapter, lookup)

    print('[RebarBeam] Calculating stirrups...')
    stirrups = _calculate_stirrups(adapter)

    all_results = main_bars + stirrups

    print('[RebarBeam] Computing splice coordinates...')
    _compute_splice_coords(all_results, adapter)

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
        'b_mm', 'h_mm', 'shape',
        'col_width_start_mm', 'col_width_end_mm',
        'support_extends_below_start', 'support_extends_below_end',
    ]

    df = pd.DataFrame(all_results)
    avail = [c for c in column_order if c in df.columns]
    df = df[avail]

    print(f'[RebarBeam] {len(main_bars)} main bar records + '
          f'{len(stirrups)} stirrup records = {len(df)} total')

    return df
