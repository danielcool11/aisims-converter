"""
Rebar specification string parser.

Handles MIDAS Gen rebar notation formats:
    Main bars:   "12-4-D22"  → total=16, main=12, add=4, dia=22
                 "3-D22"     → total=3, main=3, add=0, dia=22
                 "2-2-D13"   → total=4, main=2, add=2, dia=13
    Stirrups:    "2-D10 @200" → legs=2, dia=10, spacing=200
                 "3-D13 @150" → legs=3, dia=13, spacing=150
    Bar@spacing: "D10@200"    → dia=10, spacing=200
                 "D13@150"    → dia=13, spacing=150
    Composite:   "D16+13"     → [dia=16, dia=13] (alternating)
"""

import re
from typing import Optional


def parse_main_bar(spec: str) -> Optional[dict]:
    """
    Parse main bar specification.

    "12-4-D22"  → {total: 16, main: 12, additional: 4, dia: 22}
    "3-D22"     → {total: 3, main: 3, additional: 0, dia: 22}
    "2-2-D13"   → {total: 4, main: 2, additional: 2, dia: 13}

    Returns None if cannot parse.
    """
    if not spec or not isinstance(spec, str):
        return None

    spec = spec.strip()
    if spec == '' or spec == '0':
        return None

    # Pattern: N1-N2-D## (main-additional-diameter)
    m = re.match(r'(\d+)-(\d+)-D(\d+)', spec)
    if m:
        main = int(m.group(1))
        add = int(m.group(2))
        dia = int(m.group(3))
        return {'total': main + add, 'main': main, 'additional': add, 'dia': dia}

    # Pattern: N-D## (total bars, no additional)
    m = re.match(r'(\d+)-D(\d+)', spec)
    if m:
        total = int(m.group(1))
        dia = int(m.group(2))
        return {'total': total, 'main': total, 'additional': 0, 'dia': dia}

    return None


def parse_stirrup(spec: str) -> Optional[dict]:
    """
    Parse stirrup specification.

    "2-D10 @200"  → {legs: 2, dia: 10, spacing: 200}
    "3-D13 @150"  → {legs: 3, dia: 13, spacing: 150}

    Returns None if cannot parse.
    """
    if not spec or not isinstance(spec, str):
        return None

    spec = spec.strip()
    if spec == '':
        return None

    m = re.match(r'(\d+)-D(\d+)\s*@\s*(\d+)', spec)
    if m:
        return {
            'legs': int(m.group(1)),
            'dia': int(m.group(2)),
            'spacing': int(m.group(3)),
        }

    return None


def parse_bar_at_spacing(spec: str) -> Optional[dict]:
    """
    Parse bar@spacing format (used in wall/slab reinforcement).

    "D10@200"   → {dia: 10, spacing: 200}
    "D13@150"   → {dia: 13, spacing: 150}
    "D13 @150"  → {dia: 13, spacing: 150}

    Returns None if cannot parse.
    """
    if not spec or not isinstance(spec, str):
        return None

    spec = spec.strip()
    if spec == '' or spec == 'Not Use' or 'Not Use' in spec:
        return None

    m = re.match(r'[A-Za-z]*D?(\d+)\s*@\s*(\d+)', spec)
    if m:
        return {
            'dia': int(m.group(1)),
            'spacing': int(m.group(2)),
        }

    return None


def parse_composite_bar(spec: str) -> list:
    """
    Parse composite bar specification (alternating bars).

    "D16+13"    → [{dia: 16}, {dia: 13}]
    "D16+D13"   → [{dia: 16}, {dia: 13}]
    "D16"       → [{dia: 16}]

    Returns list of dicts. For composite bars with spacing (e.g., in walls),
    the original spacing is doubled for each individual bar type.
    """
    if not spec or not isinstance(spec, str):
        return []

    spec = spec.strip()
    if spec == '':
        return []

    # Check for composite: D16+13 or D16+D13
    if '+' in spec:
        parts = spec.split('+')
        results = []
        for part in parts:
            part = part.strip()
            m = re.search(r'(\d+)', part)
            if m:
                results.append({'dia': int(m.group(1))})
        return results

    # Single bar
    m = re.search(r'D(\d+)', spec)
    if m:
        return [{'dia': int(m.group(1))}]

    return []


def extract_dia_from_bar_size(bar_size: str) -> Optional[int]:
    """
    Extract numeric diameter from bar_size string.

    "D16"    → 16
    "HD13"   → 13
    "SHD16"  → 16
    "D16+13" → 16 (primary bar)

    Returns None if cannot parse.
    """
    if not bar_size or not isinstance(bar_size, str):
        return None

    bar_size = bar_size.strip()
    if bar_size == '':
        return None

    # For composite: take first number
    if '+' in bar_size:
        m = re.search(r'(\d+)', bar_size)
        return int(m.group(1)) if m else None

    # Strip all leading letters, get first number
    m = re.search(r'(\d+)', bar_size)
    return int(m.group(1)) if m else None
