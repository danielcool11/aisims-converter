"""
MIDAS Gen Text (.mgt) file parser.
Extracts project-level configuration data:
  - Rebar material grades (per diameter)
  - Wall mark definitions
  - Unit system
  - Story definitions

NOTE: MGT *REBAR-BEAM/COLUMN/WALL sections are INPUT configuration,
NOT verified design output. Do not use for member reinforcement data.
Use Design CSV output instead (see Part A Findings).
"""

import re
from typing import Optional


def parse_mgt(filepath: str) -> dict:
    """
    Parse MGT file and extract project-level configuration.

    Returns:
        dict with keys:
            'unit': str — unit system (e.g., 'KN, MM')
            'rebar_grades': dict — {fy_mpa: [diameter_list]}
            'wall_marks': dict — {mark_name: [wall_id_list]}
            'stories': list — [{name, level_mm}]
            'design_materials': list — [{mat_id, name, main_rebar, stirrup_rebar, fy_main, fy_stirrup}]
    """
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()

    result = {
        'unit': None,
        'rebar_grades': {},
        'wall_marks': {},
        'stories': [],
        'design_materials': [],
    }

    # Find section boundaries
    sections = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('*') and not stripped.startswith(';'):
            keyword = stripped.split()[0] if stripped.split() else stripped
            sections[keyword] = i

    # Parse *UNIT
    if '*UNIT' in sections:
        idx = sections['*UNIT']
        for j in range(idx + 1, min(idx + 5, len(lines))):
            line = lines[j].strip()
            if line and not line.startswith(';') and not line.startswith('*'):
                result['unit'] = line
                break

    # Parse *REBAR-MATL-CODE
    if '*REBAR-MATL-CODE' in sections:
        idx = sections['*REBAR-MATL-CODE']
        for j in range(idx + 1, min(idx + 5, len(lines))):
            line = lines[j].strip()
            if line and not line.startswith(';') and not line.startswith('*'):
                result['default_rebar_code'] = line
                break

    # Parse *WALLMARK
    if '*WALLMARK' in sections:
        idx = sections['*WALLMARK']
        for j in range(idx + 1, len(lines)):
            line = lines[j].strip()
            if line.startswith('*'):
                break
            if line.startswith(';') or not line:
                continue
            # Format: MARKNAME, WID_LIST
            parts = [p.strip() for p in line.split(',', 1)]
            if len(parts) == 2:
                mark_name = parts[0]
                wid_str = parts[1]
                # Parse wall IDs: "11to14" or "111 112" or "2001to2009"
                wall_ids = _parse_id_list(wid_str)
                result['wall_marks'][mark_name] = wall_ids

    # Parse *DGNCRITERIA — rebar grades per diameter
    if '*DGNCRITERIA' in sections:
        idx = sections['*DGNCRITERIA']
        for j in range(idx + 1, len(lines)):
            line = lines[j].strip()
            if line.startswith('*'):
                break
            if line.startswith(';') or not line:
                continue
            # Look for lines with V/H, rebar, grade, fy pattern
            # Format: V, D10, SD400, 0.4, V, D13, SD400, 0.4, ...
            parts = [p.strip() for p in line.split(',')]
            i = 0
            while i + 3 < len(parts):
                direction = parts[i].strip()
                if direction in ('V', 'H'):
                    rebar = parts[i + 1].strip()
                    grade = parts[i + 2].strip()
                    fy_str = parts[i + 3].strip()
                    try:
                        fy = float(fy_str)
                        fy_mpa = int(fy * 1000) if fy < 10 else int(fy)  # 0.4 → 400, 400 → 400
                        dia_match = re.search(r'D(\d+)', rebar)
                        if dia_match:
                            dia = int(dia_match.group(1))
                            if fy_mpa not in result['rebar_grades']:
                                result['rebar_grades'][fy_mpa] = []
                            if dia not in result['rebar_grades'][fy_mpa]:
                                result['rebar_grades'][fy_mpa].append(dia)
                    except (ValueError, IndexError):
                        pass
                    i += 4
                else:
                    i += 1

    # Parse *DGN-MATL — design material assignments
    if '*DGN-MATL' in sections:
        idx = sections['*DGN-MATL']
        for j in range(idx + 1, len(lines)):
            line = lines[j].strip()
            if line.startswith('*'):
                break
            if line.startswith(';') or not line:
                continue
            # Extract: mat_id, type, name, ... rebar codes
            parts = [p.strip().strip('"') for p in line.split(',')]
            if len(parts) >= 3 and parts[1].strip().upper() == 'CONC':
                mat_entry = {
                    'mat_id': parts[0].strip(),
                    'name': parts[2].strip(),
                }
                # Look for SD### patterns in the line
                sd_matches = re.findall(r'SD(\d+)', line)
                if len(sd_matches) >= 2:
                    mat_entry['main_rebar'] = f'SD{sd_matches[0]}'
                    mat_entry['stirrup_rebar'] = f'SD{sd_matches[1]}'
                elif len(sd_matches) == 1:
                    mat_entry['main_rebar'] = f'SD{sd_matches[0]}'
                    mat_entry['stirrup_rebar'] = f'SD{sd_matches[0]}'

                # Look for fy values (0.55, 0.5 format)
                fy_matches = re.findall(r'(?:^|,)\s*(0\.\d+)\s*(?:,|$)', line)
                if len(fy_matches) >= 2:
                    mat_entry['fy_main'] = float(fy_matches[0]) * 1000
                    mat_entry['fy_stirrup'] = float(fy_matches[1]) * 1000

                result['design_materials'].append(mat_entry)

    return result


def _parse_id_list(id_str: str) -> list:
    """
    Parse MIDAS ID list format.
    "11to14"     → [11, 12, 13, 14]
    "111 112"    → [111, 112]
    "2001to2009" → [2001, 2002, ..., 2009]
    "131to134"   → [131, 132, 133, 134]
    """
    ids = []
    parts = id_str.split()
    for part in parts:
        if 'to' in part:
            m = re.match(r'(\d+)to(\d+)', part)
            if m:
                start = int(m.group(1))
                end = int(m.group(2))
                ids.extend(range(start, end + 1))
        else:
            try:
                ids.append(int(part))
            except ValueError:
                pass
    return ids
