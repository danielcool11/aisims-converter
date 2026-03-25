"""
Column reinforcement converter — transforms DesignColumn.csv into
ReinforcementColumn.csv.

Input:  DesignColumn.csv (2-row blocks per member)
        Row 1: MEMB=elem_id, SEL=sec_num, Section=name, fck, fy, CHK, LCB,
               V-Rebar, φPn-max, Pu, MF.y, Mcy, Mcz, LCB, H-Rebar.end, Vu.end, Rat-V.end
        Row 2: MEMB=sec_num, SEL=blank, Bc, Hc, Height, fys,
               Rat-P, MF.z, Rat-My, Rat-Mz, H-Rebar.mid, Vu.mid, Rat-V.mid
Output: ReinforcementColumn.csv
"""

import pandas as pd
from parsers.rebar_spec import parse_main_bar, parse_stirrup


def convert_reinforcement_column(
    design_col_df: pd.DataFrame,
    section_lookup: dict = None,
) -> pd.DataFrame:
    """
    Convert DesignColumn 2-row blocks into standardized ReinforcementColumn.

    Columns by position (0-indexed):
    Row1: 0:MEMB, 1:SEL, 2:Section, 3:C, 4:fck, 5:fy, 6:CHK, 7:LCB,
          8:V-Rebar, 9:φPn-max, 10:Pu, 11:MF.y, 12:Mcy, 13:Mcz,
          14:LCB, 15:H-Rebar.end, 16:Vu.end, 17:Rat-V.end
    Row2: 0:sec_num, 1:blank, 2:Bc, 3:Hc, 4:Height, 5:fys,
          6:blank, 7:blank, 8:blank, 9:blank, 10:Rat-P, 11:MF.z,
          12:Rat-My, 13:Rat-Mz, 14:blank, 15:H-Rebar.mid, 16:Vu.mid, 17:Rat-V.mid
    """

    results = []
    design_results = []
    data = design_col_df.values.tolist()

    # Skip header rows
    start_idx = 0
    for i, row_data in enumerate(data):
        first_val = str(row_data[0]).strip()
        if first_val not in ('MEMB', 'SECT', 'nan', ''):
            start_idx = i
            break

    # Process 2-row blocks
    i = start_idx
    while i + 1 < len(data):
        row1 = data[i]
        row2 = data[i + 1]

        # Validate: row1 should have a section name at col 2
        sec_name = str(row1[2]).strip() if len(row1) > 2 else ''
        if not sec_name or sec_name == 'nan':
            i += 1
            continue

        # Check that row2 has numeric Bc at col 2
        bc_val = _safe_float(row2[2]) if len(row2) > 2 else None
        if bc_val is None:
            i += 1
            continue

        elem_id = str(row1[0]).strip()
        sec_num = str(row1[1]).strip()
        fck = _safe_float(row1[4])
        fy = _safe_float(row1[5])

        # Row2 details
        b_mm = _safe_float(row2[2])
        h_mm = _safe_float(row2[3])
        height_mm = _safe_float(row2[4])
        fys = _safe_float(row2[5])

        # Get member info from section lookup
        member_id = sec_name
        if section_lookup and sec_num in section_lookup:
            member_id = section_lookup[sec_num].get('member_id', sec_name)

        # Vertical rebar (main bars) — col 8
        v_rebar_str = str(row1[8]).strip() if len(row1) > 8 else ''
        v_rebar = parse_main_bar(v_rebar_str)

        # Horizontal rebar end — col 15 row1
        h_rebar_end_str = str(row1[15]).strip() if len(row1) > 15 else ''
        h_rebar_end = parse_stirrup(h_rebar_end_str)

        # Horizontal rebar mid — col 15 row2
        h_rebar_mid_str = str(row2[15]).strip() if len(row2) > 15 else ''
        h_rebar_mid = parse_stirrup(h_rebar_mid_str)

        # Design ratios
        rat_p = _safe_float(row2[10])
        rat_my = _safe_float(row2[12])
        rat_mz = _safe_float(row2[13])
        rat_v_end = _safe_float(row1[17])
        rat_v_mid = _safe_float(row2[17])

        # Reinforcement record (rebar only)
        record = {
            'element_id': elem_id,
            'member_id': member_id,
            'section_id': sec_num,
            'fck_MPa': fck,
            'fy_MPa': fy,
            'fys_MPa': fys,
            'b_mm': b_mm,
            'h_mm': h_mm,
            'height_mm': height_mm,
            'main_bar_spec': v_rebar_str if v_rebar else None,
            'main_total': v_rebar['total'] if v_rebar else None,
            'main_count': v_rebar['main'] if v_rebar else None,
            'main_additional': v_rebar['additional'] if v_rebar else None,
            'main_dia_mm': v_rebar['dia'] if v_rebar else None,
            'tie_end_spec': h_rebar_end_str if h_rebar_end else None,
            'tie_end_legs': h_rebar_end['legs'] if h_rebar_end else None,
            'tie_end_dia_mm': h_rebar_end['dia'] if h_rebar_end else None,
            'tie_end_spacing_mm': h_rebar_end['spacing'] if h_rebar_end else None,
            'tie_mid_spec': h_rebar_mid_str if h_rebar_mid else None,
            'tie_mid_legs': h_rebar_mid['legs'] if h_rebar_mid else None,
            'tie_mid_dia_mm': h_rebar_mid['dia'] if h_rebar_mid else None,
            'tie_mid_spacing_mm': h_rebar_mid['spacing'] if h_rebar_mid else None,
        }
        results.append(record)

        # Design results record (capacity + ratios)
        phiPn_max = _safe_float(row1[9])
        Pu = _safe_float(row1[10])
        Vu_end = _safe_float(row1[16])
        Vu_mid = _safe_float(row2[16])

        design_record = {
            'member_id': member_id,
            'fck_MPa': fck,
            'fy_MPa': fy,
            'fys_MPa': fys,
            'b_mm': b_mm,
            'h_mm': h_mm,
            'height_mm': height_mm,
            'main_bar_spec': v_rebar_str if v_rebar else None,
            'phiPn_max': phiPn_max,
            'Pu': Pu,
            'ratio_axial': rat_p,
            'ratio_moment_y': rat_my,
            'ratio_moment_z': rat_mz,
            'Vu_end': Vu_end,
            'ratio_shear_end': rat_v_end,
            'Vu_mid': Vu_mid,
            'ratio_shear_mid': rat_v_mid,
        }
        design_results.append(design_record)

        i += 2  # next block

    result_df = pd.DataFrame(results)
    design_df = pd.DataFrame(design_results)
    print(f'[ReinfColumn] {len(result_df)} columns')

    return result_df, design_df


def _safe_float(val):
    if val is None:
        return None
    try:
        v = float(val)
        return v if not pd.isna(v) else None
    except (ValueError, TypeError):
        return None
