"""
Beam reinforcement converter — transforms DesignBeam.csv into
ReinforcementBeam.csv.

Input:  DesignBeam.csv (3-row blocks per member: I/M/J positions)
        Row 1 (I): MEMB=elem_id, SEL=sec_num, Section=name, fck, POS=I,
                   Negative rebar, Positive rebar, Stirrup
        Row 2 (M): MEMB=sec_num, SEL=blank, bf, hf, POS=M, ...
        Row 3 (J): MEMB=span, SEL=blank, bf=0, hf=0, POS=J, ...
Output: ReinforcementBeam.csv
"""

import pandas as pd
from parsers.rebar_spec import parse_main_bar, parse_stirrup


def convert_reinforcement_beam(
    design_beam_df: pd.DataFrame,
    section_lookup: dict = None,
) -> pd.DataFrame:
    """
    Convert DesignBeam 3-row blocks into standardized ReinforcementBeam.

    Args:
        design_beam_df: DataFrame from DesignBeam.csv (raw, skip header rows)
        section_lookup: section_lookup dict from sections converter

    Returns:
        DataFrame for ReinforcementBeam.csv
    """

    # The CSV has 3 header rows (merged cells), skip them and use positional columns
    # Columns by position (0-indexed):
    # 0:MEMB, 1:SEL, 2:Section/Bc/bf, 3:C/Hc/hf, 4:fck/fy/fys, 5:POS,
    # 6:CHK, 7:Neg_Rebar, 8:Neg_As, 9:Neg_Mu, 10:Neg_LCB, 11:Neg_phiMn, 12:Rat_N,
    # 13:Pos_Rebar, 14:Pos_As, 15:Pos_Mu, 16:Pos_LCB, 17:Pos_phiMn, 18:Rat_P,
    # 19:Stirrup, 20:Vu, 21:Shear_LCB, 22:phiVc, 23:Rat_V

    results = []

    # Read raw data — skip the 3 header rows
    data = design_beam_df.values.tolist()

    # Find where actual data starts (skip rows starting with MEMB, SECT, Span)
    start_idx = 0
    for i, row_data in enumerate(data):
        first_val = str(row_data[0]).strip()
        if first_val not in ('MEMB', 'SECT', 'Span', 'nan', ''):
            start_idx = i
            break

    # Process 3-row blocks
    i = start_idx
    while i + 2 < len(data):
        row_i = data[i]      # I position (start)
        row_m = data[i + 1]  # M position (mid)
        row_j = data[i + 2]  # J position (end)

        # Validate this is a proper 3-row block
        pos_i = str(row_i[5]).strip() if len(row_i) > 5 else ''
        pos_m = str(row_m[5]).strip() if len(row_m) > 5 else ''
        pos_j = str(row_j[5]).strip() if len(row_j) > 5 else ''

        if pos_i != 'I':
            i += 1
            continue

        # Extract member info from row_i
        elem_id = str(row_i[0]).strip()
        sec_num = str(row_i[1]).strip()
        sec_name = str(row_i[2]).strip()
        fck = _safe_float(row_i[4])

        # Get member info from section lookup
        member_id = sec_name
        if section_lookup and sec_num in section_lookup:
            member_id = section_lookup[sec_num].get('member_id', sec_name)

        # Span (from row_j col 0, or row_m col 0)
        span = _safe_float(row_j[0])

        # Dimensions from row_m
        b_mm = _safe_float(row_m[2])  # Bc / bf
        h_mm = _safe_float(row_m[3])  # Hc / hf
        fy = _safe_float(row_m[4])    # fy
        fys = _safe_float(row_j[4])   # fys (stirrup yield)

        # Process each position (I, M, J)
        for pos_label, row_data in [('I', row_i), ('M', row_m), ('J', row_j)]:
            # Negative moment rebar (col 7)
            neg_rebar_str = str(row_data[7]).strip() if len(row_data) > 7 else ''
            neg_rebar = parse_main_bar(neg_rebar_str)

            # Positive moment rebar (col 13)
            pos_rebar_str = str(row_data[13]).strip() if len(row_data) > 13 else ''
            pos_rebar = parse_main_bar(pos_rebar_str)

            # Stirrup (col 19)
            stirrup_str = str(row_data[19]).strip() if len(row_data) > 19 else ''
            stirrup = parse_stirrup(stirrup_str)

            # Design ratios
            rat_n = _safe_float(row_data[12]) if len(row_data) > 12 else None
            rat_p = _safe_float(row_data[18]) if len(row_data) > 18 else None
            rat_v = _safe_float(row_data[23]) if len(row_data) > 23 else None

            record = {
                'element_id': elem_id if pos_label == 'I' else None,
                'member_id': member_id,
                'section_id': sec_num,
                'position': pos_label,
                'fck_MPa': fck,
                'fy_MPa': fy,
                'fys_MPa': fys,
                'bar_role': f'top_{pos_label}',
                'top_bar_spec': neg_rebar_str if neg_rebar else None,
                'top_total': neg_rebar['total'] if neg_rebar else None,
                'top_main': neg_rebar['main'] if neg_rebar else None,
                'top_additional': neg_rebar['additional'] if neg_rebar else None,
                'top_dia_mm': neg_rebar['dia'] if neg_rebar else None,
                'bot_bar_spec': pos_rebar_str if pos_rebar else None,
                'bot_total': pos_rebar['total'] if pos_rebar else None,
                'bot_main': pos_rebar['main'] if pos_rebar else None,
                'bot_additional': pos_rebar['additional'] if pos_rebar else None,
                'bot_dia_mm': pos_rebar['dia'] if pos_rebar else None,
                'stirrup_spec': stirrup_str if stirrup else None,
                'stirrup_legs': stirrup['legs'] if stirrup else None,
                'stirrup_dia_mm': stirrup['dia'] if stirrup else None,
                'stirrup_spacing_mm': stirrup['spacing'] if stirrup else None,
                'ratio_negative': rat_n,
                'ratio_positive': rat_p,
                'ratio_shear': rat_v,
            }
            results.append(record)

        i += 3  # next block

    result_df = pd.DataFrame(results)
    n_members = len(result_df) // 3 if len(result_df) > 0 else 0
    print(f'[ReinfBeam] {n_members} beams × 3 positions = {len(result_df)} rows')

    return result_df


def _safe_float(val):
    """Convert to float safely."""
    if val is None:
        return None
    try:
        v = float(val)
        return v if not pd.isna(v) else None
    except (ValueError, TypeError):
        return None
