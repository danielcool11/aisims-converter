"""
Slab reinforcement converter — expands compact SlabReinforcement.csv
into standardized ReinforcementSlab.csv.

Input:  SlabReinforcement.csv (member_id, position, slab_type, thickness_mm,
                               X_Top, X_Bot, Y_Top, Y_Bot)
        Each row = 1 slab → expanded to 4 rows (X_Top, X_Bot, Y_Top, Y_Bot)
Output: ReinforcementSlab.csv
"""

import pandas as pd
from parsers.rebar_spec import parse_bar_at_spacing, parse_composite_bar
from parsers.level_normalizer import normalize_level


def convert_reinforcement_slab(
    slab_reinf_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Expand compact slab reinforcement into standardized rows.

    Each input row expands to 4 rows:
        direction=X, layer=Top
        direction=X, layer=Bot
        direction=Y, layer=Top
        direction=Y, layer=Bot

    For composite bars (D16+13), each expands further into 2 rows
    with doubled spacing.
    """

    # Normalize columns
    col_map = {}
    for col in slab_reinf_df.columns:
        cl = col.strip().lower().replace('㎜', 'mm').replace('.', '')
        if cl == 'member_id':
            col_map[col] = 'member_id'
        elif cl == 'position':
            col_map[col] = 'position'
        elif cl == 'slab_type':
            col_map[col] = 'slab_type'
        elif 'thickness' in cl:
            col_map[col] = 'thickness_mm'
        elif cl == 'x_top':
            col_map[col] = 'x_top'
        elif cl == 'x_bot':
            col_map[col] = 'x_bot'
        elif cl == 'y_top':
            col_map[col] = 'y_top'
        elif cl == 'y_bot':
            col_map[col] = 'y_bot'
    slab_reinf_df = slab_reinf_df.rename(columns=col_map)

    results = []

    for _, row in slab_reinf_df.iterrows():
        member_id = str(row.get('member_id', '')).strip()
        if not member_id:
            continue

        position = normalize_level(str(row.get('position', '')).strip())
        thickness = _safe_float(row.get('thickness_mm'))

        # Expand 4 directions
        for direction, layer, col_name in [
            ('X', 'Top', 'x_top'),
            ('X', 'Bot', 'x_bot'),
            ('Y', 'Top', 'y_top'),
            ('Y', 'Bot', 'y_bot'),
        ]:
            spec_str = str(row.get(col_name, '')).strip()
            if not spec_str or spec_str == 'nan':
                continue

            # Check for composite bars
            composite = parse_composite_bar(spec_str)
            base_spec = parse_bar_at_spacing(spec_str)

            if len(composite) > 1 and base_spec:
                # Composite: D16+13@200 → two rows with spacing×2
                for bar in composite:
                    results.append({
                        'member_id': member_id,
                        'level': position,
                        'direction': direction,
                        'layer': layer,
                        'bar_spec': f"D{bar['dia']}@{base_spec['spacing'] * 2}",
                        'bar_dia_mm': bar['dia'],
                        'bar_spacing_mm': base_spec['spacing'] * 2,
                        'thickness_mm': thickness,
                    })
            elif base_spec:
                # Simple: D10@200
                results.append({
                    'member_id': member_id,
                    'level': position,
                    'direction': direction,
                    'layer': layer,
                    'bar_spec': spec_str,
                    'bar_dia_mm': base_spec['dia'],
                    'bar_spacing_mm': base_spec['spacing'],
                    'thickness_mm': thickness,
                })

    result_df = pd.DataFrame(results)
    n_input = len(slab_reinf_df)
    print(f'[ReinfSlab] {n_input} slabs expanded to {len(result_df)} reinforcement rows')

    return result_df


def _safe_float(val):
    if val is None:
        return None
    try:
        v = float(val)
        return v if not pd.isna(v) else None
    except (ValueError, TypeError):
        return None
