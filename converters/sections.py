"""
Sections converter — transforms raw MIDAS sections + thickness data
into standardized Sections.csv.

Input:  Sections.csv (ID, Name, Shape, Size(H), Size(B), Area, Iyy, Izz, ...)
        Thickness.csv (ID, NAME, Thick-In_mm)
        CoverRequirements.csv (member_type, cover_mm)
Output: Sections.csv (section_id, member_type, member_id, level_from, level_to,
                       shape, b_mm, h_mm, area_m2, inertia_y_m4, inertia_z_m4,
                       effective_depth_mm, cover_mm)
"""

import pandas as pd
import os
from parsers.section_name import parse_section_name


def load_cover_requirements(config_path: str = None) -> dict:
    """Load cover requirements from CSV. Returns {member_type: cover_mm}."""
    default = {'BEAM': 50, 'COLUMN': 50, 'WALL': 50, 'SLAB': 30, 'FOOTING': 75, 'STAIR': 30}

    if config_path and os.path.exists(config_path):
        df = pd.read_csv(config_path)
        for _, row in df.iterrows():
            default[row['member_type']] = float(row['cover_mm'])

    return default


def convert_sections(
    sections_df: pd.DataFrame,
    thickness_df: pd.DataFrame = None,
    cover_path: str = None,
) -> tuple:
    """
    Convert raw MIDAS sections to standardized format.

    Args:
        sections_df: DataFrame from Sections.csv
        thickness_df: DataFrame from Thickness.csv (wall thicknesses)
        cover_path: path to CoverRequirements.csv

    Returns:
        tuple: (sections_result_df, section_lookup_dict, thickness_lookup_dict)
            section_lookup: {section_number: {section_id, member_id, member_type, b_mm, h_mm}}
            thickness_lookup: {thickness_id: thickness_mm}
    """

    cover_req = load_cover_requirements(cover_path)

    # Build thickness lookup
    thickness_lookup = {}
    if thickness_df is not None:
        for _, row in thickness_df.iterrows():
            tid = str(row.iloc[0]).strip()  # ID column
            # Find thickness column
            thick_val = None
            for col in thickness_df.columns:
                if 'thick' in col.lower() and 'in' in col.lower():
                    val = row[col]
                    if pd.notna(val) and str(val).strip() != '' and str(val).strip() != '0':
                        thick_val = float(val)
                        break
            if thick_val:
                thickness_lookup[tid] = thick_val

    # Normalize section column names
    col_map = {}
    for col in sections_df.columns:
        cl = col.strip().lower().replace('㎜', 'mm').replace('㎟', 'mm2').replace('㎜4', 'mm4')
        if cl == 'id':
            col_map[col] = 'sec_id'
        elif cl == 'name':
            col_map[col] = 'sec_name'
        elif cl == 'shape':
            col_map[col] = 'shape_raw'
        elif 'size' in cl and 'h' in cl:
            col_map[col] = 'h_mm'
        elif 'size' in cl and 'b' in cl:
            col_map[col] = 'b_mm'
        elif cl.startswith('area'):
            col_map[col] = 'area_mm2'
        elif cl.startswith('iyy'):
            col_map[col] = 'iyy_mm4'
        elif cl.startswith('izz'):
            col_map[col] = 'izz_mm4'
    sections_df = sections_df.rename(columns=col_map)

    results = []
    section_lookup = {}

    for _, row in sections_df.iterrows():
        sec_id = str(row.get('sec_id', '')).strip()
        sec_name = str(row.get('sec_name', '')).strip()

        if not sec_name:
            continue

        # Parse section name
        parsed = parse_section_name(sec_name)

        if parsed['member_type'] == 'SKIP':
            continue

        # Determine shape
        shape_raw = str(row.get('shape_raw', '')).upper()
        if 'SB' in shape_raw:
            shape = 'RECT'
        elif 'SR' in shape_raw:
            shape = 'CIRCLE'
        else:
            shape = 'RECT'  # default

        # Dimensions
        h_mm = float(row['h_mm']) if pd.notna(row.get('h_mm')) else None
        b_mm = float(row['b_mm']) if pd.notna(row.get('b_mm')) else None

        # Get cover
        member_type = parsed['member_type']
        cover = cover_req.get(member_type, 50)

        # Compute effective depth
        eff_depth = None
        if h_mm:
            eff_depth = h_mm - cover - 11  # assume D22/2 ≈ 11mm

        # Unit conversions
        area_m2 = float(row['area_mm2']) / 1e6 if pd.notna(row.get('area_mm2')) else None
        iyy_m4 = float(row['iyy_mm4']) / 1e12 if pd.notna(row.get('iyy_mm4')) else None
        izz_m4 = float(row['izz_mm4']) / 1e12 if pd.notna(row.get('izz_mm4')) else None

        # Build section_id
        level_part = ''
        if parsed.get('level_from'):
            level_part = f"_{parsed['level_from']}"
            if parsed.get('level_to'):
                level_part += f"_{parsed['level_to']}"
        section_id = f"RC_{parsed['member_id']}{level_part}"

        record = {
            'section_id': section_id,
            'member_type': member_type,
            'member_id': parsed['member_id'],
            'level_from': parsed.get('level_from'),
            'level_to': parsed.get('level_to'),
            'shape': shape,
            'b_mm': b_mm,
            'h_mm': h_mm,
            'diameter_mm': None,
            'thickness_mm': None,
            'area_m2': area_m2,
            'inertia_y_m4': iyy_m4,
            'inertia_z_m4': izz_m4,
            'effective_depth_mm': eff_depth,
            'cover_mm': cover,
        }
        results.append(record)

        # Store in lookup (by original section number)
        section_lookup[sec_id] = {
            'section_id': section_id,
            'member_id': parsed['member_id'],
            'member_type': member_type,
            'b_mm': b_mm,
            'h_mm': h_mm,
            'raw_name': sec_name,
        }

    result_df = pd.DataFrame(results)

    # Log summary
    types = result_df['member_type'].value_counts().to_dict() if not result_df.empty else {}
    print(f'[Sections] {len(result_df)} sections parsed:')
    for mtype, count in sorted(types.items()):
        print(f'  {mtype}: {count}')
    print(f'[Sections] Thickness lookup: {len(thickness_lookup)} entries')

    return result_df, section_lookup, thickness_lookup
