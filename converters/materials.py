"""
Materials converter — transforms raw MIDAS materials + MGT rebar grades
into standardized Materials.csv.

Input:  Materials.csv (ID, Name, Type, DB, Elasticity, Poisson, Density, ...)
        MGT parsed data (rebar_grades from *DGNCRITERIA)
Output: Materials.csv (material_id, type, grade, fck_MPa, fy_MPa, E_MPa, density_kN_m3)
        material_map: {raw_midas_id: {concrete_grade, fck, fy_main, fy_sub}}
        dia_fy_map: {diameter_mm: fy_MPa} — project-level rebar grade by diameter
"""

import pandas as pd
import re
from collections import Counter


def _parse_fy_from_grade(grade_str):
    """Extract fy from rebar grade string: 'SD600' → 600, ' SD400' → 400."""
    if not grade_str or not isinstance(grade_str, str):
        return None
    m = re.search(r'(\d+)', grade_str.strip())
    return int(m.group(1)) if m else None


def _build_material_map(materials_df):
    """Build per-MIDAS-ID material property map from raw Materials.csv.

    Returns:
        material_map: {int_id: {concrete_grade, fck, fy_main, fy_sub}}
        dia_fy_map: {dia_mm: fy_MPa} — project-level diameter→fy mapping
    """
    material_map = {}

    # Find relevant column names (handle Korean headers)
    id_col = 'ID'
    fck_col = None
    grade_main_col = None
    grade_sub_col = None
    fy_col = None
    fys_col = None

    for col in materials_df.columns:
        cl = col.lower().strip()
        if 'fck' in cl:
            fck_col = col
        elif 'grade' in cl and 'main' in cl:
            grade_main_col = col
        elif 'grade' in cl and 'sub' in cl:
            grade_sub_col = col
        elif cl.startswith('fy') and 'fys' not in cl and 's' not in cl.replace('fy', '', 1)[:1]:
            fy_col = col
        elif 'fys' in cl:
            fys_col = col

    # Also try direct fy/fys columns
    if not fy_col:
        for col in materials_df.columns:
            if col.strip().startswith('fy') and 'fys' not in col and col.strip() != 'fys':
                fy_col = col
                break
    if not fys_col:
        for col in materials_df.columns:
            if 'fys' in col.lower():
                fys_col = col
                break

    for _, row in materials_df.iterrows():
        mat_type = str(row.get('Type', '')).strip()
        if mat_type.lower() != 'concrete':
            continue

        raw_id = row.get(id_col)
        if pd.isna(raw_id):
            continue
        try:
            raw_id = int(float(raw_id))
        except (ValueError, TypeError):
            continue

        # Concrete grade from DB column
        grade = str(row.get('DB', '')).strip()
        fck_match = re.search(r'C(\d+)', grade)
        fck = int(fck_match.group(1)) if fck_match else None

        # Rebar grades (may be blank for vertical-only materials)
        fy_main = None
        fy_sub = None
        if grade_main_col and pd.notna(row.get(grade_main_col)):
            fy_main = _parse_fy_from_grade(str(row[grade_main_col]))
        if grade_sub_col and pd.notna(row.get(grade_sub_col)):
            fy_sub = _parse_fy_from_grade(str(row[grade_sub_col]))

        # Fallback: use fy/fys numeric columns if grade columns didn't parse
        if fy_main is None and fy_col and pd.notna(row.get(fy_col)):
            try:
                fy_main = int(float(row[fy_col]))
            except (ValueError, TypeError):
                pass
        if fy_sub is None and fys_col and pd.notna(row.get(fys_col)):
            try:
                fy_sub = int(float(row[fys_col]))
            except (ValueError, TypeError):
                pass

        material_map[raw_id] = {
            'concrete_grade': grade if grade else f'C{fck}' if fck else 'C35',
            'fck': fck,
            'fy_main': fy_main if fy_main and fy_main > 0 else None,
            'fy_sub': fy_sub if fy_sub and fy_sub > 0 else None,
        }

    # Build project-level diameter→fy map from horizontal materials (those with rebar grades)
    # Find the most common fy_main/fy_sub pair
    fy_main_vals = [m['fy_main'] for m in material_map.values() if m['fy_main']]
    fy_sub_vals = [m['fy_sub'] for m in material_map.values() if m['fy_sub']]
    dominant_fy_main = Counter(fy_main_vals).most_common(1)[0][0] if fy_main_vals else 600
    dominant_fy_sub = Counter(fy_sub_vals).most_common(1)[0][0] if fy_sub_vals else 400

    # D10/D13 = sub rebar, D16+ = main rebar
    dia_fy_map = {}
    for d in [10, 13]:
        dia_fy_map[d] = dominant_fy_sub
    for d in [16, 19, 22, 25, 29, 32, 35]:
        dia_fy_map[d] = dominant_fy_main

    return material_map, dia_fy_map


def convert_materials(
    materials_df: pd.DataFrame,
    mgt_data: dict = None,
) -> tuple:
    """
    Convert raw MIDAS materials to standardized format.

    Args:
        materials_df: DataFrame from Materials.csv
        mgt_data: parsed MGT data (from parsers.mgt.parse_mgt)

    Returns:
        (materials_df, material_map, dia_fy_map):
            materials_df: DataFrame with columns [material_id, type, grade, fck_MPa, fy_MPa, E_MPa, density_kN_m3]
            material_map: {raw_midas_id: {concrete_grade, fck, fy_main, fy_sub}}
            dia_fy_map: {dia_mm: fy_MPa}
    """
    # Build material_map from raw data before dedup
    material_map, dia_fy_map = _build_material_map(materials_df)

    results = []

    # ── 1. Concrete materials from Materials sheet ──
    seen_grades = set()

    for _, row in materials_df.iterrows():
        mat_type = str(row.get('Type', '')).strip()
        if mat_type.lower() != 'concrete':
            continue

        grade = str(row.get('DB', '')).strip()
        if not grade or grade in seen_grades:
            continue
        seen_grades.add(grade)

        # Extract fck from grade string: "C35" → 35
        fck_match = re.search(r'C(\d+)', grade)
        fck = int(fck_match.group(1)) if fck_match else None

        # Elasticity: N/mm2 = MPa (direct, no conversion needed)
        e_col = None
        for col in materials_df.columns:
            if 'elastic' in col.lower():
                e_col = col
                break
        E_mpa = float(row[e_col]) if e_col and pd.notna(row[e_col]) else None

        # Density: N/mm3 → kN/m3
        # N/mm3 × 1e9 mm3/m3 = N/m3, then ÷ 1000 = kN/m3
        density_col = None
        for col in materials_df.columns:
            cl = col.lower()
            if 'density' in cl and 'mass' not in cl:
                density_col = col
                break
        if density_col and pd.notna(row[density_col]):
            density_raw = float(row[density_col])
            if density_raw < 1:  # N/mm3 format (e.g., 2.35E-05)
                density_kn_m3 = density_raw * 1e9 / 1000
            else:  # already in kN/m3
                density_kn_m3 = density_raw
        else:
            density_kn_m3 = 25.0  # default concrete

        results.append({
            'material_id': grade,
            'type': 'concrete',
            'grade': grade,
            'fck_MPa': fck,
            'fy_MPa': None,
            'E_MPa': E_mpa,
            'density_kN_m3': round(density_kn_m3, 1),
        })

    # ── 2. Rebar materials from MGT ──
    if mgt_data and 'rebar_grades' in mgt_data:
        for fy_mpa, diameters in sorted(mgt_data['rebar_grades'].items()):
            grade = f'SD{fy_mpa}'
            results.append({
                'material_id': grade,
                'type': 'rebar',
                'grade': grade,
                'fck_MPa': None,
                'fy_MPa': fy_mpa,
                'E_MPa': 200000,
                'density_kN_m3': 78.5,
            })

    # ── 3. Fallback: if no MGT, derive from design data ──
    if not mgt_data or not mgt_data.get('rebar_grades'):
        # Add default SD400 if no rebar info at all
        if not any(r['type'] == 'rebar' for r in results):
            results.append({
                'material_id': 'SD400',
                'type': 'rebar',
                'grade': 'SD400',
                'fck_MPa': None,
                'fy_MPa': 400,
                'E_MPa': 200000,
                'density_kN_m3': 78.5,
            })

    result_df = pd.DataFrame(results)

    # Log summary
    concrete = [r for r in results if r['type'] == 'concrete']
    rebar = [r for r in results if r['type'] == 'rebar']
    print(f'[Materials] {len(results)} materials: '
          f'{len(concrete)} concrete ({", ".join(r["grade"] for r in concrete)}), '
          f'{len(rebar)} rebar ({", ".join(r["grade"] for r in rebar)})')
    if material_map:
        mapped = [(k, v['concrete_grade'], v['fy_main'], v['fy_sub'])
                  for k, v in sorted(material_map.items())]
        print(f'[Materials] material_map: {len(mapped)} entries')
        for mid, grade, fym, fys in mapped:
            print(f'  ID {mid}: {grade}, fy_main={fym}, fy_sub={fys}')
    print(f'[Materials] dia_fy_map: D10/D13={dia_fy_map.get(10)}, D16+={dia_fy_map.get(16)}')

    return result_df, material_map, dia_fy_map
