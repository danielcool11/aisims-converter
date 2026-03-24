"""
Stair reinforcement converter — expands StairReinforcement.csv into
ReinforcementStair.csv.

Input:  StairReinforcement.csv — each row has 8 rebar specs:
        landing(Left) transverse Top/Bot, longitudinal Top/Bot
        Stair transverse Top/Bot, longitudinal Top/Bot
Output: ReinforcementStair.csv — expanded rows per rebar position
"""

import pandas as pd
from parsers.rebar_spec import parse_bar_at_spacing, parse_composite_bar


def convert_reinforcement_stair(
    stair_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Expand stair reinforcement into standardized rows.

    Each stair expands to up to 8 rows:
        zone=landing_left × direction=transverse/longitudinal × layer=Top/Bot
        zone=stair × direction=transverse/longitudinal × layer=Top/Bot
    """

    # Map columns by examining actual header names
    # Expected columns after the geometry:
    # landing(Left)_transverse_Top, landing(Left)_transverse_Bot,
    # landing(Left)_logitudinal_Top, landing(Left)_logitudinal_Bot,
    # Stair_transverse_Top, Stair_transverse_Bot,
    # Stair_logitudinal_Top, Stair_logitudinal_Bot

    rebar_mappings = []
    for col in stair_df.columns:
        cl = col.strip().lower()
        zone = None
        direction = None
        layer = None

        if 'landing' in cl and 'left' in cl:
            zone = 'landing_left'
        elif 'landing' in cl and 'right' in cl:
            zone = 'landing_right'
        elif 'stair' in cl and ('transverse' in cl or 'logitudinal' in cl or 'longitudinal' in cl):
            zone = 'stair'

        if 'transverse' in cl:
            direction = 'transverse'
        elif 'logitudinal' in cl or 'longitudinal' in cl:
            direction = 'longitudinal'

        if cl.endswith('top'):
            layer = 'Top'
        elif cl.endswith('bot') or cl.endswith('bot.'):
            layer = 'Bot'

        if zone and direction and layer:
            rebar_mappings.append((col, zone, direction, layer))

    results = []

    for _, row in stair_df.iterrows():
        member_id = str(row.get('member_id', row.iloc[0] if len(row) > 0 else '')).strip()
        if not member_id:
            continue

        # Get level info
        level_from = None
        level_to = None
        for col in stair_df.columns:
            cl = col.strip().lower()
            if 'level_start' in cl:
                level_from = str(row[col]).strip()
            elif 'level_end' in cl:
                level_to = str(row[col]).strip()

        for col_name, zone, direction, layer in rebar_mappings:
            spec_str = str(row.get(col_name, '')).strip()
            if not spec_str or spec_str == 'nan':
                continue

            # Check composite
            composite = parse_composite_bar(spec_str)
            base_spec = parse_bar_at_spacing(spec_str)

            if len(composite) > 1 and base_spec:
                for bar in composite:
                    results.append({
                        'member_id': member_id,
                        'level_from': level_from,
                        'level_to': level_to,
                        'zone': zone,
                        'direction': direction,
                        'layer': layer,
                        'bar_spec': f"D{bar['dia']}@{base_spec['spacing'] * 2}",
                        'bar_dia_mm': bar['dia'],
                        'bar_spacing_mm': base_spec['spacing'] * 2,
                    })
            elif base_spec:
                results.append({
                    'member_id': member_id,
                    'level_from': level_from,
                    'level_to': level_to,
                    'zone': zone,
                    'direction': direction,
                    'layer': layer,
                    'bar_spec': spec_str,
                    'bar_dia_mm': base_spec['dia'],
                    'bar_spacing_mm': base_spec['spacing'],
                })

    result_df = pd.DataFrame(results)
    n_input = len(stair_df)
    print(f'[ReinfStair] {n_input} stairs expanded to {len(result_df)} reinforcement rows')

    return result_df
