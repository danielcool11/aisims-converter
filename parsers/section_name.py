"""
Section name parser for MIDAS Gen section names.
Handles multiple Korean structural naming conventions.

Examples:
    "6C1"       → level_from=6F, member_id=C1, member_type=COLUMN
    "3~4TC1"    → level_from=3F, level_to=4F, member_id=TC1, member_type=COLUMN
    "-2~-1TC1"  → level_from=B2, level_to=B1, member_id=TC1, member_type=COLUMN
    "RG1"       → level_from=Roof, member_id=G1, member_type=BEAM
    "LB1"       → member_id=LB1, member_type=BEAM
    "PHRWG1"    → level_from=PHR, member_id=WG1, member_type=BEAM (needs clarification)
"""

import re


# Prefix → member_type mapping (order matters — longer prefixes first)
_PREFIX_MAP = [
    ('TCG', 'BEAM'),       # Transfer Cantilever Girder (must be before TC)
    ('TC', 'COLUMN'),      # Transfer Column
    ('RCG', 'BEAM'),       # Roof Cantilever Girder
    ('WCG', 'BEAM'),       # Wall Cantilever Girder
    ('CG', 'BEAM'),        # Cantilever Girder (must be before CB, C)
    ('CB', 'BEAM'),        # Cantilever Beam (must be before C)
    ('C', 'COLUMN'),       # Column (after CG/CB to avoid false match)
    ('BT', 'WALL'),        # Buttress (부벽/벽기둥 — thick wall for lateral support)
    ('RWG', 'BEAM'),       # Roof Wall Girder
    ('WG', 'BEAM'),        # Wall Girder
    ('RWB', 'BEAM'),       # Roof Wall Beam
    ('WB', 'BEAM'),        # Wall Beam
    ('PHRW', 'BEAM'),      # Penthouse Roof Wall (unconfirmed)
    ('TB', 'BEAM'),        # Transfer Beam
    ('TG', 'BEAM'),        # Transfer Girder
    ('RG', 'BEAM'),        # Roof Girder
    ('G', 'BEAM'),         # Girder
    ('RB', 'BEAM'),        # Roof Beam
    ('B', 'BEAM'),         # Beam
    ('LB', 'BEAM'),        # Load Bearing (modeled as frame)
]


def classify_prefix(member_id: str) -> str:
    """Classify member type from ID prefix."""
    upper = member_id.upper()
    for prefix, mtype in _PREFIX_MAP:
        if upper.startswith(prefix):
            return mtype
    return 'BEAM'  # default


def normalize_level(level_str: str) -> str:
    """
    Convert level number to standard level name.
    Positive → floor (6 → 6F)
    Negative → basement (-2 → B2)
    """
    try:
        num = int(level_str)
        if num < 0:
            return f'B{abs(num)}'
        else:
            return f'{num}F'
    except (ValueError, TypeError):
        return str(level_str)


def parse_section_name(name: str) -> dict:
    """
    Parse MIDAS section name into structured data.

    Returns dict with:
        member_id: str      — member identifier (C1, TC1, G1, LB1)
        member_type: str    — COLUMN / BEAM
        level_from: str     — start level (6F, B2, Roof, PHR) or None
        level_to: str       — end level or None (for ranges like 3~4)
        raw_name: str       — original name

    Patterns handled:
        1. "6C1"           → single floor + member
        2. "3~4TC1"        → floor range + member
        3. "-2~-1TC1"      → basement range + member
        4. "RG1"           → R/PH/PHR prefix + member
        5. "PHRWG1"        → composite prefix + member
        6. "LB1"           → simple member (no level)
    """

    result = {
        'member_id': name,
        'member_type': 'UNKNOWN',
        'level_from': None,
        'level_to': None,
        'raw_name': name,
    }

    if not name or name.strip() == '':
        return result

    name = name.strip()

    # Skip separators and dummies
    if '===' in name or name.upper() == 'DM':
        result['member_type'] = 'SKIP'
        return result

    # Pattern 1: level_range + member — "3~4TC1", "-2~-1TC1", "-4~-3BT1"
    m = re.match(r'^(-?\d+)~(-?\d+)([A-Za-z]+\d+[A-Za-z]*)$', name)
    if m:
        result['level_from'] = normalize_level(m.group(1))
        result['level_to'] = normalize_level(m.group(2))
        result['member_id'] = m.group(3)
        result['member_type'] = classify_prefix(m.group(3))
        return result

    # Pattern 2: single_floor + member — "6C1", "-3BT1", "1G1"
    m = re.match(r'^(-?\d+)([A-Za-z]+\d+[A-Za-z]*)$', name)
    if m:
        result['level_from'] = normalize_level(m.group(1))
        result['member_id'] = m.group(2)
        result['member_type'] = classify_prefix(m.group(2))
        return result

    # Pattern 3: R/PH/PHR prefix + member — "RG1", "PHRWG1", "PHG1"
    m = re.match(r'^(PHR?|R)([A-Za-z]*\d+[A-Za-z]*)$', name)
    if m:
        prefix = m.group(1)
        rest = m.group(2)
        result['level_from'] = 'Roof' if prefix == 'R' else prefix
        result['member_id'] = rest
        result['member_type'] = classify_prefix(rest)
        return result

    # Pattern 4: simple member — "LB1", "WG1", "CG11"
    m = re.match(r'^([A-Za-z]+\d+[A-Za-z]*)$', name)
    if m:
        result['member_id'] = m.group(1)
        result['member_type'] = classify_prefix(m.group(1))
        return result

    # Fallback — couldn't parse
    result['member_type'] = 'UNKNOWN'
    return result
