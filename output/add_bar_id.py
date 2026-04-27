"""Post-processing script: add bar_id column to converted rebar CSVs.

Bar ID format: Building-Floor-Symbol-ID-BarMark

Components:
  Building:  3-digit code (default '001')
  Floor:     B1, B2... for basement; F1, F2... for upper floors; PIT, Roof, FTG
  Symbol:    member_id (C11, B11, G11, W11, S11, SS11, MF11, etc.)
  ID:        3-digit instance number (001-999), unique per Symbol
  Bar Mark:  a-DXX@xx-serial
             a = n_bars (MAIN) or quantity_pieces (stirrups/hoops)
             DXX = diameter
             @xx = spacing (only if spacing exists)
             serial = floor-based sequential, lowest floor = 1

Usage:
  python add_bar_id.py <input_folder> <output_folder> [--building 001]

Example:
  python add_bar_id.py "C:\...\Project Buldang-dong_Converted_18Apr26m" "C:\...\output_with_bar_id"
"""

import argparse
import csv
import os
import re
import shutil
from collections import defaultdict
from pathlib import Path


# ── Floor formatting ──

# Ordered floor levels from bottom to top (for serial numbering)
FLOOR_ORDER = [
    'FTG',
    'B5', 'B4', 'B3', 'B2', 'B1',
    'PIT',
    '1F', '2F', '3F', '4F', '5F', '6F', '7F', '8F', '9F', '10F',
    '11F', '12F', '13F', '14F', '15F', '16F', '17F', '18F', '19F', '20F',
    '21F', '22F', '23F', '24F', '25F', '26F', '27F', '28F', '29F', '30F',
    'Roof',
]
FLOOR_RANK = {f: i for i, f in enumerate(FLOOR_ORDER)}


def _format_floor(level: str) -> str:
    """Convert level string to V2 bar_id floor format (3 chars fixed).

    1F → F01, 2F → F02, 10F → F10
    B1 → B01, B5 → B05
    PIT → PIT, Roof → ROF, FTG → FTG, FOOTING → FTG
    """
    if not level:
        return 'UNK'
    level = level.strip()
    # FOOTING → FTG
    if level.upper() == 'FOOTING':
        return 'FTG'
    # Roof → ROF
    if level.upper() == 'ROOF':
        return 'ROF'
    # Match upper floors: 1F, 2F, 10F → F01, F02, F10
    m = re.match(r'^(\d+)F$', level)
    if m:
        return f'F{int(m.group(1)):02d}'
    # Match basement: B1, B2 → B01, B02
    m = re.match(r'^B(\d+)$', level)
    if m:
        return f'B{int(m.group(1)):02d}'
    # PIT, FTG, PH1, PH2 — keep as-is if 3 chars, pad if needed
    if len(level) <= 3:
        return level
    return level[:3]


def _floor_rank(level: str) -> int:
    """Get numeric rank for floor ordering (lower = lower floor)."""
    level = level.strip() if level else ''
    if level in FLOOR_RANK:
        return FLOOR_RANK[level]
    # Try to parse unknown floors
    m = re.match(r'^(\d+)F$', level)
    if m:
        return 100 + int(m.group(1))
    m = re.match(r'^B(\d+)$', level)
    if m:
        return -int(m.group(1))
    return 999


# ── Symbol formatting (V2) ──

# Floor prefixes used in slab/stair member_ids that need stripping
_SLAB_STAIR_FLOOR_PATTERNS = [
    # Order matters: longer patterns first
    (r'^PHR\d*', 'PHR'),   # PHR1SS1, PHRS13, PHRCS1
    (r'^PH\d+', 'PH'),     # PH2S12, PH2S13
    (r'^PIT', 'PIT'),       # PITS1, PITSS1
    (r'^R(?=S)', 'R'),      # RS11, RSS1 (R before S only)
    (r'^B\d+', 'B'),        # B4SS1, B4S2A, B1S1
    (r'^\d+', 'NUM'),       # 5S12, 10S210, 1RaS1
]


def _strip_floor_prefix(member_id: str, level: str) -> str:
    """Strip floor prefix from slab/stair member_ids.

    Uses the known level to determine what to strip.
    Returns the design type without floor prefix.
    """
    if not member_id:
        return member_id

    # Try each pattern
    for pattern, ptype in _SLAB_STAIR_FLOOR_PATTERNS:
        m = re.match(pattern, member_id)
        if m:
            stripped = member_id[m.end():]
            if stripped:  # Don't strip if nothing left
                return stripped

    return member_id


def _pad_symbol(member_id: str) -> str:
    """Pad the numeric part of a symbol to 3 digits.

    C1 → C001, TC2A → TC002A, G64A → G064A, LB200 → LB200
    """
    if not member_id:
        return member_id

    # Match: alpha prefix + digits + optional alpha suffix
    m = re.match(r'^([A-Za-z]+)(\d+)([A-Za-z]*)$', member_id)
    if m:
        prefix, num, suffix = m.groups()
        padded = num.zfill(3)
        return f'{prefix}{padded}{suffix}'

    # No match (e.g., TWG, Sejong grid names) — return as-is
    return member_id


def _format_symbol(member_id: str, level: str, member_type: str) -> str:
    """Format member_id to V2 symbol: strip floor prefix (slab/stair) + pad number.

    Sejong-specific rules:
    - Footing F1-X1Y1 → strip grid → F001
    - Footing AF-X10Y1 → strip grid → AF
    - Column AC_X10Y1 → strip grid → AC
    - Beam AF-G28 → merge prefix → AFG028 (avoid conflict with regular G28)
    """
    if not member_id:
        return member_id

    # Sejong: grid-based footings/columns — strip grid location
    # Pattern: TYPE-X##Y## or TYPE_X##Y## (TYPE can have digits, e.g., F1, F3)
    # Grid part is stripped; instance numbering handled by _assign_grid_instances()
    m = re.match(r'^([A-Za-z]+\d*)[-_](X\d+p?Y\d+)$', member_id)
    if m:
        return _pad_symbol(m.group(1))  # Keep type only (F1→F001, AF→AF, AC→AC)

    # Sejong: AF- prefixed beams — merge AF with beam type
    # AF-G28 → AFG028, AF-WG2 → AFWG002
    m = re.match(r'^AF-([A-Za-z]+\d+[A-Za-z]*)$', member_id)
    if m:
        return 'AF' + _pad_symbol(m.group(1))  # AFG028, AFWG002

    # Standard: strip floor prefix for slabs and stairs
    if member_type in ('SLAB', 'STAIR'):
        member_id = _strip_floor_prefix(member_id, level)

    return _pad_symbol(member_id)


# ── Bar mark formatting (V2) ──

def _get_position_prefix(bar_position: str, member_type: str, face: str = '') -> str:
    """Get position prefix for diameter in bar mark.

    T=Top, B=Bottom, M=Middle, A=All faces, N=Near, F=Far, D=default
    """
    pos = (bar_position or '').upper()
    mtype = (member_type or '').upper()
    fc = (face or '').upper()

    # Basement wall: use face
    if mtype == 'BASEMENT_WALL':
        if fc == 'INTERIOR':
            return 'N'
        elif fc == 'EXTERIOR':
            return 'F'
        return 'D'

    # Regular wall: All faces (double layer)
    if mtype in ('WALL',):
        return 'A'

    # Beam, slab, footing: use bar_position
    if mtype in ('BEAM', 'SLAB', 'FOOTING'):
        if pos == 'TOP':
            return 'T'
        elif pos == 'BOT' or pos == 'BOTTOM':
            return 'B'
        elif pos == 'MIDDLE' or pos == 'MID':
            return 'M'
        # Stirrups don't have T/B
        return 'D'

    # Column, stair, others: default D
    return 'D'


# ── Grid-based instance assignment (Sejong) ──

def _extract_grid_key(member_id: str) -> tuple | None:
    """Extract (type, X, Y) from grid-based member_id like F1-X3Y2 or AC_X10Y1.

    Returns (type_str, x_num, y_num) for sorting, or None if not grid-based.
    """
    m = re.match(r'^([A-Za-z]+\d*)[-_]X(\d+)(p?)Y(\d+)$', member_id)
    if m:
        type_str, x_num, x_prime, y_num = m.groups()
        # p (prime) adds 0.5 to sort after the base grid
        x_sort = int(x_num) + (0.5 if x_prime else 0)
        return (type_str, x_sort, int(y_num))
    return None


def _assign_grid_instances(rows: list[dict], member_key: str, level_key: str) -> dict[int, str]:
    """Assign sequential instance numbers to grid-based members.

    Sorted by Y ascending, then X ascending within the same Y.
    Returns: {row_index → instance_str}
    """
    # Group by (formatted_type, level)
    groups: dict[tuple, list[tuple[int, float, int]]] = defaultdict(list)
    for i, r in enumerate(rows):
        mid = r.get(member_key, '')
        gk = _extract_grid_key(mid)
        if gk:
            type_str, x_sort, y_num = gk
            level = r.get(level_key, '')
            groups[(type_str, level)].append((i, x_sort, y_num))

    result: dict[int, str] = {}
    for key, entries in groups.items():
        # Sort by Y first, then X
        entries.sort(key=lambda e: (e[2], e[1]))
        for idx, (row_idx, _, _) in enumerate(entries, 1):
            result[row_idx] = str(idx).zfill(3)

    return result


# ── Instance ID assignment ──

def _extract_seg_instance(segment_id: str) -> str | None:
    """Extract instance number from segment_id like 'B1A-GAP006' → '006'."""
    if not segment_id:
        return None
    m = re.search(r'(?:GAP|SEG)(\d+)', segment_id)
    if m:
        return m.group(1).zfill(3)
    return None


def _assign_position_ids(rows: list[dict], member_key: str, x_key: str, y_key: str) -> dict[tuple, str]:
    """Assign instance IDs based on (member_id, x, y) positions.

    Returns: {(member_id, round(x), round(y)) → '001', '002', ...}
    """
    # Collect unique positions per member
    positions_per_member: dict[str, set[tuple[float, float]]] = defaultdict(set)
    for r in rows:
        mid = r.get(member_key, '')
        x = r.get(x_key, '')
        y = r.get(y_key, '')
        if not mid or not x or not y:
            continue
        try:
            fx, fy = round(float(x), 0), round(float(y), 0)
        except (ValueError, TypeError):
            continue
        positions_per_member[mid].add((fx, fy))

    # Sort positions (by x then y) and assign IDs
    result: dict[tuple, str] = {}
    for mid, positions in positions_per_member.items():
        sorted_pos = sorted(positions)
        for idx, (px, py) in enumerate(sorted_pos, 1):
            result[(mid, px, py)] = str(idx).zfill(3)

    return result


# ── Bar Mark ──

def _build_bar_mark(row: dict, serial: int, member_type: str) -> str:
    """Build V2 bar mark: count-PositionDia@spacing-serial.

    count = n_bars for MAIN/ADD, quantity_pieces for stirrups/hoops
    Position = T/B/M/A/N/F/D (from bar_position + member_type + face)
    Dia = diameter value (no 'D' prefix for positioned bars)
    @spacing = only if spacing_mm > 0
    serial = 3-digit floor-based sequential number
    """
    dia = row.get('dia_mm', '')
    try:
        dia_int = int(round(float(dia)))
    except (ValueError, TypeError):
        dia_int = 0

    # Count: n_bars for main, quantity_pieces for stirrups
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

    # Spacing
    spacing = row.get('spacing_mm', '')
    try:
        spacing_int = int(round(float(spacing)))
    except (ValueError, TypeError):
        spacing_int = 0

    # Position prefix
    bar_position = row.get('bar_position', '') or row.get('layer', '') or ''
    face = row.get('face', '')
    prefix = _get_position_prefix(bar_position, member_type, face)

    # Build: count-PrefixDia@spacing-serial
    count_str = str(qty_int) if qty_int > 0 else '0'
    dia_str = f'{prefix}{dia_int}'

    mark = f'{count_str}-{dia_str}'
    if spacing_int > 0:
        mark += f'@{spacing_int}'
    mark += f'-{str(serial).zfill(3)}'

    return mark


# ── Serial number assignment ──

def _assign_serials(rows: list[dict], level_key: str, group_keys: list[str]) -> dict[int, int]:
    """Assign floor-based serial numbers to rows.

    Groups rows by group_keys, then within each group sorts by floor rank.
    Returns: {row_index → serial_number}
    """
    # Group rows by their identity (same bar at different floors)
    groups: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
    for i, r in enumerate(rows):
        key = tuple(r.get(k, '') for k in group_keys)
        groups[key].append((i, r))

    result: dict[int, int] = {}
    for key, group_rows in groups.items():
        # Sort by floor rank (lowest floor first)
        sorted_rows = sorted(group_rows, key=lambda x: _floor_rank(x[1].get(level_key, '')))
        for serial, (idx, _) in enumerate(sorted_rows, 1):
            result[idx] = serial

    return result


# ── Per-file processors ──

def _process_beam(rows: list[dict], building: str, member_spans: list[dict] | None = None) -> list[dict]:
    """Process RebarLengthsBeam.csv — look up instance ID from MembersBeam spans.

    Each rebar bar's (member_id, level, x_start, y_start) is matched to the
    MembersBeam span it falls within. The span's member_instance_id is used.
    """
    # Build lookup: list of (member_id, level, direction, x_min, x_max, y_min, y_max, instance_id)
    span_lookup: list[tuple] = []
    if member_spans:
        for ms in member_spans:
            mid = ms.get('member_id', '')
            level = ms.get('level', '')
            direction = (ms.get('direction', '') or '').upper()
            mid_instance = ms.get('member_instance_id', '')
            try:
                x_from = float(ms.get('x_from_mm', 0) or 0)
                y_from = float(ms.get('y_from_mm', 0) or 0)
                x_to = float(ms.get('x_to_mm', 0) or 0)
                y_to = float(ms.get('y_to_mm', 0) or 0)
            except (ValueError, TypeError):
                continue
            x_min, x_max = min(x_from, x_to), max(x_from, x_to)
            y_min, y_max = min(y_from, y_to), max(y_from, y_to)
            span_lookup.append((mid, level, direction, x_min, x_max, y_min, y_max, mid_instance))

    # Index by (member_id, level) for fast lookup
    from collections import defaultdict as _dd
    span_index: dict[tuple, list] = _dd(list)
    for entry in span_lookup:
        span_index[(entry[0], entry[1])].append(entry)

    # Serial assignment
    serials = _assign_serials(rows, 'level', ['segment_id', 'bar_position', 'bar_role', 'dia_mm', 'bar_type'])

    for i, r in enumerate(rows):
        raw_member_id = r.get('member_id', '')
        raw_level = r.get('level', '')
        floor = _format_floor(raw_level)
        symbol = _format_symbol(raw_member_id, raw_level, "BEAM")

        # Find matching member span using RAW member_id (not formatted symbol)
        instance = _extract_seg_instance(r.get('segment_id', '')) or '000'  # fallback
        mid_instance_id = ''

        try:
            rx = float(r.get('x_start_mm', 0) or 0)
            ry = float(r.get('y_start_mm', 0) or 0)
        except (ValueError, TypeError):
            rx, ry = 0, 0

        candidates = span_index.get((raw_member_id, raw_level), [])
        tol = 500  # mm tolerance (wider for diagonal beams)
        for (_, _, direction, x_min, x_max, y_min, y_max, m_inst) in candidates:
            # Bbox containment check — works for straight and diagonal beams
            if x_min - tol <= rx <= x_max + tol and y_min - tol <= ry <= y_max + tol:
                mid_instance_id = m_inst
                break

        if mid_instance_id:
            r['member_instance_id'] = mid_instance_id
            # Use the member's floor + instance for bar_id
            parts = mid_instance_id.split('-')
            if len(parts) >= 4:
                floor = parts[1]
                instance = parts[3]
        else:
            r['member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'

        serial = serials.get(i, 1)
        bar_mark = _build_bar_mark(r, serial, 'BEAM')
        r['bar_id'] = f'{r["member_instance_id"]}-{bar_mark}'

    return rows


def _process_column(rows: list[dict], building: str, member_rows: list[dict] | None = None) -> list[dict]:
    """Process RebarLengthsColumn.csv — look up instance from MembersColumn by position."""
    # Build member lookup: (member_id, level_from) → list of (x, y, member_instance_id)
    member_lookup: dict[tuple, list] = defaultdict(list)
    if member_rows:
        for mr in member_rows:
            mid = mr.get('member_id', '')
            lvl = mr.get('level_from', '')
            m_inst = mr.get('member_instance_id', '')
            try:
                mx = float(mr.get('x_mm', 0) or 0)
                my = float(mr.get('y_mm', 0) or 0)
            except (ValueError, TypeError):
                mx, my = 0, 0
            member_lookup[(mid, lvl)].append((mx, my, m_inst))

    # Fallback: position-based if no member data
    if not member_rows:
        pos_ids = _assign_position_ids(rows, 'member_id', 'x_start_mm', 'y_start_mm')

    serials = _assign_serials(rows, 'level_from', ['member_id', 'x_start_mm', 'y_start_mm', 'bar_role', 'dia_mm'])

    for i, r in enumerate(rows):
        level = r.get('level_from', '')
        floor = _format_floor(level)
        raw_mid = r.get('member_id', '')
        symbol = _format_symbol(raw_mid, level, "COLUMN")
        raw_mid = r.get('member_id', '')
        symbol = _format_symbol(raw_mid, level, "COLUMN")

        try:
            rx = float(r.get('x_start_mm', 0) or 0)
            ry = float(r.get('y_start_mm', 0) or 0)
        except (ValueError, TypeError):
            rx, ry = 0, 0

        mid_instance_id = ''
        candidates = member_lookup.get((raw_mid, level), [])
        # Fallback: if FOOTING level has no members, try all levels for this member
        if not candidates and level.upper() == 'FOOTING':
            for (m, l), cands in member_lookup.items():
                if m == raw_mid and cands:
                    candidates = cands
                    break
        if candidates:
            best_dist = float('inf')
            best_mid = ''
            for mx, my, m_inst in candidates:
                dist = (rx - mx) ** 2 + (ry - my) ** 2
                if dist < best_dist:
                    best_dist = dist
                    best_mid = m_inst
            mid_instance_id = best_mid
        elif not member_rows:
            px = round(rx, 0)
            py = round(ry, 0)
            instance = pos_ids.get((symbol, px, py), '000')
            mid_instance_id = f'{building}-{floor}-{symbol}-{instance}'

        if mid_instance_id:
            r['member_instance_id'] = mid_instance_id
        else:
            r['member_instance_id'] = f'{building}-{floor}-{symbol}-000'

        serial = serials.get(i, 1)
        bar_mark = _build_bar_mark(r, serial, 'COLUMN')
        r['bar_id'] = f'{r["member_instance_id"]}-{bar_mark}'

    return rows


def _process_wall(rows: list[dict], building: str, member_rows: list[dict] | None = None, member_type_override: str = 'WALL') -> list[dict]:
    """Process RebarLengthsWall.csv — look up instance from MembersWall by position containment."""
    member_key = 'wall_mark' if any(r.get('wall_mark') for r in rows[:10]) else 'member_id'

    # Build member lookup: (wall_mark, level) → list of (centroid_x, centroid_y, member_instance_id)
    member_lookup: dict[tuple, list] = defaultdict(list)
    if member_rows:
        mk = 'wall_mark' if any(r.get('wall_mark') for r in member_rows[:10]) else 'member_id'
        for mr in member_rows:
            wm = mr.get(mk, '')
            lvl = mr.get('level', '')
            mid = mr.get('member_instance_id', '')
            try:
                cx = float(mr.get('centroid_x_mm', 0) or 0)
                cy = float(mr.get('centroid_y_mm', 0) or 0)
            except (ValueError, TypeError):
                cx, cy = 0, 0
            member_lookup[(wm, lvl)].append((cx, cy, mid))

    serials = _assign_serials(rows, 'level', [member_key, 'mesh_origin_x_mm', 'mesh_origin_y_mm', 'bar_role', 'dia_mm'])

    for i, r in enumerate(rows):
        floor = _format_floor(r.get('level', ''))
        raw_mid = r.get(member_key, '')
        symbol = _format_symbol(raw_mid, r.get('level', ''), "WALL")
        raw_mid = r.get(member_key, '')
        symbol = _format_symbol(raw_mid, r.get('level', ''), "WALL")
        level = r.get('level', '')

        # Find closest member by position
        instance = '000'
        try:
            rx = float(r.get('mesh_origin_x_mm', 0) or 0)
            ry = float(r.get('mesh_origin_y_mm', 0) or 0)
        except (ValueError, TypeError):
            rx, ry = 0, 0

        mid_instance_id = ''
        candidates = member_lookup.get((raw_mid, level), [])
        if candidates:
            best_dist = float('inf')
            best_mid = ''
            for cx, cy, mid in candidates:
                dist = (rx - cx) ** 2 + (ry - cy) ** 2
                if dist < best_dist:
                    best_dist = dist
                    best_mid = mid
            mid_instance_id = best_mid

        r['member_instance_id'] = mid_instance_id or f'{building}-{floor}-{symbol}-000'

        serial = serials.get(i, 1)
        bar_mark = _build_bar_mark(r, serial, member_type_override)
        r['bar_id'] = f'{r["member_instance_id"]}-{bar_mark}'

    return rows


def _process_basement_wall(rows: list[dict], building: str, member_rows: list[dict] | None = None) -> list[dict]:
    """Process RebarLengthsBasementWall.csv — same as wall but with BASEMENT_WALL member_type."""
    return _process_wall(rows, building, member_rows=member_rows, member_type_override='BASEMENT_WALL')


def _process_slab(rows: list[dict], building: str, member_rows: list[dict] | None = None) -> list[dict]:
    """Process RebarLengthsSlab.csv — look up instance from MembersSlab by position."""
    # Build member lookup: (member_id, level) → list of (centroid_x, centroid_y, member_instance_id)
    member_lookup: dict[tuple, list] = defaultdict(list)
    if member_rows:
        for mr in member_rows:
            mid = mr.get('member_id', '')
            lvl = mr.get('level', '')
            m_inst = mr.get('member_instance_id', '')
            try:
                cx = float(mr.get('centroid_x_mm', 0) or 0)
                cy = float(mr.get('centroid_y_mm', 0) or 0)
            except (ValueError, TypeError):
                cx, cy = 0, 0
            member_lookup[(mid, lvl)].append((cx, cy, m_inst))

    serials = _assign_serials(rows, 'level', ['member_id', 'mesh_origin_x_mm', 'mesh_origin_y_mm', 'bar_role', 'dia_mm'])

    for i, r in enumerate(rows):
        floor = _format_floor(r.get('level', ''))
        raw_mid = r.get('member_id', '')
        symbol = _format_symbol(raw_mid, r.get('level', ''), "SLAB")
        raw_mid = r.get('member_id', '')
        symbol = _format_symbol(raw_mid, r.get('level', ''), "SLAB")
        level = r.get('level', '')

        try:
            rx = float(r.get('mesh_origin_x_mm', 0) or 0)
            ry = float(r.get('mesh_origin_y_mm', 0) or 0)
        except (ValueError, TypeError):
            rx, ry = 0, 0

        mid_instance_id = ''
        candidates = member_lookup.get((raw_mid, level), [])
        if candidates:
            best_dist = float('inf')
            best_mid = ''
            for cx, cy, m_inst in candidates:
                dist = (rx - cx) ** 2 + (ry - cy) ** 2
                if dist < best_dist:
                    best_dist = dist
                    best_mid = m_inst
            mid_instance_id = best_mid

        r['member_instance_id'] = mid_instance_id or f'{building}-{floor}-{symbol}-000'

        serial = serials.get(i, 1)
        bar_mark = _build_bar_mark(r, serial, 'SLAB')
        r['bar_id'] = f'{r["member_instance_id"]}-{bar_mark}'

    return rows


def _process_footing(rows: list[dict], building: str, member_rows: list[dict] | None = None) -> list[dict]:
    """Process RebarLengthsFooting.csv — look up instance from MembersFooting."""
    has_level = any(r.get('level') for r in rows[:20])

    # Build member lookup: member_id → list of (centroid_x, centroid_y, level, member_instance_id)
    member_lookup: dict[str, list] = defaultdict(list)
    if member_rows:
        for mr in member_rows:
            mid = mr.get('member_id', '')
            m_inst = mr.get('member_instance_id', '')
            m_level = mr.get('level', '')
            try:
                cx = float(mr.get('centroid_x_mm', 0) or 0)
                cy = float(mr.get('centroid_y_mm', 0) or 0)
            except (ValueError, TypeError):
                cx, cy = 0, 0
            member_lookup[mid].append((cx, cy, m_level, m_inst))

    if has_level:
        serials = _assign_serials(rows, 'level', ['member_id', 'mesh_origin_x_mm', 'mesh_origin_y_mm', 'bar_role', 'dia_mm'])

    for i, r in enumerate(rows):
        raw_mid = r.get('member_id', '')
        symbol = _format_symbol(raw_mid, r.get("level", ""), "FOOTING")
        raw_mid = r.get('member_id', '')
        symbol = _format_symbol(raw_mid, r.get("level", ""), "FOOTING")

        try:
            rx = float(r.get('mesh_origin_x_mm', 0) or 0)
            ry = float(r.get('mesh_origin_y_mm', 0) or 0)
        except (ValueError, TypeError):
            rx, ry = 0, 0

        mid_instance_id = ''
        candidates = member_lookup.get(raw_mid, [])
        if candidates:
            best_dist = float('inf')
            best_mid = ''
            for cx, cy, m_lvl, m_inst in candidates:
                dist = (rx - cx) ** 2 + (ry - cy) ** 2
                if dist < best_dist:
                    best_dist = dist
                    best_mid = m_inst
            mid_instance_id = best_mid

        if not mid_instance_id:
            floor = _format_floor(r.get('level', '')) if has_level else 'FTG'
            if floor == 'UNK': floor = 'FTG'
            mid_instance_id = f'{building}-{floor}-{symbol}-001'

        r['member_instance_id'] = mid_instance_id

        serial = serials.get(i, 1) if has_level else 1
        bar_mark = _build_bar_mark(r, serial, 'FOOTING')
        r['bar_id'] = f'{r["member_instance_id"]}-{bar_mark}'

    return rows


def _process_stair(rows: list[dict], building: str) -> list[dict]:
    """Process RebarLengthsStair.csv — segment_id provides instance ID."""
    # Stair level is story_group (e.g., 'B5~B4'), use the lower floor
    for r in rows:
        sg = r.get('story_group', '')
        if '~' in sg:
            r['_stair_level'] = sg.split('~')[0]
        else:
            r['_stair_level'] = sg

    serials = _assign_serials(rows, '_stair_level', ['segment_id', 'dia_mm'])

    for i, r in enumerate(rows):
        floor = _format_floor(r.get('_stair_level', ''))
        raw_mid = r.get('member_id', '')
        symbol = _format_symbol(raw_mid, r.get('_stair_level', ''), "STAIR")
        raw_mid = r.get('member_id', '')
        symbol = _format_symbol(raw_mid, r.get('_stair_level', ''), "STAIR")
        instance = _extract_seg_instance(r.get('segment_id', '')) or '000'
        r['member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
        serial = serials.get(i, 1)
        bar_mark = _build_bar_mark(r, serial, 'STAIR')
        r['bar_id'] = f'{r["member_instance_id"]}-{bar_mark}'
        # Clean up temp field
        del r['_stair_level']

    return rows


# ── Members processors (member_instance_id only, no bar_id/bar_mark) ──


def _process_member_beam(rows: list[dict], building: str, **kwargs) -> list[dict]:
    """MembersBeam.csv — assign globally unique member_instance_id per physical beam.

    This is the SOURCE OF TRUTH. RebarLengthsBeam will look up from here.

    Each physical beam span gets a unique instance number within (member_id, level).
    Sorted by x_from first, then y_from (left-to-right, bottom-to-top).
    """
    # Group by (member_id, level) — each group gets unique sequential numbering
    groups: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
    for i, r in enumerate(rows):
        mid = r.get('member_id', '')
        level = r.get('level', '')
        groups[(mid, level)].append((i, r))

    row_instance: dict[int, str] = {}
    for (mid, level), entries in groups.items():
        # Sort by x_from, then y_from (natural reading order)
        entries.sort(key=lambda e: (
            float(e[1].get('x_from_mm', 0) or 0),
            float(e[1].get('y_from_mm', 0) or 0),
        ))
        for idx, (row_idx, r) in enumerate(entries, 1):
            row_instance[row_idx] = str(idx).zfill(3)

    for i, r in enumerate(rows):
        floor = _format_floor(r.get('level', ''))
        raw_mid = r.get('member_id', '')
        symbol = _format_symbol(raw_mid, r.get('level', ''), "BEAM")
        raw_mid = r.get('member_id', '')
        symbol = _format_symbol(raw_mid, r.get('level', ''), "BEAM")
        instance = row_instance.get(i, '000')
        r['member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
    return rows


def _process_member_column(rows: list[dict], building: str) -> list[dict]:
    """MembersColumn.csv — grid-based (Sejong AC_) or position-based."""
    has_grid = any(_extract_grid_key(r.get('member_id', '')) for r in rows[:20])
    grid_instances = _assign_grid_instances(rows, 'member_id', 'level_from') if has_grid else {}
    pos_ids = _assign_position_ids(rows, 'member_id', 'x_mm', 'y_mm') if not has_grid else {}

    for i, r in enumerate(rows):
        floor = _format_floor(r.get('level_from', r.get('level', '')))
        raw_mid = r.get('member_id', '')
        symbol = _format_symbol(raw_mid, r.get('level_from', r.get('level', '')), "COLUMN")

        if i in grid_instances:
            instance = grid_instances[i]
        else:
            try:
                px = round(float(r.get('x_mm', 0)), 0)
                py = round(float(r.get('y_mm', 0)), 0)
            except (ValueError, TypeError):
                px, py = 0, 0
            instance = pos_ids.get((raw_mid, px, py), '000')

        r['member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
    return rows


def _process_member_wall(rows: list[dict], building: str) -> list[dict]:
    """MembersWall.csv — unique instance per quad panel.

    With segment_id (Sejong): extract instance from segment_id.
    Without segment_id (Buldang/Cheongdam): assign sequential per (wall_mark, level),
    sorted by centroid position (x then y). Each quad panel gets a unique instance.
    """
    member_key = 'wall_mark' if any(r.get('wall_mark') for r in rows[:10]) else 'member_id'
    has_seg = any(r.get('segment_id') for r in rows[:20])

    if has_seg:
        for r in rows:
            floor = _format_floor(r.get('level', ''))
            raw_mid = r.get(member_key, '')
            symbol = _format_symbol(raw_mid, r.get('level', ''), "WALL")
            instance = _extract_seg_instance(r.get('segment_id', '')) or '000'
            r['member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
    else:
        # Group by (wall_mark, level), assign sequential instance per panel
        groups: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
        for i, r in enumerate(rows):
            wm = r.get(member_key, '')
            level = r.get('level', '')
            groups[(wm, level)].append((i, r))

        row_instance: dict[int, str] = {}
        for (wm, level), entries in groups.items():
            # Sort by centroid x then y
            entries.sort(key=lambda e: (
                float(e[1].get('centroid_x_mm', 0) or 0),
                float(e[1].get('centroid_y_mm', 0) or 0),
            ))
            for idx, (row_idx, r) in enumerate(entries, 1):
                row_instance[row_idx] = str(idx).zfill(3)

        for i, r in enumerate(rows):
            floor = _format_floor(r.get('level', ''))
            raw_mid = r.get(member_key, '')
            symbol = _format_symbol(raw_mid, r.get('level', ''), "WALL")
            instance = row_instance.get(i, '000')
            r['member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
    return rows


def _process_member_basement_wall(rows: list[dict], building: str) -> list[dict]:
    """MembersBasementWall.csv — from panel_no or position."""
    has_panel = any(r.get('panel_no') for r in rows[:20])
    if has_panel:
        for r in rows:
            floor = _format_floor(r.get('level', ''))
            raw_mid = r.get('wall_mark', '')
            symbol = _format_symbol(raw_mid, r.get('level', ''), "BASEMENT_WALL")
            raw_mid = r.get('wall_mark', '')
            symbol = _format_symbol(raw_mid, r.get('level', ''), "BASEMENT_WALL")
            instance = str(r.get('panel_no', '0')).zfill(3)
            r['member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
    else:
        pos_ids = _assign_position_ids(rows, 'wall_mark', 'centroid_x_mm', 'centroid_y_mm')
        for r in rows:
            floor = _format_floor(r.get('level', ''))
            raw_mid = r.get('wall_mark', '')
            symbol = _format_symbol(raw_mid, r.get('level', ''), "BASEMENT_WALL")
            raw_mid = r.get('wall_mark', '')
            symbol = _format_symbol(raw_mid, r.get('level', ''), "BASEMENT_WALL")
            try:
                px = round(float(r.get('centroid_x_mm', 0)), 0)
                py = round(float(r.get('centroid_y_mm', 0)), 0)
            except (ValueError, TypeError):
                px, py = 0, 0
            instance = pos_ids.get((symbol, px, py), '000')
            r['member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
    return rows


def _process_member_slab(rows: list[dict], building: str) -> list[dict]:
    """MembersSlab.csv — from segment_id or sequential per (member_id, level).

    With segment_id (Sejong): extract instance from segment_id.
    Without segment_id (Buldang/Cheongdam): assign sequential per (member_id, level),
    sorted by centroid position (x then y). Each slab panel gets a unique instance.
    """
    has_seg = any(r.get('segment_id') for r in rows[:20])
    if has_seg:
        for r in rows:
            floor = _format_floor(r.get('level', ''))
            raw_mid = r.get('member_id', '')
            symbol = _format_symbol(raw_mid, r.get('level', ''), "SLAB")
            instance = _extract_seg_instance(r.get('segment_id', '')) or '000'
            r['member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
    else:
        groups: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
        for i, r in enumerate(rows):
            mid = r.get('member_id', '')
            level = r.get('level', '')
            groups[(mid, level)].append((i, r))

        row_instance: dict[int, str] = {}
        for (mid, level), entries in groups.items():
            entries.sort(key=lambda e: (
                float(e[1].get('centroid_x_mm', 0) or 0),
                float(e[1].get('centroid_y_mm', 0) or 0),
            ))
            for idx, (row_idx, r) in enumerate(entries, 1):
                row_instance[row_idx] = str(idx).zfill(3)

        for i, r in enumerate(rows):
            floor = _format_floor(r.get('level', ''))
            raw_mid = r.get('member_id', '')
            symbol = _format_symbol(raw_mid, r.get('level', ''), "SLAB")
            instance = row_instance.get(i, '001')
            r['member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
    return rows


def _process_member_footing(rows: list[dict], building: str) -> list[dict]:
    """MembersFooting.csv — grid-based (Sejong) or position-based."""
    has_seg = any(r.get('segment_id') for r in rows[:20])
    has_level = any(r.get('level') for r in rows[:20])
    has_grid = any(_extract_grid_key(r.get('member_id', '')) for r in rows[:20])

    # Grid-based instances (Sejong: F1-X1Y1 sorted by Y then X)
    grid_instances = _assign_grid_instances(rows, 'member_id', 'level') if has_grid else {}

    if has_seg and not has_grid:
        for r in rows:
            floor = _format_floor(r.get('level', '')) if has_level else 'FTG'
            if floor == 'UNK': floor = 'FTG'
            raw_mid = r.get('member_id', '')
            symbol = _format_symbol(raw_mid, r.get('level', ''), "FOOTING")
            instance = _extract_seg_instance(r.get('segment_id', '')) or '000'
            r['member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
    else:
        pos_ids = _assign_position_ids(rows, 'member_id', 'centroid_x_mm', 'centroid_y_mm') if not has_grid else {}
        for i, r in enumerate(rows):
            floor = _format_floor(r.get('level', '')) if has_level else 'FTG'
            if floor == 'UNK': floor = 'FTG'
            raw_mid = r.get('member_id', '')
            symbol = _format_symbol(raw_mid, r.get('level', ''), "FOOTING")

            if i in grid_instances:
                instance = grid_instances[i]
            else:
                try:
                    px = round(float(r.get('centroid_x_mm', 0)), 0)
                    py = round(float(r.get('centroid_y_mm', 0)), 0)
                except (ValueError, TypeError):
                    px, py = 0, 0
                instance = pos_ids.get((raw_mid, px, py), '000')
            r['member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
    return rows


def _process_member_stair(rows: list[dict], building: str) -> list[dict]:
    """MembersStair.csv — segment_id provides instance ID."""
    for r in rows:
        sg = r.get('story_group', '')
        level = sg.split('~')[0] if '~' in sg else r.get('level_from', sg)
        floor = _format_floor(level)
        raw_mid = r.get('member_id', '')
        symbol = _format_symbol(raw_mid, level, "STAIR")
        raw_mid = r.get('member_id', '')
        symbol = _format_symbol(raw_mid, level, "STAIR")
        instance = _extract_seg_instance(r.get('segment_id', '')) or '000'
        r['member_instance_id'] = f'{building}-{floor}-{symbol}-{instance}'
    return rows


# ── File mapping ──

FILE_PROCESSORS = {
    'RebarLengthsBeam.csv': _process_beam,
    'RebarLengthsColumn.csv': _process_column,
    'RebarLengthsWall.csv': _process_wall,
    'RebarLengthsBasementWall.csv': _process_basement_wall,
    'RebarLengthsSlab.csv': _process_slab,
    'RebarLengthsFooting.csv': _process_footing,
    'RebarLengthsStair.csv': _process_stair,
}

MEMBER_PROCESSORS = {
    'MembersBeam.csv': _process_member_beam,
    'MembersColumn.csv': _process_member_column,
    'MembersWall.csv': _process_member_wall,
    'MembersBasementWall.csv': _process_member_basement_wall,
    'MembersSlab.csv': _process_member_slab,
    'MembersFooting.csv': _process_member_footing,
    'MembersStair.csv': _process_member_stair,
}


def process_folder(input_folder: str, output_folder: str, building: str = '001'):
    """Add bar_id + member_instance_id to all CSVs in input_folder, write to output_folder."""
    os.makedirs(output_folder, exist_ok=True)

    # ── Phase 1: Process Members CSVs first (source of truth for instance IDs) ──
    member_data: dict[str, list[dict]] = {}  # filename → processed rows

    for filename, processor in MEMBER_PROCESSORS.items():
        input_path = os.path.join(input_folder, filename)
        output_path = os.path.join(output_folder, filename)

        if not os.path.exists(input_path):
            print(f'  SKIP {filename} (not found)')
            continue

        with open(input_path, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)

        print(f'  Processing {filename}: {len(rows)} rows ...', end=' ')
        rows = processor(rows, building)
        member_data[filename] = rows

        # Write with member_instance_id as first column
        out_fields = ['member_instance_id'] + fieldnames
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)

        if rows:
            print(f'done. Sample: {rows[0].get("member_instance_id", "?")}')
        else:
            print('done (empty).')

    # ── Phase 2: Process RebarLengths CSVs (look up instance ID from Members) ──
    for filename, processor in FILE_PROCESSORS.items():
        input_path = os.path.join(input_folder, filename)
        output_path = os.path.join(output_folder, filename)

        if not os.path.exists(input_path):
            print(f'  SKIP {filename} (not found)')
            continue

        with open(input_path, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)

        print(f'  Processing {filename}: {len(rows)} rows ...', end=' ')

        # Pass corresponding Member data to rebar processors
        member_map = {
            'RebarLengthsBeam.csv': ('MembersBeam.csv', 'member_spans'),
            'RebarLengthsColumn.csv': ('MembersColumn.csv', 'member_rows'),
            'RebarLengthsWall.csv': ('MembersWall.csv', 'member_rows'),
            'RebarLengthsBasementWall.csv': ('MembersBasementWall.csv', 'member_rows'),
            'RebarLengthsSlab.csv': ('MembersSlab.csv', 'member_rows'),
            'RebarLengthsFooting.csv': ('MembersFooting.csv', 'member_rows'),
        }
        if filename in member_map:
            mfile, kwarg = member_map[filename]
            rows = processor(rows, building, **{kwarg: member_data.get(mfile)})
        else:
            rows = processor(rows, building)

        # member_instance_id is already set by the processor from Members lookup.
        # bar_id is built as: member_instance_id + bar_mark by each processor.
        # For processors without Members data (stair), derive from bar_id.
        for r in rows:
            if not r.get('member_instance_id'):
                bid = r.get('bar_id', '')
                if bid:
                    segs = bid.split('-')
                    r['member_instance_id'] = '-'.join(segs[:4]) if len(segs) >= 4 else bid
                else:
                    r['member_instance_id'] = ''

        # Write with bar_id + member_instance_id as first columns
        out_fields = ['bar_id', 'member_instance_id'] + fieldnames
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)

        if rows:
            print(f'done. Sample: {rows[0].get("bar_id", "?")}')
        else:
            print('done (empty).')

    # Copy remaining CSVs as-is
    all_processed = set(FILE_PROCESSORS.keys()) | set(MEMBER_PROCESSORS.keys())
    for f in os.listdir(input_folder):
        if f.endswith('.csv') and f not in all_processed:
            src = os.path.join(input_folder, f)
            dst = os.path.join(output_folder, f)
            shutil.copy2(src, dst)
            print(f'  COPY {f}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Add bar_id column to converted rebar CSVs')
    parser.add_argument('input_folder', help='Path to converted CSV folder')
    parser.add_argument('output_folder', help='Path to output folder (new CSVs with bar_id)')
    parser.add_argument('--building', default='001', help='Building code (default: 001)')
    args = parser.parse_args()

    print(f'Input:    {args.input_folder}')
    print(f'Output:   {args.output_folder}')
    print(f'Building: {args.building}')
    print()

    process_folder(args.input_folder, args.output_folder, args.building)
    print('\nDone!')
