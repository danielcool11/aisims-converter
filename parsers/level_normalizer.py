"""
Level name normalizer — maps all level format variants to
canonical StoryDefinition names.

Two-stage approach:
1. normalize_level(): handles obvious mappings (R->Roof, 1->1F, -2->B2)
2. resolve_ambiguous_levels(): matches ambiguous tokens (P) against
   actual StoryDefinition names from the project

Variants found in data:
    '1', '2', '6'        -> '1F', '2F', '6F'     (StairReinf, SlabReinf)
    'R'                  -> 'Roof'                (StairReinf, SlabReinf)
    'B1', 'B2', 'B3'    -> 'B1', 'B2', 'B3'     (already canonical)
    'PHR'                -> 'PHR'                 (Penthouse Roof)
    'Roof'               -> 'Roof'                (already canonical)
    '1F', '7F'           -> '1F', '7F'            (already canonical)
    'P'                  -> 'PIT' or 'PH'         (project-dependent)
"""

import re
import pandas as pd


def normalize_level(level: str) -> str:
    """
    Normalize a level string to canonical StoryDefinition format.
    Handles obvious cases. Ambiguous tokens like 'P' pass through unchanged.

    Returns the canonical level name.
    """
    if not level or not isinstance(level, str):
        return str(level) if level is not None else ''

    level = level.strip()

    # Already canonical
    if level in ('Roof', 'PHR', 'PH', 'PIT', 'P'):
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


def resolve_ambiguous_levels(df: pd.DataFrame, story_names: list,
                             level_columns: list = None) -> None:
    """
    Resolve ambiguous level tokens (like 'P') against actual StoryDefinition names.
    Modifies DataFrame in-place.

    Args:
        df: DataFrame with level columns to resolve
        story_names: list of actual story names from StoryDefinition
            e.g., ['Roof', '15F', ..., 'PIT', '2F', '1F', 'B1', ..., 'B5']
        level_columns: list of column names to resolve (auto-detect if None)
    """
    if level_columns is None:
        level_columns = [c for c in df.columns
                         if 'level' in c.lower() or c in ('level', 'position')]

    # Build mapping for ambiguous tokens
    # P -> match against story names containing P (PIT, PH, Penthouse, etc.)
    story_upper = {s.upper(): s for s in story_names}

    ambiguous_map = {}

    # Try to resolve 'P'
    if 'P' not in story_upper:
        for s_upper, s_orig in story_upper.items():
            if s_upper.startswith('P') and s_upper not in ('PHR',):
                ambiguous_map['P'] = s_orig
                break

    # Try to resolve 'PH'
    if 'PH' not in story_upper:
        for s_upper, s_orig in story_upper.items():
            if s_upper in ('PENTHOUSE', 'PH', 'PHF'):
                ambiguous_map['PH'] = s_orig
                break

    if not ambiguous_map:
        return

    for col in level_columns:
        if col not in df.columns:
            continue
        df[col] = df[col].apply(
            lambda x: ambiguous_map.get(str(x).strip(), x) if pd.notna(x) else x
        )


def build_story_names(story_df: pd.DataFrame) -> list:
    """Extract story names from StoryDefinition DataFrame."""
    names = []
    for col in story_df.columns:
        if 'story' in col.lower() and 'name' in col.lower():
            names = story_df[col].dropna().astype(str).str.strip().tolist()
            break
    return names


def normalize_level_column(df, column: str) -> None:
    """Normalize a level column in a DataFrame in-place."""
    if column in df.columns:
        df[column] = df[column].apply(
            lambda x: normalize_level(str(x)) if pd.notna(x) else x
        )
