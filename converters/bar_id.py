"""Bar ID & Member Instance ID assignment — integrated into converter pipeline.

Operates on the outputs dict (DataFrames) directly, no file I/O needed.
Adds member_instance_id to all Members DataFrames and bar_id + member_instance_id
to all RebarLengths DataFrames.

Also fixes:
- Footing boundary_nodes: raw MIDAS node numbers → N_{level}_OFF{num} format
- Beam N_SPLIT nodes: adds synthetic split-point nodes to Nodes DataFrame

Bar ID V2 format: Building-Floor-Symbol-Serial-BarMark
  Building: BD01-BD99
  Floor: B01, F01, PIT, ROF, FTG (3 chars)
  Symbol: padded number (C001, B001A, W002, MF001, S012, SS001)
  Serial: 001-999, unique per (Symbol, Floor)
  BarMark: count-PositionDia@spacing-serial
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Optional

import pandas as pd


# ── Floor formatting ──

FLOOR_ORDER = [
    'FTG', 'B5', 'B4', 'B3', 'B2', 'B1', 'PIT',
    '1F', '2F', '3F', '4F', '5F', '6F', '7F', '8F', '9F', '10F',
    '11F', '12F', '13F', '14F', '15F', '16F', '17F', '18F', '19F', '20F',
    '21F', '22F', '23F', '24F', '25F', '26F', '27F', '28F', '29F', '30F',
    'Roof',
]
FLOOR_RANK = {f: i for i, f in enumerate(FLOOR_ORDER)}


def _format_floor(level: str) -> str:
    if not level:
        return 'UNK'
    level = level.strip()
    if level.upper() == 'FOOTING':
        return 'FTG'
    if level.upper() == 'ROOF':
        return 'ROF'
    m = re.match(r'^(\d+)F$', level)
    if m:
        return f'F{int(m.group(1)):02d}'
    m = re.match(r'^B(\d+)$', level)
    if m:
        return f'B{int(m.group(1)):02d}'
    if len(level) <= 3:
        return level
    return level[:3]


def _floor_rank(level: str) -> int:
    level = level.strip() if level else ''
    if level in FLOOR_RANK:
        return FLOOR_RANK[level]
    m = re.match(r'^(\d+)F$', level)
    if m:
        return 100 + int(m.group(1))
    m = re.match(r'^B(\d+)$', level)
    if m:
        return -int(m.group(1))
    return 999


# ── Symbol formatting ──

_SLAB_STAIR_FLOOR_PATTERNS = [
    (r'^PHR\d*', 'PHR'),
    (r'^PH\d+', 'PH'),
    (r'^PIT', 'PIT'),
    (r'^R(?=S)', 'R'),
    (r'^B\d+', 'B'),
    (r'^\d+', 'NUM'),
]


def _strip_floor_prefix(member_id: str, level: str) -> str:
    if not member_id:
        return member_id
    for pattern, ptype in _SLAB_STAIR_FLOOR_PATTERNS:
        m = re.match(pattern, member_id)
        if m:
            stripped = member_id[m.end():]
            if stripped:
                return stripped
    return member_id


def _pad_symbol(member_id: str) -> str:
    if not member_id:
        return member_id
    m = re.match(r'^([A-Za-z]+)(\d+)([A-Za-z]*)$', member_id)
    if m:
        prefix, num, suffix = m.groups()
        return f'{prefix}{num.zfill(3)}{suffix}'
    return member_id


def _format_symbol(member_id: str, level: str, member_type: str) -> str:
    if not member_id:
        return member_id
    # Sejong grid-based
    m = re.match(r'^([A-Za-z]+\d*)[-_](X\d+p?Y\d+)$', member_id)
    if m:
        return _pad_symbol(m.group(1))
    m = re.match(r'^AF-([A-Za-z]+\d+[A-Za-z]*)$', member_id)
    if m:
        return 'AF' + _pad_symbol(m.group(1))
    if member_type in ('SLAB', 'STAIR'):
        member_id = _strip_floor_prefix(member_id, level)
    return _pad_symbol(member_id)


# ── Position prefix ──

def _get_position_prefix(bar_position: str, member_type: str, face: str = '') -> str:
    pos = (bar_position or '').upper()
    mtype = (member_type or '').upper()
    fc = (face or '').upper()
    if mtype == 'BASEMENT_WALL':
        if fc == 'INTERIOR':
            return 'N'
        elif fc == 'EXTERIOR':
            return 'F'
        return 'D'
    if mtype in ('WALL',):
        return 'A'
    if mtype in ('BEAM', 'SLAB', 'FOOTING'):
        if pos == 'TOP':
            return 'T'
        elif pos in ('BOT', 'BOTTOM'):
            return 'B'
        elif pos in ('MIDDLE', 'MID'):
            return 'M'
        return 'D'
    return 'D'


# ── Bar mark ──

def _build_bar_mark(row: dict, serial: int, member_type: str) -> str:
    dia = row.get('dia_mm', '')
    try:
        dia_int = int(round(float(dia)))
    except (ValueError, TypeError):
        dia_int = 0

    bar_type = (row.get('bar_type') or '').upper()
    bar_role = (row.get('bar_role') or '').upper()
    is_stirrup_hoop = bar_type in ('STIRRUP',) or 'HOOP' in bar_role or 'TIE' in bar_role

    if is_stirrup_hoop:
        qty = row.get('quantity_pieces', '') or row.get('n_bars', '')
    else:
        qty = row.get('n_bars', '')
    try:
        qty_int = int(round(float(qty)))
    except (ValueError, TypeError):
        qty_int = 0

    spacing = row.get('spacing_mm', '')
    try:
        spacing_int = int(round(float(spacing)))
    except (ValueError, TypeError):
        spacing_int = 0

    bar_position = row.get('bar_position', '') or row.get('layer', '') or ''
    face = row.get('face', '')
    prefix = _get_position_prefix(bar_position, member_type, face)

    mark = f'{qty_int}-{prefix}{dia_int}'
    if spacing_int > 0:
        mark += f'@{spacing_int}'
    mark += f'-{str(serial).zfill(3)}'
    return mark


# ── Grid instance assignment (Sejong) ──

def _extract_grid_key(member_id: str):
    m = re.match(r'^([A-Za-z]+\d*)[-_]X(\d+)(p?)Y(\d+)$', member_id)
    if m:
        type_str, x_num, x_prime, y_num = m.groups()
        x_sort = int(x_num) + (0.5 if x_prime else 0)
        return (type_str, x_sort, int(y_num))
    return None


def _extract_seg_instance(segment_id: str) -> str | None:
    if not segment_id:
        return None
    m = re.search(r'(?:GAP|SEG)(\d+)', segment_id)
    if m:
        return m.group(1).zfill(3)
    return None


# ── Main integration function ──

def assign_bar_ids(outputs: dict, building: str = 'BD01', log_fn=None):
    """Add member_instance_id and bar_id to all Member and RebarLengths DataFrames.

    Also fixes footing boundary_nodes and adds beam N_SPLIT nodes.

    Args:
        outputs: converter outputs dict with DataFrames
        building: building code (default BD01)
        log_fn: optional logging function
    """
    def log(msg):
        if log_fn:
            log_fn(msg)

    nodes_df = outputs.get('nodes')

    # ── Phase 1: Members ──

    # Beam
    beams_df = outputs.get('beams')
    if beams_df is not None and not beams_df.empty:
        groups = defaultdict(list)
        for i, r in beams_df.iterrows():
            groups[(r.get('member_id', ''), r.get('level', ''))].append(i)
        for (mid, level), indices in groups.items():
            sorted_idx = sorted(indices, key=lambda i: (
                float(beams_df.at[i, 'x_from_mm'] or 0),
                float(beams_df.at[i, 'y_from_mm'] or 0),
            ))
            for seq, idx in enumerate(sorted_idx, 1):
                floor = _format_floor(level)
                symbol = _format_symbol(mid, level, 'BEAM')
                beams_df.at[idx, 'member_instance_id'] = f'{building}-{floor}-{symbol}-{str(seq).zfill(3)}'
        log(f"Bar ID: MembersBeam {len(beams_df)} rows assigned")

    # Column
    cols_df = outputs.get('columns')
    if cols_df is not None and not cols_df.empty:
        has_grid = any(_extract_grid_key(str(r.get('member_id', ''))) for _, r in cols_df.head(20).iterrows())
        if has_grid:
            # Sejong grid-based
            groups = defaultdict(list)
            for i, r in cols_df.iterrows():
                gk = _extract_grid_key(str(r.get('member_id', '')))
                if gk:
                    groups[(gk[0], r.get('level_from', ''))].append((i, gk[1], gk[2]))
            for key, entries in groups.items():
                entries.sort(key=lambda e: (e[2], e[1]))  # Y then X
                for seq, (idx, _, _) in enumerate(entries, 1):
                    r = cols_df.loc[idx]
                    floor = _format_floor(r.get('level_from', r.get('level', '')))
                    symbol = _format_symbol(str(r['member_id']), str(r.get('level_from', '')), 'COLUMN')
                    cols_df.at[idx, 'member_instance_id'] = f'{building}-{floor}-{symbol}-{str(seq).zfill(3)}'
        else:
            # Position-based
            # Assign by unique positions per member_id
            member_positions = defaultdict(set)
            for i, r in cols_df.iterrows():
                mid = str(r.get('member_id', ''))
                px = round(float(r.get('x_mm', 0) or 0), 0)
                py = round(float(r.get('y_mm', 0) or 0), 0)
                member_positions[mid].add((px, py))

            pos_ids = {}
            for mid, positions in member_positions.items():
                for seq, (px, py) in enumerate(sorted(positions), 1):
                    pos_ids[(mid, px, py)] = str(seq).zfill(3)

            for i, r in cols_df.iterrows():
                mid = str(r.get('member_id', ''))
                floor = _format_floor(str(r.get('level_from', r.get('level', ''))))
                symbol = _format_symbol(mid, str(r.get('level_from', '')), 'COLUMN')
                px = round(float(r.get('x_mm', 0) or 0), 0)
                py = round(float(r.get('y_mm', 0) or 0), 0)
                instance = pos_ids.get((mid, px, py), '000')
                cols_df.at[i, 'member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
        log(f"Bar ID: MembersColumn {len(cols_df)} rows assigned")

    # Wall — sequential per (wall_mark, level)
    walls_df = outputs.get('walls')
    if walls_df is not None and not walls_df.empty:
        member_key = 'wall_mark' if 'wall_mark' in walls_df.columns else 'member_id'
        has_seg = 'segment_id' in walls_df.columns and walls_df['segment_id'].notna().any()

        if has_seg:
            for i, r in walls_df.iterrows():
                floor = _format_floor(str(r.get('level', '')))
                raw_mid = str(r.get(member_key, ''))
                symbol = _format_symbol(raw_mid, str(r.get('level', '')), 'WALL')
                instance = _extract_seg_instance(str(r.get('segment_id', ''))) or '000'
                walls_df.at[i, 'member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
        else:
            groups = defaultdict(list)
            for i, r in walls_df.iterrows():
                wm = str(r.get(member_key, ''))
                level = str(r.get('level', ''))
                groups[(wm, level)].append(i)
            for (wm, level), indices in groups.items():
                sorted_idx = sorted(indices, key=lambda i: (
                    float(walls_df.at[i, 'centroid_x_mm'] or 0),
                    float(walls_df.at[i, 'centroid_y_mm'] or 0),
                ))
                for seq, idx in enumerate(sorted_idx, 1):
                    floor = _format_floor(level)
                    symbol = _format_symbol(wm, level, 'WALL')
                    walls_df.at[idx, 'member_instance_id'] = f'{building}-{floor}-{symbol}-{str(seq).zfill(3)}'
        log(f"Bar ID: MembersWall {len(walls_df)} rows assigned")

    # Basement Wall
    bwall_df = outputs.get('bwall_members')
    if bwall_df is not None and not bwall_df.empty:
        member_key = 'wall_mark' if 'wall_mark' in bwall_df.columns else 'member_id'
        has_panel = 'panel_no' in bwall_df.columns and bwall_df['panel_no'].notna().any()
        if has_panel:
            for i, r in bwall_df.iterrows():
                floor = _format_floor(str(r.get('level', '')))
                raw_mid = str(r.get(member_key, ''))
                symbol = _format_symbol(raw_mid, str(r.get('level', '')), 'BASEMENT_WALL')
                instance = str(int(r.get('panel_no', 0))).zfill(3)
                bwall_df.at[i, 'member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
        else:
            pos = defaultdict(set)
            for i, r in bwall_df.iterrows():
                mid = str(r.get(member_key, ''))
                px = round(float(r.get('centroid_x_mm', 0) or 0), 0)
                py = round(float(r.get('centroid_y_mm', 0) or 0), 0)
                pos[mid].add((px, py))
            pos_ids = {}
            for mid, positions in pos.items():
                for seq, (px, py) in enumerate(sorted(positions), 1):
                    pos_ids[(mid, px, py)] = str(seq).zfill(3)
            for i, r in bwall_df.iterrows():
                floor = _format_floor(str(r.get('level', '')))
                raw_mid = str(r.get(member_key, ''))
                symbol = _format_symbol(raw_mid, str(r.get('level', '')), 'BASEMENT_WALL')
                px = round(float(r.get('centroid_x_mm', 0) or 0), 0)
                py = round(float(r.get('centroid_y_mm', 0) or 0), 0)
                instance = pos_ids.get((raw_mid, px, py), '000')
                bwall_df.at[i, 'member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
        log(f"Bar ID: MembersBasementWall {len(bwall_df)} rows assigned")

    # Slab
    slabs_df = outputs.get('slabs')
    if slabs_df is not None and not slabs_df.empty:
        has_seg = 'segment_id' in slabs_df.columns and slabs_df['segment_id'].notna().any()
        if has_seg:
            for i, r in slabs_df.iterrows():
                floor = _format_floor(str(r.get('level', '')))
                raw_mid = str(r.get('member_id', ''))
                symbol = _format_symbol(raw_mid, str(r.get('level', '')), 'SLAB')
                instance = _extract_seg_instance(str(r.get('segment_id', ''))) or '000'
                slabs_df.at[i, 'member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
        else:
            pos = defaultdict(set)
            for i, r in slabs_df.iterrows():
                mid = str(r.get('member_id', ''))
                px = round(float(r.get('centroid_x_mm', 0) or 0), 0)
                py = round(float(r.get('centroid_y_mm', 0) or 0), 0)
                pos[mid].add((px, py))
            pos_ids = {}
            for mid, positions in pos.items():
                for seq, (px, py) in enumerate(sorted(positions), 1):
                    pos_ids[(mid, px, py)] = str(seq).zfill(3)
            for i, r in slabs_df.iterrows():
                floor = _format_floor(str(r.get('level', '')))
                raw_mid = str(r.get('member_id', ''))
                symbol = _format_symbol(raw_mid, str(r.get('level', '')), 'SLAB')
                px = round(float(r.get('centroid_x_mm', 0) or 0), 0)
                py = round(float(r.get('centroid_y_mm', 0) or 0), 0)
                instance = pos_ids.get((raw_mid, px, py), '000')
                slabs_df.at[i, 'member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
        log(f"Bar ID: MembersSlab {len(slabs_df)} rows assigned")

    # Footing
    footings_df = outputs.get('footings')
    if footings_df is not None and not footings_df.empty:
        has_seg = 'segment_id' in footings_df.columns and footings_df['segment_id'].notna().any()
        has_level = 'level' in footings_df.columns and footings_df['level'].notna().any()
        has_grid = any(_extract_grid_key(str(r.get('member_id', ''))) for _, r in footings_df.head(20).iterrows())

        if has_grid:
            groups = defaultdict(list)
            for i, r in footings_df.iterrows():
                gk = _extract_grid_key(str(r.get('member_id', '')))
                if gk:
                    groups[(gk[0], r.get('level', ''))].append((i, gk[1], gk[2]))
            for key, entries in groups.items():
                entries.sort(key=lambda e: (e[2], e[1]))
                for seq, (idx, _, _) in enumerate(entries, 1):
                    r = footings_df.loc[idx]
                    floor = _format_floor(str(r.get('level', ''))) if has_level else 'FTG'
                    if floor == 'UNK': floor = 'FTG'
                    symbol = _format_symbol(str(r['member_id']), str(r.get('level', '')), 'FOOTING')
                    footings_df.at[idx, 'member_instance_id'] = f'{building}-{floor}-{symbol}-{str(seq).zfill(3)}'
        elif has_seg:
            for i, r in footings_df.iterrows():
                floor = _format_floor(str(r.get('level', ''))) if has_level else 'FTG'
                if floor == 'UNK': floor = 'FTG'
                raw_mid = str(r.get('member_id', ''))
                symbol = _format_symbol(raw_mid, str(r.get('level', '')), 'FOOTING')
                instance = _extract_seg_instance(str(r.get('segment_id', ''))) or '001'
                footings_df.at[i, 'member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
        else:
            pos = defaultdict(set)
            for i, r in footings_df.iterrows():
                mid = str(r.get('member_id', ''))
                px = round(float(r.get('centroid_x_mm', 0) or 0), 0)
                py = round(float(r.get('centroid_y_mm', 0) or 0), 0)
                pos[mid].add((px, py))
            pos_ids = {}
            for mid, positions in pos.items():
                for seq, (px, py) in enumerate(sorted(positions), 1):
                    pos_ids[(mid, px, py)] = str(seq).zfill(3)
            for i, r in footings_df.iterrows():
                floor = _format_floor(str(r.get('level', ''))) if has_level else 'FTG'
                if floor == 'UNK': floor = 'FTG'
                raw_mid = str(r.get('member_id', ''))
                symbol = _format_symbol(raw_mid, str(r.get('level', '')), 'FOOTING')
                px = round(float(r.get('centroid_x_mm', 0) or 0), 0)
                py = round(float(r.get('centroid_y_mm', 0) or 0), 0)
                instance = pos_ids.get((raw_mid, px, py), '001')
                footings_df.at[i, 'member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
        log(f"Bar ID: MembersFooting {len(footings_df)} rows assigned")

    # Stair
    stairs_df = outputs.get('stairs')
    if stairs_df is not None and not stairs_df.empty:
        for i, r in stairs_df.iterrows():
            sg = str(r.get('story_group', ''))
            level = sg.split('~')[0] if '~' in sg else str(r.get('level_from', sg))
            floor = _format_floor(level)
            raw_mid = str(r.get('member_id', ''))
            symbol = _format_symbol(raw_mid, level, 'STAIR')
            instance = _extract_seg_instance(str(r.get('segment_id', ''))) or '000'
            stairs_df.at[i, 'member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
        log(f"Bar ID: MembersStair {len(stairs_df)} rows assigned")

    # ── Phase 2: RebarLengths (lookup from Members) ──
    _assign_rebar_ids(outputs, building, log)

    # ── Phase 3: Node fixes ──
    _fix_footing_boundary_nodes(outputs, log)
    _fix_beam_split_nodes(outputs, log)

    # ── Phase 4: Reorder columns (member_instance_id first, bar_id first for rebar) ──
    member_keys = ['beams', 'columns', 'walls', 'bwall_members', 'slabs', 'footings', 'stairs']
    for key in member_keys:
        df = outputs.get(key)
        if df is not None and 'member_instance_id' in df.columns:
            cols = ['member_instance_id'] + [c for c in df.columns if c != 'member_instance_id']
            outputs[key] = df[cols]

    rebar_keys = ['rebar_beam', 'rebar_column', 'rebar_wall', 'rebar_bwall',
                  'rebar_slab', 'rebar_footing', 'rebar_stair']
    for key in rebar_keys:
        df = outputs.get(key)
        if df is not None and 'bar_id' in df.columns and 'member_instance_id' in df.columns:
            first = ['bar_id', 'member_instance_id']
            cols = first + [c for c in df.columns if c not in first]
            outputs[key] = df[cols]


def _assign_rebar_ids(outputs: dict, building: str, log):
    """Assign bar_id + member_instance_id to all RebarLengths DataFrames."""

    # Build member instance lookups
    def _build_beam_lookup(beams_df):
        if beams_df is None or beams_df.empty:
            return {}
        lookup = defaultdict(list)
        for _, r in beams_df.iterrows():
            mid = str(r.get('member_id', ''))
            level = str(r.get('level', ''))
            try:
                x_from = float(r.get('x_from_mm', 0) or 0)
                y_from = float(r.get('y_from_mm', 0) or 0)
                x_to = float(r.get('x_to_mm', 0) or 0)
                y_to = float(r.get('y_to_mm', 0) or 0)
            except (ValueError, TypeError):
                continue
            inst = r.get('member_instance_id', '')
            lookup[(mid, level)].append((min(x_from, x_to), max(x_from, x_to),
                                          min(y_from, y_to), max(y_from, y_to), inst))
        return lookup

    def _build_centroid_lookup(members_df, key_col='member_id', level_col='level'):
        if members_df is None or members_df.empty:
            return {}
        lookup = defaultdict(list)
        for _, r in members_df.iterrows():
            mid = str(r.get(key_col, ''))
            lvl = str(r.get(level_col, ''))
            try:
                cx = float(r.get('centroid_x_mm', r.get('x_mm', 0)) or 0)
                cy = float(r.get('centroid_y_mm', r.get('y_mm', 0)) or 0)
            except (ValueError, TypeError):
                cx, cy = 0, 0
            inst = r.get('member_instance_id', '')
            lookup[(mid, lvl)].append((cx, cy, inst))
        return lookup

    beam_lookup = _build_beam_lookup(outputs.get('beams'))
    col_lookup = _build_centroid_lookup(outputs.get('columns'), 'member_id', 'level_from')
    wall_key = 'wall_mark' if outputs.get('walls') is not None and 'wall_mark' in outputs.get('walls', pd.DataFrame()).columns else 'member_id'
    wall_lookup = _build_centroid_lookup(outputs.get('walls'), wall_key, 'level')
    bwall_key = 'wall_mark' if outputs.get('bwall_members') is not None and 'wall_mark' in outputs.get('bwall_members', pd.DataFrame()).columns else 'member_id'
    bwall_lookup = _build_centroid_lookup(outputs.get('bwall_members'), bwall_key, 'level')
    slab_lookup = _build_centroid_lookup(outputs.get('slabs'), 'member_id', 'level')
    footing_lookup = defaultdict(list)
    if outputs.get('footings') is not None and not outputs.get('footings').empty:
        for _, r in outputs['footings'].iterrows():
            mid = str(r.get('member_id', ''))
            try:
                cx = float(r.get('centroid_x_mm', 0) or 0)
                cy = float(r.get('centroid_y_mm', 0) or 0)
            except (ValueError, TypeError):
                cx, cy = 0, 0
            inst = r.get('member_instance_id', '')
            lvl = str(r.get('level', ''))
            footing_lookup[mid].append((cx, cy, lvl, inst))

    rebar_map = {
        'rebar_beam': ('BEAM', 'level', beam_lookup, 'bbox'),
        'rebar_column': ('COLUMN', 'level_from', col_lookup, 'closest'),
        'rebar_wall': ('WALL', 'level', wall_lookup, 'closest'),
        'rebar_bwall': ('BASEMENT_WALL', 'level', bwall_lookup, 'closest'),
        'rebar_slab': ('SLAB', 'level', slab_lookup, 'closest'),
        'rebar_footing': ('FOOTING', None, footing_lookup, 'footing'),
        'rebar_stair': ('STAIR', None, None, 'stair'),
    }

    for rebar_key, (mtype, level_col, lookup, match_type) in rebar_map.items():
        rebar_df = outputs.get(rebar_key)
        if rebar_df is None or rebar_df.empty:
            continue

        rows = rebar_df.to_dict('records')

        # Serial assignment
        if level_col:
            serial_groups = defaultdict(list)
            for i, r in enumerate(rows):
                if mtype == 'BEAM':
                    key = (r.get('segment_id', ''), r.get('bar_position', ''), r.get('bar_role', ''), r.get('dia_mm', ''), r.get('bar_type', ''))
                else:
                    mk = 'wall_mark' if 'wall_mark' in r else 'member_id'
                    xk = 'mesh_origin_x_mm' if 'mesh_origin_x_mm' in r else 'x_start_mm'
                    yk = 'mesh_origin_y_mm' if 'mesh_origin_y_mm' in r else 'y_start_mm'
                    key = (r.get(mk, ''), r.get(xk, ''), r.get(yk, ''), r.get('bar_role', ''), r.get('dia_mm', ''))
                serial_groups[key].append((i, r))

            serials = {}
            for key, group in serial_groups.items():
                sorted_g = sorted(group, key=lambda x: _floor_rank(str(x[1].get(level_col, ''))))
                for serial, (idx, _) in enumerate(sorted_g, 1):
                    serials[idx] = serial

        for i, r in enumerate(rows):
            raw_mid = str(r.get('member_id', '') or r.get('wall_mark', ''))
            level = str(r.get(level_col, '')) if level_col else ''

            mid_instance = ''

            if match_type == 'bbox':
                # Beam: bbox containment
                try:
                    rx = float(r.get('x_start_mm', 0) or 0)
                    ry = float(r.get('y_start_mm', 0) or 0)
                except (ValueError, TypeError):
                    rx, ry = 0, 0
                tol = 500
                for (x_min, x_max, y_min, y_max, inst) in lookup.get((raw_mid, level), []):
                    if x_min - tol <= rx <= x_max + tol and y_min - tol <= ry <= y_max + tol:
                        mid_instance = inst
                        break

            elif match_type == 'closest':
                # Closest centroid
                xk = 'mesh_origin_x_mm' if 'mesh_origin_x_mm' in r else 'x_start_mm'
                yk = 'mesh_origin_y_mm' if 'mesh_origin_y_mm' in r else 'y_start_mm'
                try:
                    rx = float(r.get(xk, 0) or 0)
                    ry = float(r.get(yk, 0) or 0)
                except (ValueError, TypeError):
                    rx, ry = 0, 0
                best_dist = float('inf')
                for (cx, cy, inst) in lookup.get((raw_mid, level), []):
                    dist = (rx - cx) ** 2 + (ry - cy) ** 2
                    if dist < best_dist:
                        best_dist = dist
                        mid_instance = inst

            elif match_type == 'footing':
                try:
                    rx = float(r.get('mesh_origin_x_mm', 0) or 0)
                    ry = float(r.get('mesh_origin_y_mm', 0) or 0)
                except (ValueError, TypeError):
                    rx, ry = 0, 0
                best_dist = float('inf')
                for (cx, cy, lvl, inst) in footing_lookup.get(raw_mid, []):
                    dist = (rx - cx) ** 2 + (ry - cy) ** 2
                    if dist < best_dist:
                        best_dist = dist
                        mid_instance = inst

            elif match_type == 'stair':
                sg = str(r.get('story_group', ''))
                level = sg.split('~')[0] if '~' in sg else sg
                floor = _format_floor(level)
                symbol = _format_symbol(raw_mid, level, 'STAIR')
                instance = _extract_seg_instance(str(r.get('segment_id', ''))) or '000'
                mid_instance = f'{building}-{floor}-{symbol}-{instance}'

            if not mid_instance:
                floor = _format_floor(level)
                symbol = _format_symbol(raw_mid, level, mtype)
                mid_instance = f'{building}-{floor}-{symbol}-000'

            serial = serials.get(i, 1) if level_col else 1
            bar_mark = _build_bar_mark(r, serial, mtype)

            rebar_df.at[rebar_df.index[i], 'member_instance_id'] = mid_instance
            rebar_df.at[rebar_df.index[i], 'bar_id'] = f'{mid_instance}-{bar_mark}'

        log(f"Bar ID: {rebar_key} {len(rebar_df)} rows assigned")


def _fix_footing_boundary_nodes(outputs: dict, log):
    """Fix footing boundary_nodes: raw MIDAS numbers -> N_{level}_OFF{num} format."""
    footings_df = outputs.get('footings')
    nodes_df = outputs.get('nodes')
    if footings_df is None or nodes_df is None or footings_df.empty:
        return

    num_to_id = {}
    for _, n in nodes_df.iterrows():
        nn = n.get('node_number')
        if pd.notna(nn):
            num_to_id[str(int(nn))] = n['node_id']

    fixed = 0
    for i, r in footings_df.iterrows():
        bn = str(r.get('boundary_nodes', ''))
        if not bn:
            continue
        parts = bn.split(';')
        new_parts = []
        changed = False
        for p in parts:
            p = p.strip()
            if p in num_to_id:
                new_parts.append(num_to_id[p])
                fixed += 1
                changed = True
            else:
                new_parts.append(p)
        if changed:
            footings_df.at[i, 'boundary_nodes'] = ';'.join(new_parts)

    if fixed > 0:
        log(f"Bar ID: Fixed {fixed} footing boundary_node refs")


def _fix_beam_split_nodes(outputs: dict, log):
    """Add N_SPLIT_* nodes to Nodes DataFrame."""
    beams_df = outputs.get('beams')
    nodes_df = outputs.get('nodes')
    if beams_df is None or nodes_df is None or beams_df.empty:
        return

    existing_ids = set(nodes_df['node_id'].values)
    new_rows = []

    for _, r in beams_df.iterrows():
        for ncol, xcol, ycol in [('node_from', 'x_from_mm', 'y_from_mm'),
                                   ('node_to', 'x_to_mm', 'y_to_mm')]:
            nid = str(r.get(ncol, ''))
            if 'SPLIT' in nid and nid not in existing_ids:
                m = re.search(r'_(\d+F|B\d+|PIT|Roof)_', nid)
                level = m.group(1) if m else str(r.get('level', ''))
                new_rows.append({
                    'node_id': nid,
                    'node_number': None,
                    'x_mm': r[xcol],
                    'y_mm': r[ycol],
                    'z_mm': r.get('z_mm', 0),
                    'level': level,
                    'grid': 'SPLIT',
                    'grid_offset_x_mm': 0.0,
                    'grid_offset_y_mm': 0.0,
                    'source': 'CONVERTER_SPLIT',
                })
                existing_ids.add(nid)

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        outputs['nodes'] = pd.concat([nodes_df, new_df], ignore_index=True)
        log(f"Bar ID: Added {len(new_rows)} N_SPLIT nodes to Nodes")
