"""
Wall reinforcement converter — transforms DesignWall.csv into
ReinforcementWall.csv.

Input:  DesignWall.csv (2-row blocks per wall element)
        Row 1: Wall ID, SEL, Wall Mark, D, fck, fy, CHK, LCB,
               V-Rebar, End-Rebar, φPn-max, Rat-Py, MF.y, Mcy, Rat-My,
               Vu, CHK, V-Rebar, ρ.max, ρ.use, ρ.min, s.max, s.use,
               H-Rebar ρ.use, ρ.min, s.max, s.use
        Row 2: Story, blank, Lw, HTw, hw, fys, ..., H-Rebar, Bar Layer,
               Pu, Rat-Pz, MF.z, Mcz, Rat-Mz, Rat-V, ...
Output: ReinforcementWall.csv (reinforcement only)
        DesignResultsWall.csv (geometry + design ratios)
"""

import pandas as pd
from parsers.rebar_spec import parse_bar_at_spacing


def convert_reinforcement_wall(
    design_wall_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Convert DesignWall 2-row blocks into standardized ReinforcementWall.

    Columns by position (0-indexed):
    Row1: 0:WallID, 1:SEL, 2:WallMark, 3:D, 4:fck, 5:fy, 6:CHK, 7:LCB,
          8:V-Rebar, 9:End-Rebar, 10:φPn-max, 11:Rat-Py, 12:MF.y, 13:Mcy, 14:Rat-My,
          15:Vu, 16:CHK, 17:V-Rebar(shear), 18:ρ.max, 19:ρ.use, 20:ρ.min,
          21:s.max, 22:s.use(V), 23:H-Rebar ρ.use, 24:ρ.min, 25:s.max, 26:s.use(H)
    Row2: 0:Story, 1:blank, 2:Lw, 3:HTw, 4:hw, 5:fys,
          8:H-Rebar, 9:BarLayer, 10:Pu, 11:Rat-Pz, 12:MF.z, 13:Mcz, 14:Rat-Mz,
          15:Rat-V
    """

    results = []
    design_results = []
    data = design_wall_df.values.tolist()

    # Skip header rows
    start_idx = 0
    for i, row_data in enumerate(data):
        first_val = str(row_data[0]).strip()
        if first_val not in ('Wall ID', 'Story', 'Wall', 'nan', ''):
            try:
                int(first_val)
                start_idx = i
                break
            except ValueError:
                continue

    # Process 2-row blocks
    i = start_idx
    while i + 1 < len(data):
        row1 = data[i]
        row2 = data[i + 1]

        # Validate: row1 should have Wall Mark at col 2
        wall_mark = str(row1[2]).strip() if len(row1) > 2 else ''
        story = str(row2[0]).strip() if len(row2) > 0 else ''

        if not wall_mark or wall_mark == 'nan':
            i += 1
            continue

        wall_id = str(row1[0]).strip()
        fck = _safe_float(row1[4])
        fy = _safe_float(row1[5])
        fys = _safe_float(row2[5])

        # Geometry from row2
        lw_mm = _safe_float(row2[2])    # wall length
        htw_mm = _safe_float(row2[3])   # wall total height
        hw_mm = _safe_float(row2[4])    # wall thickness

        # Vertical rebar (col 8 row1)
        v_rebar_str = str(row1[8]).strip() if len(row1) > 8 else ''
        v_rebar = parse_bar_at_spacing(v_rebar_str)

        # End rebar (col 9 row1)
        end_rebar_str = str(row1[9]).strip() if len(row1) > 9 else ''

        # Horizontal rebar (col 8 row2)
        h_rebar_str = str(row2[8]).strip() if len(row2) > 8 else ''
        h_rebar = parse_bar_at_spacing(h_rebar_str)

        # Bar layer (col 9 row2)
        bar_layer = str(row2[9]).strip() if len(row2) > 9 else ''

        # Design ratios
        rat_py = _safe_float(row1[11])
        rat_my = _safe_float(row1[14])
        rat_v = _safe_float(row2[15]) if len(row2) > 15 else None

        # Reinforcement data only (geometry → MembersWall, ratios → DesignResultsWall)
        record = {
            'wall_mark': wall_mark,
            'level': story,
            'v_bar_spec': v_rebar_str if v_rebar_str and v_rebar_str != 'nan' else None,
            'v_dia_mm': v_rebar['dia'] if v_rebar else None,
            'v_spacing_mm': v_rebar['spacing'] if v_rebar else None,
            'h_bar_spec': h_rebar_str if h_rebar_str and h_rebar_str != 'nan' else None,
            'h_dia_mm': h_rebar['dia'] if h_rebar else None,
            'h_spacing_mm': h_rebar['spacing'] if h_rebar else None,
            'bar_layer': bar_layer if bar_layer and bar_layer != 'nan' else None,
            'end_rebar': end_rebar_str if end_rebar_str and 'Not Use' not in end_rebar_str else None,
        }
        results.append(record)

        # Store design results for separate DesignResultsWall output
        design_record = {
            'wall_mark': wall_mark,
            'level': story,
            'fck_MPa': fck,
            'fy_MPa': fy,
            'fys_MPa': fys,
            'lw_mm': lw_mm,
            'htw_mm': htw_mm,
            'thickness_mm': hw_mm,
            'ratio_axial': rat_py,
            'ratio_moment': rat_my,
            'ratio_shear': rat_v,
        }
        design_results.append(design_record)

        i += 2

    result_df = pd.DataFrame(results)
    design_df = pd.DataFrame(design_results)
    print(f'[ReinfWall] {len(result_df)} wall elements')

    return result_df, design_df


def _safe_float(val):
    if val is None:
        return None
    try:
        v = float(val)
        return v if not pd.isna(v) else None
    except (ValueError, TypeError):
        return None
