"""
Section name parser for MIDAS Gen section names.
Handles multiple Korean structural naming conventions across projects.

Project 1 style (no space):
    "6C1"       -> level_from=6F, member_id=C1, member_type=COLUMN
    "3~4TC1"    -> level_from=3F, level_to=4F, member_id=TC1
    "-2~-1TC1"  -> level_from=B2, level_to=B1, member_id=TC1
    "RG1"       -> level_from=Roof, member_id=G1
    "LB1"       -> member_id=LB1

Project 2 style (space-separated):
    "1 B1"          -> level_from=1F, member_id=B1
    "-1~-4 G1"      -> level_from=B1, level_to=B4, member_id=G1
    "P G1"          -> level_from=PH, member_id=G1
    "3~R LB1"       -> level_from=3F, level_to=Roof, member_id=LB1
    "TC1 (1-P)"     -> member_id=TC1, level_from=1F, level_to=PH
    "C1 (B5-B1)"    -> member_id=C1, level_from=B5, level_to=B1
    "P G8A (sayOK)" -> level_from=PH, member_id=G8A (annotation stripped)
"""

import re


# Prefix -> member_type mapping (order matters — longer prefixes first)
_PREFIX_MAP = [
    ('TCG', 'BEAM'),       # Transfer Cantilever Girder (must be before TC)
    ('TC', 'COLUMN'),      # Transfer Column
    ('RCG', 'BEAM'),       # Roof Cantilever Girder
    ('WCG', 'BEAM'),       # Wall Cantilever Girder
    ('CG', 'BEAM'),        # Cantilever Girder (must be before CB, C)
    ('CB', 'BEAM'),        # Cantilever Beam (must be before C)
    ('C', 'COLUMN'),       # Column (after CG/CB to avoid false match)
    ('BT', 'WALL'),        # Buttress (thick wall for lateral support)
    ('TWG', 'BEAM'),       # Transfer Wall Girder
    ('RWG', 'BEAM'),       # Roof Wall Girder
    ('WG', 'BEAM'),        # Wall Girder
    ('RWB', 'BEAM'),       # Roof Wall Beam
    ('WB', 'BEAM'),        # Wall Beam
    ('PHRW', 'BEAM'),      # Penthouse Roof Wall
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
    Convert level string to standard level name.
    Positive number -> floor (6 -> 6F)
    Negative number -> basement (-2 -> B2)
    P -> PH (Penthouse)
    R -> Roof
    """
    if not level_str:
        return str(level_str) if level_str is not None else ''

    level_str = level_str.strip()

    # Already standard
    if level_str in ('Roof', 'PH', 'PHR'):
        return level_str

    # P -> keep as 'P' (resolved later against StoryDefinition: PIT, PH, etc.)
    if level_str == 'P':
        return 'P'

    # R -> Roof
    if level_str == 'R':
        return 'Roof'

    # Already has F suffix or B prefix
    if re.match(r'^\d+F$', level_str) or re.match(r'^B\d+$', level_str):
        return level_str

    try:
        num = int(level_str)
        if num < 0:
            return f'B{abs(num)}'
        else:
            return f'{num}F'
    except (ValueError, TypeError):
        return str(level_str)


def _strip_annotation(name: str) -> str:
    """Remove trailing annotations like (sayOK) but keep structural parentheses."""
    # Remove known annotations
    name = re.sub(r'\s*\(say\w*\)\s*$', '', name, flags=re.IGNORECASE)
    return name.strip()


def _parse_level_token(token: str) -> str:
    """Parse a single level token: number, P, R, B1, etc."""
    token = token.strip()
    if token.upper() == 'P':
        return 'P'  # ambiguous: PIT or PH — resolved against StoryDefinition
    if token.upper() == 'PH':
        return 'PH'
    if token.upper() in ('R', 'ROOF'):
        return 'Roof'
    if token.upper() == 'PHR':
        return 'PHR'
    # B5, B1 — already has B prefix
    if re.match(r'^B\d+$', token, re.IGNORECASE):
        return token.upper()
    # Number
    try:
        num = int(token)
        if num < 0:
            return f'B{abs(num)}'
        else:
            return f'{num}F'
    except ValueError:
        return token


def parse_section_name(name: str) -> dict:
    """
    Parse MIDAS section name into structured data.

    Returns dict with:
        member_id: str      — member identifier (C1, TC1, G1, LB1)
        member_type: str    — COLUMN / BEAM / WALL
        level_from: str     — start level (6F, B2, Roof, PH) or None
        level_to: str       — end level or None
        raw_name: str       — original name
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

    # Strip annotations like (sayOK)
    name_clean = _strip_annotation(name)

    # ══════════════════════════════════════════════════════════
    # PROJECT 2 STYLE: member_id (level_range) — "TC1 (1-P)", "C1 (B5-B1)"
    # ══════════════════════════════════════════════════════════
    m = re.match(r'^([A-Za-z]+\d+[A-Za-z]*)\s*\(([^)]+)\)$', name_clean)
    if m:
        member_id = m.group(1)
        level_range = m.group(2).strip()
        # Parse level range: "1-P", "B5-B1"
        parts = re.split(r'[-~]', level_range)
        if len(parts) == 2:
            result['level_from'] = _parse_level_token(parts[0])
            result['level_to'] = _parse_level_token(parts[1])
        elif len(parts) == 1:
            result['level_from'] = _parse_level_token(parts[0])
        result['member_id'] = member_id
        result['member_type'] = classify_prefix(member_id)
        return result

    # ══════════════════════════════════════════════════════════
    # SPACE-SEPARATED: level_part + space + member_id
    # "1 B1", "-1~-4 G1", "P G8A", "3~R LB1", "1~P WG2"
    # ══════════════════════════════════════════════════════════
    if ' ' in name_clean:
        parts = name_clean.split(None, 1)  # split on first whitespace
        level_part = parts[0].strip()
        member_part = parts[1].strip()

        # Handle member_part with sub-range like "WB2~5" -> treat as member_id
        # Remove trailing ~ range from member_part
        member_id = re.sub(r'~\d+$', '', member_part)
        if not member_id:
            member_id = member_part

        # Parse level_part
        if '~' in level_part:
            # Range: "-1~-4", "3~R", "1~P", "-4~1"
            range_parts = level_part.split('~')
            if len(range_parts) == 2:
                result['level_from'] = _parse_level_token(range_parts[0])
                result['level_to'] = _parse_level_token(range_parts[1])
        else:
            # Single level: "1", "-4", "P", "R"
            result['level_from'] = _parse_level_token(level_part)

        result['member_id'] = member_id
        result['member_type'] = classify_prefix(member_id)
        return result

    # ══════════════════════════════════════════════════════════
    # PROJECT 1 STYLE (no space): level+member joined
    # ══════════════════════════════════════════════════════════

    # Pattern 1: level_range + member — "3~4TC1", "-2~-1TC1", "-4~-3BT1"
    m = re.match(r'^(-?\d+)~(-?\d+)([A-Za-z]+\d+[A-Za-z]*)$', name_clean)
    if m:
        result['level_from'] = normalize_level(m.group(1))
        result['level_to'] = normalize_level(m.group(2))
        result['member_id'] = m.group(3)
        result['member_type'] = classify_prefix(m.group(3))
        return result

    # Pattern 2: single_floor + member — "6C1", "-3BT1", "1G1"
    m = re.match(r'^(-?\d+)([A-Za-z]+\d+[A-Za-z]*)$', name_clean)
    if m:
        result['level_from'] = normalize_level(m.group(1))
        result['member_id'] = m.group(2)
        result['member_type'] = classify_prefix(m.group(2))
        return result

    # Pattern 3: R/PH/PHR prefix + member — "RG1", "PHRWG1", "PHG1"
    m = re.match(r'^(PHR?|R)([A-Za-z]*\d+[A-Za-z]*)$', name_clean)
    if m:
        prefix = m.group(1)
        rest = m.group(2)
        result['level_from'] = 'Roof' if prefix == 'R' else prefix
        result['member_id'] = rest
        result['member_type'] = classify_prefix(rest)
        return result

    # Pattern 4: simple member — "LB1", "WG1", "CG11", "TWG"
    m = re.match(r'^([A-Za-z]+\d*[A-Za-z]*)$', name_clean)
    if m:
        result['member_id'] = m.group(1)
        result['member_type'] = classify_prefix(m.group(1))
        return result

    # Fallback — couldn't parse
    result['member_type'] = 'UNKNOWN'
    return result
