"""
Level name normalizer — maps all level format variants to
canonical StoryDefinition names.

Variants found in data:
    '1', '2', '6'        -> '1F', '2F', '6F'     (StairReinf, SlabReinf)
    'R'                  -> 'Roof'                (StairReinf, SlabReinf)
    'B1', 'B2', 'B3'    -> 'B1', 'B2', 'B3'     (already canonical)
    'PHR'                -> 'PHR'                 (Penthouse Roof)
    'Roof'               -> 'Roof'                (already canonical)
    '1F', '7F'           -> '1F', '7F'            (already canonical)
"""

import re


def normalize_level(level: str) -> str:
    """
    Normalize a level string to canonical StoryDefinition format.

    Returns the canonical level name.
    """
    if not level or not isinstance(level, str):
        return str(level) if level is not None else ''

    level = level.strip()

    # Already canonical
    if level in ('Roof', 'PHR'):
        return level

    # Already has F suffix (1F, 7F) or B prefix (B1, B4)
    if re.match(r'^\d+F$', level) or re.match(r'^B\d+$', level):
        return level

    # 'R' -> 'Roof'
    if level == 'R':
        return 'Roof'

    # Bare number: '1' -> '1F', '6' -> '6F'
    if re.match(r'^\d+$', level):
        return f'{level}F'

    # Negative number: '-2' -> 'B2'
    m = re.match(r'^-(\d+)$', level)
    if m:
        return f'B{m.group(1)}'

    # Unknown format — return as-is
    return level


def normalize_level_column(df, column: str) -> None:
    """Normalize a level column in a DataFrame in-place."""
    if column in df.columns:
        df[column] = df[column].apply(
            lambda x: normalize_level(str(x)) if pd.notna(x) else x
        )


# Need pandas for normalize_level_column
import pandas as pd
